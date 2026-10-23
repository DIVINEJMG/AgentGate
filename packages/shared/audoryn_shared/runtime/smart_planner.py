from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from audoryn_shared.application.services.ai_gateway import ProviderAIGateway
from audoryn_shared.application.services.integration_foundation import notify_integration_work
from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.domain.ai.providers import AIErrorCategory, AIProviderError
from audoryn_shared.domain.ai.registry import ModelRegistry, ModelRoute
from audoryn_shared.infrastructure.ai.connections import (
    route_account_name,
    route_credential,
    transport_identity,
)
from audoryn_shared.infrastructure.ai.model_profiles import generation_allowance
from audoryn_shared.infrastructure.ai.openrouter import OpenRouterPlannerProvider
from audoryn_shared.infrastructure.ai.telemetry import (
    DurableAIInvocationRecorder,
    TransportInvocationRecorder,
)
from audoryn_shared.infrastructure.ai.text_routes import snapshot_routes, transport_for_text_route
from audoryn_shared.infrastructure.database.models import PlannerAttempt, PlannerDecision
from audoryn_shared.infrastructure.redis.coordination import RedisCoordinator

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
        """One planner call; an answer cut off with no finish reason (a dropped stream, F33) is asked
        of the same model once more, within the attempt's two calls, before any fallback."""
        try:
            return await self._call(model, request)
        except AIProviderError as error:
            if error.finish_reason != "none" or self.attempt.call_count >= 2:
                raise
            logger.warning("Planner answer was cut off; asking the same model again attempt=%s model=%s",
                           self.attempt.id, model)
            return await self._call(model, request)

    async def _call(self, model, request):
        if self.attempt.call_count >= 2:
            raise AIProviderError(
                "invalid_provider_response", "Planner correction budget exhausted.", retryable=False
            )
        profile, token_bound = await self._checked_bound(model, request)
        if profile and profile.streaming:
            self.provider.planner_progress = self._progress_reporter(model)
            request = replace(request, stream=True)
        from audoryn_shared.infrastructure.ai.workloads import (
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
                                  retryable=True, account_scoped=True, retry_after_seconds=15,
                                  budget_scope="concurrency")
        transport_started = time.monotonic()
        reservation, called, usage = None, False, None
        try:
            from audoryn_shared.infrastructure.ai.workloads import reserve_workload_budgets
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
            from audoryn_shared.infrastructure.ai.workloads import settle_workload_budget
            await settle_workload_budget(self.coordinator, reservation, called=called, usage=usage)
            await release_workload_slot(self.coordinator, account_slot)
            logger.info("Planner inference ended workload=complex purpose=execution_planning model=%s connection=%s elapsed_seconds=%.3f", model, self.name, time.monotonic() - transport_started)
        self.attempt.transport_success = True
        await self.session.commit()
        return response

    async def _checked_bound(self, model, request):
        """The model's profile and a conservative UTF-8 byte bound of the request, reserved before any
        tenant evidence is sent; raises when the endpoint cannot take it."""
        profile = getattr(self.provider, "planner_profile", None)
        if isinstance(self.provider, OpenRouterPlannerProvider):
            profile = self.provider.profile_for(model)
        token_bound = len((request.system + request.full_prompt).encode()) + (
            generation_allowance(profile, request.max_output_tokens) if profile else request.max_output_tokens)
        if callable(getattr(self.provider, "validate_request", None)):
            self.provider.validate_request(model=model, request=request)
        if not isinstance(self.provider, OpenRouterPlannerProvider):
            return profile, token_bound
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
        advertised_context = metadata.get("contextTokens")
        if isinstance(advertised_context, int) and token_bound > advertised_context:
            raise AIProviderError("context_too_large", "Required evidence exceeds the endpoint context limit.", retryable=False)
        return profile, token_bound

    def _progress_reporter(self, model):
        """Records streamed response activity (at most every 10 s) so a slow answer is seen as alive."""
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
        return progress

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
        """The planning decision for this step: the accepted one when the context is unchanged,
        otherwise the first acceptable answer from the saved routes, in order, within the decision's
        deadline. Failures are recorded per attempt; a retry or pause is raised when none answers."""
        row = self.decision
        assert row is not None
        digest = fingerprint(context)
        if row.status == "accepted" and row.context_fingerprint == digest:
            from audoryn_shared.runtime.planner.adaptive import AdaptivePlanDecision

            return AdaptivePlanDecision(**dict(row.accepted_proposal or {}))
        self._raise_if_unavailable(row)
        row.context_fingerprint = digest
        row.generation += 1
        generation = row.generation
        row.status = "pending"
        row.accepted_proposal = None
        await self.session.commit()
        coordinator = RedisCoordinator.from_settings()
        from audoryn_shared.infrastructure.ai.workloads import (
            acquire_workload_slot,
            release_workload_slot,
        )
        slot = await acquire_workload_slot(coordinator, settings, self.item.organization_id,
                                           "complex", int(self.remaining()) + 30)
        if slot is None:
            await coordinator.close()
            raise AIProviderError("rate_limited", "Organization planning capacity is full.", retryable=True,
                                  budget_scope="concurrency",
                                  retry_after_seconds=15)
        try:
            self.attempt_count = len((await self.session.scalars(select(PlannerAttempt).where(
                PlannerAttempt.decision_id == row.id,
                PlannerAttempt.organization_id == self.item.organization_id))).all())
            routes = _RouteState(attempts=await self._round_attempts(row))
            for index, route in enumerate(row.routes):
                if any(a.route_index == index for a in routes.attempts) or route["provider"] in routes.blocked:
                    continue
                if self.remaining() <= 0:
                    break
                proposal = await self._try_route(row, index, route, generation, callback, coordinator, routes)
                if proposal is not None:
                    return proposal
            await self._retry_or_exhaust(row, routes)
        finally:
            await release_workload_slot(coordinator, slot)
            await coordinator.close()

    def _raise_if_unavailable(self, row) -> None:
        if row.status == "exhausted" or self.remaining() <= 0:
            raise AIProviderError("timeout", "The saved planning decision budget is exhausted.", retryable=False)
        if row.retry_at and row.retry_at > datetime.now(UTC):
            raise AIProviderError("rate_limited", "Planning is waiting for its saved cooldown.", retryable=True,
                                  retry_after_seconds=max(1, int((row.retry_at - datetime.now(UTC)).total_seconds())))

    async def _round_attempts(self, row) -> list:
        """This recovery round's attempts (a fresh decision from Retry planning starts a new round,
        so every route is tried again; earlier attempts stay on record)."""
        return list((await self.session.scalars(select(PlannerAttempt).where(
            PlannerAttempt.decision_id == row.id,
            PlannerAttempt.organization_id == self.item.organization_id,
            PlannerAttempt.recovery_round == row.recovery_round))).all())

    def _rounds_used(self, row) -> int:
        """Recovery rounds used since this decision last started afresh."""
        fresh = dict((self.item.payload or {}).get("runtime") or {}).get("plannerFreshRound") or {}
        return row.recovery_round - (int(fresh.get("round", 0)) if fresh.get("decisionId") == str(row.id) else 0)

    async def _route_ready(self, route, coordinator, routes: _RouteState):
        """Whether the route may be tried now: (ready, health key, credential id, probe lock)."""
        account = route["provider"]
        credential_id = fingerprint(route_credential(route, settings) or "unconfigured")[:16]
        account_name = route_account_name(route, settings)
        health_key = f"planner:health:{settings.environment}:{account_name}:{credential_id}:{route['model']}"
        if await coordinator.cache_get(f"planner:account:{settings.environment}:{account_name}:{credential_id}"):
            routes.blocked.add(account)
            routes.deferred_until.append(datetime.now(UTC) + timedelta(seconds=60))
            return False, health_key, credential_id, None
        if await coordinator.cache_get(health_key):
            routes.deferred_until.append(datetime.now(UTC) + timedelta(seconds=120))
            return False, health_key, credential_id, None
        probe = None
        if int(await coordinator.cache_get(f"{health_key}:failures") or 0) >= 5:
            probe = await coordinator.acquire_lock(f"{health_key}:probe", ttl_seconds=int(self.remaining()) + 10)
            if probe is None:
                routes.deferred_until.append(datetime.now(UTC) + timedelta(seconds=15))
                return False, health_key, credential_id, None
        return True, health_key, credential_id, probe

    async def _start_attempt(self, row, index, route, generation):
        attempt = PlannerAttempt(
            organization_id=self.item.organization_id, decision_id=row.id, route_index=index,
            recovery_round=row.recovery_round, generation=generation, provider=route["provider"],
            model=route["model"], transport_identity=transport_identity(route), status="started", call_count=0,
            transport_success=False, schema_valid=False, accepted=False, retryable=False)
        self.attempt_count += 1
        payload = dict(self.item.payload or {})
        runtime = dict(payload.get("runtime") or {})
        runtime.update(plannerDecisionId=str(row.id), plannerAttemptCount=self.attempt_count,
                       plannerRecoveryRound=row.recovery_round, plannerStatus="trying")
        payload["runtime"] = runtime
        self.item.payload = payload
        self.session.add(attempt)
        await self.session.commit()
        logger.info(
            "Planner selection started workload=complex purpose=execution_planning work_item=%s run=%s decision=%s attempt=%s "
            "model=%s connection=%s round=%s remaining_seconds=%.1f",
            self.item.id, self.run.id, row.id, attempt.id, route["model"], route["provider"],
            row.recovery_round, self.remaining(),
        )
        return attempt

    async def _try_route(self, row, index, route, generation, callback, coordinator, routes: _RouteState):
        """One route's attempt: its accepted proposal, or None after recording a failure the
        next route may repair. Failures no other route can repair are raised."""
        ready, health_key, credential_id, probe = await self._route_ready(route, coordinator, routes)
        if not ready:
            return None
        attempt = await self._start_attempt(row, index, route, generation)
        started = time.monotonic()
        try:
            gateway = AttemptGateway(
                session=self.session, attempt=attempt,
                providers={route["provider"]: CountedProvider(provider_for(route), self.session, attempt, coordinator,
                                                              self.item.organization_id)},
                registry=ModelRegistry([ModelRoute(role="planner", provider=route["provider"], model=route["model"])]),
                recorder=TransportInvocationRecorder(recorder, transport_identity(route))
                if (recorder := DurableAIInvocationRecorder()) else None,
                max_retries=0, default_max_output_tokens=settings.smart_planner_output_tokens,
                cancellation_deadline=row.deadline,
            )
            if routes.attempts and time.monotonic() - self.started >= 10:
                await notify_integration_work(
                    self.session, self.item, key=f"planner-switch:{row.id}:{row.recovery_round}:{index}",
                    content="The planning service had a problem. I am trying another service; completed work remains saved.")
                await self.session.commit()
            async with asyncio.timeout(self.remaining()):
                proposal = await callback(gateway)
            return await self._accept(row, attempt, route, generation, proposal, health_key, coordinator, started)
        except (AIProviderError, TimeoutError, RuntimeError) as error:
            await self._record_failure(row, attempt, route, generation, error, health_key, credential_id,
                                       coordinator, routes, started)
            return None
        finally:
            if probe is not None:
                await coordinator.release_lock(probe)

    async def _accept(self, row, attempt, route, generation, proposal, health_key, coordinator, started):
        # Lock acceptance against a newer owner generation, then refresh the task. A stale
        # inference response cannot overwrite a newer proposal.
        locked = await self.session.scalar(select(PlannerDecision).where(
            PlannerDecision.id == row.id, PlannerDecision.organization_id == self.item.organization_id).with_for_update())
        if locked is None:
            raise AIProviderError("invalid_provider_response", "The saved planning decision no longer exists.", retryable=False)
        await self.session.refresh(row)
        await self.session.refresh(self.item)
        await self.session.refresh(self.run)
        if self.remaining() <= 0:
            raise AIProviderError("timeout", "Planning response arrived after the saved deadline.", retryable=False)
        if row.generation != generation or self.item.status in {"cancelled", "failed", "completed"}:
            raise AIProviderError("invalid_provider_response", "A stale planning response was discarded.", retryable=False)
        row.status = "accepted"
        row.accepted_proposal = asdict(proposal)
        row.accepted_provider, row.accepted_model = route["provider"], route["model"]
        row.retry_at = None
        attempt.schema_valid = attempt.accepted = True
        attempt.status = "accepted"
        attempt.latency_ms = int((time.monotonic() - started) * 1000)
        await coordinator.cache_delete(f"{health_key}:failures")
        await self.session.commit()
        logger.info("Planner model accepted work_item=%s decision=%s attempt=%s model=%s connection=%s calls=%s elapsed_ms=%s",
                    self.item.id, row.id, attempt.id, route["model"], route["provider"], attempt.call_count, attempt.latency_ms)
        return proposal

    async def _record_failure(self, row, attempt, route, generation, error, health_key, credential_id,
                              coordinator, routes: _RouteState, started) -> None:
        """Record the attempt's failure; raise when no other route can repair it."""
        await self.session.rollback()
        for record in (row, attempt, self.item, self.run):
            await self.session.refresh(record)
        if self.item.status in {"cancelled", "failed", "completed"}:
            raise AIProviderError("invalid_provider_response", "Planning stopped because the task ended.", retryable=False) from error
        if row.generation != generation:
            raise AIProviderError("invalid_provider_response", "Planning ownership changed.", retryable=False) from error
        exc = error if isinstance(error, AIProviderError) else AIProviderError(
            "timeout" if isinstance(error, TimeoutError) else "invalid_provider_response",
            "Planner did not produce an acceptable decision.", retryable=isinstance(error, TimeoutError))
        not_called = attempt.call_count == 0
        attempt.status = "blocked" if not_called else "failed"
        attempt.failure_details = {"budgetScope": exc.budget_scope, "organizationScoped": exc.organization_scoped,
            "accountScoped": exc.account_scoped, "modelCalled": exc.model_called if exc.model_called is not None else not not_called,
            "httpStatus": exc.status_code, "rejectionReason": exc.rejection_reason, "finishReason": exc.finish_reason}
        logger.warning(
            "Planner %s work_item=%s run=%s decision=%s attempt=%s model=%s connection=%s category=%s http_status=%s "
            "retryable=%s account_scoped=%s organization_scoped=%s retry_after_seconds=%s error_type=%s",
            "not called" if not_called else "model failed", self.item.id, self.run.id, row.id, attempt.id, route["model"],
            route["provider"], exc.category, exc.status_code, exc.retryable, exc.account_scoped, exc.organization_scoped,
            exc.retry_after_seconds, type(error).__name__)
        attempt.error_category, attempt.retryable = exc.category, exc.retryable
        attempt.latency_ms = int((time.monotonic() - started) * 1000)
        if exc.retry_after_seconds:
            attempt.retry_at = datetime.now(UTC) + timedelta(seconds=exc.retry_after_seconds)
        await self.session.commit()
        routes.attempts.append(attempt)
        if exc.organization_scoped:
            await self._organization_limit(row, exc)
        if (isinstance(error, TimeoutError) or exc.category == "timeout") and exc.status_code not in {408, 504}:
            # Deadline/transport timeout pauses the decision; never switch on slowness.
            await self._exhaust(row, routes, "deadline" if self.remaining() <= 0 else "transport_wait")
            raise AIProviderError("timeout", "Planning wait ended; remaining models are untried. Saved work is preserved; "
                                  "no model switch was made.", retryable=False) from error
        if exc.account_scoped or exc.category in {"authentication_failed", "quota_exhausted"}:
            routes.blocked.add(route["provider"])
            delay = exc.retry_after_seconds or (int(self.remaining()) + 30 if exc.category == "authentication_failed" else 0)
            if delay:
                account_name = route_account_name(route, settings)
                await coordinator.cache_set(f"planner:account:{settings.environment}:{account_name}:{credential_id}",
                                            exc.category, ttl_seconds=delay)
        if exc.category == "content_rejected":
            row.status = "exhausted"
            await self.session.commit()
            raise exc
        if exc.category in {"provider_unavailable", "timeout"}:
            if await coordinator.increment_counter(f"{health_key}:failures", ttl_seconds=600) >= 5:
                await coordinator.cache_set(health_key, "cooldown", ttl_seconds=120)
        elif exc.category in {"invalid_provider_response", "authentication_failed"}:
            # A reachable endpoint's validation/permission response breaks the sequence of
            # transport failures; it must not trip the circuit.
            await coordinator.cache_delete(f"{health_key}:failures")

    async def _organization_limit(self, row, exc: AIProviderError):
        """A shared organization limit cannot be repaired by another model or account."""
        row.status = "exhausted"
        if exc.retryable and self._rounds_used(row) < 2:
            retry_at = datetime.now(UTC) + timedelta(seconds=exc.retry_after_seconds or 60)
            if retry_at < row.deadline:
                row.recovery_round += 1
                row.retry_at, row.status = retry_at, "pending"
        if row.status == "exhausted":
            exc.retryable = False
        await self.session.commit()
        raise exc

    async def _exhaust(self, row, routes: _RouteState, reason: str) -> None:
        """No route can answer in this decision: record why, for the person and the record."""
        row.status = "exhausted"
        attempts = routes.attempts
        payload = dict(self.item.payload or {})
        runtime = dict(payload.get("runtime") or {})
        runtime["plannerExhaustion"] = {
            "reason": reason,
            "failed": sum(a.status == "failed" for a in attempts),
            "untried": len(row.routes) - len({a.route_index for a in attempts}),
            "unavailable": sum(a.error_category in {"model_not_found", "configuration_missing", "authentication_failed"}
                               for a in attempts),
        }
        payload["runtime"] = runtime
        self.item.payload = payload
        await self.session.commit()

    async def _retry_or_exhaust(self, row, routes: _RouteState):
        """Every route was tried or skipped: retry later when a failure was transient, otherwise
        exhaust the decision with the most specific reason."""
        attempts = routes.attempts
        transient = bool(routes.deferred_until) or any(a.retryable or a.status == "started" for a in attempts)
        if transient and self._rounds_used(row) < min(2, settings.ai_max_retries) and self.remaining() > 15:
            row.recovery_round += 1
            delay = 15 * 2 ** (row.recovery_round - 1)
            retry_times = [a.retry_at for a in attempts if a.retry_at] + routes.deferred_until
            retry_at = max([datetime.now(UTC) + timedelta(seconds=delay), *retry_times])
            row.retry_at = retry_at
            if retry_at < row.deadline:
                await self.session.commit()
                raise AIProviderError("provider_unavailable", "Eligible planners are temporarily unavailable.", retryable=True,
                                      retry_after_seconds=max(delay, int((retry_at - datetime.now(UTC)).total_seconds())))
        await self._exhaust(row, routes, "deadline" if self.remaining() <= 0 else "no_eligible_model")
        categories = {a.error_category for a in attempts}
        category: AIErrorCategory = "provider_unavailable"
        for specific in EXHAUSTION_CATEGORIES:
            if specific in categories:
                category = specific
                break
        if not attempts or categories <= {"configuration_missing", "model_not_found", "authentication_failed"}:
            category = "configuration_missing"
        raise AIProviderError(
            category,
            "The planning deadline was reached; some models may remain untried. Saved work is preserved."
            if self.remaining() <= 0 else "No eligible planner produced an acceptable decision. Saved work is preserved.",
            retryable=False,
        )


# The most specific reason a decision ran out, in order of preference.
EXHAUSTION_CATEGORIES: tuple[AIErrorCategory, ...] = ("quota_exhausted", "context_too_large", "invalid_provider_response")


@dataclass
class _RouteState:
    """What trying the routes has learned so far in one choice."""

    attempts: list
    blocked: set = field(default_factory=set)
    deferred_until: list = field(default_factory=list)


async def restart_planning(session, item, run) -> bool:
    """Retry planning (F16): the task's latest planning decision starts afresh in a new recovery
    round (new deadline, every route tried again; earlier attempts stay on record under their own
    round) and the task goes back in the queue."""
    row = await session.scalar(select(PlannerDecision).where(
        PlannerDecision.organization_id == item.organization_id, PlannerDecision.work_item_id == item.id,
        PlannerDecision.run_id == run.id).order_by(PlannerDecision.step_index.desc()).limit(1).with_for_update())
    if row is None or item.status != "waiting_ai":
        return False
    now = datetime.now(UTC)
    used = await session.scalar(select(func.max(PlannerAttempt.recovery_round)).where(PlannerAttempt.decision_id == row.id))
    row.status, row.retry_at, row.accepted_proposal = "pending", None, None
    row.recovery_round = max(row.recovery_round, used if used is not None else -1) + 1
    row.deadline = now + timedelta(seconds=settings.runtime_planner_timeout_seconds)
    row.generation += 1  # any answer still in flight for the old window is discarded
    payload = dict(item.payload or {})
    runtime = {key: value for key, value in dict(payload.get("runtime") or {}).items()
               if key not in {"plannerExhaustion", "aiRetryAt", "lastAIErrorCategory"}}
    runtime.update(aiRetryCount=0, queueState="queued", plannerStatus="retrying",
                   waitingReason="Planning again, as you asked.",
                   plannerFreshRound={"decisionId": str(row.id), "round": row.recovery_round})
    item.payload = {**payload, "runtime": runtime}
    item.status, item.scheduled_at = "queued", now
    run.status = "running"
    await session.commit()
    return True
