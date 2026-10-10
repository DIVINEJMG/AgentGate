"""Durable preparation uses the same ordered selector, catalog and authority boundary."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from sqlalchemy import select, update

from app.application.services.ai_gateway import UnconfiguredAIGateway
from app.application.services.ordered_ai import OrderedTextGateway
from app.bootstrap.settings import settings
from app.domain.ai.providers import (
    AIInvocationContext,
    AIModelProvider,
    AIModelRole,
    AIProviderError,
)
from app.infrastructure.ai.connections import transport_identity
from app.infrastructure.ai.telemetry import DurableAIInvocationRecorder
from app.infrastructure.ai.text_routes import snapshot_routes, text_gateway
from app.infrastructure.database.models import PlannerAttempt, PlannerDecision
from app.infrastructure.redis.coordination import RedisCoordinator

logger = logging.getLogger("uvicorn.error")
PREPARABLE = {
    "accepted",
    "waiting_integration",
    "clarification_required",
    "waiting_ai",
    "policy_denied",
}


async def restart_exhausted_preparation(session, command):
    """Called only by human resume endpoints while holding the command row lock."""
    if not settings.smart_planner_enabled:
        return "queued"
    if command.target_id:
        return "already_prepared"
    old = await session.scalar(select(PlannerDecision).where(
        PlannerDecision.organization_id == command.organization_id,
        PlannerDecision.command_id == command.id,
        PlannerDecision.purpose == "integration_preparation",
    ).order_by(PlannerDecision.step_index.desc()).limit(1).with_for_update())
    now = datetime.now(UTC)
    if old is None or (old.status not in {"exhausted", "superseded"} and old.deadline > now):
        return "queued"
    old.status, old.generation, old.retry_at = "superseded", old.generation + 1, None
    fresh = PlannerDecision(id=uuid4(), organization_id=command.organization_id,
        command_id=command.id, purpose="integration_preparation", workload="complex",
        step_index=old.step_index + 1, context_fingerprint="", routes=snapshot_routes(settings, "complex"),
        deadline=now + timedelta(seconds=settings.runtime_planner_timeout_seconds),
        status="pending", generation=0, recovery_round=0)
    session.add(fresh)
    task = dict(command.payload.get("integrationTask", {}))
    task.pop("draft", None)
    command.payload = {**command.payload, "integrationTask": task,
                       "activePreparationDecisionId": str(fresh.id)}
    logger.info("AI preparation human retry command=%s previous_decision=%s decision=%s budget_seconds=%s",
        command.id, old.id, fresh.id, settings.runtime_planner_timeout_seconds)
    await session.flush()
    return "fresh_decision_queued"


class PreparationCalls:
    def __init__(self, provider, observer):
        self.provider, self.observer = provider, observer
        self.name, self.capabilities = provider.name, provider.capabilities

    async def generate_text(self, *, model, request):
        attempt = self.observer.attempt
        if attempt.call_count >= 2:
            raise AIProviderError(
                "invalid_provider_response", "Correction budget exhausted.", retryable=False
            )
        attempt.call_count += 1
        await self.observer.session.commit()
        result = await self.provider.generate_text(model=model, request=request)
        attempt.transport_success = True
        await self.observer.session.commit()
        return result

    async def close(self):
        await self.provider.close()

    async def analyze_media(self, *, model, request, media):
        raise AIProviderError(
            "configuration_missing", "Preparation does not route vision.", retryable=False
        )


class PreparationObserver:
    def __init__(self, session, decision, command, coordinator, lease, revision=None):
        self.session, self.decision, self.command = session, decision, command
        self.coordinator, self.lease = coordinator, lease
        self.revision = revision
        self.attempt: PlannerAttempt
        self.schema: dict
        self.started = 0.0
        self.request_fingerprint = request_fingerprint(command) if revision is None else None

    async def begin(self, index, route):
        existing = await self.session.scalar(
            select(PlannerAttempt).where(
                PlannerAttempt.decision_id == self.decision.id,
                PlannerAttempt.organization_id == self.command.organization_id,
                PlannerAttempt.recovery_round == self.decision.recovery_round,
                PlannerAttempt.route_index == index,
            )
        )
        if existing:
            if existing.status == "started":
                existing.status, existing.error_category, existing.retryable = (
                    "failed",
                    "cancelled",
                    True,
                )
            await self.session.commit()
            return False
        self.attempt = PlannerAttempt(
            organization_id=self.command.organization_id,
            decision_id=self.decision.id,
            route_index=index,
            recovery_round=self.decision.recovery_round,
            generation=self.decision.generation,
            provider=route["provider"],
            model=route["model"],
            transport_identity=transport_identity(route),
            status="started",
            call_count=0,
        )
        self.session.add(self.attempt)
        await self.session.commit()
        self.started = time.monotonic()
        return True

    async def accept(self, proposal):
        # Reacquire state only after inference; no transaction remains open while waiting.
        await self.session.refresh(self.command, with_for_update=True)
        if self.revision is not None:
            pending = (
                self.command.status == "waiting_integration"
                and self.command.current_revision == self.revision.revision
            )
        else:
            pending = self.command.status in PREPARABLE and not self.command.target_id
        if not pending:
            raise AIProviderError("cancelled", "Preparation is no longer pending.", retryable=False)
        if self.revision is None and request_fingerprint(self.command) != self.request_fingerprint:
            raise AIProviderError(
                "cancelled",
                "The human request changed; stale preparation was discarded.",
                retryable=False,
            )
        if datetime.now(UTC) >= self.decision.deadline or not await self.coordinator.renew_lock(
            self.lease, ttl_seconds=30
        ):
            raise AIProviderError(
                "cancelled", "Preparation ownership or deadline expired.", retryable=False
            )
        from jsonschema import Draft202012Validator

        Draft202012Validator(self.schema).validate(proposal)
        result = await self.session.execute(
            update(PlannerDecision)
            .where(
                PlannerDecision.id == self.decision.id,
                PlannerDecision.organization_id == self.command.organization_id,
                PlannerDecision.generation == self.decision.generation,
                PlannerDecision.status == "pending",
            )
            .values(
                status="accepted",
                accepted_proposal=proposal,
                accepted_model=self.attempt.model,
                accepted_provider=self.attempt.provider,
            )
        )
        if result.rowcount != 1:
            raise AIProviderError(
                "cancelled", "A stale preparation response was discarded.", retryable=False
            )
        self.attempt.status, self.attempt.accepted, self.attempt.schema_valid = (
            "accepted",
            True,
            True,
        )
        self.attempt.latency_ms = int((time.monotonic() - self.started) * 1000)
        await self.session.commit()

    async def reject(self, error):
        self.attempt.status = "failed"
        self.attempt.error_category, self.attempt.retryable = error.category, error.retryable
        self.attempt.failure_details = {"budgetScope": error.budget_scope,
            "organizationScoped": error.organization_scoped, "accountScoped": error.account_scoped,
            "modelCalled": error.model_called, "httpStatus": error.status_code,
            "rejectionReason": error.rejection_reason}
        self.attempt.latency_ms = int((time.monotonic() - self.started) * 1000)
        if error.retry_after_seconds:
            self.attempt.retry_at = datetime.now(UTC) + timedelta(seconds=error.retry_after_seconds)
        await self.session.commit()


class DurablePreparationGateway(UnconfiguredAIGateway):
    def __init__(self, session, command, purpose="integration_preparation", revision=None):
        super().__init__("Durable preparation only supports structured task decisions.")
        self.session, self.command, self.purpose = session, command, purpose
        self.revision = revision

    def target_filter(self):
        return (
            PlannerDecision.preparation_revision_id == self.revision.id
            if self.revision is not None
            else PlannerDecision.command_id == self.command.id
        )

    def target_fields(self):
        return (
            {"preparation_revision_id": self.revision.id}
            if self.revision is not None
            else {"command_id": self.command.id}
        )

    def with_workload(self, workload, purpose):
        if workload != "complex":
            raise AIProviderError(
                "configuration_missing",
                "Preparation cannot select interactive routing.",
                retryable=False,
            )
        return self

    async def prepare(self):
        # Caller owns the command row lock. Start the budget before catalog/tool preparation.
        row = await self.session.scalar(
            select(PlannerDecision)
            .where(
                PlannerDecision.organization_id == self.command.organization_id,
                self.target_filter(),
                PlannerDecision.purpose == self.purpose,
            )
            .order_by(PlannerDecision.step_index.desc()).limit(1)
            .with_for_update()
        )
        if row is None:
            self.session.add(
                PlannerDecision(
                    organization_id=self.command.organization_id,
                    **self.target_fields(),
                    step_index=0,
                    purpose=self.purpose,
                    workload="complex",
                    routes=snapshot_routes(settings, "complex"),
                    context_fingerprint="",
                    deadline=datetime.now(UTC)
                    + timedelta(seconds=settings.runtime_planner_timeout_seconds),
                    status="pending",
                    generation=0,
                    recovery_round=0,
                )
            )
        await self.session.commit()

    async def generate_structured(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        schema_name: str,
        schema: dict[str, object],
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> dict[str, object]:
        kwargs = {"system": system, "prompt": prompt, "schema_name": schema_name, "schema": schema}
        # Fingerprints contain no stored raw prompt. Changed catalog/context creates a new
        # generation, while preserving the original deadline/order and attempt history.
        digest = hashlib.sha256(
            json.dumps(
                {key: kwargs[key] for key in ("system", "prompt", "schema", "schema_name")},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        coordinator = RedisCoordinator.from_settings()
        lease = await coordinator.acquire_lock(
            f"preparation:{self.command.organization_id}:{self.command.id}:"
            f"{self.command.payload.get('activePreparationDecisionId', 'initial') if self.revision is None else self.revision.id}",
            ttl_seconds=settings.runtime_planner_timeout_seconds + 30,
        )
        if not lease:
            await coordinator.close()
            raise AIProviderError(
                "rate_limited",
                "Preparation is already running.",
                retryable=True,
                retry_after_seconds=30,
            )
        try:
            row = await self.session.scalar(
                select(PlannerDecision)
                .where(
                    PlannerDecision.organization_id == self.command.organization_id,
                    self.target_filter(),
                    PlannerDecision.purpose == self.purpose,
                )
                .order_by(PlannerDecision.step_index.desc()).limit(1)
                .with_for_update()
            )
            if row is None:
                row = PlannerDecision(
                    organization_id=self.command.organization_id,
                    **self.target_fields(),
                    step_index=0,
                    purpose=self.purpose,
                    workload="complex",
                    routes=snapshot_routes(settings, "complex"),
                    context_fingerprint=digest,
                    deadline=datetime.now(UTC)
                    + timedelta(seconds=settings.runtime_planner_timeout_seconds),
                    status="pending",
                    generation=0,
                    recovery_round=0,
                )
                self.session.add(row)
                await self.session.commit()
            if row.deadline <= datetime.now(UTC) or row.status in {"exhausted", "superseded"}:
                row.status = "exhausted"
                await self.session.commit()
                raise AIProviderError(
                    "timeout",
                    "Task preparation deadline expired; the saved request is preserved.",
                    retryable=False,
                )
            if row.context_fingerprint != digest:
                row.context_fingerprint, row.accepted_proposal, row.status = digest, None, "pending"
                # Do not forget attempted models or extend the budget when evidence changes.
            elif row.status == "accepted":
                from jsonschema import Draft202012Validator

                Draft202012Validator(kwargs["schema"]).validate(row.accepted_proposal)
                await self.session.commit()
                return cast(dict[str, object], row.accepted_proposal)
            if row.retry_at and row.retry_at > datetime.now(UTC):
                raise AIProviderError(
                    "rate_limited",
                    "Preparation is waiting for recovery.",
                    retryable=True,
                    retry_after_seconds=max(
                        1, int((row.retry_at - datetime.now(UTC)).total_seconds())
                    ),
                )
            row.generation += 1
            await self.session.commit()
            observer = PreparationObserver(
                self.session, row, self.command, coordinator, lease, self.revision
            )
            observer.schema = schema

            def factory(route, role, context):
                gateway = text_gateway(
                    route, role, context, settings, DurableAIInvocationRecorder(), "complex"
                )
                gateway._providers = {
                    name: cast(AIModelProvider, PreparationCalls(provider, observer))
                    for name, provider in gateway._providers.items()
                }
                return gateway

            remaining = max(0, (row.deadline - datetime.now(UTC)).total_seconds())
            gateway = OrderedTextGateway(
                legacy=None,
                routes=row.routes,
                factory=factory,
                budget_seconds=remaining,
                attempt_seconds=remaining,
                workload="complex",
                purpose=self.purpose,
                observer=observer,
            )
            try:
                return await gateway.generate_structured(
                    role=role,
                    system=system,
                    prompt=prompt,
                    schema_name=schema_name,
                    schema=schema,
                    context=context,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                )
            except AIProviderError as error:
                if error.category == "cancelled":
                    # A stale owner cannot exhaust or overwrite another generation's decision.
                    await self.session.commit()
                    raise
                attempts = list(
                    (
                        await self.session.scalars(
                            select(PlannerAttempt).where(
                                PlannerAttempt.decision_id == row.id,
                                PlannerAttempt.recovery_round == row.recovery_round,
                            )
                        )
                    ).all()
                )
                transient = any(a.retryable for a in attempts)
                # Terminal permission/safety/quota failures must not be converted into model retries.
                retry_at = datetime.now(UTC)
                delay = 0
                retry = (
                    transient
                    and row.recovery_round < 2
                    and row.deadline > datetime.now(UTC)
                    and error.category not in {"content_rejected", "cancelled", "quota_exhausted"}
                )
                if retry:
                    delay = max(15 * 2**row.recovery_round, error.retry_after_seconds or 0)
                    retry_at = max(
                        [
                            datetime.now(UTC) + timedelta(seconds=delay),
                            *[a.retry_at for a in attempts if a.retry_at],
                        ]
                    )
                    retry = retry_at < row.deadline
                if retry:
                    row.recovery_round += 1
                    row.retry_at, row.status = retry_at, "pending"
                    error.retryable, error.retry_after_seconds = True, delay
                else:
                    row.status, error.retryable = "exhausted", False
                await self.session.commit()
                logger.warning(
                    "AI preparation paused workload=complex purpose=%s command=%s category=%s retryable=%s",
                    self.purpose,
                    self.command.id,
                    error.category,
                    error.retryable,
                )
                raise
        finally:
            try:
                if self.session.is_active:
                    await self.session.commit()
                else:
                    await self.session.rollback()
            finally:
                try:
                    await coordinator.release_lock(lease)
                finally:
                    await coordinator.close()


def request_fingerprint(command):
    task = (command.payload or {}).get("integrationTask", {})
    return hashlib.sha256(
        json.dumps(
            {**{key: task.get(key) for key in ("workerId", "instruction", "context")},
             "activeDecision": command.payload.get("activePreparationDecisionId")}, sort_keys=True
        ).encode()
    ).hexdigest()
