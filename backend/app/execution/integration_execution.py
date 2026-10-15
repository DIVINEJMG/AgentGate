"""Bounded native actions with cross-process coordination and write reconciliation."""

from __future__ import annotations

import asyncio
import logging
import time

from jsonschema import ValidationError, validate
from redis.exceptions import RedisError

from app.bootstrap.settings import settings
from app.execution.contracts import ExecutionProviderError, ExecutionResult
from app.execution.providers.integration_hooks import ReconciliationHooks
from app.execution.recovery import RecoveryPlan, RecoveryPolicy
from app.infrastructure.redis.coordination import RedisCoordinator

logger = logging.getLogger(__name__)


async def initialization_budget(request):
    """Read only the saved deadline/stage, never source or command evidence."""
    from datetime import UTC, datetime
    from uuid import UUID

    from sqlalchemy import select

    from app.infrastructure.database.models import CodingSession
    from app.infrastructure.database.session import session_factory

    async with session_factory() as session:
        row = (
            await session.execute(
                select(
                    CodingSession.evidence["initialization"]["deadline"].astext,
                    CodingSession.evidence["initialization"]["stage"].astext,
                    CodingSession.status,
                ).where(
                    CodingSession.organization_id == request.organization_id,
                    CodingSession.work_item_id == request.work_item_id,
                    CodingSession.resource_id == UUID(request.resource.id),
                )
            )
        ).first()
    if not row or not row[0] or row[2] == "running":
        return settings.coding_initialization_seconds
    remaining = (datetime.fromisoformat(row[0]) - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        raise ExecutionProviderError.for_request(
            request,
            "timeout",
            f"Workspace initialization deadline expired at {row[1]}; saved artifacts and sandbox state remain available.",
        )
    return min(settings.coding_initialization_seconds, max(1, int(remaining)))


class DurableIntegrationRecovery(RecoveryPolicy):
    def plan(self, **kwargs):
        # Retry timing and attempt counters belong to the durable work item,
        # not an unrecorded inner loop inside one HTTP delivery.
        return RecoveryPlan(
            action="escalate",
            reason="Preserve the checkpoint and use the managed runtime recovery budget.",
        )


UNCERTAIN_WRITE_CODES = frozenset(
    {"timeout", "provider_unavailable", "temporary_provider_error", "verification_failed"}
)


class _NativeAction:
    """One native action's admission (cooldown, concurrency slots, rate limits, the mutation
    lock), dispatch, and the reconciliation of a write whose outcome is unknown."""

    def __init__(self, provider, request, context, coordination):
        self.provider, self.request, self.context, self.coordination = (
            provider,
            request,
            context,
            coordination,
        )
        self.label = (
            getattr(getattr(provider, "manifest", None), "display_name", None)
            or request.resource.provider
        )
        self.initialization = (
            request.operation == "repository.workspace.open"
            and request.resource.provider == "github"
        )
        self.execution_budget = (
            settings.coding_initialization_seconds if self.initialization else 90
        )
        self.lease = None
        self.slots = []
        self.connection = str(context.resource.metadata["connectionId"])
        self.partition = request.resource.provider
        if request.capability.target == "workspace":
            self.partition += ":workspace"
            self.connection += ":workspace"
        # A provider that said "slow down" to one action is not asked again by the others
        # on the same account until its wait is over.
        self.cooldown_key = f"integration-cooldown:{self.connection}"

    def wait(self, message, seconds: float = 60):
        return ExecutionProviderError.for_request(
            self.request, "rate_limited", message, retryable=True, retry_after_seconds=seconds
        )

    async def admit(self):
        if self.initialization:
            self.execution_budget = await initialization_budget(self.request)
        lease_seconds = self.execution_budget + 120
        cooling_until = float(await self.coordination.cache_get(self.cooldown_key) or "0")
        if cooling_until > time.time():
            raise self.wait(
                f"{self.label} is limiting requests right now", cooling_until - time.time()
            )
        organization_key = f"integration-org:{self.request.organization_id}:{self.partition}"
        connection_key = f"integration-connection:{self.connection}"
        for key, limit in (
            (organization_key, settings.integration_organization_concurrency),
            (connection_key, settings.integration_connection_concurrency),
        ):
            slot = await self.coordination.acquire_slot(key, limit=limit, ttl_seconds=lease_seconds)
            if slot is None:
                raise self.wait(
                    f"Other {self.label} actions are running, so this one is waiting for a free slot"
                )
            self.slots.append(slot)
        for key, limit, message in (
            (
                organization_key,
                settings.integration_organization_rate_per_minute,
                f"This workspace has reached its {self.label} actions for this minute",
            ),
            (
                connection_key,
                settings.integration_connection_rate_per_minute,
                f"This {self.label} account has reached its actions for this minute",
            ),
        ):
            if not await self.coordination.reserve_limit(key, limit=limit, ttl_seconds=60):
                raise self.wait(message)
        if self.request.capability.side_effect:
            self.lease = await self.coordination.acquire_lock(
                f"integration-mutation:{self.connection}:{self.request.resource.id}",
                ttl_seconds=lease_seconds,
            )
            if self.lease is None:
                raise self.wait(
                    "Another action is changing this resource, so this one is waiting for it to finish"
                )

    async def dispatch(self, reconcile_only, before_dispatch) -> ExecutionResult:
        request = self.request
        validate(request.input, request.capability.input_schema)
        if before_dispatch is not None:
            reconcile_only = await before_dispatch()
        if reconcile_only:
            result = await self.reconcile()
            if result is not None:
                return result
            raise ExecutionProviderError.for_request(
                request,
                "uncertain_outcome",
                "A previous delivery may have dispatched this write. Its outcome cannot be reconciled, so automatic replay is paused.",
            )
        try:
            async with asyncio.timeout(self.execution_budget):
                result = await self.provider.execute(
                    request=request,
                    configuration=self.context.configuration,
                    credential=self.context.credential,
                )
        except TimeoutError as error:
            raise ExecutionProviderError.for_request(
                request,
                "timeout",
                "Integration action exceeded its execution time budget.",
                retryable=True,
            ) from error
        try:
            validate(result.output, request.capability.output_schema)
        except ValidationError as error:
            raise ExecutionProviderError.for_request(
                request,
                "verification_failed",
                "The provider output did not satisfy its declared contract.",
            ) from error
        return result

    async def cool_down(self, error):
        """Pause the account's other actions for the wait a provider asked for."""
        if (
            error.code == "rate_limited"
            and error.retry_after_seconds
            and self.request.capability.target != "workspace"
        ):
            pause = min(float(error.retry_after_seconds), 86400.0)
            await self.coordination.cache_set(
                self.cooldown_key, str(time.time() + pause), ttl_seconds=max(1, int(pause) + 1)
            )

    def outcome_unknown(self, error) -> bool:
        """A write that may have happened. Workspace edits, patches and rollbacks are safe to repeat
        (a repeat finds its work done); the coding service marks those failures retryable. Only
        writes that reach other systems, and workspace commands (reported as uncertain_outcome),
        pause for checking."""
        repeat_safe = (
            self.request.capability.target == "workspace"
            and error.code == "temporary_provider_error"
            and error.retryable
        )
        return (
            self.request.capability.side_effect
            and error.code in UNCERTAIN_WRITE_CODES
            and not repeat_safe
        )

    async def reconcile(self) -> ExecutionResult | None:
        if not isinstance(self.provider, ReconciliationHooks):
            return None
        try:
            async with asyncio.timeout(20):
                return await self.provider.reconcile_write(
                    request=self.request,
                    configuration=self.context.configuration,
                    credential=self.context.credential,
                )
        except Exception:  # noqa: BLE001 - every inconclusive investigation must pause a write
            # An error while investigating a dispatched write is not evidence
            # that it never happened, including an expired credential here.
            logger.warning("Integration write reconciliation could not establish the outcome.")
            return None

    async def release(self, *, close: bool):
        try:
            if self.lease:
                await self.coordination.release_lock(self.lease)
            for slot in self.slots:
                await self.coordination.release_slot(slot)
            if close:
                await self.coordination.close()
        except RedisError:
            logger.warning("Integration coordination cleanup is unavailable; leases will expire.")


async def execute_native_action(
    *, provider, request, context, coordinator=None, reconcile_only=False, before_dispatch=None
) -> ExecutionResult:
    action = _NativeAction(
        provider, request, context, coordinator or RedisCoordinator.from_settings()
    )
    try:
        await action.admit()
        try:
            return await action.dispatch(reconcile_only, before_dispatch)
        except ExecutionProviderError as error:
            await action.cool_down(error.error)
            if not action.outcome_unknown(error.error):
                raise
            result = await action.reconcile()
            if result is not None:
                return result
            raise ExecutionProviderError.for_request(
                request,
                "uncertain_outcome",
                "The provider may have accepted this write, but its outcome could not be reconciled. Automatic replay is paused.",
            ) from error
    except RedisError as error:
        raise ExecutionProviderError.for_request(
            request,
            "provider_unavailable",
            "Integration coordination is unavailable. Work remains at its saved checkpoint.",
            retryable=True,
        ) from error
    finally:
        await action.release(close=coordinator is None)
