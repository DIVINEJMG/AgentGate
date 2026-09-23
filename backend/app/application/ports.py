from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from app.domain.canonical import (
    ActionProposal,
    AgentIdentity,
    Approval,
    Artifact,
    AuditEvent,
    Capability,
    Incident,
    Integration,
    Job,
    Membership,
    Memory,
    Organization,
    Policy,
    Result,
    RiskAssessment,
    WorkItem,
    Worker,
)
from app.domain.identity.principals import HumanPrincipal


class OrganizationRepository(Protocol):
    async def get(self, organization_id: UUID) -> Organization | None: ...
    async def list_for_user(self, user_id: UUID) -> list[Organization]: ...
    async def save(self, organization: Organization) -> None: ...


class MembershipRepository(Protocol):
    async def get(self, organization_id: UUID, user_id: UUID) -> Membership | None: ...
    async def list_for_user(self, user_id: UUID) -> list[Membership]: ...


class AgentRepository(Protocol):
    async def get(self, organization_id: UUID, agent_id: UUID) -> AgentIdentity | None: ...
    async def list(self, organization_id: UUID) -> list[AgentIdentity]: ...


class WorkerRepository(Protocol):
    async def get(self, organization_id: UUID, worker_id: UUID) -> Worker | None: ...
    async def list(self, organization_id: UUID) -> list[Worker]: ...


class JobRepository(Protocol):
    async def get(self, organization_id: UUID, job_id: UUID) -> Job | None: ...
    async def list(self, organization_id: UUID) -> list[Job]: ...


class WorkItemRepository(Protocol):
    async def get(self, organization_id: UUID, work_item_id: UUID) -> WorkItem | None: ...
    async def enqueue(self, item: WorkItem) -> None: ...


class IntegrationRepository(Protocol):
    async def get(self, organization_id: UUID, integration_id: UUID) -> Integration | None: ...
    async def list(self, organization_id: UUID) -> list[Integration]: ...


class CapabilityRepository(Protocol):
    async def list_for_agent(self, organization_id: UUID, agent_id: UUID) -> list[Capability]: ...


class PolicyRepository(Protocol):
    async def list(self, organization_id: UUID) -> list[Policy]: ...


class RiskRepository(Protocol):
    async def assess(self, proposal: ActionProposal) -> RiskAssessment: ...


class ApprovalRepository(Protocol):
    async def get(self, organization_id: UUID, approval_id: UUID) -> Approval | None: ...


class IncidentRepository(Protocol):
    async def list(self, organization_id: UUID) -> list[Incident]: ...


class ActionRepository(Protocol):
    async def record(self, proposal: ActionProposal, status: str) -> UUID: ...


class AuditRepository(Protocol):
    async def append(self, event: AuditEvent) -> None: ...


class MemoryRepository(Protocol):
    async def list(self, organization_id: UUID) -> list[Memory]: ...


class ResultRepository(Protocol):
    async def get(self, organization_id: UUID, result_id: UUID) -> Result | None: ...
    async def list(self, organization_id: UUID) -> list[Result]: ...


class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes, *, media_type: str) -> Artifact: ...
    async def signed_url(self, key: str, *, ttl_seconds: int) -> str: ...


class CacheStore(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None: ...
    async def delete(self, key: str) -> None: ...


class QueueBroker(Protocol):
    async def publish(
        self,
        destination: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
    ) -> str: ...


class SecretVault(Protocol):
    async def read(self, secret_id: str) -> str: ...


class IdentityProvider(Protocol):
    async def authenticate(self, token: str) -> HumanPrincipal: ...


class IntegrationAdapter(Protocol):
    async def execute(self, proposal: ActionProposal) -> Mapping[str, object]: ...
