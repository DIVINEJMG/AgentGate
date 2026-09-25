from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from app.bootstrap.settings import settings
from app.infrastructure.qstash.provider import UpstashQStashProvider

logger = logging.getLogger("audoryn.runtime.qstash")


async def request_runtime_execution(
    *,
    organization_id: UUID,
    work_item_id: UUID,
    expected_step: int | None = None,
    reason: str = "queued",
) -> str | None:
    if not settings.runtime_execution_enabled:
        return None
    if not settings.qstash_runtime_execute_url:
        logger.warning("Runtime execution URL is not configured; recovery sweep must pick up work.")
        return None

    step = max(0, int(expected_step or 0))
    body = json.dumps(
        {
            "organization_id": str(organization_id),
            "work_item_id": str(work_item_id),
            "expected_step": step,
            "reason": reason,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    dedupe_suffix = reason
    if reason == "recovery":
        dedupe_suffix = f"recovery:{datetime.now(UTC).strftime('%Y%m%d%H%M')}"
    try:
        message = await UpstashQStashProvider.from_settings().publish(
            destination=settings.qstash_runtime_execute_url,
            body=body,
            idempotency_key=f"runtime:{work_item_id}:{step}:{dedupe_suffix}",
            retries=3,
            timeout_seconds=settings.runtime_delivery_timeout_seconds,
        )
    except Exception:
        logger.exception("QStash runtime execution signal failed for %s", work_item_id)
        return None
    return message.id


async def request_runtime_sweep(*, reason: str = "recovery") -> str | None:
    if not settings.runtime_execution_enabled or not settings.qstash_runtime_sweep_url:
        return None
    body = json.dumps({"reason": reason}, separators=(",", ":"), sort_keys=True)
    try:
        message = await UpstashQStashProvider.from_settings().publish(
            destination=settings.qstash_runtime_sweep_url,
            body=body,
            idempotency_key=f"runtime-sweep:{reason}",
            retries=3,
            timeout_seconds=60,
        )
    except Exception:
        logger.exception("QStash runtime sweep signal failed.")
        return None
    return message.id
