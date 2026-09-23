from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.agents import (
    AgentCredentialView,
    AgentDomainError,
    AgentIdentityView,
)
from app.infrastructure.database.models import (
    AgentCredential,
    AgentIdentity,
    AuditEvent,
    OrganizationMembership,
)


class SQLAlchemyAgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def membership_role(self, user_id: UUID, organization_id: UUID) -> str | None:
        return await self._session.scalar(
            select(OrganizationMembership.role).where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.organization_id == organization_id,
            )
        )

    async def _latest_credential(self, agent_id: UUID) -> AgentCredential | None:
        return await self._session.scalar(
            select(AgentCredential)
            .where(AgentCredential.agent_id == agent_id)
            .order_by(desc(AgentCredential.version))
            .limit(1)
        )

    async def _view(self, agent: AgentIdentity) -> AgentIdentityView:
        credential = await self._latest_credential(agent.id)
        if credential is None:
            raise AgentDomainError("Agent credential is unavailable.", 500)
        return AgentIdentityView(
            id=agent.id,
            organization_id=agent.organization_id,
            name=agent.name,
            description=agent.description,
            owner_user_id=agent.created_by,
            status=agent.status,
            credential=AgentCredentialView(
                status=credential.status,
                fingerprint=credential.fingerprint,
                version=credential.version,
                scopes=tuple(credential.scopes),
                expires_at=credential.expires_at,
                last_used_at=credential.last_used_at,
            ),
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )

    async def list_agents(self, organization_id: UUID) -> list[AgentIdentityView]:
        agents = list(
            (
                await self._session.scalars(
                    select(AgentIdentity)
                    .where(AgentIdentity.organization_id == organization_id)
                    .order_by(desc(AgentIdentity.created_at), desc(AgentIdentity.id))
                )
            ).all()
        )
        return [await self._view(agent) for agent in agents]

    async def get_agent(
        self,
        organization_id: UUID,
        agent_id: UUID,
    ) -> AgentIdentityView | None:
        agent = await self._session.scalar(
            select(AgentIdentity).where(
                AgentIdentity.organization_id == organization_id,
                AgentIdentity.id == agent_id,
            )
        )
        return None if agent is None else await self._view(agent)

    def _audit(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        agent_id: UUID,
        agent_name: str,
        event_type: str,
        category: str,
        severity: str,
        outcome: str,
        summary: str,
        metadata: dict[str, object],
    ) -> None:
        self._session.add(
            AuditEvent(
                organization_id=organization_id,
                event_type=event_type,
                category=category,
                severity=severity,
                correlation_id=None,
                actor={"type": "human", "id": str(user_id), "label": None},
                resource={"type": "agent", "id": str(agent_id), "name": agent_name},
                payload={"outcome": outcome, "summary": summary, "metadata": metadata},
            )
        )

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
    ) -> AgentIdentityView:
        agent = AgentIdentity(
            organization_id=organization_id,
            name=name,
            description=description,
            status="active",
            created_by=user_id,
            created_at=created_at,
            updated_at=created_at,
        )
        self._session.add(agent)
        await self._session.flush()
        credential = AgentCredential(
            agent_id=agent.id,
            fingerprint=credential_fingerprint,
            secret_hash=credential_hash,
            status="active",
            version=1,
            scopes=list(scopes),
            expires_at=None,
            last_used_at=None,
            revoked_at=None,
            created_at=created_at,
            updated_at=created_at,
        )
        self._session.add(credential)
        self._audit(
            organization_id=organization_id,
            user_id=user_id,
            agent_id=agent.id,
            agent_name=name,
            event_type="agent.registered",
            category="identity",
            severity="info",
            outcome="created",
            summary=f"Agent {name} registered.",
            metadata={"credentialFingerprint": credential_fingerprint},
        )
        await self._session.commit()
        return await self._view(agent)

    async def _agent_or_error(
        self,
        organization_id: UUID,
        agent_id: UUID,
    ) -> AgentIdentity:
        agent = await self._session.scalar(
            select(AgentIdentity).where(
                AgentIdentity.organization_id == organization_id,
                AgentIdentity.id == agent_id,
            )
        )
        if agent is None:
            raise AgentDomainError("Agent identity not found.", 404)
        return agent

    async def set_status(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        agent_id: UUID,
        status: str,
        changed_at: datetime,
    ) -> AgentIdentityView:
        agent = await self._agent_or_error(organization_id, agent_id)
        previous = agent.status
        credential = await self._latest_credential(agent.id)
        if credential is None:
            raise AgentDomainError("Agent credential is unavailable.", 500)
        agent.status = status
        agent.updated_at = changed_at
        if status == "disabled":
            credential.status = "revoked"
            credential.revoked_at = changed_at
            credential.updated_at = changed_at
        self._audit(
            organization_id=organization_id,
            user_id=user_id,
            agent_id=agent.id,
            agent_name=agent.name,
            event_type="agent.lifecycle.changed",
            category="identity",
            severity="warning" if status == "disabled" else "info",
            outcome=status,
            summary=f"Agent lifecycle changed to {status}.",
            metadata={"previousStatus": previous},
        )
        await self._session.commit()
        return await self._view(agent)

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
    ) -> AgentIdentityView:
        agent = await self._agent_or_error(organization_id, agent_id)
        current = await self._latest_credential(agent.id)
        if current is None:
            raise AgentDomainError("Agent credential is unavailable.", 500)
        current.status = "revoked"
        current.revoked_at = created_at
        current.updated_at = created_at
        self._session.add(
            AgentCredential(
                agent_id=agent.id,
                fingerprint=credential_fingerprint,
                secret_hash=credential_hash,
                status="active",
                version=version,
                scopes=list(scopes),
                expires_at=None,
                last_used_at=None,
                revoked_at=None,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        agent.updated_at = created_at
        self._audit(
            organization_id=organization_id,
            user_id=user_id,
            agent_id=agent.id,
            agent_name=agent.name,
            event_type="agent.credential.rotated",
            category="security",
            severity="warning",
            outcome="rotated",
            summary="Agent credential rotated.",
            metadata={"version": version, "fingerprint": credential_fingerprint},
        )
        await self._session.commit()
        return await self._view(agent)

    async def revoke_credential(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        agent_id: UUID,
        changed_at: datetime,
    ) -> AgentIdentityView:
        agent = await self._agent_or_error(organization_id, agent_id)
        credential = await self._latest_credential(agent.id)
        if credential is None:
            raise AgentDomainError("Agent credential is unavailable.", 500)
        credential.status = "revoked"
        credential.revoked_at = changed_at
        credential.updated_at = changed_at
        if agent.status != "disabled":
            agent.status = "suspended"
        agent.updated_at = changed_at
        self._audit(
            organization_id=organization_id,
            user_id=user_id,
            agent_id=agent.id,
            agent_name=agent.name,
            event_type="agent.credential.revoked",
            category="security",
            severity="warning",
            outcome="revoked",
            summary="Agent credential revoked; identity suspended.",
            metadata={"fingerprint": credential.fingerprint},
        )
        await self._session.commit()
        return await self._view(agent)
