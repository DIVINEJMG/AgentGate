from __future__ import annotations

# ruff: noqa: I001

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from app.api.governance_routes import _evaluate
from app.api.integration_capability_routes import _catalog
from app.api.product_common import utcnow
from app.application.services.browser_origin_authority import (
    browser_integration_autonomy_suitable,
    browser_integration_origins,
    explicit_http_origins,
)
from app.application.services.live_results import append_live_result_message
from app.application.services.integration_foundation import notify_integration_work
from app.application.services.worker_memory import WorkerMemoryService
from app.bootstrap.settings import settings
from app.domain.actions.gateway import (
    ActionGateway,
    ActionProposal,
    AuthorizationDecision,
)
from app.domain.ai.providers import AIInvocationContext, AIProviderError
from app.domain.identity.principals import AgentPrincipal, HumanPrincipal
from app.execution.authorization import UniversalActionRequest, action_fingerprint
from app.execution.bootstrap import browser_provider, execution_provider_registry
from app.execution.browser.http_reader import fetch_http_page
from app.execution.browser.policy import BrowserDomainPolicy
from app.execution.contracts import ExecutionProviderError
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

logger = logging.getLogger(__name__)
telemetry_logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True, slots=True)
class RuntimeStepOutcome:
    state: RuntimeState
    work_item_id: UUID
    run_id: UUID | None
    current_step: int
    summary: str
    continuation_phase: Literal["plan", "execute"] | None = None


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
            ai_gateway_from_settings(planner=True)
        )

    async def execute_step(
        self,
        *,
        item: WorkItem,
        expected_step: int | None = None,
    ) -> RuntimeStepOutcome:
        if item.status in {"waiting_reconnect", "uncertain_outcome", "policy_denied", "partial_completion"}:
            return RuntimeStepOutcome("noop", item.id, None, int(_runtime_meta(item).get("currentStep", 0)),
                str(_runtime_meta(item).get("waitingReason", "Integration action is waiting.")))
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
            _write_runtime_meta(item, plannerPhase="preparing_tools", queueState="planning",
                plannerTiming={},
                plannerPhaseStartedAt=utcnow().isoformat(),
                waitingReason="Preparing the authorized tools for the next action.")
            # Commit the run identity before cancellable preparation or inference.
            await self._session.commit()
            planner_started = time.monotonic()
            planner_timeout_seconds = settings.runtime_planner_timeout_seconds
            logger.info(
                "Runtime planner started work_item=%s run=%s step=%s timeout=%ss",
                item.id,
                run.id,
                current_step,
                planner_timeout_seconds,
            )
            try:
                async with asyncio.timeout(planner_timeout_seconds):
                    decision = await self._plan_next_step(
                        item=item,
                        run=run,
                        job=job,
                        revision=revision,
                        worker=worker,
                        agent=agent,
                        steps=steps,
                    )
            except TimeoutError as exc:
                logger.warning(
                    "Runtime planner timed out work_item=%s run=%s step=%s elapsed=%.3fs",
                    item.id,
                    run.id,
                    current_step,
                    time.monotonic() - planner_started,
                )
                raise AIProviderError(
                    "provider_unavailable",
                    "Planning exceeded its configured time budget.",
                    retryable=True,
                ) from exc
            except SQLAlchemyError as exc:
                from app.runtime.planning_diagnostics import database_planning_error
                raise database_planning_error(exc, work_item_id=item.id, run_id=run.id) from exc
            logger.info(
                "Runtime planner completed work_item=%s run=%s step=%s elapsed=%.3fs "
                "decision=%s scope=%s",
                item.id,
                run.id,
                current_step,
                time.monotonic() - planner_started,
                decision.decision,
                decision.scope,
            )
            if item.status in {"completed", "failed", "cancelled"} or run.status in {"completed", "failed", "cancelled"}:
                return RuntimeStepOutcome("noop", item.id, run.id, current_step,
                    "Run was stopped while planning; no planned action was dispatched.")
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
                    result_status=decision.result_status,
                )
            await self._append_action_step(
                item=item,
                run=run,
                decision=decision,
                step_index=current_step,
            )
            logger.info(
                "Runtime action checkpointed work_item=%s run=%s step=%s scope=%s",
                item.id,
                run.id,
                current_step,
                decision.scope,
            )
            return RuntimeStepOutcome(
                state="continue",
                work_item_id=item.id,
                run_id=run.id,
                current_step=current_step,
                summary="Next governed action planned and queued for execution.",
                continuation_phase="execute",
            )

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
                continuation_phase="plan",
            )

        if step.status == "waiting_approval":
            return await self._integration_guarded_action(self._resume_approved_action,
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

        spec = dict(step.input or {})
        logger.info(
            "Runtime action started work_item=%s run=%s step=%s provider=%s scope=%s operation=%s",
            item.id,
            run.id,
            current_step,
            str(spec.get("provider", "")),
            str(spec.get("scope", "")),
            str(spec.get("operation", "")),
        )
        action_started = time.monotonic()
        outcome = await self._integration_guarded_action(self._execute_action_step,
            item=item,
            run=run,
            step=step,
            job=job,
            worker=worker,
            agent=agent,
            current_step=current_step,
        )
        logger.info(
            "Runtime action finished work_item=%s run=%s step=%s state=%s elapsed=%.3fs",
            item.id,
            run.id,
            current_step,
            outcome.state,
            time.monotonic() - action_started,
        )
        return outcome

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
            item.status in {"queued", "admitted"}
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
        tool_started = time.monotonic()
        self._tool_preparation_metrics = {"policyChecks": 0, "policySeconds": 0.0, "slowestPolicySeconds": 0.0}
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
        raw_autonomy = definition.get("autonomy")
        autonomy = dict(raw_autonomy) if isinstance(raw_autonomy, dict) else {}
        raw_authorized_origins = autonomy.get("authorizedBrowserOrigins")
        authorized_browser_origins = (
            tuple(str(item) for item in raw_authorized_origins if str(item))
            if isinstance(raw_authorized_origins, list)
            else ()
        )
        if not authorized_browser_origins:
            authorized_browser_origins = explicit_http_origins(
                "\n".join(
                    (
                        str(definition.get("objective", "")),
                        str(definition.get("instructions", "")),
                    )
                )
            )
        active = await self._active_scopes(agent.id)
        catalog_started = time.monotonic()
        catalog = await self._execution_catalog(item)
        self._tool_preparation_metrics["catalogSeconds"] = round(time.monotonic() - catalog_started, 3)
        self._capability_exclusions = list(catalog.get("capabilityExclusions", []))
        resources = list(catalog.get("resources", []))
        tools: list[dict[str, object]] = []
        preparation_cache: dict[Any, Any] = {}

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
            resource_browser_origins = (
                browser_integration_origins(integration)
                if provider_name == "browser"
                else ()
            )
            if provider_name == "browser" and authorized_browser_origins:
                resource_origin_set = set(resource_browser_origins)
                authorized_origin_set = set(authorized_browser_origins)
                if (
                    not resource_origin_set
                    or not resource_origin_set.issubset(authorized_origin_set)
                ):
                    continue
                if (
                    bool(autonomy.get("createdByAI"))
                    and not browser_integration_autonomy_suitable(
                        integration,
                        authorized_origin_set,
                    )
                ):
                    continue
            actions = list(resource.get("actions", []))
            for action in actions:
                scope = str(action.get("scope", ""))
                if scope not in selected or scope not in active:
                    self._capability_exclusions.append({"resourceId": resource_id, "scope": scope,
                        "reason": "Not selected by this job revision" if scope not in selected else "Worker capability profile is inactive"})
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
                    self._capability_exclusions.append({"resourceId": resource_id, "scope": scope, "reason": "Provider tool is disabled or unavailable"})
                    continue
                policy_started = time.monotonic()
                policy = await self._policy_decision(
                    item=item,
                    agent=agent,
                    resource_id=resource_id,
                    scope=scope,
                    preparation_cache=preparation_cache,
                )
                policy_seconds = time.monotonic() - policy_started
                self._tool_preparation_metrics["policyChecks"] += 1
                self._tool_preparation_metrics["policySeconds"] += policy_seconds
                self._tool_preparation_metrics["slowestPolicySeconds"] = max(self._tool_preparation_metrics["slowestPolicySeconds"], policy_seconds)
                if policy.get("outcome") == "DENY":
                    self._capability_exclusions.append({"resourceId": resource_id, "scope": scope,
                        "reason": str(policy.get("reason") or "Organizational policy denies this action")})
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
                        "authorityConstraints": resource.get("authorityConstraints", {}),
                        "defaultStartUrl": (
                            str(config.get("startUrl", ""))
                            if scope == "browser.navigation.open"
                            else ""
                        ),
                        "allowedOrigins": (
                            list(resource_browser_origins)
                            if provider_name == "browser"
                            else []
                        ),
                    }
                )

        self._tool_preparation_metrics["totalToolSeconds"] = round(time.monotonic() - tool_started, 3)
        self._tool_preparation_metrics["policySeconds"] = round(self._tool_preparation_metrics["policySeconds"], 3)
        telemetry_logger.info("Planner tool preparation work_item=%s timing=%s", item.id, self._tool_preparation_metrics)
        return tools

    async def _execution_catalog(self, item: WorkItem) -> dict:
        catalog = await _catalog(self._session, item.organization_id)
        if not settings.integration_foundation_enabled:
            return catalog
        from app.infrastructure.database.models import IntegrationResource, IntegrationTaskGrant
        grants = (await self._session.scalars(select(IntegrationTaskGrant).where(
            IntegrationTaskGrant.organization_id == item.organization_id,
            IntegrationTaskGrant.job_revision_id == item.job_revision_id,
            IntegrationTaskGrant.active.is_(True)))).all()
        # Browser/research keep their existing contracts. Native integrations must
        # be explicitly rebound during adoption; legacy scope grants are not expanded.
        resources = [r for r in catalog.get("resources", []) if r.get("provider") in {"browser", "web_research"}]
        from app.application.services.integration_foundation import IntegrationFoundation
        foundation = IntegrationFoundation(self._session)
        exclusions = []
        job = await self._session.get(Job, item.job_id)
        for grant in grants:
            resource = await self._session.get(IntegrationResource, grant.resource_id)
            if not resource or resource.organization_id != item.organization_id:
                continue
            worker = await self._session.get(Worker, grant.worker_id)
            if not worker or not job or worker.id != job.worker_id:
                exclusions.append({"resourceId": str(resource.id), "reason": "Granted worker no longer exists"})
                continue
            try:
                await foundation.authorize(organization_id=item.organization_id,
                    agent_id=worker.agent_identity_id, work_item_id=item.id,
                    resource_id=resource.id, scope=None, payload={})
            except PermissionError as error:
                exclusions.append({"resourceId": str(resource.id), "reason": str(error)})
                continue
            provider = self._registry.get(resource.provider)
            available = {c.scope for c in provider.manifest.capabilities} & set(resource.capabilities)
            for scope in set(grant.scopes) - available:
                exclusions.append({"resourceId": str(resource.id), "scope": scope,
                    "reason": "Current provider consent, resource availability or runtime setup excludes this capability"})
            resources.append({"id": str(resource.id), "displayName": resource.display_name,
                "provider": resource.provider, "status": "connected", "scopes": grant.scopes,
                "authorityConstraints": getattr(grant, "constraints", {}) or {},
                "actions": [{"scope": c.scope, "providerOperation": c.operation} for c in provider.manifest.capabilities
                            if c.scope in grant.scopes and c.scope in resource.capabilities]})
        return {**catalog, "resources": resources, "capabilityExclusions": exclusions}

    async def _integration_guarded_action(self, action, **kwargs):
        try:
            return await action(**kwargs)
        except ExecutionProviderError as error:
            item, run, step = kwargs["item"], kwargs["run"], kwargs["step"]
            if settings.integration_foundation_enabled and (item.payload or {}).get("integrationOrigin") and error.error.code in {"authentication_error", "uncertain_outcome"}:
                return await self._wait_integration(item, run, step, error.error, kwargs["current_step"])
            if settings.integration_foundation_enabled and (item.payload or {}).get("integrationOrigin"):
                count = int(_runtime_meta(item).get("providerRetryCount", 0))
                if error.error.retryable and count < settings.runtime_provider_retry_limit:
                    delay = max(settings.runtime_provider_retry_backoff_seconds * (2**count), error.error.retry_after_seconds or 0)
                    item.status, run.status = "queued", "running"
                    item.scheduled_at = utcnow() + timedelta(seconds=delay)
                    _write_runtime_meta(item, queueState="retrying", waitingReason=error.error.safe_message,
                        providerRetryCount=count + 1, providerRetryAt=item.scheduled_at.isoformat())
                    await notify_integration_work(self._session, item, key=f"prepare-retry:{kwargs['current_step']}:{count}",
                        content=f"I’m waiting before retrying this action: {error.error.safe_message} Completed steps remain saved.")
                    await self._session.commit()
                    return RuntimeStepOutcome("continue", item.id, run.id, kwargs["current_step"], error.error.safe_message)
                step.status = "failed"
                step.output = {"summary": error.error.safe_message, "error": error.error.safe_message,
                    "data": {"integrationFailure": {"code": error.error.code, "executed": False,
                        "instruction": "Do not repeat this failed action or assume its prerequisites succeeded. Replan independent authorized objectives or finish with remaining work."}}}
                next_step = kwargs["current_step"] + 1
                invalid_count = int(_runtime_meta(item).get("invalidActionCount", 0)) + (error.error.code == "validation_error")
                _write_runtime_meta(item, currentStep=next_step, providerRetryCount=0,
                    invalidActionCount=invalid_count, queueState="replanning", waitingReason=error.error.safe_message)
                if error.error.code == "validation_error" and invalid_count > settings.runtime_provider_retry_limit:
                    steps = list((await self._session.scalars(select(RunStep).where(
                        RunStep.run_id == run.id).order_by(RunStep.step_index))).all())
                    return await self._complete(item, run, kwargs["job"], kwargs["worker"], steps,
                        completion_summary="I could not produce a valid action within the correction budget. "
                            "Completed work is preserved. The remaining task needs attention. " + error.error.safe_message,
                        result_status="attention")
                item.status, run.status = "running", "running"
                await notify_integration_work(self._session, item, key=f"action-failed:{kwargs['current_step']}",
                    content="This action could not complete: " + error.error.safe_message + " I will assess the remaining authorized work without repeating completed actions.")
                await TransactionalOutbox(self._session).enqueue(topic="run.progress", aggregate_type="run", aggregate_id=str(run.id),
                    payload={"organization_id": str(item.organization_id), "work_item_id": str(item.id), "run_id": str(run.id),
                        "job_id": str(item.job_id), "current_step": next_step, "continuation_phase": "plan", "correlation_id": item.correlation_id})
                await self._session.commit()
                return RuntimeStepOutcome("continue", item.id, run.id, next_step, error.error.safe_message, "plan")
            raise
        except (LookupError, PermissionError) as error:
            item, run = kwargs["item"], kwargs["run"]
            if not settings.integration_foundation_enabled or not (item.payload or {}).get("integrationOrigin"):
                raise
            item.status, run.status = "policy_denied", "policy_denied"
            _write_runtime_meta(item, queueState="policy_denied", waitingReason=str(error), executionCertainty="not_executed")
            await notify_integration_work(self._session, item, key=f"denied:{kwargs['current_step']}",
                content=f"I cannot execute the pending action: {error}. Completed steps remain saved. Review the account and exact resource authority before continuing.")
            await self._session.commit()
            return RuntimeStepOutcome("noop", item.id, run.id, kwargs["current_step"], str(error))

    async def _wait_integration(self, item, run, step, error, current_step):
        state = "waiting_reconnect" if error.code == "authentication_error" else "uncertain_outcome"
        item.status, run.status = state, state
        _write_runtime_meta(item, queueState=state, waitingReason=error.safe_message,
            executionCertainty="uncertain" if state == "uncertain_outcome" else "not_executed")
        await notify_integration_work(self._session, item, key=f"{state}:{current_step}", content=error.safe_message + " Completed steps remain saved.")
        await self._session.commit()
        return RuntimeStepOutcome("noop", item.id, run.id, current_step, error.safe_message)

    def _planner_observations(self, steps: list[RunStep]) -> list[dict[str, object]]:
        observations: list[dict[str, object]] = []
        for step in steps:
            if step.status != "completed" and not (step.status == "failed" and (_step_output(step).get("data") or {}).get("integrationFailure")):
                continue
            spec = dict(step.input or {}) if isinstance(step.input, dict) else {}
            output = _step_output(step)
            entry: dict[str, object] = {
                "trust": "untrusted_tool_output",
                "step": step.step_index + 1,
                "status": step.status,
                "title": str(spec.get("title", "")),
                "scope": str(spec.get("scope", "")),
                "resourceId": str(spec.get("resourceId", "")),
                "summary": str(output.get("summary", ""))[:2000],
            }
            action_input = spec.get("actionInput")
            if spec.get("scope") == "github.repository.contents.read" and isinstance(action_input, dict):
                entry["actionInput"] = {key: action_input[key] for key in ("path", "ref") if key in action_input}
            if isinstance(action_input, dict):
                entry["actionFingerprint"] = hashlib.sha256(
                    json.dumps(
                        {"scope": spec.get("scope"), "resource": spec.get("resourceId"),
                         "input": action_input}, sort_keys=True, default=str,
                    ).encode("utf-8")
                ).hexdigest()
            if spec.get("scope") == "browser.element.press_key":
                action_input = spec.get("actionInput")
                if isinstance(action_input, dict):
                    entry["actionInput"] = {"value": str(action_input.get("value") or "")[:32]}
            data = output.get("data")
            data_map = data if isinstance(data, dict) else {}
            if data_map.get("integrationFailure"):
                entry["data"] = {"integrationFailure": data_map["integrationFailure"]}
            provider_output = data_map.get("output")
            provider_map = provider_output if isinstance(provider_output, dict) else {}
            browser_observation = provider_map.get("observation")
            if isinstance(browser_observation, dict):
                raw_elements = browser_observation.get("elements")
                elements = [
                    element
                    for element in (raw_elements if isinstance(raw_elements, list) else [])
                    if isinstance(element, dict)
                ]
                raw_form_details = browser_observation.get("formDetails")
                form_details = (
                    raw_form_details if isinstance(raw_form_details, list) else []
                )
                form_refs: set[str] = set()
                for form in form_details:
                    if not isinstance(form, dict):
                        continue
                    for key in ("fieldRefs", "submitRefs"):
                        refs = form.get(key)
                        if isinstance(refs, list):
                            form_refs.update(str(ref) for ref in refs)

                ranked_elements: list[tuple[int, str, dict[str, object]]] = []
                for element in elements:
                    ref = str(element.get("ref") or "")
                    tag = str(element.get("tag") or "").lower()
                    role = str(element.get("role") or "").lower()
                    element_type = str(element.get("element_type") or "").lower()
                    if ref in form_refs:
                        priority = 0
                    elif (
                        tag in {"input", "textarea", "select", "button"}
                        or role in {"textbox", "checkbox", "combobox", "button"}
                        or element_type in {"text", "email", "checkbox", "submit"}
                    ):
                        priority = 1
                    else:
                        priority = 2
                    ranked_elements.append((priority, ref, element))
                ranked_elements.sort(key=lambda item: (item[0], item[1]))

                compact_elements: list[dict[str, object]] = []
                for _, _, element in ranked_elements[:100]:
                    compact: dict[str, object] = {}
                    for key in (
                        "ref",
                        "tag",
                        "role",
                        "element_type",
                        "field_name",
                        "checked",
                        "disabled",
                        "keyboard_enter_safe",
                    ):
                        if key in element:
                            compact[key] = element.get(key)
                    for key, limit in (
                        ("name", 240),
                        ("text", 240),
                        ("href", 320),
                        ("value", 240),
                    ):
                        if key in element and element.get(key) is not None:
                            compact[key] = str(element.get(key))[:limit]
                    compact_elements.append(compact)

                action_evidence = provider_map.get("actionEvidence")
                compact_action_evidence = (
                    {
                        "operation": action_evidence.get("operation"),
                        "result": action_evidence.get("result"),
                        "outcome": action_evidence.get("outcome"),
                        "verification": action_evidence.get("verification"),
                        "stateChanged": action_evidence.get("stateChanged"),
                        "elementReference": action_evidence.get("elementReference"),
                        "beforeObservationId": action_evidence.get("beforeObservationId"),
                        "afterObservationId": action_evidence.get("afterObservationId"),
                    }
                    if isinstance(action_evidence, dict)
                    else {}
                )
                entry["browserObservation"] = {
                    "id": browser_observation.get("id"),
                    "sessionId": browser_observation.get("sessionId"),
                    "url": browser_observation.get("url"),
                    "title": browser_observation.get("title"),
                    "formDetails": form_details,
                    "elements": compact_elements,
                    "pageState": browser_observation.get("pageState", {}),
                    "actionEvidence": compact_action_evidence,
                    "visibleText": str(browser_observation.get("visibleText") or "")[:12000],
                    "ariaSnapshot": str(browser_observation.get("ariaSnapshot") or "")[:2500],
                }
            else:
                from app.runtime.evidence import bounded_evidence
                entry["providerOutput"] = bounded_evidence(provider_map)
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

    async def _http_first_observations(
        self,
        *,
        item: WorkItem,
        tools: list[dict[str, object]],
        steps: list[RunStep],
    ) -> list[dict[str, object]]:
        completed_browser = any(
            step.status == "completed"
            and isinstance(step.input, dict)
            and str(step.input.get("scope", "")).startswith("browser.")
            for step in steps
        )
        if completed_browser:
            return []

        runtime_meta = _runtime_meta(item)
        cached = runtime_meta.get("httpFirstObservations")
        if isinstance(cached, list) and cached:
            telemetry_logger.info(
                "HTTP-first bootstrap cache reused work_item=%s observations=%s",
                item.id,
                len(cached),
            )
            return [dict(entry) for entry in cached if isinstance(entry, dict)]

        observations: list[dict[str, object]] = []
        seen_resources: set[str] = set()
        for tool in tools:
            if (
                str(tool.get("provider", "")) != "browser"
                or str(tool.get("scope", "")) != "browser.navigation.open"
            ):
                continue
            resource_id = str(tool.get("resourceId", ""))
            start_url = str(tool.get("defaultStartUrl", "")).strip()
            if not resource_id or not start_url or resource_id in seen_resources:
                continue
            seen_resources.add(resource_id)
            if len(seen_resources) > 2:
                break

            try:
                integration = await self._session.get(Integration, UUID(resource_id))
            except ValueError:
                integration = None
            if integration is None:
                continue
            config = _integration_config(integration)
            try:
                policy = BrowserDomainPolicy.from_configuration(
                    {str(key): str(value) for key, value in config.items() if value is not None},
                    fallback_url=start_url,
                )
                observation = await fetch_http_page(
                    start_url,
                    policy=policy,
                    max_bytes=settings.browser_http_read_max_bytes,
                    timeout_seconds=settings.browser_http_read_timeout_seconds,
                )
            except (PermissionError, RuntimeError, ValueError) as error:
                telemetry_logger.info(
                    "HTTP-first bootstrap skipped work_item=%s resource=%s error=%s",
                    item.id,
                    resource_id,
                    type(error).__name__,
                )
                continue

            telemetry_logger.info(
                "HTTP-first bootstrap completed work_item=%s resource=%s status=%s "
                "text_chars=%s links=%s forms=%s truncated=%s",
                item.id,
                resource_id,
                observation.status_code,
                len(observation.visible_text),
                len(observation.links),
                len(observation.forms),
                observation.truncated,
            )
            observations.append(
                {
                    "trust": "untrusted_tool_output",
                    "step": 0,
                    "title": "Authorized HTTP-first page read",
                    "scope": "browser.http.bootstrap",
                    "summary": (
                        "Lightweight authorized page content fetched without starting Chromium. "
                        "Use governed browser actions only when interaction or JavaScript state is needed."
                    ),
                    "resourceId": resource_id,
                    "httpObservation": observation.as_dict(),
                }
            )

        if observations:
            _write_runtime_meta(
                item,
                httpFirstObservations=observations,
                httpFirstObservedAt=utcnow().isoformat(),
            )
            await self._session.flush()
        return observations

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
        smart = None
        if settings.smart_planner_enabled:
            from app.runtime.smart_planner import SmartPlanner
            smart = SmartPlanner(self._session, item, run, len(steps))
            await smart.prepare()
        preparation_started = time.monotonic()
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
        preparation_breakdown = dict(getattr(self, "_tool_preparation_metrics", {}))
        memory_started = time.monotonic()
        memory_context = await self._worker_memory_context(worker)
        preparation_breakdown["memorySeconds"] = round(time.monotonic() - memory_started, 3)
        durable_observations = self._planner_observations(steps)
        observation_started = time.monotonic()
        http_first = await self._http_first_observations(
            item=item,
            tools=tools,
            steps=steps,
        )
        preparation_breakdown["observationSeconds"] = round(time.monotonic() - observation_started, 3)
        preparation_seconds = time.monotonic() - preparation_started
        telemetry_logger.info("Planner preparation completed work_item=%s run=%s seconds=%.3f breakdown=%s", item.id, run.id, preparation_seconds, preparation_breakdown)
        _write_runtime_meta(item, plannerPhase="awaiting_ai",
            plannerPhaseStartedAt=utcnow().isoformat(),
            plannerTiming={"preparationSeconds": round(preparation_seconds, 3), "preparationBreakdown": preparation_breakdown},
            waitingReason="The AI is planning the next action.")
        # Release the database transaction while waiting for inference. Telemetry
        # has its own short-lived transactions and cannot roll back this run.
        await self._session.commit()
        logger.info("Runtime tool preparation completed work_item=%s run=%s elapsed=%.3fs tools=%s",
            item.id, run.id, preparation_seconds, len(tools))
        ai_started = time.monotonic()
        logger.info("Runtime AI response started work_item=%s run=%s", item.id, run.id)
        try:
            arguments = {"item": item, "run": run, "job": job, "worker": worker,
                "definition": definition, "profile": profile, "memory_context": memory_context,
                "trigger": trigger, "tools": tools, "observations": [*http_first, *durable_observations], "steps": steps}
            if smart is None:
                decision = await self._choose_planner_decision(**arguments)
            else:
                assert smart.decision is not None
                from app.runtime.smart_planner import recovery_context
                recovery = recovery_context(steps, durable_observations, smart.decision)
                arguments["trigger"] = {**trigger, "recovery": recovery}
                async def choose_route(gateway):
                    return await self._choose_planner_decision(**arguments,
                        planner=AdaptiveRuntimePlanner(gateway))
                decision = await smart.choose({"revision": str(revision.id), "definition": definition,
                    "tools": tools, "observations": arguments["observations"], "trigger": trigger,
                    "profile": profile, "memory": memory_context}, choose_route)
                refreshed_tools = await self._planner_tools(item=item, revision=revision, agent=agent)
                if refreshed_tools != tools:
                    smart.decision.status = "pending"
                    smart.decision.accepted_proposal = None
                    await self._session.commit()
                    raise AIProviderError("invalid_provider_response",
                        "Available authority changed during planning; revalidation is required.", retryable=False)
                payload = dict(item.payload or {})
                runtime = dict(payload.get("runtime") or {})
                runtime.update(plannerDecisionId=str(smart.decision.id),
                    plannerRecoveryRound=smart.decision.recovery_round,
                    plannerRecoveryMode=recovery["mode"], plannerStatus="accepted",
                    plannerAttemptCount=smart.attempt_count,
                    plannerElapsedSeconds=round(settings.runtime_planner_timeout_seconds-smart.remaining(), 3))
                runtime["aiRetryCount"] = 0
                payload["runtime"] = runtime
                item.payload = payload
                await self._session.commit()
        finally:
            ai_seconds = time.monotonic() - ai_started
            logger.info("Runtime AI response finished work_item=%s run=%s elapsed=%.3fs",
                item.id, run.id, ai_seconds)
        # A user may cancel during a long response; do not overwrite that state
        # with stale planner metadata or finish a cancelled run as successful.
        await self._session.refresh(item)
        await self._session.refresh(run)
        if item.status in {"completed", "failed", "cancelled"} or run.status in {"completed", "failed", "cancelled"}:
            return decision
        _write_runtime_meta(item, plannerPhase="planned",
            plannerPhaseStartedAt=None,
            plannerTiming={"preparationSeconds": round(preparation_seconds, 3), "preparationBreakdown": preparation_breakdown, "aiResponseSeconds": round(ai_seconds, 3)},
            waitingReason=None, planSummary=decision.summary, plannerMode="adaptive",
            plannerModel=smart.decision.accepted_model if smart and smart.decision else settings.ai_coordinator_model)
        await self._session.flush()
        return decision

    async def _choose_planner_decision(self, *, item, run, job, worker, definition, profile,
        memory_context, trigger, tools, observations, steps, planner=None):
        decision = await (planner or self._planner).choose_next(
            job={
                "name": job.name,
                "objective": str(definition.get("objective", "")),
                "instructions": str(definition.get("instructions", "")),
                "completionCriteria": list(definition.get("completionCriteria", [])),
                "capabilityExclusions": getattr(self, "_capability_exclusions", [])[:30],
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
            observations=observations,
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
        return decision

    async def _append_action_step(
        self,
        *,
        item: WorkItem,
        run: Run,
        decision: AdaptivePlanDecision,
        step_index: int,
    ) -> RunStep:
        catalog = await self._execution_catalog(item)
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
        if decision.scope == "github.repository.contents.read":
            step.input = {**step.input, "plannerInstruction": decision.instruction,
                "instruction": "Inspect only the repository path " + str(decision.action_input.get("path", ""))
                + " at " + str(decision.action_input.get("ref") or "the default revision")
                + ". Other paths require separate actions; no creation or edit is performed by this read."}
        self._session.add(step)
        await self._session.flush()
        logger.info("Runtime action selected work_item=%s run=%s step=%s scope=%s resource=%s",
            item.id, run.id, step_index, decision.scope, decision.resource_id)
        await TransactionalOutbox(self._session).enqueue(
            topic="run.progress",
            aggregate_type="run",
            aggregate_id=str(run.id),
            payload={
                "organization_id": str(item.organization_id),
                "work_item_id": str(item.id),
                "run_id": str(run.id),
                "job_id": str(item.job_id),
                "current_step": step_index,
                "continuation_phase": "execute",
                "planner_scope": decision.scope,
                "queue_partition": str(resource.get("provider", "")) + ":" + decision.resource_id + (":workspace" if ".workspace." in decision.scope else ""),
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

        if scope.startswith("browser."):
            session_id, observation_id = await self._latest_browser_context(
                step.run_id,
                step.step_index,
            )
            if not session_id and scope != "browser.navigation.open":
                raise RuntimeError(
                    f"Runtime capability {scope} requires an active browser session. "
                    "The planner must open the governed browser first."
                )
            if session_id:
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

    async def _close_browser_session(
        self,
        run_id: UUID,
        *,
        before_index: int,
    ) -> None:
        session_id, _ = await self._latest_browser_context(run_id, before_index)
        if session_id is None:
            return
        try:
            await browser_provider.close_session_id(UUID(session_id))
        except (LookupError, ValueError):
            # Recovery/restart may already have dropped the in-memory Browser session.
            return

    async def _policy_decision(
        self,
        *,
        item: WorkItem,
        agent: AgentIdentity,
        resource_id: str,
        scope: str,
        preparation_cache: dict[Any, Any] | None = None,
    ) -> dict[str, Any]:
        return await _evaluate(
            self._session,
            item.organization_id,
            _system_principal(item.organization_id),
            agent_id=agent.id,
            resource_id=resource_id,
            scope=scope,
            preparation_cache=preparation_cache,
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
            work_item_id=item.id,
            risk=str((decision.get("riskAssessment") or {}).get("effectiveRisk") or "low"),
        )
        universal = await self._universal_request(
            proposal=proposal,
            worker=worker,
            job=job,
            item=item,
            run=run,
        )
        if (settings.integration_foundation_enabled and universal.provider_permissions.adapter == "native_api"
            and universal.execution.capability.approval_recommendation == "required" and decision["outcome"] != "DENY"):
            decision = {**decision, "outcome": "REQUIRE_APPROVAL",
                "reason": "This provider action requires approval for the exact resource, content and revision."}
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
                continuation_phase="plan",
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
                "approvalReason": str(decision.get("reason") or "Human approval is required."),
            }
            run.status = "waiting_approval"
            item.status = "waiting_approval"
            _write_runtime_meta(
                item,
                waitingReason=str(decision.get("reason") or "Human approval is required."),
                approvalId=str(approval.id),
            )
            await self._queue_action_events(
                item=item,
                action=action,
                approval=approval,
                event="approval.created",
            )
            await notify_integration_work(self._session, item, key=f"approval:{approval.id}",
                content=f"I need approval for {scope} on {resource_id}. {decision.get('reason', 'Organizational policy requires approval.')} Proposed content: {json.dumps(input_payload, ensure_ascii=False)[:3000]}",
                references={"type": "approval", "id": str(approval.id)})
            await self._session.commit()
            return RuntimeStepOutcome(
                state="waiting_approval",
                work_item_id=item.id,
                run_id=run.id,
                current_step=current_step,
                summary=str(decision.get("reason") or "Action is waiting for human approval."),
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
            work_item_id=item.id,
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
        _write_runtime_meta(item, queueState="executing", activeActionScope=proposal.scope,
                            actionStartedAt=utcnow().isoformat())
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
        except ExecutionProviderError as exc:
            runtime_meta = _runtime_meta(item)
            if settings.integration_foundation_enabled and (item.payload or {}).get("integrationOrigin") and exc.error.code in {"authentication_error", "uncertain_outcome"}:
                action.payload = self._action_record_payload(proposal=proposal, decision=decision,
                    fingerprint=action_fingerprint(universal), result=None, error=exc.error.safe_message)
                action.status = "processing"
                if exc.error.code == "authentication_error":
                    action.payload = {**action.payload, "integrationCertainty": "not_executed"}
                else:
                    action.payload = {**action.payload, "integrationCertainty": "dispatching"}
                return await self._wait_integration(item, run, step, exc.error, current_step)
            if (settings.integration_foundation_enabled and (item.payload or {}).get("integrationOrigin")
                and not universal.execution.capability.side_effect
                and not proposal.scope.startswith("browser.")
                and exc.error.code != "authorization_error"):
                # One handler owns read retries and replanning. Keep dispatched
                # writes/permission denials on their existing certainty boundaries.
                action.status = "failed"
                action.payload = self._action_record_payload(proposal=proposal, decision=decision,
                    fingerprint=action_fingerprint(universal), result=None, error=exc.error.safe_message)
                logger.warning("Runtime read returned to recovery work_item=%s run=%s step=%s scope=%s code=%s retryable=%s",
                    item.id, run.id, current_step, proposal.scope, exc.error.code, exc.error.retryable)
                raise
            if exc.error.code == "browser_capacity_unavailable":
                retry_at = utcnow() + timedelta(
                    seconds=max(5, settings.browser_runtime_retry_backoff_seconds)
                )
                action.status = "processing"
                item.status = "queued"
                item.scheduled_at = retry_at
                run.status = "running"
                _write_runtime_meta(
                    item,
                    queueState="waiting_browser_capacity",
                    waitingReason="All available browser sessions are in use.",
                    capacityRetryAt=retry_at.isoformat(),
                )
                await self._session.commit()
                return RuntimeStepOutcome(
                    "continue", item.id, run.id, current_step,
                    "Waiting for browser capacity.",
                )
            try:
                provider_retry_count = int(runtime_meta.get("providerRetryCount", 0))
            except (TypeError, ValueError):
                provider_retry_count = 0
            browser_runtime_error = exc.error.code in {
                "browser_capacity_unavailable",
                "browser_cold_start_timeout",
            }
            retry_limit = (
                settings.browser_runtime_retry_limit
                if browser_runtime_error
                else settings.runtime_provider_retry_limit
            )
            retry_backoff = (
                settings.browser_runtime_retry_backoff_seconds
                if browser_runtime_error
                else settings.runtime_provider_retry_backoff_seconds
            )
            if exc.error.retryable and provider_retry_count < retry_limit:
                backoff_seconds = retry_backoff * (2**provider_retry_count)
                if exc.error.retry_after_seconds is not None:
                    backoff_seconds = max(backoff_seconds, exc.error.retry_after_seconds)
                retry_at = utcnow() + timedelta(seconds=backoff_seconds)
                action.status = "processing"
                action.payload = self._action_record_payload(
                    proposal=proposal,
                    decision=decision,
                    fingerprint=action_fingerprint(universal),
                    result=None,
                    error=exc.error.safe_message,
                )
                item.status = "queued"
                item.scheduled_at = retry_at
                run.status = "running"
                _write_runtime_meta(
                    item,
                    queueState="retrying",
                    waitingReason=exc.error.safe_message,
                    providerRetryAt=retry_at.isoformat(),
                    providerRetryReason=exc.error.safe_message,
                    providerRetryCount=provider_retry_count + 1,
                    providerRetryCode=exc.error.code,
                )
                await notify_integration_work(self._session, item, key=f"retry:{current_step}:{provider_retry_count}",
                    content=f"This action will retry after {retry_at.isoformat()}. {exc.error.safe_message} Completed steps remain saved.")
                await self._session.commit()
                return RuntimeStepOutcome(
                    "continue",
                    item.id,
                    run.id,
                    current_step,
                    "Execution provider returned a retryable error; action will retry shortly.",
                )
            action.status = "failed"
            action.payload = self._action_record_payload(
                proposal=proposal,
                decision=decision,
                fingerprint=action_fingerprint(universal),
                result=None,
                error=exc.error.safe_message,
            )
            await self._fail(
                item=item,
                run=run,
                step=step,
                message=exc.error.safe_message,
            )
            return RuntimeStepOutcome(
                "failed",
                item.id,
                run.id,
                current_step,
                exc.error.safe_message,
            )
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
        from app.application.services.task_activity import action_phase
        progress_label = action_phase(str((step.input or {}).get("scope", "")), (step.input or {}).get("actionInput"))[1]
        result_data = result.data if isinstance(result.data, dict) else {}
        logger.info("Runtime action completed work_item=%s run=%s step=%s scope=%s certainty=%s",
            item.id, run.id, current_step, (step.input or {}).get("scope"),
            result_data.get("executionCertainty"))
        await notify_integration_work(self._session, item, key=f"progress:{current_step}",
            content=f"{progress_label}: {result.summary}")
        run.status = "running"
        item.status = "running"
        _write_runtime_meta(
            item,
            currentStep=current_step + 1,
            providerRetryCount=0,
            providerRetryAt=None,
            providerRetryReason=None,
            providerRetryCode=None,
            waitingReason=None,
            approvalId=None,
        )
        await TransactionalOutbox(self._session).enqueue(
            topic="run.progress",
            aggregate_type="run",
            aggregate_id=str(run.id),
            payload={
                "organization_id": str(item.organization_id),
                "work_item_id": str(item.id),
                "run_id": str(run.id),
                "job_id": str(job.id),
                "current_step": current_step + 1,
                "continuation_phase": "plan",
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
            "plan",
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
        if (item.payload or {}).get("integrationOrigin"):
            completed = await self._session.scalar(select(RunStep.id).where(RunStep.run_id == run.id,
                RunStep.kind == "action", RunStep.status == "completed").limit(1))
            if completed is not None:
                item.status, run.status = "partial_completion", "partial_completion"
        _write_runtime_meta(item, failure=message[:4000], completedAt=utcnow().isoformat())
        await self._close_browser_session(
            run.id,
            before_index=step.step_index + 1,
        )
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
        await notify_integration_work(self._session, item, key="failed",
            content=f"I could not complete the remaining work: {message[:2000]} Already completed actions remain saved; no rollback is claimed.")
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
        result_status: Literal["completed", "attention"] = "completed",
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
        completed_actions = [step for step in steps if step.kind == "action" and step.status == "completed"]
        if (item.payload or {}).get("integrationOrigin") and not completed_actions:
            revision_definition = await self._session.get(JobRevision, item.job_revision_id)
            if revision_definition and (revision_definition.definition or {}).get("requiredCapabilities"):
                result_status = "attention"
        if (item.payload or {}).get("integrationOrigin") and any(step.status == "failed" for step in steps):
            result_status = "attention"
            unresolved = [str(_step_output(step).get("summary") or "An action could not complete.")[:500] for step in steps if step.status == "failed"]
            summary += "\n\nWork requiring attention: " + " ".join(unresolved)
        run.status = "completed"
        run.result_summary = summary
        item.status = "completed"
        if (item.payload or {}).get("integrationOrigin") and result_status == "attention":
            item.status, run.status = "partial_completion", "partial_completion"
        payload = dict(item.payload or {})
        payload["completedAt"] = now.isoformat()
        item.payload = payload
        runtime = _write_runtime_meta(
            item,
            currentStep=len(steps),
            resultSummary=summary,
            resultStatus=result_status,
            executionOutcome=("completed" if result_status == "completed" else
                "partial" if completed_actions else "blocked"),
            failure=None,
            completedAt=now.isoformat(),
        )

        if not runtime.get("resultId"):
            result = Result(
                organization_id=item.organization_id,
                worker_id=worker.id,
                job_id=job.id,
                latest_version=1,
                status=result_status,
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
                        "completionCriteriaVerified": result_status == "completed",
                        "executionOutcome": runtime["executionOutcome"],
                        "completedAt": now.isoformat(),
                        "workItemId": str(item.id),
                        "runId": str(run.id),
                        "agentId": str(worker.agent_identity_id),
                        "blocks": [{"type": "paragraph", "text": summary}],
                        "artifactIds": [],
                        "sourceReferences": [reference for step in steps
                            for reference in ((_step_output(step).get("data") or {}).get("externalReferences", []))],
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
            await append_live_result_message(
                self._session,
                item=item,
                run=run,
                job=job,
                worker=worker,
                result=result,
                summary=summary,
            )

        await self._close_browser_session(
            run.id,
            before_index=len(steps) + 1,
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
