from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from uuid import uuid4

from redis.exceptions import RedisError

from app.bootstrap.settings import settings
from app.infrastructure.qstash.provider import UpstashQStashProvider
from app.infrastructure.redis.coordination import RedisCoordinator

logger = logging.getLogger(__name__)

# Marks that a drain request is on its way. The drain clears it before it reads the outbox,
# so every row committed while it was set is read by that drain: a commit that sees the marker
# committed before the drain cleared it, and so before the drain read.
DRAIN_PENDING = "outbox-drain-pending"
COALESCED = "coalesced"
DRAINED_HERE = "drained-here"
# Set while the app is serving (app/bootstrap/lifecycle.py), so scripts and tests that commit
# never drain from their own process.
serving = False


async def drain_here() -> str:
    """Drain the outbox in this process, with the same grouping as a QStash drain request:
    while one drain is on its way, later commits rely on it; it clears the marker before it
    reads, so rows committed meanwhile are read by it or ask for the next one."""
    coordinator = RedisCoordinator.from_settings()
    try:
        if not await coordinator.claim_flag(DRAIN_PENDING, ttl_seconds=settings.outbox_drain_coalesce_seconds):
            return COALESCED
    except RedisError:
        logger.warning("Outbox drain coalescing is unavailable; draining on its own.")
    finally:
        with suppress(RedisError):
            await coordinator.close()
    from app.infrastructure.database.usage_records import record_usage
    from app.observability.usage import Usage, metered
    from app.runtime.outbox_publisher import drain_outbox_batch

    usage, outcome, limit = Usage(), "error", 100
    try:
        with metered(usage):
            published = limit
            while published >= limit:  # a full batch may have left more behind
                await drain_started()
                published = await drain_outbox_batch(limit=limit)
        outcome = "ok"
    finally:
        await record_usage(usage, kind="outbox.drain", organization_id=None, phase="local", outcome=outcome)
    return DRAINED_HERE


async def request_outbox_drain(*, reason: str) -> str | None:
    """Ask QStash to invoke the protected drain endpoint, unless a drain is already on its way.

    Returns the message id, COALESCED when an earlier request will read these rows, or None
    when draining is not configured. Failure to enqueue is deliberately non-fatal for the
    committed business transaction; the periodic recovery drain reads any missed rows.
    """

    if serving and settings.outbox_drain_local:
        return await drain_here()
    if (
        settings.qstash_outbox_drain_url is None
        or settings.qstash_url is None
        or settings.qstash_token is None
    ):
        return None

    coordinator = RedisCoordinator.from_settings()
    try:
        try:
            if not await coordinator.claim_flag(DRAIN_PENDING, ttl_seconds=settings.outbox_drain_coalesce_seconds):
                return COALESCED
        except RedisError:
            logger.warning("Outbox drain coalescing is unavailable; sending this drain request on its own.")
        provider = UpstashQStashProvider.from_settings()
        try:
            message = await provider.publish(
                destination=settings.qstash_outbox_drain_url,
                body=json.dumps({"reason": reason}, separators=(",", ":")),
                idempotency_key=f"outbox-drain:{uuid4()}",
                retries=3,
                timeout_seconds=20,
            )
        except Exception:
            # Nothing is on its way after all; let the next commit ask again.
            with suppress(RedisError):
                await coordinator.clear_flag(DRAIN_PENDING)
            raise
        return message.id
    finally:
        with suppress(RedisError):
            await coordinator.close()


async def drain_started() -> None:
    """Called by the drain before it reads: commits from now on ask for their own drain."""
    coordinator = RedisCoordinator.from_settings()
    try:
        await coordinator.clear_flag(DRAIN_PENDING)
    except RedisError:
        logger.warning("Outbox drain marker could not be cleared; it expires on its own.")
    finally:
        with suppress(RedisError):
            await coordinator.close()


_pending: set[asyncio.Task[str | None]] = set()
# Set by the delivery watchdog while it runs (app/runtime/delivery_watchdog.py).
watchdog_wake: asyncio.Event | None = None


def arm_watchdog() -> None:
    """Activity happened: the watchdog checks a little later that its deliveries arrived."""
    if watchdog_wake is not None:
        watchdog_wake.set()


def request_outbox_drain_after_commit(*, reason: str) -> None:
    """Best-effort immediate QStash signal from SQLAlchemy's after_commit hook."""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    arm_watchdog()
    task = loop.create_task(request_outbox_drain(reason=reason))
    # The event loop keeps only a weak reference to tasks: without this one, a pending request
    # can be garbage-collected mid-flight ("Task was destroyed but it is pending"). Since it may
    # already hold the drain marker, losing it would hold back every commit for up to a minute.
    _pending.add(task)
    task.add_done_callback(_pending.discard)

    def _consume_result(completed: asyncio.Task[str | None]) -> None:
        if completed.cancelled():
            logger.warning("Post-commit outbox drain was cancelled.")
            return
        error = completed.exception()
        if error is not None:
            # Never turn an already-successful DB commit into an application failure.
            # The scheduled QStash recovery sweep is the durability backstop.
            logger.warning(
                "Post-commit outbox drain failed; the watchdog and scheduled drain retry: %s",
                error,
            )

    task.add_done_callback(_consume_result)
