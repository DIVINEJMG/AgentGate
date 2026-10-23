from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.observability.metrics import ExecutionMetrics
from audoryn_shared.observability.usage import MeteredRedis
from audoryn_shared.realtime.contracts import RealtimeEvent


class RedisRealtimeBus:
    """Redis Streams are replayable delivery; Pub/Sub is the low-latency fast lane.

    PostgreSQL remains the system of record. This class only carries committed events.
    """

    def __init__(self, client: Redis) -> None:
        self._client: Any = client
        self._metrics = ExecutionMetrics()

    @classmethod
    def from_settings(cls) -> RedisRealtimeBus:
        return cls(MeteredRedis.from_url(settings.redis_dsn, decode_responses=True))

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
        claimed = True
        if hasattr(self._client, "set") and hasattr(self._client, "get"):
            claimed = await self._client.set(dedupe_key, "1", nx=True, ex=86400)
            if not claimed:
                # Already delivered (an outbox row re-drained after a crash): nothing to write.
                return "duplicate"

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
            if hasattr(self._client, "delete"):
                await self._client.delete(dedupe_key)
            raise
        # Written once. Per-run and per-worker copies were never read; readers of one run or
        # worker filter the organisation stream instead (read(run_id=..., worker_id=...)).
        payload["stream_id"] = str(stream_id)
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
        run_id: UUID | None = None,
        worker_id: UUID | None = None,
    ) -> list[dict[str, object]]:
        events, _ = await self.read_page(organization_id=organization_id, after=after, count=count,
                                         block_ms=block_ms, run_id=run_id, worker_id=worker_id)
        return events

    async def read_page(
        self,
        *,
        organization_id: UUID,
        after: str = "0-0",
        count: int = 100,
        block_ms: int | None = None,
        run_id: UUID | None = None,
        worker_id: UUID | None = None,
    ) -> tuple[list[dict[str, object]], str]:
        """The matching events after a position, and the last position scanned. With a filter,
        the position moves past events for other runs or workers, so a reader never stalls."""
        last = after
        rows = await self._client.xread(
            {self.organization_stream(organization_id): after},
            count=count,
            block=block_ms,
        )
        events: list[dict[str, object]] = []
        for _, messages in rows:
            for stream_id, fields in messages:
                last = str(stream_id)
                if run_id is not None and fields.get("run_id") != str(run_id):
                    continue
                if worker_id is not None and fields.get("worker_id") != str(worker_id):
                    continue
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
        return events, last

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
