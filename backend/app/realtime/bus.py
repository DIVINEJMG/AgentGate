from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from app.bootstrap.settings import settings
from app.observability.metrics import ExecutionMetrics
from app.realtime.contracts import RealtimeEvent


class RedisRealtimeBus:
    """Redis Streams are replayable delivery; Pub/Sub is the low-latency fast lane.

    PostgreSQL remains the system of record. This class only carries committed events.
    """

    def __init__(self, client: Redis) -> None:
        self._client: Any = client
        self._metrics = ExecutionMetrics()

    @classmethod
    def from_settings(cls) -> RedisRealtimeBus:
        return cls(Redis.from_url(settings.redis_dsn, decode_responses=True))

    @staticmethod
    def organization_stream(organization_id: UUID | str) -> str:
        return f"audoryn:events:{organization_id}"

    @staticmethod
    def run_stream(run_id: UUID | str) -> str:
        return f"audoryn:runs:{run_id}"

    @staticmethod
    def worker_stream(worker_id: UUID | str) -> str:
        return f"audoryn:worker:{worker_id}"

    @staticmethod
    def organization_channel(organization_id: UUID | str) -> str:
        return f"audoryn:realtime:{organization_id}"

    @staticmethod
    def dedupe_key(organization_id: UUID | str, event_id: str) -> str:
        return f"audoryn:event-dedupe:{organization_id}:{event_id}"

    async def emit(self, event: RealtimeEvent) -> str:
        dedupe_key = self.dedupe_key(event.organization_id, event.event_id)
        claimed = await self._client.set(dedupe_key, "pending", nx=True, ex=86400)
        if not claimed:
            existing = await self._client.get(dedupe_key)
            return str(existing or "duplicate")

        payload = event.as_dict()
        fields = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "organization_id": str(event.organization_id),
            "worker_id": str(event.worker_id) if event.worker_id else "",
            "job_id": str(event.job_id) if event.job_id else "",
            "run_id": str(event.run_id) if event.run_id else "",
            "resource_id": event.resource_id or "",
            "correlation_id": event.correlation_id or "",
            "timestamp": event.timestamp.isoformat(),
            "payload": json.dumps(event.payload, separators=(",", ":"), default=str),
        }
        try:
            stream_id = await self._client.xadd(
                self.organization_stream(event.organization_id),
                fields,
                maxlen=10000,
                approximate=True,
            )
        except Exception:
            await self._client.delete(dedupe_key)
            raise
        if event.run_id is not None:
            await self._client.xadd(
                self.run_stream(event.run_id),
                fields,
                maxlen=2000,
                approximate=True,
            )
        if event.worker_id is not None:
            await self._client.xadd(
                self.worker_stream(event.worker_id),
                fields,
                maxlen=2000,
                approximate=True,
            )
        payload["stream_id"] = str(stream_id)
        await self._client.set(dedupe_key, str(stream_id), ex=86400)
        await self._client.publish(
            self.organization_channel(event.organization_id),
            json.dumps(payload, separators=(",", ":"), default=str),
        )
        latency_ms = max(
            0.0,
            (datetime.now(UTC) - event.timestamp).total_seconds() * 1000,
        )
        await self._metrics.realtime_delivery(
            event_type=event.event_type,
            correlation_id=event.correlation_id or "",
            latency_ms=latency_ms,
        )
        return str(stream_id)

    async def read(
        self,
        *,
        organization_id: UUID,
        after: str = "0-0",
        count: int = 100,
        block_ms: int | None = None,
    ) -> list[dict[str, object]]:
        rows = await self._client.xread(
            {self.organization_stream(organization_id): after},
            count=count,
            block=block_ms,
        )
        events: list[dict[str, object]] = []
        for _, messages in rows:
            for stream_id, fields in messages:
                payload_raw = fields.get("payload") or "{}"
                try:
                    payload = json.loads(payload_raw)
                except json.JSONDecodeError:
                    payload = {}
                events.append(
                    {
                        "stream_id": str(stream_id),
                        "event_id": fields.get("event_id"),
                        "event_type": fields.get("event_type"),
                        "organization_id": fields.get("organization_id"),
                        "worker_id": fields.get("worker_id") or None,
                        "job_id": fields.get("job_id") or None,
                        "run_id": fields.get("run_id") or None,
                        "resource_id": fields.get("resource_id") or None,
                        "correlation_id": fields.get("correlation_id") or None,
                        "timestamp": fields.get("timestamp"),
                        "payload": payload,
                    }
                )
        return events

    async def subscribe(self, *, organization_id: UUID) -> AsyncIterator[dict[str, object]]:
        pubsub = self._client.pubsub()
        await pubsub.subscribe(self.organization_channel(organization_id))
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if not isinstance(data, str):
                    continue
                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    yield parsed
        finally:
            await pubsub.unsubscribe(self.organization_channel(organization_id))
            await pubsub.aclose()

    async def close(self) -> None:
        await self._client.aclose()
