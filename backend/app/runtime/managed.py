from __future__ import annotations

# ruff: noqa: I001

import hashlib
from dataclasses import dataclass, replace
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.governance_routes import _evaluate
from app.api.integration_capability_routes import _catalog
from app.api.product_common import utcnow
from app.application.services.worker_memory import WorkerMemoryService
from app.bootstrap.settings import settings
from app.domain.actions.gateway import (
    ActionGateway,
    ActionProposal,
    AuthorizationDecision,
)
from app.domain.ai.providers import AIInvocationContext
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
    Memory,
    Result,
    ResultVersion,
    Run,
    RunStep,
    Worker,
    WorkerDirective,
    WorkItem,
)
from app.infrastructure.ai.provider import ai_gateway_from_settings
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


def _attach_observation_id(value: object, observation_id: str | None) -> object:
    if observation_id is None:
        return value
    if isinstance(value, dict):
        result = {
            str(key): _attach_observation_id(nested, observation_id)
            for key, nested in value.items()
        }
        if (
            result.get("strategy") == "observation_ref"
            and result.get("value")
            and not result.get("observationId")
        ):
            result["observationId"] = observation_id
        return result
    if isinstance(value, list):
        return [_attach_observation_id(item, observation_id) for item in value]
    return value


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
        self._planner = AdaptiveRuntimePlanner(
            ai_gateway_from_settings(session=session)
        )

    async def execute_step(
        self,
        *,
        item: WorkItem,
        expected_step: int | None = None,
    ) -> RuntimeStepOutcome:
        job, revision, worker, agent = await self._load_context(item)
        run = await self._ensure_run(item)
        steps = await self._load_steps(run)

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
            decision = await self._plan_next_step(
                item=item,
                run=run,
                job=job,
                revision=revision,
                worker=worker,
                agent=agent,
                steps=steps,
            )
            if decision.decision == "finish":
                return await self._complete(
                    item,
                    run,
                    job,
                    worker,
                    steps,
                    completion_summary=decision.summary,
                    finish_title=decision.title,
                    finish_instruction=decision.instruction,
                )
            step = await self._append_action_step(
                item=item,
                run=run,
                decision=decision,
                step_index=current_step,
            )
            steps.append(step)

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

    async def _load_steps(self, run: Run) -> list[RunStep]:
        return list(
            (
                await self._session.scalars(
                    select(RunStep)
                    .where(RunStep.run_id == run.id)
                    .order_by(RunStep.step_index)
                )
            ).all()
        )

    async def _planner_tools(
        self,
        *,
        item: WorkItem,
        revision: JobRevision,
        agent: AgentIdentity,
    ) -> list[dict[str, object]]:
        definition = (
            dict(revision.definition or {})
            if isinstance(revision.definition, dict)
            else {}
        )
        selected = {
            str(scope)
            for scope in definition.get("requiredCapabilities", [])
            if str(scope)
        }
        active = await self._active_scopes(agent.id)
        catalog = await _catalog(self._session, item.organization_id)
        resources = list(catalog.get("resources", []))
        tools: list[dict[str, object]] = []

        for resource in resources:
            if resource.get("status") != "connected":
                continue
            provider_name = str(resource.get("provider", ""))
            try:
                provider = self._registry.get(provider_name)
            except KeyError:
                continue
            resource_id = str(resource.get("id", ""))
            integration: Integration | None = None
            try:
                integration = await self._session.get(Integration, UUID(resource_id))
            except ValueError:
                integration = None
            config = _integration_config(integration)
            actions = list(resource.get("actions", []))
            for action in actions:
                scope = str(action.get("scope", ""))
                if scope not in selected or scope not in active:
                    continue
                capability = next(
                    (
                        entry
                        for entry in provider.manifest.capabilities
                        if entry.scope == scope
                    ),
                    None,
                )
                if capability is None:
                    continue
                policy = await self._policy_decision(
                    item=item,
                    agent=agent,
                    resource_id=resource_id,
                    scope=scope,
                )
                if policy.get("outcome") == "DENY":
                    continue
                tools.append(
                    {
                        "resourceId": resource_id,
                        "resourceName": str(resource.get("displayName", resource_id)),
                        "provider": provider_name,
                        "scope": scope,
                        "operation": capability.operation,
                        "description": capability.description,
                        "risk": str(
                            (policy.get("riskAssessment") or {}).get("effectiveRisk")
                            or capability.risk
                        ),
                        "sideEffect": capability.side_effect,
                        "approvalRecommendation": capability.approval_recommendation,
                        "inputSchema": capability.input_schema,
                        "defaultStartUrl": (
                            str(config.get("startUrl", ""))
                            if scope == "browser.navigation.open"
                            else ""
                        ),
                    }
                )

        return tools

    def _planner_observations(self, steps: list[RunStep]) -> list[dict[str, object]]:
        observations: list[dict[str, object]] = []
        for step in steps:
            if step.status != "completed":
                continue
            spec = dict(step.input or {}) if isinstance(step.input, dict) else {}
            output = _step_output(step)
            entry: dict[str, object] = {
                "trust": "untrusted_tool_output",
                "step": step.step_index + 1,
                "title": str(spec.get("title", "")),
                "scope": str(spec.get("scope", "")),
                "summary": str(output.get("summary", ""))[:2000],
            }
            data = output.get("data")
            data_map = data if isinstance(data, dict) else {}
            provider_output = data_map.get("output")
            provider_map = provider_output if isinstance(provider_output, dict) else {}
            browser_observation = provider_map.get("observation")
            if isinstance(browser_observation, dict):
                raw_elements = browser_observation.get("elements")
                elements = raw_elements if isinstance(raw_elements, list) else []
                compact_elements = [
                    {
                        key: element.get(key)
                        for key in (
                            "ref",
                            "tag",
                            "role",
                            "name",
                            "text",
                            "element_type",
                            "value",
                            "checked",
                            "selected",
                            "disabled",
                            "href",
                        )
                        if key in element
                    }
                    for element in elements[:120]
                    if isinstance(element, dict)
                ]
                entry["browserObservation"] = {
                    "id": browser_observation.get("id"),
                    "sessionId": browser_observation.get("sessionId"),
                    "url": browser_observation.get("url"),
                    "title": browser_observation.get("title"),
                    "visibleText": str(browser_observation.get("visibleText") or "")[:7000],
                    "ariaSnapshot": str(browser_observation.get("ariaSnapshot") or "")[:4000],
                    "elements": compact_elements,
                    "formDetails": browser_observation.get("formDetails", []),
                    "pageState": browser_observation.get("pageState", {}),
                }
            else:
                entry["providerOutput"] = str(provider_map)[:4000]
            verification = data_map.get("verification")
            if isinstance(verification, dict):
                entry["verification"] = {
                    "verified": verification.get("verified"),
                    "summary": verification.get("summary"),
                }
            observations.append(entry)
        return observations

    async def _worker_memory_context(
        self,
        worker: Worker,
    ) -> dict[str, object]:
        now = utcnow()
        memories = list(
            (
                await self._session.scalars(
                    select(Memory)
                    .where(
                        Memory.organization_id == worker.organization_id,
                        Memory.owner_id == str(worker.id),
                        Memory.status == "active",
                        or_(Memory.expires_at.is_(None), Memory.expires_at > now),
                    )
                    .order_by(desc(Memory.created_at))
                    .limit(20)
                )
            ).all()
        )
        directives = list(
            (
                await self._session.scalars(
                    select(WorkerDirective)
                    .where(
                        WorkerDirective.organization_id == worker.organization_id,
                        WorkerDirective.worker_id == worker.id,
                        WorkerDirective.status == "active",
                    )
                    .order_by(desc(WorkerDirective.created_at))
                    .limit(20)
                )
            ).all()
        )
        return {
            "standingInstructions": [directive.text[:1200] for directive in directives],
            "memory": [
                {
                    "type": memory.memory_type,
                    "title": memory.title[:300],
                    "content": memory.content[:1200],
                    "source": memory.source,
                    "provenance": dict(memory.provenance or {}),
                    "sensitivity": memory.sensitivity,
                }
                for memory in memories
                if memory.sensitivity != "secret"
            ],
        }

    async def _plan_next_step(
        self,
        *,
        item: WorkItem,
        run: Run,
        job: Job,
        revision: JobRevision,
        worker: Worker,
        agent: AgentIdentity,
        steps: list[RunStep],
    ) -> AdaptivePlanDecision:
        tools = await self._planner_tools(
            item=item,
            revision=revision,
            agent=agent,
        )
        definition = (
            dict(revision.definition or {})
            if isinstance(revision.definition, dict)
            else {}
        )
        payload = dict(item.payload or {})
        raw_trigger = payload.get("trigger")
        trigger = (
            {str(key): value for key, value in raw_trigger.items()}
            if isinstance(raw_trigger, dict)
            else {}
        )
        profile = dict(worker.profile or {}) if isinstance(worker.profile, dict) else {}
        memory_context = await self._worker_memory_context(worker)
        decision = await self._planner.choose_next(
            job={
                "name": job.name,
                "objective": str(definition.get("objective", "")),
                "instructions": str(definition.get("instructions", "")),
                "completionCriteria": list(definition.get("completionCriteria", [])),
                "availableCapabilityScopes": [
                    str(tool.get("scope", "")) for tool in tools
                ],
            },
            worker={
                "name": worker.name,
                "role": profile.get("role"),
                "department": profile.get("department"),
                "responsibilities": profile.get("responsibilities", []),
                "instructions": profile.get("instructions", ""),
                "standingInstructions": memory_context["standingInstructions"],
                "memory": memory_context["memory"],
            },
            trigger=trigger,
            tools=tools,
            observations=self._planner_observations(steps),
            action_count=len([step for step in steps if step.kind == "action"]),
            max_actions=settings.runtime_max_action_steps,
            invocation_context=AIInvocationContext(
                organization_id=item.organization_id,
                worker_id=worker.id,
                job_id=job.id,
                run_id=run.id,
                correlation_id=item.correlation_id,
            ),
        )
        _write_runtime_meta(
            item,
            planSummary=decision.summary,
            plannerMode="adaptive",
            plannerModel=settings.ai_coordinator_model,
        )
        await self._session.flush()
        return decision

    async def _append_action_step(
        self,
        *,
        item: WorkItem,
        run: Run,
        decision: AdaptivePlanDecision,
        step_index: int,
    ) -> RunStep:
        catalog = await _catalog(self._session, item.organization_id)
        resource = next(
            (
                candidate
                for candidate in list(catalog.get("resources", []))
                if str(candidate.get("id", "")) == decision.resource_id
                and decision.scope in list(candidate.get("scopes", []))
            ),
            None,
        )
        if resource is None:
            raise RuntimeError("Planner-selected execution resource is no longer available.")
        action = next(
            (
                entry
                for entry in list(resource.get("actions", []))
                if entry.get("scope") == decision.scope
            ),
            None,
        )
        if action is None:
            raise RuntimeError("Planner-selected capability is no longer executable.")

        step = RunStep(
            run_id=run.id,
            step_index=step_index,
            kind="action",
            status="pending",
            input={
                "title": decision.title,
                "instruction": decision.instruction,
                "resourceId": decision.resource_id,
                "scope": decision.scope,
                "provider": str(resource.get("provider", "")),
                "operation": str(action.get("providerOperation", "")),
                "actionInput": decision.action_input,
            },
            output={},
        )
        self._session.add(step)
        await self._session.flush()
        await TransactionalOutbox(self._session).enqueue(
            topic="run.progress",
            aggregate_type="run",
            aggregate_id=str(run.id),
            payload={
                "organization_id": str(item.organization_id),
                "run_id": str(run.id),
                "job_id": str(item.job_id),
                "current_step": step_index,
                "planner_scope": decision.scope,
                "correlation_id": item.correlation_id,
            },
        )
        await self._session.commit()
        return step

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

        planned_raw = spec.get("actionInput")
        action_input: dict[str, object] = (
            {str(key): value for key, value in planned_raw.items()}
            if isinstance(planned_raw, dict)
            else {}
        )

        explicit = payload.get("actionInputs")
        trigger = payload.get("trigger")
        if not isinstance(explicit, dict) and isinstance(trigger, dict):
            trigger_payload = trigger.get("payload")
            if isinstance(trigger_payload, dict):
                explicit = trigger_payload.get("actionInputs")
        if isinstance(explicit, dict):
            raw = explicit.get(scope, explicit.get(operation))
            if isinstance(raw, dict):
                action_input.update({str(key): value for key, value in raw.items()})

        resource_id = str(spec.get("resourceId", ""))
        integration: Integration | None = None
        try:
            integration = await self._session.get(Integration, UUID(resource_id))
        except ValueError:
            integration = None
        config = _integration_config(integration)

        if scope == "browser.navigation.open" and not action_input.get("url"):
            start_url = str(config.get("startUrl", "")).strip()
            if start_url:
                action_input["url"] = start_url

        if scope.startswith("browser.") and scope != "browser.navigation.open":
            session_id, observation_id = await self._latest_browser_context(
                step.run_id,
                step.step_index,
            )
            if not session_id:
                raise RuntimeError(
                    f"Runtime capability {scope} requires an active browser session. "
                    "The planner must open the governed browser first."
                )
            action_input.setdefault("sessionId", session_id)
            normalized = _attach_observation_id(action_input, observation_id)
            action_input = (
                {str(key): value for key, value in normalized.items()}
                if isinstance(normalized, dict)
                else action_input
            )

        provider = self._registry.get(str(spec.get("provider", "")))
        capability = next(
            (entry for entry in provider.manifest.capabilities if entry.scope == scope),
            None,
        )
        if capability is None:
            raise RuntimeError(f"Runtime capability is not registered: {scope}.")
        raw_required = capability.input_schema.get("required", [])
        required = [str(key) for key in raw_required] if isinstance(raw_required, list) else []
        missing = [
            key
            for key in required
            if key not in action_input or action_input.get(key) in (None, "")
        ]
        if missing:
            raise RuntimeError(
                f"Planner produced incomplete structured input for {scope}: "
                + ", ".join(missing)
                + "."
            )
        return action_input

    async def _latest_browser_context(
        self,
        run_id: UUID,
        before_index: int,
    ) -> tuple[str | None, str | None]:
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
            observation = provider_output.get("observation")
            observation = observation if isinstance(observation, dict) else {}
            raw_session_id = session.get("id") or observation.get("sessionId")
            if raw_session_id:
                raw_observation_id = observation.get("id")
                return (
                    str(raw_session_id),
                    str(raw_observation_id) if raw_observation_id else None,
                )
        return None, None

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
            idempotency_key=f"runtime:{run.id}:{current_step}",
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

    async def _apply_stop_after_next_run(
        self,
        *,
        item: WorkItem,
        job: Job,
    ) -> None:
        revision = await self._session.scalar(
            select(JobRevision).where(
                JobRevision.job_id == job.id,
                JobRevision.revision == job.current_revision,
            )
        )
        if revision is None or not isinstance(revision.definition, dict):
            return
        definition = dict(revision.definition)
        raw_autonomy = definition.get("autonomy")
        autonomy = dict(raw_autonomy) if isinstance(raw_autonomy, dict) else {}
        if not bool(autonomy.get("stopAfterNextRun")):
            return
        raw_trigger = definition.get("triggerConfig")
        trigger = dict(raw_trigger) if isinstance(raw_trigger, dict) else {}
        raw_schedule = trigger.get("schedule")
        schedule = dict(raw_schedule) if isinstance(raw_schedule, dict) else {}
        if not schedule.get("enabled") and job.status == "paused":
            return

        from app.api.jobs_routes import job_status_v1, save_trigger_config

        principal = HumanPrincipal(
            user_id=revision.created_by,
            organization_id=item.organization_id,
            membership_id=UUID(int=0),
            role="system",
            permissions=frozenset({"jobs.manage"}),
        )
        if trigger and schedule:
            await save_trigger_config(
                self._session,
                item.organization_id,
                job.id,
                principal,
                {
                    "schedule": {**schedule, "enabled": False},
                    "apiEnabled": bool(trigger.get("apiEnabled", False)),
                    "internalEventKeys": list(trigger.get("internalEventKeys", [])),
                    "dependencyJobIds": list(trigger.get("dependencyJobIds", [])),
                },
            )
        await job_status_v1(
            item.organization_id,
            job.id,
            {"status": "paused"},
            principal,
            self._session,
        )
        _write_runtime_meta(item, stopAfterNextRunApplied=True)

    async def _complete(
        self,
        item: WorkItem,
        run: Run,
        job: Job,
        worker: Worker,
        steps: list[RunStep],
        *,
        completion_summary: str | None = None,
        finish_title: str = "Finish",
        finish_instruction: str = "Verify completion from recorded execution evidence.",
    ) -> RuntimeStepOutcome:
        current = max(0, int(_runtime_meta(item).get("currentStep", 0)))
        finish_output = completion_summary or "Runtime completion checkpoint verified."
        if current >= len(steps):
            finish = RunStep(
                run_id=run.id,
                step_index=current,
                kind="finish",
                status="completed",
                input={
                    "title": finish_title or "Finish",
                    "instruction": finish_instruction
                    or "Verify completion from recorded execution evidence.",
                    "resourceId": "",
                    "scope": "",
                },
                output={
                    "output": finish_output,
                    "completedAt": utcnow().isoformat(),
                },
            )
            self._session.add(finish)
            await self._session.flush()
            steps.append(finish)
        else:
            finish = steps[current]
            if finish.kind == "finish":
                finish.status = "completed"
                finish.output = {
                    "output": finish_output,
                    "completedAt": utcnow().isoformat(),
                }

        action_summaries = [
            str(_step_output(step).get("summary") or "").strip()
            for step in steps
            if step.kind == "action" and step.status == "completed"
        ]
        summary = (completion_summary or "").strip()
        if not summary:
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
            memory_service = WorkerMemoryService(self._session)
            await memory_service.record_operational(
                worker=worker,
                title=f"{job.name} latest outcome",
                content=summary,
                source_type="runtime_result",
                source_id=str(result.id),
                provenance={
                    "runId": str(run.id),
                    "resultId": str(result.id),
                    "jobId": str(job.id),
                },
            )
            await memory_service.record_episode(
                worker=worker,
                run_id=run.id,
                result_id=result.id,
                summary=summary,
            )
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

        await self._apply_stop_after_next_run(item=item, job=job)
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
