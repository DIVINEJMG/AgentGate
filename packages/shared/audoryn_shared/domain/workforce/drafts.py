from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ScheduleKind = Literal["manual", "daily", "weekly", "interval", "once", "event"]
ApprovalBoundaryKind = Literal[
    "submit_requires_approval",
    "external_send_requires_approval",
    "write_requires_approval",
    "allowed_origins_only",
]


class CapabilityNeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=64)
    need: str = Field(min_length=1, max_length=500)
    actions: list[str] = Field(default_factory=list, max_length=20)
    # For an action that cannot be undone (submit, upload, publish, merge, delete, send): the exact
    # words of the request asking for it. Without them, such actions need approval.
    request_excerpt: str | None = Field(default=None, max_length=300)


class ScheduleDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ScheduleKind = "manual"
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    local_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    weekdays: list[str] = Field(default_factory=list, max_length=7)
    interval_minutes: int | None = Field(default=None, ge=60, le=10080)
    once_at: datetime | None = None
    event_key: str | None = Field(default=None, max_length=180)
    human_readable: str = Field(default="", max_length=300)
    stop_after_next_run: bool = False

    @field_validator("local_time", mode="before")
    @classmethod
    def _clock(cls, value: object) -> object:
        """Read common ways of writing a time ("9:00", "9am", "9:30 pm", "21.15") as HH:MM; anything
        else is left for validation to reject."""
        if not isinstance(value, str):
            return value
        match = re.fullmatch(r"\s*(\d{1,2})(?:[:.](\d{2}))?\s*([ap])?\.?\s*m?\.?\s*", value.lower())
        if not match:
            return value
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        if match.group(3) == "p" and hour < 12:
            hour += 12
        if match.group(3) == "a" and hour == 12:
            hour = 0
        if match.group(3) is None and match.group(2) is None:
            return value  # a bare number is not a clear time
        return f"{hour:02d}:{minute:02d}" if hour < 24 and minute < 60 else value


class ApprovalBoundaryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ApprovalBoundaryKind
    provider: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="", max_length=500)
    allowed_origins: list[str] = Field(default_factory=list, max_length=50)
    # The human's own words asking to be consulted, quoted from the request.
    request_excerpt: str | None = Field(default=None, max_length=300)


class IntegrationRequirementDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=500)


class SiteTargetDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mention: str = Field(min_length=2, max_length=240)
    candidate_url: str | None = Field(default=None, max_length=1000)


class JobDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=160)
    objective: str = Field(min_length=1, max_length=3000)
    instructions: str = Field(default="", max_length=5000)
    completion_criteria: list[str] = Field(default_factory=list, max_length=30)
    capability_needs: list[CapabilityNeed] = Field(default_factory=list, max_length=30)
    schedule: ScheduleDraft = Field(default_factory=ScheduleDraft)
    approval_boundaries: list[ApprovalBoundaryDraft] = Field(default_factory=list, max_length=20)
    integration_requirements: list[IntegrationRequirementDraft] = Field(
        default_factory=list, max_length=20
    )
    site_targets: list[SiteTargetDraft] = Field(default_factory=list, max_length=20)
    public_web_research: bool = False
    start_when_ready: bool = True


class WorkerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggested_name: str = Field(min_length=2, max_length=120)
    role: str = Field(min_length=2, max_length=120)
    department: str = Field(default="", max_length=120)
    supervisor_name: str | None = Field(default=None, max_length=160)
    charter: str = Field(min_length=1, max_length=4000)
    responsibilities: list[str] = Field(default_factory=list, max_length=40)
    standing_instructions: list[str] = Field(default_factory=list, max_length=30)
    public_web_request_excerpt: str | None = Field(default=None, max_length=240)
    initial_jobs: list[JobDraft] = Field(min_length=1, max_length=20)

    @field_validator("responsibilities", "standing_instructions")
    @classmethod
    def _clean_strings(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]
