from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, get_args
from uuid import UUID, uuid4

RealtimeEventType = Literal[
    "run.created",
    "run.started",
    "run.progress",
    "run.step.started",
    "run.step.completed",
    "run.waiting_approval",
    "run.failed",
    "run.completed",
    "result.created",
    "result.updated",
    "worker.status.changed",
    "action.proposed",
    "action.approved",
    "action.blocked",
    "action.executed",
    "approval.created",
    "approval.decided",
    "incident.created",
    "incident.updated",
    "integration.health.changed",
    "browser.session.created",
    "browser.navigation.started",
    "browser.navigation.completed",
    "browser.observation.created",
    "browser.action.started",
    "browser.action.completed",
    "browser.verification.failed",
    "browser.session.closed",
    "conversation.thread.created",
    "conversation.message.created",
    "conversation.response.created",
    "conversation.command.accepted",
    "conversation.command.completed",
    "conversation.clarification_required",
    "conversation.approval_required",
    "conversation.integration_required",
]

REALTIME_EVENT_TYPES: frozenset[str] = frozenset(str(item) for item in get_args(RealtimeEventType))


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    event_type: RealtimeEventType
    organization_id: UUID
    payload: dict[str, object]
    event_id: str = field(default_factory=lambda: str(uuid4()))
    worker_id: UUID | None = None
    job_id: UUID | None = None
    run_id: UUID | None = None
    resource_id: str | None = None
    correlation_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.event_type not in REALTIME_EVENT_TYPES:
            raise ValueError(f"Unsupported realtime event type: {self.event_type}")

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "organization_id": str(self.organization_id),
            "worker_id": str(self.worker_id) if self.worker_id else None,
            "job_id": str(self.job_id) if self.job_id else None,
            "run_id": str(self.run_id) if self.run_id else None,
            "resource_id": self.resource_id,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }
