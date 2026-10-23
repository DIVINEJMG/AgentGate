from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Organization:
    id: UUID
    name: str
    created_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Membership:
    id: UUID
    organization_id: UUID
    user_id: UUID
    role: str


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    id: UUID
    organization_id: UUID
    name: str
    status: str
    owner_user_id: UUID


@dataclass(frozen=True, slots=True)
class AgentCredential:
    id: UUID
    agent_id: UUID
    fingerprint: str
    status: str
    version: int
    scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Worker:
    id: UUID
    organization_id: UUID
    agent_identity_id: UUID
    name: str
    department: str
    status: str


@dataclass(frozen=True, slots=True)
class Job:
    id: UUID
    organization_id: UUID
    worker_id: UUID
    name: str
    status: str
    current_revision: int


@dataclass(frozen=True, slots=True)
class JobRevision:
    id: UUID
    job_id: UUID
    revision: int
    definition: dict[str, object]


@dataclass(frozen=True, slots=True)
class WorkItem:
    id: UUID
    organization_id: UUID
    job_id: UUID
    job_revision_id: UUID
    status: str
    correlation_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class Integration:
    id: UUID
    organization_id: UUID
    provider: str
    display_name: str
    status: str


@dataclass(frozen=True, slots=True)
class Capability:
    id: UUID
    organization_id: UUID
    agent_id: UUID
    scope: str
    active: bool


@dataclass(frozen=True, slots=True)
class Policy:
    id: UUID
    organization_id: UUID
    name: str
    status: str
    current_revision: int


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    outcome: str
    reason: str
    policy_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    organization_id: UUID
    agent_id: UUID
    effective_risk: str
    factors: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionProposal:
    organization_id: UUID
    agent_id: UUID
    provider: str
    operation: str
    scope: str
    resource_id: str
    payload: dict[str, object]
    correlation_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ActionDecision:
    outcome: str
    reason: str


@dataclass(frozen=True, slots=True)
class Approval:
    id: UUID
    organization_id: UUID
    action_id: UUID
    status: str
    decided_by: UUID | None = None


@dataclass(frozen=True, slots=True)
class Incident:
    id: UUID
    organization_id: UUID
    status: str
    severity: str
    summary: str


@dataclass(frozen=True, slots=True)
class Run:
    id: UUID
    organization_id: UUID
    work_item_id: UUID
    status: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class RunStep:
    id: UUID
    run_id: UUID
    step_index: int
    kind: str
    status: str


@dataclass(frozen=True, slots=True)
class Memory:
    id: UUID
    organization_id: UUID
    scope: str
    owner_id: str
    title: str
    content: str


@dataclass(frozen=True, slots=True)
class Artifact:
    id: UUID
    organization_id: UUID
    run_id: UUID | None
    storage_key: str
    media_type: str


@dataclass(frozen=True, slots=True)
class Result:
    id: UUID
    organization_id: UUID
    worker_id: UUID
    job_id: UUID
    latest_version: int
    status: str
    title: str


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: UUID
    organization_id: UUID
    event_type: str
    category: str
    severity: str
    correlation_id: str | None
