"""Bounded native actions with cross-process coordination and write reconciliation."""

from __future__ import annotations

import asyncio
import logging

from jsonschema import ValidationError, validate
from redis.exceptions import RedisError

from app.bootstrap.settings import settings
from app.execution.contracts import ExecutionProviderError
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
        raise ExecutionProviderError(
            code="timeout",
            retryable=False,
            provider="github",
            operation=request.operation,
            correlation_id=request.correlation_id,
            safe_message=f"Workspace initialization deadline expired at {row[1]}; saved artifacts and sandbox state remain available.",
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


async def execute_native_action(
    *, provider, request, context, coordinator=None, reconcile_only=False, before_dispatch=None
):
    coordination = coordinator or RedisCoordinator.from_settings()
    lease = None
    slots = []
    initialization = (
        request.operation == "repository.workspace.open" and request.resource.provider == "github"
    )
    execution_budget = settings.coding_initialization_seconds if initialization else 90
    lease_seconds = execution_budget + 120

    def wait(message):
        return ExecutionProviderError(
            code="rate_limited",
            retryable=True,
            provider=request.resource.provider,
            operation=request.operation,
            correlation_id=request.correlation_id,
            safe_message=message,
            retry_after_seconds=60,
        )

    async def reconcile():
        if not isinstance(provider, ReconciliationHooks):
            return None
        try:
            async with asyncio.timeout(20):
                return await provider.reconcile_write(
                    request=request,
                    configuration=context.configuration,
                    credential=context.credential,
                )
        except Exception:  # noqa: BLE001 - every inconclusive investigation must pause a write
            # An error while investigating a dispatched write is not evidence
            # that it never happened, including an expired credential here.
            logger.warning("Integration write reconciliation could not establish the outcome.")
            return None

    try:
        if initialization:
            execution_budget = await initialization_budget(request)
            lease_seconds = execution_budget + 120
        org = str(request.organization_id)
        connection = context.resource.metadata["connectionId"]
        partition = request.resource.provider
        if request.capability.target == "workspace":
            partition += ":workspace"
            connection = str(connection) + ":workspace"
        for key, limit in [
            (f"integration-org:{org}:{partition}", settings.integration_organization_concurrency),
            (f"integration-connection:{connection}", settings.integration_connection_concurrency),
        ]:
            slot = await coordination.acquire_slot(key, limit=limit, ttl_seconds=lease_seconds)
            if slot is None:
                raise wait("Integration concurrency is full; this action will wait.")
            slots.append(slot)
        if not await coordination.reserve_limit(
            f"integration-org:{org}:{partition}",
            limit=settings.integration_organization_rate_per_minute,
            ttl_seconds=60,
        ):
            raise wait("Workspace integration rate budget is exhausted; queued work will wait.")
        if not await coordination.reserve_limit(
            f"integration-connection:{connection}",
            limit=settings.integration_connection_rate_per_minute,
            ttl_seconds=60,
        ):
            raise wait("Account integration rate budget is exhausted; queued work will wait.")
        if request.capability.side_effect:
            lease = await coordination.acquire_lock(
                f"integration-mutation:{connection}:{request.resource.id}",
                ttl_seconds=lease_seconds,
            )
            if lease is None:
                raise wait("Another action is changing this resource; this action will wait.")
        try:
            validate(request.input, request.capability.input_schema)
            if before_dispatch is not None:
                reconcile_only = await before_dispatch()
            if reconcile_only:
                result = await reconcile()
                if result is not None:
                    return result
                raise ExecutionProviderError(
                    code="uncertain_outcome",
                    retryable=False,
                    provider=request.resource.provider,
                    operation=request.operation,
                    correlation_id=request.correlation_id,
                    safe_message="A previous delivery may have dispatched this write. Its outcome cannot be reconciled, so automatic replay is paused.",
                )
            try:
                async with asyncio.timeout(execution_budget):
                    result = await provider.execute(
                        request=request,
                        configuration=context.configuration,
                        credential=context.credential,
                    )
            except TimeoutError as error:
                raise ExecutionProviderError(
                    code="timeout",
                    retryable=True,
                    provider=request.resource.provider,
                    operation=request.operation,
                    correlation_id=request.correlation_id,
                    safe_message="Integration action exceeded its execution time budget.",
                ) from error
            try:
                validate(result.output, request.capability.output_schema)
            except ValidationError as error:
                raise ExecutionProviderError(
                    code="verification_failed",
                    retryable=False,
                    provider=request.resource.provider,
                    operation=request.operation,
                    correlation_id=request.correlation_id,
                    safe_message="The provider output did not satisfy its declared contract.",
                ) from error
            return result
        except ExecutionProviderError as error:
            uncertain = request.capability.side_effect and error.error.code in {
                "timeout",
                "provider_unavailable",
                "temporary_provider_error",
                "verification_failed",
            }
            if not uncertain:
                raise
            result = await reconcile()
            if result is not None:
                return result
            raise ExecutionProviderError(
                code="uncertain_outcome",
                retryable=False,
                provider=request.resource.provider,
                operation=request.operation,
                correlation_id=request.correlation_id,
                safe_message="The provider may have accepted this write, but its outcome could not be reconciled. Automatic replay is paused.",
            ) from error
    except RedisError as error:
        raise ExecutionProviderError(
            code="provider_unavailable",
            retryable=True,
            provider=request.resource.provider,
            operation=request.operation,
            correlation_id=request.correlation_id,
            safe_message="Integration coordination is unavailable. Work remains at its saved checkpoint.",
        ) from error
    finally:
        try:
            if lease:
                await coordination.release_lock(lease)
            for slot in slots:
                await coordination.release_slot(slot)
            if coordinator is None:
                await coordination.close()
        except RedisError:
            logger.warning("Integration coordination cleanup is unavailable; leases will expire.")
