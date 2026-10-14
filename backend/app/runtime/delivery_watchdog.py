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
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from app.bootstrap.settings import settings
from app.infrastructure.database.models import OutboxEvent, WorkItem
from app.infrastructure.database.session import session_factory
from app.runtime import outbox_trigger

logger = logging.getLogger(__name__)

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
        from app.runtime.outbox_publisher import drain_outbox_batch

        published = await drain_outbox_batch(limit=100)
        logger.warning("Delivery watchdog published %s outbox rows whose drain request was lost.", published)
    if open_work is not None and settings.runtime_execution_enabled:
        from app.api.internal.runtime import run_sweep

        result = await run_sweep()
        if result.get("queued"):
            logger.warning("Delivery watchdog re-sent %s runtime wake-ups.", result["queued"])
    return stuck_outbox is not None or open_work is not None


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
                still_open = await recover_lost_deliveries()
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
