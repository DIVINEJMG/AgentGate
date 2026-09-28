from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from redis.exceptions import RedisError

from app.bootstrap.settings import settings
from app.infrastructure.qstash.provider import (
    QStashRateLimitedError,
    UpstashQStashProvider,
)
from app.infrastructure.redis.coordination import RedisCoordinator

logger = logging.getLogger("audoryn.runtime.qstash")
_RUNTIME_DISPATCH_HEALTH_KEY = "runtime-dispatch-health"


@dataclass(frozen=True, slots=True)
class RuntimeSignalResult:
    message_id: str | None
    state: str
    detail: str | None
    updated_at: str

    def as_dict(self) -> dict[str, str | None]:
        return {
            "messageId": self.message_id,
            "state": self.state,
            "detail": self.detail,
            "updatedAt": self.updated_at,
        }


async def _record_dispatch_health(
    *,
    state: str,
    detail: str,
    ttl_seconds: int,
) -> None:
    coordinator = RedisCoordinator.from_settings()
    try:
        await coordinator.cache_set(
            _RUNTIME_DISPATCH_HEALTH_KEY,
            json.dumps(
                {
                    "state": state,
                    "detail": detail,
                    "updatedAt": datetime.now(UTC).isoformat(),
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            ttl_seconds=ttl_seconds,
        )
    except RedisError as error:
        logger.warning("Could not persist runtime dispatch health: %s", error)
    finally:
        try:
            await coordinator.close()
        except RedisError:
            pass


async def _clear_dispatch_health() -> None:
    coordinator = RedisCoordinator.from_settings()
    try:
        await coordinator.cache_delete(_RUNTIME_DISPATCH_HEALTH_KEY)
    except RedisError as error:
        logger.warning("Could not clear runtime dispatch health: %s", error)
    finally:
        try:
            await coordinator.close()
        except RedisError:
            pass


async def get_runtime_dispatch_health() -> dict[str, str | None]:
    coordinator = RedisCoordinator.from_settings()
    try:
        raw = await coordinator.cache_get(_RUNTIME_DISPATCH_HEALTH_KEY)
    except RedisError as error:
        logger.warning("Could not read runtime dispatch health: %s", error)
        return {
            "state": "unknown",
            "detail": "Runtime queue health telemetry is temporarily unavailable.",
            "updatedAt": None,
        }
    finally:
        try:
            await coordinator.close()
        except RedisError:
            pass

    if raw is None:
        return {"state": "ok", "detail": None, "updatedAt": None}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "state": "unknown",
            "detail": "Runtime queue health telemetry is temporarily unavailable.",
            "updatedAt": None,
        }
    return {
        "state": str(payload.get("state") or "unknown"),
        "detail": str(payload["detail"]) if payload.get("detail") else None,
        "updatedAt": (
            str(payload["updatedAt"]) if payload.get("updatedAt") else None
        ),
    }


async def request_runtime_execution_detailed(
    *,
    organization_id: UUID,
    work_item_id: UUID,
    expected_step: int | None = None,
    reason: str = "queued",
) -> RuntimeSignalResult:
    now = datetime.now(UTC).isoformat()
    if not settings.runtime_execution_enabled:
        return RuntimeSignalResult(
            message_id=None,
            state="disabled",
            detail="Runtime execution is disabled.",
            updated_at=now,
        )
    if not settings.qstash_runtime_execute_url:
        detail = (
            "Runtime execution URL is not configured; recovery sweep must pick up work."
        )
        logger.warning(detail)
        await _record_dispatch_health(
            state="unavailable",
            detail=detail,
            ttl_seconds=300,
        )
        return RuntimeSignalResult(
            message_id=None,
            state="unavailable",
            detail=detail,
            updated_at=now,
        )

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
    except QStashRateLimitedError as error:
        if error.daily_quota_exhausted:
            state = "quota_exhausted"
            detail = (
                "QStash daily message quota is exhausted. Work remains safely queued "
                "and will resume when QStash accepts publishes again."
            )
            ttl_seconds = 86_400
        else:
            state = "rate_limited"
            detail = (
                "QStash is temporarily rate limiting runtime dispatch. "
                "Work remains safely queued for recovery."
            )
            ttl_seconds = 300
        await _record_dispatch_health(
            state=state,
            detail=detail,
            ttl_seconds=ttl_seconds,
        )
        logger.error(
            "QStash runtime execution signal blocked state=%s work_item=%s: %s",
            state,
            work_item_id,
            error,
        )
        return RuntimeSignalResult(
            message_id=None,
            state=state,
            detail=detail,
            updated_at=now,
        )
    except Exception as error:
        detail = (
            "Runtime queue provider is temporarily unavailable. "
            "Work remains safely queued for recovery."
        )
        await _record_dispatch_health(
            state="unavailable",
            detail=detail,
            ttl_seconds=300,
        )
        logger.exception(
            "QStash runtime execution signal failed for %s: %s",
            work_item_id,
            error,
        )
        return RuntimeSignalResult(
            message_id=None,
            state="unavailable",
            detail=detail,
            updated_at=now,
        )

    await _clear_dispatch_health()
    return RuntimeSignalResult(
        message_id=message.id,
        state="queued",
        detail=None,
        updated_at=now,
    )


async def request_runtime_execution(
    *,
    organization_id: UUID,
    work_item_id: UUID,
    expected_step: int | None = None,
    reason: str = "queued",
) -> str | None:
    result = await request_runtime_execution_detailed(
        organization_id=organization_id,
        work_item_id=work_item_id,
        expected_step=expected_step,
        reason=reason,
    )
    return result.message_id


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
