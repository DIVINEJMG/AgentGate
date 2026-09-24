import logging
from dataclasses import dataclass
from typing import Protocol

from app.observability.context import structured_event

RUN_DURATION = "audoryn_run_duration"
ACTION_LATENCY = "audoryn_action_latency"
POLICY_DENIALS = "audoryn_policy_denials"
APPROVAL_WAIT = "audoryn_approval_wait"
RUNTIME_RETRY = "audoryn_runtime_retry"
PROVIDER_FAILURES = "audoryn_provider_failures"

PROVIDER_EXECUTION_LATENCY = "audoryn_provider_execution_latency_ms"
CAPABILITY_SUCCESS = "audoryn_capability_success"
VERIFICATION_FAILURE = "audoryn_verification_failure"
EXECUTION_RETRY = "audoryn_execution_retry"
EXECUTION_FALLBACK = "audoryn_execution_fallback"
REDIS_STREAM_LAG = "audoryn_redis_stream_lag_ms"
WEBSOCKET_CLIENTS = "audoryn_websocket_clients"
EVENT_DELIVERY_LATENCY = "audoryn_event_delivery_latency_ms"
EVENTS_DROPPED = "audoryn_events_dropped"
PROVIDER_HEALTH = "audoryn_provider_health"


@dataclass(frozen=True, slots=True)
class MetricPoint:
    name: str
    value: float
    labels: dict[str, str]


class MetricsSink(Protocol):
    async def record(self, point: MetricPoint) -> None: ...


class LoggingMetricsSink:
    async def record(self, point: MetricPoint) -> None:
        structured_event(
            logging.getLogger("audoryn.metrics"),
            "metric",
            fields={
                "metric": point.name,
                "value": point.value,
                "labels": point.labels,
            },
        )


class ExecutionMetrics:
    def __init__(self, sink: MetricsSink | None = None) -> None:
        self._sink = sink or LoggingMetricsSink()

    async def execution(
        self,
        *,
        provider: str,
        capability: str,
        adapter: str,
        correlation_id: str,
        latency_ms: int,
        verified: bool,
        retries: int,
        fallback_used: bool,
    ) -> None:
        labels = {
            "provider": provider,
            "capability": capability,
            "adapter": adapter,
            "correlation_id": correlation_id,
        }
        await self._sink.record(
            MetricPoint(PROVIDER_EXECUTION_LATENCY, float(latency_ms), labels)
        )
        await self._sink.record(
            MetricPoint(CAPABILITY_SUCCESS, 1.0 if verified else 0.0, labels)
        )
        if not verified:
            await self._sink.record(MetricPoint(VERIFICATION_FAILURE, 1.0, labels))
        if retries:
            await self._sink.record(MetricPoint(EXECUTION_RETRY, float(retries), labels))
        if fallback_used:
            await self._sink.record(MetricPoint(EXECUTION_FALLBACK, 1.0, labels))

    async def realtime_delivery(
        self,
        *,
        event_type: str,
        correlation_id: str,
        latency_ms: float,
        dropped: bool = False,
    ) -> None:
        labels = {
            "event_type": event_type,
            "correlation_id": correlation_id,
        }
        await self._sink.record(
            MetricPoint(EVENT_DELIVERY_LATENCY, latency_ms, labels)
        )
        if dropped:
            await self._sink.record(MetricPoint(EVENTS_DROPPED, 1.0, labels))
