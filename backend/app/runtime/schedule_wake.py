"""On-time schedules (improvement plan, phase 2).

When a job's next scheduled time is set, one QStash message is sent, delayed to that time, to
`/internal/v1/runtime/schedule-due`, which queues just that job. Without it, scheduled work
waited for the 10-minute runtime sweep, which remains the backstop if a message is lost.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from app.bootstrap.settings import settings
from app.infrastructure.qstash.provider import UpstashQStashProvider

logger = logging.getLogger(__name__)
SCHEDULE_DUE_PATH = "/internal/v1/runtime/schedule-due"


def schedule_due_destination() -> str | None:
    runtime_url = settings.qstash_runtime_execute_url
    if not runtime_url:
        return None
    parts = urlsplit(runtime_url)
    if parts.scheme != "https" or not parts.hostname:
        return None
    return urlunsplit((parts.scheme, parts.netloc, SCHEDULE_DUE_PATH, "", ""))


async def arm_schedule(*, organization_id: UUID, job_id: UUID, due: datetime, now: datetime | None = None) -> str | None:
    """Ask QStash to wake the job at `due`. Arming the same due time again is deduplicated.

    QStash limits how far ahead a message may be delayed; a later time is reached in hops, the
    wake-up re-arming itself until the time arrives. Failure is logged, never raised: the sweep
    still queues the run.
    """
    destination = schedule_due_destination()
    if destination is None or settings.qstash_url is None or settings.qstash_token is None:
        return None
    now = now or datetime.now(UTC)
    delay = max(0, int((due - now).total_seconds()))
    hop = min(delay, settings.qstash_max_delay_seconds)
    deliver_at = int(now.timestamp()) + hop if hop < delay else int(due.timestamp())
    try:
        message = await UpstashQStashProvider.from_settings().publish(
            destination=destination,
            body=json.dumps({"organization_id": str(organization_id), "job_id": str(job_id),
                             "due": due.isoformat()}, separators=(",", ":")),
            idempotency_key=f"schedule-due:{job_id}:{due.isoformat()}:{deliver_at}",
            retries=3,
            timeout_seconds=60,
            delay_seconds=hop or None,
        )
    except Exception:
        logger.exception("Schedule wake-up not sent job=%s due=%s; the sweep will queue it", job_id, due.isoformat())
        return None
    return message.id
