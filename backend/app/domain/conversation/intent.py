from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

WorkerCommandFamily = Literal[
    "worker.create",
    "worker.update",
    "worker.status",
    "worker.pause",
    "worker.resume",
    "worker.delete",
    "job.create",
    "job.update",
    "job.stop",
    "job.retry",
    "job.status",
    "schedule.create",
    "schedule.update",
    "schedule.pause",
    "schedule.resume",
    "policy.add",
    "policy.update",
    "policy.remove",
    "instruction.add",
    "instruction.remove",
    "result.query",
    "failure.explain",
    "work.execute_now",
    "integration.require",
    "attachment.analyze",
    "conversation.answer",
]


class EntityReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    name: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def normalize(self) -> EntityReference:
        if self.name is not None:
            self.name = self.name.strip() or None
        return self


class WorkerCommandIntent(BaseModel):
    """Schema-validated interpretation of one human conversational turn.

    The model may propose these values, but this object carries no execution authority.
    """

    model_config = ConfigDict(extra="forbid")

    family: WorkerCommandFamily
    worker: EntityReference | None = None
    job: EntityReference | None = None
    schedule: EntityReference | None = None
    policy: EntityReference | None = None
    work_item: EntityReference | None = None
    run: EntityReference | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    response_hint: str = Field(default="", max_length=600)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class CommandReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    id: str
    name: str | None = None


class CommandReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "completed",
        "accepted",
        "waiting_confirmation",
        "clarification_required",
        "waiting_integration",
        "unavailable",
    ]
    message: str
    references: list[CommandReference] = Field(default_factory=list)
    result_references: list[CommandReference] = Field(default_factory=list)
    command_id: UUID | None = None
