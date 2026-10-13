"""Saves usage meters (app.observability.usage) as rows in `usage_records`."""
from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar
from uuid import UUID

from app.bootstrap.settings import settings
from app.infrastructure.database.models import UsageRecord
from app.observability.context import structured_event
from app.observability.usage import Usage, metered

logger = logging.getLogger("uvicorn.error")
P = ParamSpec("P")
T = TypeVar("T")


def _uuid(value: Any) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        return None


async def record_usage(
    usage: Usage,
    *,
    kind: str,
    organization_id: Any,
    work_item_id: Any = None,
    run_id: Any = None,
    thread_id: Any = None,
    step: int | None = None,
    phase: str | None = None,
    outcome: str | None = None,
) -> None:
    """Log the meter and save it. Measuring must never fail or block the work it measures,
    so errors are logged and dropped, and the save is not itself counted."""
    fields = {"kind": kind, "work_item_id": str(work_item_id) if work_item_id else None,
              "run_id": str(run_id) if run_id else None, "step": step, "phase": phase,
              "outcome": outcome, **usage.as_dict()}
    structured_event(logger, "usage", fields=fields)
    if not settings.usage_records_enabled or _uuid(organization_id) is None:
        return
    from app.infrastructure.database.session import session_factory

    row = UsageRecord(
        organization_id=_uuid(organization_id), work_item_id=_uuid(work_item_id),
        run_id=_uuid(run_id), thread_id=_uuid(thread_id), kind=kind, step=step,
        phase=phase, outcome=(outcome or "")[:32] or None,
        db_statements=usage.db_statements, db_rows=usage.db_rows,
        qstash_messages=usage.qstash_messages, redis_commands=usage.redis_commands,
        ai_calls=usage.ai_calls, ai_failed_calls=usage.ai_failed_calls,
        ai_tokens_in=usage.ai_tokens_in, ai_tokens_out=usage.ai_tokens_out,
        ai_models=dict(usage.ai_models), duration_ms=int(usage.seconds * 1000),
    )
    try:
        async with asyncio.timeout(5):
            async with session_factory() as session:
                session.add(row)
                await session.commit()
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - measurement is best effort
        logger.warning("Usage record not saved kind=%s error_type=%s", kind, type(error).__name__)


def metered_endpoint(kind: str) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Meter a system endpoint (the outbox drain, the runtime sweep). Its usage is logged; it
    belongs to no single organisation, so no row is saved."""

    def decorate(handler: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(handler)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            usage, outcome = Usage(), "error"
            try:
                with metered(usage):
                    result = await handler(*args, **kwargs)
                outcome = "ok"
                return result
            finally:
                await record_usage(usage, kind=kind, organization_id=None, outcome=outcome)

        return wrapper

    return decorate
