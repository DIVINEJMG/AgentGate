from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

FailureCategory = Literal[
    "task_failure",
    "provider_model_outage",
    "provider_response_invalid",
    "missing_integration",
    "approval_wait",
    "policy_denial",
    "browser_site_failure",
    "authentication_expiry",
    "internal_platform_failure",
]

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
    "integration.execute",
    "attachment.analyze",
    "web.research",
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


class ChatReply(BaseModel):
    """An ordinary reply written in the same call that chose conversation.answer. The backend checks
    it with the same rules as a separate reply before using it."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=12000)
    factIds: list[str] = Field(default_factory=list, max_length=5)


# A conversation request still waiting on the person (a question, an account connection, a
# permission) or on the AI.
PENDING_COMMAND_STATUSES = ("waiting_integration", "clarification_required", "waiting_ai", "policy_denied")

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
    # Only with family conversation.answer: the reply itself, saving a second AI call.
    reply: ChatReply | None = None
    # When the request is unclear or misses an essential detail: one question for the human.
    # Nothing runs until they answer; the answer then continues this request.
    clarification_question: str | None = Field(default=None, max_length=400)
    # How the human wants the requested work to start, as the model understood it: now, at set
    # times, each time something happens (an event), or not stated. Checked against the family.
    requested_start: Literal["now", "on_schedule", "on_event", "unspecified"] = "unspecified"


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
    failure_category: FailureCategory | None = None
    action_hints: list[dict[str, str]] = Field(default_factory=list)
