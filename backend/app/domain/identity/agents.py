from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


AGENT_AUTHENTICATE_SCOPE = "agent.authenticate"
AGENT_STATUSES = frozenset({"active", "suspended", "disabled"})


class AgentDomainError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class AgentCredentialView:
    status: str
    fingerprint: str
    version: int
    scopes: tuple[str, ...]
    expires_at: datetime | None
    last_used_at: datetime | None


@dataclass(frozen=True, slots=True)
class AgentIdentityView:
    id: UUID
    organization_id: UUID
    name: str
    description: str
    owner_user_id: UUID
    status: str
    credential: AgentCredentialView
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AgentCredentialReveal:
    secret: str
    fingerprint: str
    version: int
    scopes: tuple[str, ...]
    created_at: datetime
    expires_at: datetime | None


class AgentRepository(typing.Protocol):
    async def membership_role(self, user_id: UUID, organization_id: UUID) -> str | None: ...

    async def list_agents(self, organization_id: UUID) -> list[AgentIdentityView]: ...

    async def get_agent(
        self,
        organization_id: UUID,
        agent_id: UUID,
    ) -> AgentIdentityView | None: ...

    async def create_agent(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        name: str,
        description: str,
        credential_hash: str,
        credential_fingerprint: str,
        scopes: tuple[str, ...],
        created_at: datetime,
    ) -> AgentIdentityView: ...

    async def set_status(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        agent_id: UUID,
        status: str,
        changed_at: datetime,
    ) -> AgentIdentityView: ...

    async def rotate_credential(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        agent_id: UUID,
        credential_hash: str,
        credential_fingerprint: str,
        scopes: tuple[str, ...],
        version: int,
        created_at: datetime,
    ) -> AgentIdentityView: ...

    async def revoke_credential(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        agent_id: UUID,
        changed_at: datetime,
    ) -> AgentIdentityView: ...
