from __future__ import annotations

# ruff: noqa: I001

import hashlib
from dataclasses import dataclass, replace
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.governance_routes import _evaluate
from app.api.integration_capability_routes import _catalog
from app.api.product_common import utcnow
from app.bootstrap.settings import settings
from app.domain.actions.gateway import (
    ActionGateway,
    ActionProposal,
    AuthorizationDecision,
)
from app.domain.identity.principals import AgentPrincipal, HumanPrincipal
from app.execution.authorization import UniversalActionRequest, action_fingerprint
from app.execution.bootstrap import execution_provider_registry
from app.execution.provider_executor import (
    DatabaseProviderContextLoader,
    UniversalProviderExecutor,
)
from app.infrastructure.database.models import (
    Action,
    AgentIdentity,
    Approval,
    CapabilityProfile,
    Integration,
    Job,
    JobRevision,
    Result,
    ResultVersion,
    Run,
    RunStep,
    Worker,
    WorkItem,
)
from app.infrastructure.ai.provider import model_provider_from_settings
from app.infrastructure.database.outbox import TransactionalOutbox
from app.runtime.planner.adaptive import AdaptivePlanDecision, AdaptiveRuntimePlanner


RuntimeState = Literal["completed", "continue", "waiting_approval", "failed", "noop"]


@dataclass(frozen=True, slots=True)
class RuntimeStepOutcome:
    state: RuntimeState
    work_item_id: UUID
    run_id: UUID
    current_step: int
    summary: str


@dataclass(frozen=True, slots=True)
class _StaticGuard:
    outcome: str
    reason: str

    async def evaluate(self, *, principal: AgentPrincipal, proposal: ActionProposal) -> AuthorizationDecision:
        del principal, proposal
        return AuthorizationDecision(outcome=self.outcome, reason=self.reason)


def _system_principal(organization_id: UUID) -> HumanPrincipal:
    zero = UUID(int=0)
    return HumanPrincipal(
        user_id=zero,
        organization_id=organization_id,
        membership_id=zero,
        role="system",
        permissions=frozenset({"policies.read"}),
    )


def _runtime_meta(item: WorkItem) -> dict[str, Any]:
    payload = dict(item.payload or {})
    raw = payload.get("runtime")
    return dict(raw) if isinstance(raw, dict) else {}


def _write_runtime_meta(item: WorkItem, **patch: object) -> dict[str, Any]:
    payload = dict(item.payload or {})
    runtime = _runtime_meta(item)
    runtime.update(patch)
    payload["runtime"] = runtime
    item.payload = payload
    return runtime


def _step_output(step: RunStep) -> dict[str, Any]:
    return dict(step.output or {}) if isinstance(step.output, dict) else {}


def _integration_config(integration: Integration | None) -> dict[str, Any]:
    return dict(integration.config or {}) if integration and isinstance(integration.config, dict) else {}


class ManagedRuntimeExecutor:
    """Durable, provider-neutral Managed Runtime executed one step at a time.

    QStash drives this class through signed HTTP calls. Each call performs at most
    one governed capability step, checkpoints state in PostgreSQL, and lets the
    caller queue the next continuation.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._registry = execution_provider_registry()
        self._provider_executor = UniversalProviderExecutor(
            registry=self._registry,
            context_loader=DatabaseProviderContextLoader(session, self._registry),
        )
        self._planner = AdaptiveRuntimePlanner(model_provider_from_settings())

    async def execute_step(
        self,
        *,
        item: WorkItem,
        expected_step: int | None = None,
    ) -> RuntimeStepOutcome:
        job, revision, worker, agent = await self._load_context(item)
        run = await self._ensure_run(item)
        steps = await self._ensure_plan(item, run, job, revision)

        meta = _runtime_meta(item)
        current_step = max(0, int(meta.get("currentStep", 0)))
        if expected_step is not None and expected_step != current_step:
            return RuntimeStepOutcome(
                state="noop",
                work_item_id=item.id,
                run_id=run.id,
                current_step=current_step,
                summary="Stale or duplicate runtime delivery ignored.",
            )

        if run.status in {"completed", "failed", "cancelled"}:
            return RuntimeStepOutcome(
                state="noop",
                work_item_id=item.id,
                run_id=run.id,
                current_step=current_step,
                summary=f"Run is already {run.status}.",
            )

        if current_step >= len(steps):
            return await self._complete(item, run, job, worker, steps)

        step = steps[current_step]
        if step.status == "completed":
            _write_runtime_meta(item, currentStep=current_step + 1)
            await self._session.commit()
            return RuntimeStepOutcome(
                state="continue",
                work_item_id=item.id,
                run_id=run.id,
                current_step=current_step + 1,
                summary="Completed step checkpoint advanced.",
            )

        if step.status == "waiting_approval":
            return await self._resume_approved_action(
                item=item,
                run=run,
                step=step,
                job=job,
                worker=worker,
                agent=agent,
                current_step=current_step,
            )

        if step.kind == "finish":
            return await self._complete(item, run, job, worker, steps)

        return await self._execute_action_step(
            item=item,
            run=run,
            step=step,
            job=job,
            worker=worker,
            agent=agent,
            current_step=current_step,
        )

    async def _load_context(
        self, item: WorkItem
    ) -> tuple[Job, JobRevision, Worker, AgentIdentity]:
        job = await self._session.scalar(
            select(Job).where(
                Job.organization_id == item.organization_id,
                Job.id == item.job_id,
            )
        )
        revision = await self._session.scalar(
            select(JobRevision).where(
                JobRevision.id == item.job_revision_id,
                JobRevision.job_id == item.job_id,
            )
        )
        if job is None or revision is None:
            raise RuntimeError("Runtime job definition is unavailable.")
        worker = await self._session.scalar(
            select(Worker).where(
                Worker.organization_id == item.organization_id,
                Worker.id == job.worker_id,
            )
        )
        if worker is None:
            raise RuntimeError("Runtime worker is unavailable.")
        agent = await self._session.scalar(
            select(AgentIdentity).where(
                AgentIdentity.organization_id == item.organization_id,
                AgentIdentity.id == worker.agent_identity_id,
            )
        )
        if agent is None:
            raise RuntimeError("Runtime agent identity is unavailable.")
        if job.status != "active":
            raise RuntimeError("Runtime Job must be ACTIVE.")
        if worker.status != "active":
            raise RuntimeError("Runtime Worker must be ACTIVE.")
        if agent.status != "active":
            raise RuntimeError("Runtime Agent Identity must be ACTIVE.")
        return job, revision, worker, agent

    async def _ensure_run(self, item: WorkItem) -> Run:
        run = await self._session.scalar(
            select(Run)
            .where(Run.work_item_id == item.id)
            .order_by(desc(Run.created_at))
            .limit(1)
        )
        now = utcnow()
        new_run = run is None or (
            item.status == "queued"
            and run is not None
            and run.status in {"completed", "failed", "cancelled"}
        )
        if new_run:
            run = Run(
                organization_id=item.organization_id,
                work_item_id=item.id,
                status="running",
                correlation_id=item.correlation_id,
            )
            self._session.add(run)
            await self._session.flush()
            await TransactionalOutbox(self._session).enqueue(
                topic="run.created",
                aggregate_type="run",
                aggregate_id=str(run.id),
                payload={
                    "organization_id": str(item.organization_id),
                    "run_id": str(run.id),
                    "job_id": str(item.job_id),
                    "correlation_id": item.correlation_id,
                    "status": "running",
                },
            )
        elif run is not None and run.status not in {
            "waiting_approval",
            "completed",
            "failed",
            "cancelled",
        }:
            run.status = "running"

        assert run is not None
        item.status = "running"
        meta = _runtime_meta(item)
        if new_run or not meta.get("startedAt"):
            _write_runtime_meta(
                item,
                attempt=int(meta.get("attempt", 1)),
                currentStep=int(meta.get("currentStep", 0)),
                startedAt=now.isoformat(),
            )
            await TransactionalOutbox(self._session).enqueue(
                topic="run.started",
                aggregate_type="run",
                aggregate_id=str(run.id),
                payload={
                    "organization_id": str(item.organization_id),
                    "run_id": str(run.id),
                    "job_id": str(item.job_id),
                    "correlation_id": item.correlation_id,
                },
            )
        await self._session.flush()
        return run

    async def _ensure_plan(
        self,
        item: WorkItem,
        run: Run,
        job: Job,
        revision: JobRevision,
    ) -> list[RunStep]:
        existing = list(
            (
                await self._session.scalars(
                    select(RunStep)
                    .where(RunStep.run_id == run.id)
                    .order_by(RunStep.step_index)
                )
            ).all()
        )
        if existing:
            return existing

        definition = dict(revision.definition or {}) if isinstance(revision.definition, dict) else {}
        required = [str(scope) for scope in definition.get("requiredCapabilities", []) if str(scope)]
        catalog = await _catalog(self._session, item.organization_id)
        resources = list(catalog.get("resources", []))
        steps: list[RunStep] = []

        for index, scope in enumerate(required):
            resource = next(
                (
                    candidate
                    for candidate in resources
                    if scope in list(candidate.get("scopes", []))
                    and candidate.get("status") == "connected"
                ),
                None,
            )
            if resource is None:
                raise RuntimeError(f"Required runtime capability is unavailable: {scope}.")
            action = next(
                (entry for entry in list(resource.get("actions", [])) if entry.get("scope") == scope),
                None,
            )
            if action is None:
                raise RuntimeError(f"Runtime capability has no executable operation: {scope}.")
            steps.append(
                RunStep(
                    run_id=run.id,
                    step_index=index,
                    kind="action",
                    status="pending",
                    input={
                        "title": f"Use {scope}",
                        "instruction": str(definition.get("instructions", "")) or str(definition.get("objective", "")),
                        "resourceId": str(resource["id"]),
                        "scope": scope,
                        "provider": str(resource["provider"]),
                        "operation": str(action["providerOperation"]),
                    },
                    output={},
                )
            )

        steps.append(
            RunStep(
                run_id=run.id,
                step_index=len(steps),
                kind="finish",
                status="pending",
                input={
                    "title": "Finish",
                    "instruction": "Verify the durable capability steps completed and publish the Result.",
                    "resourceId": "",
                    "scope": "",
                },
                output={},
            )
        )
        self._session.add_all(steps)
        plan_summary = (
            f"Execute {len(required)} governed capability step(s) for {job.name}."
            if required
            else f"Complete {job.name} with no external capability step."
        )
        _write_runtime_meta(item, planSummary=plan_summary, currentStep=0)
        await self._session.flush()
        return steps

    async def _active_scopes(self, agent_id: UUID) -> frozenset[str]:
        rows = list(
            (
                await self._session.scalars(
                    select(CapabilityProfile).where(
                        CapabilityProfile.agent_id == agent_id,
                        CapabilityProfile.active.is_(True),
                    )
                )
            ).all()
        )
        return frozenset(row.scope for row in rows)

    async def _action_input(
        self,
        *,
        item: WorkItem,
        step: RunStep,
    ) -> dict[str, object]:
        spec = dict(step.input or {})
        scope = str(spec.get("scope", ""))
        operation = str(spec.get("operation", ""))
        payload = dict(item.payload or {})
        explicit = payload.get("actionInputs")
        if isinstance(explicit, dict):
            raw = explicit.get(scope, explicit.get(operation))
            if isinstance(raw, dict):
                return {str(key): value for key, value in raw.items()}

        resource_id = str(spec.get("resourceId", ""))
        integration: Integration | None = None
        try:
            integration = await self._session.get(Integration, UUID(resource_id))
        except ValueError:
            integration = None
        config = _integration_config(integration)

        if scope == "browser.navigation.open":
            start_url = str(config.get("startUrl", "")).strip()
            if start_url:
                return {"url": start_url}

        if scope.startswith("browser."):
            session_id = await self._latest_browser_session(step.run_id, step.step_index)
            if session_id and scope in {
                "browser.page.read",
                "browser.navigation.back",
                "browser.navigation.forward",
                "browser.navigation.reload",
            }:
                return {"sessionId": session_id}

        provider = self._registry.get(str(spec.get("provider", "")))
        capability = next(
            (entry for entry in provider.manifest.capabilities if entry.scope == scope),
            None,
        )
        if capability is None:
            raise RuntimeError(f"Runtime capability is not registered: {scope}.")
        required = capability.input_schema.get("required", [])
        if not required:
            return {}
        raise RuntimeError(
            f"Runtime capability {scope} needs structured input. "
            f"Provide trigger payload actionInputs['{scope}']."
        )

    async def _latest_browser_session(self, run_id: UUID, before_index: int) -> str | None:
        rows = list(
            (
                await self._session.scalars(
                    select(RunStep)
                    .where(
                        RunStep.run_id == run_id,
                        RunStep.step_index < before_index,
                        RunStep.status == "completed",
                    )
                    .order_by(desc(RunStep.step_index))
                )
            ).all()
        )
        for row in rows:
            data = _step_output(row)
            nested = data.get("data")
            nested = nested if isinstance(nested, dict) else {}
            provider_output = nested.get("output")
            provider_output = provider_output if isinstance(provider_output, dict) else {}
            session = provider_output.get("session")
            session = session if isinstance(session, dict) else {}
            raw_id = session.get("id")
            if raw_id:
                return str(raw_id)
        return None

    async def _policy_decision(
        self,
        *,
        item: WorkItem,
        agent: AgentIdentity,
        resource_id: str,
        scope: str,
    ) -> dict[str, Any]:
        return await _evaluate(
            self._session,
            item.organization_id,
            _system_principal(item.organization_id),
            agent_id=agent.id,
            resource_id=resource_id,
            scope=scope,
        )

    async def _universal_request(
        self,
        *,
        proposal: ActionProposal,
        worker: Worker,
        job: Job,
        item: WorkItem,
        run: Run,
    ) -> UniversalActionRequest:
        prepared = await self._provider_executor.prepare(proposal)
        execution = replace(
            prepared.execution,
            worker_id=worker.id,
            job_id=job.id,
            work_item_id=item.id,
            run_id=run.id,
        )
        return replace(prepared, execution=execution)

    async def _execute_action_step(
        self,
        *,
        item: WorkItem,
        run: Run,
        step: RunStep,
        job: Job,
        worker: Worker,
        agent: AgentIdentity,
        current_step: int,
    ) -> RuntimeStepOutcome:
        spec = dict(step.input or {})
        resource_id = str(spec.get("resourceId", ""))
        scope = str(spec.get("scope", ""))
        provider = str(spec.get("provider", ""))
        operation = str(spec.get("operation", ""))
        decision = await self._policy_decision(
            item=item,
            agent=agent,
            resource_id=resource_id,
            scope=scope,
        )
        input_payload = await self._action_input(item=item, step=step)
        proposal = ActionProposal(
            organization_id=item.organization_id,
            agent_id=agent.id,
            provider=provider,
            operation=operation,
            scope=scope,
            resource_id=resource_id,
            payload=input_payload,
            correlation_id=item.correlation_id,
            idempotency_key=f"runtime:{item.id}:{current_step}",
            risk=str((decision.get("riskAssessment") or {}).get("effectiveRisk") or "low"),
        )
        universal = await self._universal_request(
            proposal=proposal,
            worker=worker,
            job=job,
            item=item,
            run=run,
        )
        fingerprint = action_fingerprint(universal)
        existing = await self._session.scalar(
            select(Action).where(
                Action.organization_id == item.organization_id,
                Action.idempotency_key == proposal.idempotency_key,
            )
        )
        if existing is not None and existing.status == "executed":
            step.status = "completed"
            step.output = dict((existing.payload or {}).get("result") or {})
            _write_runtime_meta(item, currentStep=current_step + 1)
            await self._session.commit()
            return RuntimeStepOutcome(
                state="continue",
                work_item_id=item.id,
                run_id=run.id,
                current_step=current_step + 1,
                summary="Previously executed action checkpoint reused.",
            )

        if decision["outcome"] == "DENY":
            action = existing or Action(
                organization_id=item.organization_id,
                agent_id=agent.id,
                run_id=run.id,
                status="blocked",
                resource_id=resource_id,
                scope=scope,
                idempotency_key=proposal.idempotency_key,
                payload={},
            )
            if existing is None:
                self._session.add(action)
            action.status = "blocked"
            action.payload = self._action_record_payload(
                proposal=proposal,
                decision=decision,
                fingerprint=fingerprint,
                result=None,
                error=str(decision["reason"]),
            )
            await self._fail(
                item=item,
                run=run,
                step=step,
                message=str(decision["reason"]),
            )
            return RuntimeStepOutcome(
                state="failed",
                work_item_id=item.id,
                run_id=run.id,
                current_step=current_step,
                summary=str(decision["reason"]),
            )

        if decision["outcome"] == "REQUIRE_APPROVAL":
            action = existing or Action(
                organization_id=item.organization_id,
                agent_id=agent.id,
                run_id=run.id,
                status="held",
                resource_id=resource_id,
                scope=scope,
                idempotency_key=proposal.idempotency_key,
                payload={},
            )
            if existing is None:
                self._session.add(action)
                await self._session.flush()
            approval = await self._session.scalar(
                select(Approval).where(Approval.action_id == action.id)
            )
            if approval is None:
                approval = Approval(
                    organization_id=item.organization_id,
                    action_id=action.id,
                    status="pending",
                    decided_by=None,
                    decision_reason=None,
                )
                self._session.add(approval)
                await self._session.flush()
            action.status = "held"
            action.payload = self._action_record_payload(
                proposal=proposal,
                decision=decision,
                fingerprint=fingerprint,
                result=None,
                error=None,
            )
            step.status = "waiting_approval"
            step.output = {
                "actionId": str(action.id),
                "approvalId": str(approval.id),
                "actionFingerprint": fingerprint,
            }
            run.status = "waiting_approval"
            item.status = "waiting_approval"
            await self._queue_action_events(
                item=item,
                action=action,
                approval=approval,
                event="approval.created",
            )
            await self._session.commit()
            return RuntimeStepOutcome(
                state="waiting_approval",
                work_item_id=item.id,
                run_id=run.id,
                current_step=current_step,
                summary="Action is waiting for human approval.",
            )

        return await self._execute_authorized(
            item=item,
            run=run,
            step=step,
            job=job,
            worker=worker,
            agent=agent,
            proposal=proposal,
            universal=universal,
            decision=decision,
            current_step=current_step,
            existing=existing,
        )

    async def _resume_approved_action(
        self,
        *,
        item: WorkItem,
        run: Run,
        step: RunStep,
        job: Job,
        worker: Worker,
        agent: AgentIdentity,
        current_step: int,
    ) -> RuntimeStepOutcome:
        output = _step_output(step)
        action_id = output.get("actionId")
        if not action_id:
            await self._fail(item=item, run=run, step=step, message="Held step lost its Action reference.")
            return RuntimeStepOutcome("failed", item.id, run.id, current_step, "Held step is invalid.")
        action = await self._session.get(Action, UUID(str(action_id)))
        if action is None:
            await self._fail(item=item, run=run, step=step, message="Held Action is unavailable.")
            return RuntimeStepOutcome("failed", item.id, run.id, current_step, "Held Action is unavailable.")
        approval = await self._session.scalar(select(Approval).where(Approval.action_id == action.id))
        if approval is None or approval.status == "pending":
            item.status = "waiting_approval"
            run.status = "waiting_approval"
            await self._session.commit()
            return RuntimeStepOutcome(
                "waiting_approval", item.id, run.id, current_step, "Waiting for human approval."
            )
        if approval.status != "approved":
            await self._fail(
                item=item,
                run=run,
                step=step,
                message="Human approval rejected the action.",
            )
            return RuntimeStepOutcome(
                "failed", item.id, run.id, current_step, "Human approval rejected the action."
            )

        payload = dict(action.payload or {})
        request = payload.get("request")
        request = request if isinstance(request, dict) else {}
        decision = payload.get("policy")
        decision = decision if isinstance(decision, dict) else {}
        proposal = ActionProposal(
            organization_id=item.organization_id,
            agent_id=agent.id,
            provider=str(request.get("provider", "")),
            operation=str(request.get("operation", "")),
            scope=action.scope,
            resource_id=action.resource_id,
            payload=dict(request.get("input") or {}),
            correlation_id=item.correlation_id,
            idempotency_key=action.idempotency_key,
            risk=str(request.get("risk") or "low"),
        )
        universal = await self._universal_request(
            proposal=proposal,
            worker=worker,
            job=job,
            item=item,
            run=run,
        )
        universal = replace(
            universal,
            approval_id=str(approval.id),
            approval_status="approved",
            approval_action_fingerprint=str(payload.get("actionFingerprint") or ""),
        )
        return await self._execute_authorized(
            item=item,
            run=run,
            step=step,
            job=job,
            worker=worker,
            agent=agent,
            proposal=proposal,
            universal=universal,
            decision={
                "outcome": "REQUIRE_APPROVAL",
                "reason": str(decision.get("reason") or "Human approval required."),
            },
            current_step=current_step,
            existing=action,
        )

    async def _execute_authorized(
        self,
        *,
        item: WorkItem,
        run: Run,
        step: RunStep,
        job: Job,
        worker: Worker,
        agent: AgentIdentity,
        proposal: ActionProposal,
        universal: UniversalActionRequest,
        decision: dict[str, Any],
        current_step: int,
        existing: Action | None,
    ) -> RuntimeStepOutcome:
        scopes = await self._active_scopes(agent.id)
        principal = AgentPrincipal(
            agent_id=agent.id,
            organization_id=item.organization_id,
            credential_fingerprint="internal-managed-runtime",
            capabilities=scopes,
            risk_level=proposal.risk,
            policy_context=(),
        )
        action = existing or Action(
            organization_id=item.organization_id,
            agent_id=agent.id,
            run_id=run.id,
            status="processing",
            resource_id=proposal.resource_id,
            scope=proposal.scope,
            idempotency_key=proposal.idempotency_key,
            payload={},
        )
        if existing is None:
            self._session.add(action)
            await self._session.flush()
        action.status = "processing"
        await TransactionalOutbox(self._session).enqueue(
            topic="action.proposed",
            aggregate_type="action",
            aggregate_id=str(action.id),
            payload={
                "organization_id": str(item.organization_id),
                "action_id": str(action.id),
                "resource_id": proposal.resource_id,
                "run_id": str(run.id),
                "correlation_id": item.correlation_id,
            },
        )
        await self._session.commit()

        try:
            gateway = ActionGateway(
                (_StaticGuard(str(decision["outcome"]), str(decision["reason"])),),
                self._provider_executor,
            )
            result = await gateway.execute_request(principal=principal, request=universal)
        except (LookupError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
            action.status = "failed"
            action.payload = self._action_record_payload(
                proposal=proposal,
                decision=decision,
                fingerprint=action_fingerprint(universal),
                result=None,
                error=str(exc),
            )
            await self._fail(item=item, run=run, step=step, message=str(exc))
            return RuntimeStepOutcome(
                "failed", item.id, run.id, current_step, str(exc)
            )

        action.status = "executed"
        action.payload = self._action_record_payload(
            proposal=proposal,
            decision=decision,
            fingerprint=action_fingerprint(universal),
            result={"summary": result.summary, "data": result.data},
            error=None,
        )
        step.status = "completed"
        step.output = {
            "actionId": str(action.id),
            "summary": result.summary,
            "data": result.data,
        }
        run.status = "running"
        item.status = "running"
        _write_runtime_meta(item, currentStep=current_step + 1)
        await TransactionalOutbox(self._session).enqueue(
            topic="run.progress",
            aggregate_type="run",
            aggregate_id=str(run.id),
            payload={
                "organization_id": str(item.organization_id),
                "run_id": str(run.id),
                "job_id": str(job.id),
                "current_step": current_step + 1,
                "correlation_id": item.correlation_id,
            },
        )
        await self._session.commit()
        return RuntimeStepOutcome(
            "continue",
            item.id,
            run.id,
            current_step + 1,
            result.summary,
        )

    def _action_record_payload(
        self,
        *,
        proposal: ActionProposal,
        decision: dict[str, Any],
        fingerprint: str,
        result: dict[str, Any] | None,
        error: str | None,
    ) -> dict[str, Any]:
        return {
            "correlationId": proposal.correlation_id,
            "idempotencyHash": hashlib.sha256(proposal.idempotency_key.encode()).hexdigest(),
            "actionFingerprint": fingerprint,
            "request": {
                "provider": proposal.provider,
                "operation": proposal.operation,
                "scope": proposal.scope,
                "resourceId": proposal.resource_id,
                "input": proposal.payload,
                "risk": proposal.risk,
            },
            "policy": {
                "outcome": decision.get("outcome"),
                "reason": decision.get("reason"),
            },
            "result": result,
            "error": error,
            "completedAt": utcnow().isoformat() if result is not None or error else None,
        }

    async def _queue_action_events(
        self,
        *,
        item: WorkItem,
        action: Action,
        approval: Approval,
        event: str,
    ) -> None:
        outbox = TransactionalOutbox(self._session)
        await outbox.enqueue(
            topic="action.proposed",
            aggregate_type="action",
            aggregate_id=str(action.id),
            payload={
                "organization_id": str(item.organization_id),
                "action_id": str(action.id),
                "resource_id": action.resource_id,
                "run_id": str(action.run_id) if action.run_id else None,
                "correlation_id": item.correlation_id,
            },
        )
        await outbox.enqueue(
            topic=event,
            aggregate_type="approval",
            aggregate_id=str(approval.id),
            payload={
                "organization_id": str(item.organization_id),
                "approval_id": str(approval.id),
                "action_id": str(action.id),
                "resource_id": action.resource_id,
                "run_id": str(action.run_id) if action.run_id else None,
                "correlation_id": item.correlation_id,
            },
        )

    async def _fail(
        self,
        *,
        item: WorkItem,
        run: Run,
        step: RunStep,
        message: str,
    ) -> None:
        step.status = "failed"
        output = _step_output(step)
        output["error"] = message[:4000]
        step.output = output
        run.status = "failed"
        run.result_summary = message[:4000]
        item.status = "failed"
        payload = dict(item.payload or {})
        payload["lastError"] = message[:1000]
        payload["completedAt"] = utcnow().isoformat()
        item.payload = payload
        _write_runtime_meta(item, failure=message[:4000], completedAt=utcnow().isoformat())
        await TransactionalOutbox(self._session).enqueue(
            topic="run.failed",
            aggregate_type="run",
            aggregate_id=str(run.id),
            payload={
                "organization_id": str(item.organization_id),
                "run_id": str(run.id),
                "job_id": str(item.job_id),
                "correlation_id": item.correlation_id,
                "error": message[:1000],
            },
        )
        await self._session.commit()

    async def _complete(
        self,
        item: WorkItem,
        run: Run,
        job: Job,
        worker: Worker,
        steps: list[RunStep],
    ) -> RuntimeStepOutcome:
        current = max(0, int(_runtime_meta(item).get("currentStep", 0)))
        if current < len(steps):
            finish = steps[current]
            if finish.kind == "finish":
                finish.status = "completed"
                finish.output = {"output": "Runtime completion checkpoint verified."}

        action_summaries = [
            str(_step_output(step).get("summary") or "").strip()
            for step in steps
            if step.kind == "action" and step.status == "completed"
        ]
        summary = " ".join(value for value in action_summaries if value).strip()
        if not summary:
            summary = f"{job.name} completed by Managed Runtime."

        now = utcnow()
        run.status = "completed"
        run.result_summary = summary
        item.status = "completed"
        payload = dict(item.payload or {})
        payload["completedAt"] = now.isoformat()
        item.payload = payload
        runtime = _write_runtime_meta(
            item,
            currentStep=len(steps),
            resultSummary=summary,
            failure=None,
            completedAt=now.isoformat(),
        )

        if not runtime.get("resultId"):
            result = Result(
                organization_id=item.organization_id,
                worker_id=worker.id,
                job_id=job.id,
                latest_version=1,
                status="completed",
                title=f"{job.name} result",
            )
            self._session.add(result)
            await self._session.flush()
            self._session.add(
                ResultVersion(
                    result_id=result.id,
                    version=1,
                    body={
                        "summary": summary,
                        "completedAt": now.isoformat(),
                        "workItemId": str(item.id),
                        "runId": str(run.id),
                        "agentId": str(worker.agent_identity_id),
                        "blocks": [{"type": "paragraph", "text": summary}],
                        "artifactIds": [],
                        "sourceReferences": [],
                        "capabilitiesUsed": [
                            str((step.input or {}).get("scope", ""))
                            for step in steps
                            if step.kind == "action" and step.status == "completed"
                        ],
                        "actionIds": [
                            str(_step_output(step).get("actionId"))
                            for step in steps
                            if _step_output(step).get("actionId")
                        ],
                        "approvalIds": [
                            str(_step_output(step).get("approvalId"))
                            for step in steps
                            if _step_output(step).get("approvalId")
                        ],
                        "correlationId": item.correlation_id,
                        "isNew": True,
                        "hasTables": False,
                    },
                    created_at=now,
                )
            )
            _write_runtime_meta(item, resultId=str(result.id))
            await TransactionalOutbox(self._session).enqueue(
                topic="result.created",
                aggregate_type="result",
                aggregate_id=str(result.id),
                payload={
                    "organization_id": str(item.organization_id),
                    "worker_id": str(worker.id),
                    "job_id": str(job.id),
                    "run_id": str(run.id),
                    "result_id": str(result.id),
                    "correlation_id": item.correlation_id,
                },
            )

        await TransactionalOutbox(self._session).enqueue(
            topic="run.completed",
            aggregate_type="run",
            aggregate_id=str(run.id),
            payload={
                "organization_id": str(item.organization_id),
                "run_id": str(run.id),
                "job_id": str(job.id),
                "correlation_id": item.correlation_id,
                "summary": summary,
            },
        )
        await self._session.commit()
        return RuntimeStepOutcome(
            "completed",
            item.id,
            run.id,
            len(steps),
            summary,
        )
