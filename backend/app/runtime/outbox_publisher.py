from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import select

from app.application.services.web_research import web_research_destination
from app.bootstrap.settings import settings
from app.infrastructure.database.models import ConversationCommand
from app.infrastructure.database.outbox import TransactionalOutbox
from app.infrastructure.database.session import session_factory
from app.infrastructure.qstash.provider import UpstashQStashProvider
from app.realtime.bus import RedisRealtimeBus
from app.realtime.contracts import REALTIME_EVENT_TYPES, RealtimeEvent
from app.runtime.qstash_trigger import request_runtime_execution_detailed

logger = logging.getLogger(__name__)


def _uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _publish_realtime(
    topic: str,
    payload: dict[str, object],
    *,
    event_id: str,
) -> None:
    if topic not in REALTIME_EVENT_TYPES:
        return
    organization_id = _uuid(payload.get("organization_id") or payload.get("organizationId"))
    if organization_id is None:
        return

    bus = RedisRealtimeBus.from_settings()
    try:
        await bus.emit(
            RealtimeEvent(
                event_type=topic,  # type: ignore[arg-type]
                event_id=event_id,
                organization_id=organization_id,
                worker_id=_uuid(payload.get("worker_id") or payload.get("workerId")),
                job_id=_uuid(payload.get("job_id") or payload.get("jobId")),
                run_id=_uuid(payload.get("run_id") or payload.get("runId")),
                resource_id=(
                    str(payload.get("resource_id") or payload.get("resourceId"))
                    if payload.get("resource_id") or payload.get("resourceId")
                    else None
                ),
                correlation_id=(
                    str(payload.get("correlation_id") or payload.get("correlationId"))
                    if payload.get("correlation_id") or payload.get("correlationId")
                    else None
                ),
                payload=payload,
            )
        )
    finally:
        await bus.close()


async def drain_outbox_batch(*, limit: int = 100) -> int:
    """Publish committed outbox rows without letting one failure block the batch.

    The caller must invoke this only after business transactions have committed.
    Duplicate delivery is safe because RedisRealtimeBus deduplicates on event_id.
    """

    if settings.coding_execution_enabled:
        try:
            from app.execution.coding.service import maintain_coding_sessions
            await maintain_coding_sessions()
        except Exception:
            logger.exception("Coding retention maintenance is unavailable; other providers continue.")
    async with session_factory() as session:
        outbox = TransactionalOutbox(session)
        events = await outbox.unpublished(limit=limit)
        published = 0
        for event in events:
            payload = dict(event.payload) if isinstance(event.payload, dict) else {}
            try:
                if event.topic in {"integration.task.prepare", "integration.worker.prepare", "integration.event.received"}:
                    if not settings.integration_foundation_enabled or not settings.qstash_runtime_execute_url:
                        raise RuntimeError("Integration foundation processing is disabled or unconfigured; outbox entry remains saved.")
                    callback = urlsplit(settings.qstash_runtime_execute_url)
                    destination = urlunsplit((callback.scheme, callback.netloc, "/internal/v1/integrations/process", "", ""))
                    await UpstashQStashProvider.from_settings().publish(destination=destination,
                        body=json.dumps(payload, separators=(",", ":")), idempotency_key=f"integration-signal:{event.id}",
                        retries=3, timeout_seconds=120,
                        flow_control_key=f"audoryn-integrations-{settings.environment}-{payload.get('provider', 'preparation')}", parallelism=2)
                if event.topic == "web.research.queued":
                    destination = web_research_destination()
                    if not destination:
                        raise RuntimeError("Web research callback is not configured.")
                    await UpstashQStashProvider.from_settings().publish(
                        destination=destination,
                        body=json.dumps(payload, separators=(",", ":")),
                        idempotency_key=f"web-research:{event.aggregate_id}",
                        retries=3,
                        timeout_seconds=120,
                        flow_control_key=f"audoryn-web-research-{settings.environment}",
                        parallelism=2,
                    )
                if event.topic == "run.progress" and payload.get("continuation_phase") in {
                    "plan", "execute"
                }:
                    organization_id = _uuid(payload.get("organization_id"))
                    work_item_id = _uuid(payload.get("work_item_id"))
                    if organization_id is None or work_item_id is None:
                        raise RuntimeError(
                            "Runtime continuation event is missing its work item identity."
                        )
                    phase = str(payload["continuation_phase"])
                    try:
                        expected_step = int(payload["current_step"])
                    except (KeyError, TypeError, ValueError) as error:
                        raise RuntimeError(
                            "Runtime continuation event has no valid step."
                        ) from error
                    options: dict[str, Any] = {"queue_partition": str(payload["queue_partition"])} if payload.get("queue_partition") else {}
                    signal = await request_runtime_execution_detailed(
                        organization_id=organization_id,
                        work_item_id=work_item_id,
                        expected_step=expected_step,
                        reason=f"continuation-{phase}",
                        **options,
                    )
                    if signal.message_id is None:
                        raise RuntimeError(
                            f"Runtime continuation dispatch is {signal.state}; "
                            "outbox event remains pending."
                        )
                # Redis broadcasts are advisory to the UI. They must not gate
                # the next runtime action when Redis is unavailable.
                await _publish_realtime(
                    event.topic,
                    payload,
                    event_id=str(event.id),
                )
            except Exception:
                # External delivery failures leave only this row pending. A
                # different Work Item can still publish its continuation.
                logger.exception(
                    "Outbox event delivery failed topic=%s event=%s",
                    event.topic,
                    event.id,
                )
                await outbox.mark_failed(event)
                continue
            await outbox.mark_published(event)
            published += 1

        await session.commit()

        destination = web_research_destination()
        if destination and settings.qstash_url and settings.qstash_token:
            stale = list((await session.scalars(
                select(ConversationCommand).where(
                    ConversationCommand.family == "web.research",
                    ConversationCommand.status == "accepted",
                    ConversationCommand.created_at < datetime.now(UTC) - timedelta(minutes=3),
                ).order_by(ConversationCommand.created_at).limit(10)
            )).all())
            minute = datetime.now(UTC).strftime("%Y%m%d%H%M")
            for command in stale:
                try:
                    await UpstashQStashProvider.from_settings().publish(
                        destination=destination,
                        body=json.dumps({
                            "organization_id": str(command.organization_id),
                            "command_id": str(command.id),
                        }, separators=(",", ":")),
                        idempotency_key=f"web-research-recovery:{command.id}:{minute}",
                        retries=3,
                        timeout_seconds=120,
                        flow_control_key=f"audoryn-web-research-{settings.environment}",
                        parallelism=2,
                    )
                except Exception:
                    logger.exception("Web research recovery dispatch failed command=%s", command.id)

        return published
