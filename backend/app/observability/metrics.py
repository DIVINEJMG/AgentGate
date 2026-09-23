from dataclasses import dataclass
from typing import Protocol


RUN_DURATION = "audoryn_run_duration"
ACTION_LATENCY = "audoryn_action_latency"
POLICY_DENIALS = "audoryn_policy_denials"
APPROVAL_WAIT = "audoryn_approval_wait"
RUNTIME_RETRY = "audoryn_runtime_retry"
PROVIDER_FAILURES = "audoryn_provider_failures"


@dataclass(frozen=True, slots=True)
class MetricPoint:
    name: str
    value: float
    labels: dict[str, str]


class MetricsSink(Protocol):
    async def record(self, point: MetricPoint) -> None: ...


class LoggingMetricsSink:
    async def record(self, point: MetricPoint) -> None:
        from app.observability.context import structured_event
        import logging

        structured_event(
            logging.getLogger("audoryn.metrics"),
            "metric",
            fields={
                "metric": point.name,
                "value": point.value,
                "labels": point.labels,
            },
        )
