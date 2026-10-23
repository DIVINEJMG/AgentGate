"""Recovers work whose wake-up was lost, so nobody has to step in.

A wake-up can be lost: a drain request dropped by the process, or a QStash delivery to a
tunnel address that has since changed. The scheduled sweep recovers these, but only where a
schedule points at this backend. This watchdog runs inside the backend, only after activity:
a commit that queued deliveries arms one check a few minutes later. An idle backend makes no
queries, so the database can still scale to zero.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.infrastructure.database.models import OutboxEvent, WorkItem
from audoryn_shared.infrastructure.database.session import session_factory
from audoryn_shared.runtime import outbox_trigger

logger = logging.getLogger(__name__)

# The full sweep, in a process that runs steps: set by the execution service
# (audoryn_execution.service.install). Without it the watchdog asks the execution service for one.
sweep: Callable[[], Awaitable[dict[str, object]]] | None = None
LONGEST_WAIT_SECONDS = 600
MAX_RECHECKS = 3
OPEN = ("queued", "admitted", "running", "waiting_approval")


async def recover_lost_deliveries() -> bool:
    """One check. Returns True while work is still open, so the check comes back later."""
    now = datetime.now(UTC)
    grace = now - timedelta(seconds=settings.delivery_watchdog_seconds)
    async with session_factory() as session:
        stuck_outbox = await session.scalar(select(OutboxEvent.id).where(
            OutboxEvent.published_at.is_(None), OutboxEvent.created_at <= grace,
            or_(OutboxEvent.next_delivery_at.is_(None), OutboxEvent.next_delivery_at <= now)).limit(1))
        open_work = await session.scalar(select(WorkItem.id).where(
            WorkItem.status.in_(OPEN), WorkItem.scheduled_at <= now).limit(1))
    if stuck_outbox is not None:
        from audoryn_shared.runtime.outbox_publisher import drain_outbox_batch

        published = await drain_outbox_batch(limit=100)
        logger.warning("Delivery watchdog published %s outbox rows whose drain request was lost.", published)
    if open_work is not None and settings.runtime_execution_enabled:
        if sweep is not None:
            result = await sweep()
            if result.get("queued"):
                logger.warning("Delivery watchdog re-sent %s runtime wake-ups.", result["queued"])
        else:  # an api process: the execution service sweeps
            from audoryn_shared.runtime.qstash_trigger import request_runtime_sweep

            await request_runtime_sweep(reason=f"watchdog:{now:%Y%m%d%H%M}")
    return stuck_outbox is not None or open_work is not None


async def _measured_check() -> bool:
    """One check, metered like the scheduled sweep, so wake-ups it re-sends are counted."""
    from audoryn_shared.infrastructure.database.usage_records import record_usage
    from audoryn_shared.observability.usage import Usage, metered

    usage, outcome = Usage(), "error"
    try:
        with metered(usage):
            still_open = await recover_lost_deliveries()
        outcome = "ok"
        return still_open
    finally:
        await record_usage(usage, kind="runtime.watchdog", organization_id=None, outcome=outcome)


async def delivery_watchdog_loop() -> None:
    wake = outbox_trigger.watchdog_wake = asyncio.Event()
    wake.set()  # one check after start-up: deliveries may have been lost while it was down
    base = float(settings.delivery_watchdog_seconds)
    delay, rechecks = base, 0
    try:
        while True:
            await wake.wait()
            wake.clear()  # activity during the wait arms the next check
            await asyncio.sleep(delay)
            try:
                still_open = await _measured_check()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Delivery watchdog check failed; it tries again later.")
                still_open = True
            # While work stays open, check again less often each time, a few times at most
            # (open work can wait hours for an approval); new activity starts over.
            if still_open and rechecks < MAX_RECHECKS:
                delay, rechecks = min(delay * 2, LONGEST_WAIT_SECONDS), rechecks + 1
                wake.set()
            else:
                delay, rechecks = base, 0
    finally:
        outbox_trigger.watchdog_wake = None
