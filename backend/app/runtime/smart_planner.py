from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.application.services.ai_gateway import ProviderAIGateway
from app.application.services.integration_foundation import notify_integration_work
from app.bootstrap.settings import settings
from app.domain.ai.providers import AIProviderError
from app.domain.ai.registry import ModelRegistry, ModelRoute
from app.infrastructure.ai.connections import (
    route_account_name,
    route_credential,
    transport_identity,
)
from app.infrastructure.ai.model_profiles import (
    generation_allowance,
)
from app.infrastructure.ai.openrouter import OpenRouterPlannerProvider
from app.infrastructure.ai.telemetry import DurableAIInvocationRecorder, TransportInvocationRecorder
from app.infrastructure.ai.text_routes import snapshot_routes, transport_for_text_route
from app.infrastructure.database.models import PlannerAttempt, PlannerDecision
from app.infrastructure.redis.coordination import RedisCoordinator

logger = logging.getLogger("uvicorn.error")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def recovery_context(steps, observations, decision):
    completed = [str(step.id) for step in steps if step.status == "completed"]
    pending = [step for step in steps if step.status != "completed"]
    uncertain = any(
        (step.output or {}).get("executionCertainty") == "uncertain"
        for step in pending
        if isinstance(step.output, dict)
    )
    compensation = any(
        str((step.input or {}).get("scope", "")).endswith(".compensate")
        for step in pending
        if isinstance(step.input, dict)
    )
    return {
        "mode": "reconcile"
        if uncertain
        else "compensate"
        if compensation
        else "continue"
        if completed
        else "start",
        "completedSteps": completed,
        "pendingSteps": [str(step.id) for step in pending],
        "pendingDecisionId": str(decision.id),
        "deadline": decision.deadline.isoformat(),
        "recoveryRound": decision.recovery_round,
        "evidenceReferences": [
            entry.get("stepId") for entry in observations if entry.get("stepId")
        ],
        "rollbackBoundary": "Completed external actions remain immutable. Reconcile uncertain writes; compensation requires an explicit supported capability and current authority.",
    }


def configured_routes() -> list[dict[str, Any]]:
    return snapshot_routes(settings, "complex")


def provider_for(route):
    return transport_for_text_route(route, settings, timeout_seconds=settings.runtime_planner_timeout_seconds)[0]


class CountedProvider:
    def __init__(self, provider, session, attempt, coordinator, org):
        self.provider, self.session, self.attempt = provider, session, attempt
        self.coordinator, self.org = coordinator, org
        self.name, self.capabilities = provider.name, provider.capabilities

    async def generate_text(self, *, model, request):
        if self.attempt.call_count >= 2:
            raise AIProviderError(
                "invalid_provider_response", "Planner correction budget exhausted.", retryable=False
            )
        # Reserve a conservative UTF-8 byte bound before sending any tenant evidence.
        profile = getattr(self.provider, "planner_profile", None)
        if isinstance(self.provider, OpenRouterPlannerProvider):
            profile = self.provider.profile_for(model)
        token_bound = len((request.system + request.prompt).encode()) + (
            generation_allowance(profile, request.max_output_tokens) if profile else request.max_output_tokens)
        if callable(getattr(self.provider, "validate_request", None)):
            self.provider.validate_request(model=model, request=request)
        if isinstance(self.provider, OpenRouterPlannerProvider):
            try:
                catalog = await self.provider.readiness(self.coordinator)
            except AIProviderError:
                raise
            except Exception as exc:
                raise AIProviderError("provider_unavailable", "Planner endpoint readiness is temporarily unavailable.",
                    retryable=True, account_scoped=True) from exc
            metadata = catalog.get(model, {})
            if not metadata.get("compatible"):
                raise AIProviderError(
                    "model_not_found",
                    "This planner endpoint is unavailable or incompatible.",
                    retryable=False,
                )
            profile = self.provider.profile_for(model)
            advertised_context = metadata.get("contextTokens")
            if isinstance(advertised_context, int):
                token_bound = len((request.system + request.prompt).encode()) + generation_allowance(profile, request.max_output_tokens)
                if token_bound > advertised_context:
                    raise AIProviderError("context_too_large", "Required evidence exceeds the endpoint context limit.", retryable=False)
        if profile and profile.streaming:
            last_update = 0.0
            async def progress(received_bytes):
                nonlocal last_update
                tick = time.monotonic()
                if last_update and tick - last_update < 10:
                    return
                last_update = tick
                logger.info("Planner response activity attempt=%s model=%s call=%s received_bytes=%s",
                            self.attempt.id, model, self.attempt.call_count, received_bytes)
                try:
                    await asyncio.wait_for(self.coordinator.cache_set(
                        f"planner:progress:{self.org}:{self.attempt.id}",
                        json.dumps({"receivedAt": datetime.now(UTC).isoformat(),
                                    "receivedBytes": received_bytes, "call": self.attempt.call_count}),
                        ttl_seconds=settings.runtime_planner_timeout_seconds + 60,
                    ), timeout=2)
                except Exception as exc:  # noqa: BLE001 - reporting failure must not fail inference
                    logger.warning("Planner progress unavailable attempt=%s error_type=%s", self.attempt.id, type(exc).__name__)
            self.provider.planner_progress = progress
            request = replace(request, stream=True)
        from app.infrastructure.ai.workloads import (
            account_identity,
            acquire_account_slot,
            release_workload_slot,
        )
        route = {"provider": self.name, "model": model}
        credential = getattr(self.provider, "credential", None) or route_credential(route, settings)
        account_slot = await acquire_account_slot(self.coordinator, settings,
            account_identity(settings, getattr(self.provider, "account_name", self.name), credential),
            "complex", settings.runtime_planner_timeout_seconds + 10)
        if account_slot is None:
            raise AIProviderError("rate_limited", "The model account is at its concurrency limit.",
                                  retryable=True, account_scoped=True, retry_after_seconds=15)
        transport_started = time.monotonic()
        reservation, called, usage = None, False, None
        try:
            from app.infrastructure.ai.workloads import reserve_workload_budgets
            reservation = await reserve_workload_budgets(self.coordinator, settings, self.org, "complex", token_bound)
            self.attempt.call_count += 1
            await self.session.commit()
            called = True
            logger.info("Planner inference started workload=complex purpose=execution_planning attempt=%s model=%s connection=%s call=%s model_called=true", self.attempt.id if hasattr(self.attempt, "id") else "unknown", model, self.name, self.attempt.call_count)
            response = await self.provider.generate_text(model=model, request=request)
            usage = response.usage
        except AIProviderError as error:
            error.model_called = called
            logger.warning("Planner inference rejected model=%s model_called=%s category=%s budget=%s",
                model, str(called).lower(), error.category, error.budget_scope or "provider_or_account")
            raise
        finally:
            from app.infrastructure.ai.workloads import settle_workload_budget
            await settle_workload_budget(self.coordinator, reservation, called=called, usage=usage)
            await release_workload_slot(self.coordinator, account_slot)
            logger.info("Planner inference ended workload=complex purpose=execution_planning model=%s connection=%s elapsed_seconds=%.3f", model, self.name, time.monotonic() - transport_started)
        self.attempt.transport_success = True
        await self.session.commit()
        return response

    async def analyze_media(self, **kwargs):
        raise AIProviderError(
            "configuration_missing", "Smart planning does not route vision.", retryable=False
        )


class AttemptGateway(ProviderAIGateway):
    def __init__(self, *, session, attempt, **kwargs):
        super().__init__(**kwargs)
        self.session, self.attempt = session, attempt

    async def generate_structured(self, **kwargs):
        result = await super().generate_structured(**kwargs)
        self.attempt.schema_valid = True
        await self.session.commit()
        return result


class SmartPlanner:
    def __init__(self, session, item, run, step_index):
        self.session, self.item, self.run = session, item, run
        self.step_index = step_index
        self.decision: PlannerDecision | None = None
        self.started = time.monotonic()
        self.attempt_count = 0

    async def prepare(self):
        if not settings.ai_enabled:
            raise AIProviderError(
                "configuration_missing",
                "Planning is disabled for this environment.",
                retryable=False,
            )
        row = await self.session.scalar(
            select(PlannerDecision)
            .where(
                PlannerDecision.organization_id == self.item.organization_id,
                PlannerDecision.work_item_id == self.item.id,
                PlannerDecision.run_id == self.run.id,
                PlannerDecision.step_index == self.step_index,
            )
            .with_for_update()
        )
        if row is None:
            row = PlannerDecision(
                organization_id=self.item.organization_id,
                work_item_id=self.item.id,
                run_id=self.run.id,
                step_index=self.step_index,
                routes=configured_routes(),
                deadline=datetime.now(UTC)
                + timedelta(seconds=settings.runtime_planner_timeout_seconds),
                context_fingerprint="",
                status="pending",
                workload="complex", purpose="execution_planning",
                generation=0,
                recovery_round=0,
            )
            self.session.add(row)
        self.decision = row
        await self.session.commit()
        return row

    def remaining(self):
        assert self.decision is not None
        return max(0.0, (self.decision.deadline - datetime.now(UTC)).total_seconds())

    async def choose(self, context, callback):
        row = self.decision
        assert row is not None
        digest = fingerprint(context)
        if row.status == "accepted" and row.context_fingerprint == digest:
            from app.runtime.planner.adaptive import AdaptivePlanDecision

            return AdaptivePlanDecision(**dict(row.accepted_proposal or {}))
        if row.status == "exhausted" or self.remaining() <= 0:
            raise AIProviderError(
                "timeout", "The saved planning decision budget is exhausted.", retryable=False
            )
        if row.retry_at and row.retry_at > datetime.now(UTC):
            retry_at = row.retry_at
            raise AIProviderError(
                "rate_limited",
                "Planning is waiting for its saved cooldown.",
                retryable=True,
                retry_after_seconds=max(1, int((retry_at - datetime.now(UTC)).total_seconds())),
            )
        row.context_fingerprint = digest
        row.generation += 1
        generation = row.generation
        row.status = "pending"
        row.accepted_proposal = None
        await self.session.commit()
        coordinator = RedisCoordinator.from_settings()
        from app.infrastructure.ai.workloads import acquire_workload_slot, release_workload_slot
        slot = await acquire_workload_slot(coordinator, settings, self.item.organization_id,
                                           "complex", int(self.remaining()) + 30)
        if slot is None:
            await coordinator.close()
            raise AIProviderError(
                "rate_limited",
                "Organization planning capacity is full.",
                retryable=True,
                retry_after_seconds=15,
            )
        try:
            self.attempt_count = len(
                (
                    await self.session.scalars(
                        select(PlannerAttempt).where(
                            PlannerAttempt.decision_id == row.id,
                            PlannerAttempt.organization_id == self.item.organization_id,
                        )
                    )
                ).all()
            )
            attempts = list(
                (
                    await self.session.scalars(
                        select(PlannerAttempt).where(
                            PlannerAttempt.decision_id == row.id,
                            PlannerAttempt.organization_id == self.item.organization_id,
                            PlannerAttempt.recovery_round == row.recovery_round,
                        )
                    )
                ).all()
            )
            deferred_until = []
            blocked_accounts = {
                a.provider
                for a in attempts
                if a.error_category in {"authentication_failed", "quota_exhausted"}
            }
            for index, route in enumerate(row.routes):
                if (
                    any(a.route_index == index for a in attempts)
                    or route["provider"] in blocked_accounts
                ):
                    continue
                if self.remaining() <= 0:
                    break
                account = route["provider"]
                credential = route_credential(route, settings)
                credential_id = fingerprint(
                    credential or "unconfigured"
                )[:16]
                account_name = route_account_name(route, settings)
                health_key = f"planner:health:{settings.environment}:{account_name}:{credential_id}:{route['model']}"
                account_cooldown = await coordinator.cache_get(
                    f"planner:account:{settings.environment}:{account_name}:{credential_id}"
                )
                if account_cooldown:
                    blocked_accounts.add(account)
                    deferred_until.append(datetime.now(UTC) + timedelta(seconds=60))
                    continue
                cooldown = await coordinator.cache_get(health_key)
                if cooldown:
                    deferred_until.append(datetime.now(UTC) + timedelta(seconds=120))
                    continue
                failures = int(await coordinator.cache_get(f"{health_key}:failures") or 0)
                probe = None
                if failures >= 5:
                    probe = await coordinator.acquire_lock(
                        f"{health_key}:probe",
                        ttl_seconds=int(self.remaining()) + 10,
                    )
                    if probe is None:
                        deferred_until.append(datetime.now(UTC) + timedelta(seconds=15))
                        continue
                attempt = PlannerAttempt(
                    organization_id=self.item.organization_id,
                    decision_id=row.id,
                    route_index=index,
                    recovery_round=row.recovery_round,
                    generation=generation,
                    provider=account,
                    model=route["model"],
                    transport_identity=transport_identity(route),
                    status="started",
                    call_count=0,
                    transport_success=False,
                    schema_valid=False,
                    accepted=False,
                    retryable=False,
                )
                self.attempt_count += 1
                payload = dict(self.item.payload or {})
                runtime = dict(payload.get("runtime") or {})
                runtime.update(
                    plannerDecisionId=str(row.id),
                    plannerAttemptCount=self.attempt_count,
                    plannerRecoveryRound=row.recovery_round,
                    plannerStatus="trying",
                )
                payload["runtime"] = runtime
                self.item.payload = payload
                self.session.add(attempt)
                await self.session.commit()
                started = time.monotonic()
                logger.info(
                    "Planner selection started workload=complex purpose=execution_planning work_item=%s run=%s decision=%s attempt=%s "
                    "model=%s connection=%s round=%s remaining_seconds=%.1f",
                    self.item.id, self.run.id, row.id, attempt.id, route["model"], account,
                    row.recovery_round, self.remaining(),
                )
                try:
                    provider = CountedProvider(
                        provider_for(route),
                        self.session,
                        attempt,
                        coordinator,
                        self.item.organization_id,
                    )
                    recorder = DurableAIInvocationRecorder()
                    gateway = AttemptGateway(
                        session=self.session,
                        attempt=attempt,
                        providers={account: provider},
                        registry=ModelRegistry(
                            [ModelRoute(role="planner", provider=account, model=route["model"])]
                        ),
                        recorder=TransportInvocationRecorder(recorder, transport_identity(route)) if recorder else None,
                        max_retries=0,
                        default_max_output_tokens=settings.smart_planner_output_tokens,
                        cancellation_deadline=row.deadline,
                    )
                    if attempts and time.monotonic() - self.started >= 10:
                        await notify_integration_work(
                            self.session,
                            self.item,
                            key=f"planner-switch:{row.id}:{row.recovery_round}:{index}",
                            content="The planning service had a problem. I am trying another service; completed work remains saved.",
                        )
                        await self.session.commit()
                    async with asyncio.timeout(
                        self.remaining()
                    ):
                        proposal = await callback(gateway)
                    # Lock acceptance against a newer owner generation, then refresh the
                    # task. A stale inference response cannot overwrite a newer proposal.
                    locked = await self.session.scalar(
                        select(PlannerDecision)
                        .where(
                            PlannerDecision.id == row.id,
                            PlannerDecision.organization_id == self.item.organization_id,
                        )
                        .with_for_update()
                    )
                    if locked is None:
                        raise AIProviderError(
                            "invalid_provider_response",
                            "The saved planning decision no longer exists.",
                            retryable=False,
                        )
                    await self.session.refresh(row)
                    await self.session.refresh(self.item)
                    await self.session.refresh(self.run)
                    if self.remaining() <= 0:
                        raise AIProviderError("timeout", "Planning response arrived after the saved deadline.", retryable=False)
                    if row.generation != generation or self.item.status in {
                        "cancelled",
                        "failed",
                        "completed",
                    }:
                        raise AIProviderError(
                            "invalid_provider_response",
                            "A stale planning response was discarded.",
                            retryable=False,
                        )
                    row.status = "accepted"
                    row.accepted_proposal = asdict(proposal)
                    row.accepted_provider, row.accepted_model = account, route["model"]
                    row.retry_at = None
                    attempt.schema_valid = True
                    attempt.accepted = True
                    attempt.status = "accepted"
                    attempt.latency_ms = int((time.monotonic() - started) * 1000)
                    await coordinator.cache_delete(f"{health_key}:failures")
                    await self.session.commit()
                    logger.info(
                        "Planner model accepted work_item=%s decision=%s attempt=%s model=%s "
                        "connection=%s calls=%s elapsed_ms=%s",
                        self.item.id, row.id, attempt.id, route["model"], account,
                        attempt.call_count, attempt.latency_ms,
                    )
                    return proposal
                except (AIProviderError, TimeoutError, RuntimeError) as error:
                    await self.session.rollback()
                    await self.session.refresh(row)
                    await self.session.refresh(attempt)
                    await self.session.refresh(self.item)
                    await self.session.refresh(self.run)
                    if self.item.status in {"cancelled", "failed", "completed"}:
                        raise AIProviderError(
                            "invalid_provider_response",
                            "Planning stopped because the task ended.",
                            retryable=False,
                        ) from error
                    if row.generation != generation:
                        raise AIProviderError(
                            "invalid_provider_response",
                            "Planning ownership changed.",
                            retryable=False,
                        ) from error
                    exc = (
                        error
                        if isinstance(error, AIProviderError)
                        else AIProviderError(
                            "timeout"
                            if isinstance(error, TimeoutError)
                            else "invalid_provider_response",
                            "Planner did not produce an acceptable decision.",
                            retryable=isinstance(error, TimeoutError),
                        )
                    )
                    not_called = attempt.call_count == 0
                    attempt.status = "blocked" if not_called else "failed"
                    attempt.failure_details = {"budgetScope": exc.budget_scope,
                        "organizationScoped": exc.organization_scoped, "accountScoped": exc.account_scoped,
                        "modelCalled": exc.model_called if exc.model_called is not None else not not_called,
                        "httpStatus": exc.status_code, "rejectionReason": exc.rejection_reason}
                    logger.warning(
                        "Planner %s work_item=%s run=%s decision=%s attempt=%s "
                        "model=%s connection=%s category=%s http_status=%s retryable=%s "
                        "account_scoped=%s organization_scoped=%s retry_after_seconds=%s error_type=%s",
                        "not called" if not_called else "model failed",
                        self.item.id, self.run.id, row.id, attempt.id, route["model"], account,
                        exc.category, exc.status_code, exc.retryable, exc.account_scoped, exc.organization_scoped,
                        exc.retry_after_seconds, type(error).__name__,
                    )
                    attempt.error_category, attempt.retryable = exc.category, exc.retryable
                    attempt.latency_ms = int((time.monotonic() - started) * 1000)
                    if exc.retry_after_seconds:
                        attempt.retry_at = datetime.now(UTC) + timedelta(
                            seconds=exc.retry_after_seconds
                        )
                    await self.session.commit()
                    attempts.append(attempt)
                    if exc.organization_scoped:
                        # A shared organization limit cannot be repaired by another model/account.
                        row.status = "exhausted"
                        if exc.retryable and row.recovery_round < 2:
                            retry_at = datetime.now(UTC) + timedelta(seconds=exc.retry_after_seconds or 60)
                            if retry_at < row.deadline:
                                row.recovery_round += 1
                                row.retry_at, row.status = retry_at, "pending"
                        if row.status == "exhausted":
                            exc.retryable = False
                        await self.session.commit()
                        raise exc
                    if (isinstance(error, TimeoutError) or exc.category == "timeout") and exc.status_code not in {408, 504}:
                        # Deadline/transport timeout pauses the decision; never switch on slowness.
                        row.status = "exhausted"
                        payload = dict(self.item.payload or {})
                        runtime = dict(payload.get("runtime") or {})
                        runtime["plannerExhaustion"] = {
                            "reason": "deadline" if self.remaining() <= 0 else "transport_wait",
                            "failed": sum(a.status == "failed" for a in attempts),
                            "untried": len(row.routes) - len({a.route_index for a in attempts}),
                            "unavailable": sum(a.error_category in {"model_not_found", "configuration_missing", "authentication_failed"} for a in attempts),
                        }
                        payload["runtime"] = runtime
                        self.item.payload = payload
                        await self.session.commit()
                        raise AIProviderError("timeout", "Planning wait ended; remaining models are untried. Saved work is preserved; no model switch was made.", retryable=False) from error
                    if exc.account_scoped or exc.category in {"authentication_failed", "quota_exhausted"}:
                        blocked_accounts.add(account)
                        account_delay = exc.retry_after_seconds or (
                            int(self.remaining()) + 30
                            if exc.category == "authentication_failed"
                            else 0
                        )
                        if account_delay:
                            await coordinator.cache_set(
                                f"planner:account:{settings.environment}:{account_name}:{credential_id}",
                                exc.category,
                                ttl_seconds=account_delay,
                            )
                    if exc.category == "content_rejected":
                        row.status = "exhausted"
                        await self.session.commit()
                        raise exc
                    if exc.category in {"provider_unavailable", "timeout"}:
                        failures = await coordinator.increment_counter(
                            f"{health_key}:failures", ttl_seconds=600
                        )
                        if failures >= 5:
                            await coordinator.cache_set(health_key, "cooldown", ttl_seconds=120)
                    elif exc.category in {"invalid_provider_response", "authentication_failed"}:
                        # A reachable endpoint's validation/permission response breaks
                        # the sequence of transport failures; it must not trip the circuit.
                        await coordinator.cache_delete(f"{health_key}:failures")
                finally:
                    if probe is not None:
                        await coordinator.release_lock(probe)
            transient = bool(deferred_until) or any(
                a.retryable or a.status == "started" for a in attempts
            )
            if (
                transient
                and row.recovery_round < min(2, settings.ai_max_retries)
                and self.remaining() > 15
            ):
                row.recovery_round += 1
                delay = 15 * 2 ** (row.recovery_round - 1)
                retry_times = [a.retry_at for a in attempts if a.retry_at] + deferred_until
                retry_at = max([datetime.now(UTC) + timedelta(seconds=delay), *retry_times])
                row.retry_at = retry_at
                if retry_at < row.deadline:
                    await self.session.commit()
                    raise AIProviderError(
                        "provider_unavailable",
                        "Eligible planners are temporarily unavailable.",
                        retryable=True,
                        retry_after_seconds=max(
                            delay, int((retry_at - datetime.now(UTC)).total_seconds())
                        ),
                    )
            row.status = "exhausted"
            attempted = {a.route_index for a in attempts}
            payload = dict(self.item.payload or {})
            runtime = dict(payload.get("runtime") or {})
            runtime["plannerExhaustion"] = {
                "reason": "deadline" if self.remaining() <= 0 else "no_eligible_model",
                "failed": sum(a.status == "failed" for a in attempts),
                "untried": len(row.routes) - len(attempted),
                "unavailable": sum(a.error_category in {"model_not_found", "configuration_missing", "authentication_failed"} for a in attempts),
            }
            payload["runtime"] = runtime
            self.item.payload = payload
            await self.session.commit()
            categories = {a.error_category for a in attempts}
            category = "provider_unavailable"
            for specific in ("quota_exhausted", "context_too_large", "invalid_provider_response"):
                if specific in categories:
                    category = specific
                    break
            if not attempts or categories <= {
                "configuration_missing",
                "model_not_found",
                "authentication_failed",
            }:
                category = "configuration_missing"
            raise AIProviderError(
                category,
                "The planning deadline was reached; some models may remain untried. Saved work is preserved."
                if self.remaining() <= 0 else "No eligible planner produced an acceptable decision. Saved work is preserved.",
                retryable=False,
            )
        finally:
            await release_workload_slot(coordinator, slot)
            await coordinator.close()
