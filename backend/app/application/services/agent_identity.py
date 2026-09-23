from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from uuid import UUID

from app.domain.identity.agents import (
    AGENT_AUTHENTICATE_SCOPE,
    AGENT_STATUSES,
    AgentCredentialReveal,
    AgentDomainError,
    AgentIdentityView,
    AgentRepository,
)
from app.domain.identity.permissions import permissions_for_role


class AgentIdentityService:
    def __init__(self, repository: AgentRepository) -> None:
        self._repository = repository

    async def _require_permission(
        self,
        user_id: UUID,
        organization_id: UUID,
        permission: str,
    ) -> None:
        role = await self._repository.membership_role(user_id, organization_id)
        if role is None:
            raise AgentDomainError("Organization not found or access denied.", 404)
        if permission not in permissions_for_role(role):
            raise AgentDomainError(
                "Forbidden: your organization role does not grant this agent permission.",
                403,
            )

    @staticmethod
    def _credential() -> tuple[str, str, str]:
        secret = f"agt_sk_{secrets.token_urlsafe(32)}"
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        fingerprint = f"sha256:{digest[:12]}"
        return secret, digest, fingerprint

    async def list_agents(
        self,
        user_id: UUID,
        organization_id: UUID,
    ) -> list[AgentIdentityView]:
        await self._require_permission(user_id, organization_id, "agents.read")
        return await self._repository.list_agents(organization_id)

    async def register_agent(
        self,
        user_id: UUID,
        organization_id: UUID,
        raw_name: str,
        raw_description: str,
    ) -> tuple[AgentIdentityView, AgentCredentialReveal]:
        await self._require_permission(user_id, organization_id, "agents.manage")
        name = raw_name.strip()
        description = raw_description.strip()
        if len(name) < 2 or len(name) > 80:
            raise AgentDomainError("Agent name must be between 2 and 80 characters.")
        if len(description) > 240:
            raise AgentDomainError("Agent description must be 240 characters or fewer.")

        secret, secret_hash, fingerprint = self._credential()
        scopes = (AGENT_AUTHENTICATE_SCOPE,)
        created_at = datetime.now(UTC)
        agent = await self._repository.create_agent(
            user_id=user_id,
            organization_id=organization_id,
            name=name,
            description=description,
            credential_hash=secret_hash,
            credential_fingerprint=fingerprint,
            scopes=scopes,
            created_at=created_at,
        )
        return agent, AgentCredentialReveal(
            secret=secret,
            fingerprint=fingerprint,
            version=1,
            scopes=scopes,
            created_at=created_at,
            expires_at=None,
        )

    async def set_lifecycle(
        self,
        user_id: UUID,
        organization_id: UUID,
        agent_id: UUID,
        status: str,
    ) -> AgentIdentityView:
        await self._require_permission(user_id, organization_id, "agents.manage")
        if status not in AGENT_STATUSES:
            raise AgentDomainError("Agent status must be active, suspended, or disabled.")
        current = await self._repository.get_agent(organization_id, agent_id)
        if current is None:
            raise AgentDomainError("Agent identity not found.", 404)
        if current.status == "disabled" and status != "disabled":
            raise AgentDomainError("Disabled agent identities cannot be reactivated.", 409)
        if status == "active" and current.credential.status != "active":
            raise AgentDomainError("Rotate the agent credential before activating this identity.", 409)
        return await self._repository.set_status(
            user_id=user_id,
            organization_id=organization_id,
            agent_id=agent_id,
            status=status,
            changed_at=datetime.now(UTC),
        )

    async def rotate_credential(
        self,
        user_id: UUID,
        organization_id: UUID,
        agent_id: UUID,
    ) -> tuple[AgentIdentityView, AgentCredentialReveal]:
        await self._require_permission(user_id, organization_id, "agents.manage")
        current = await self._repository.get_agent(organization_id, agent_id)
        if current is None:
            raise AgentDomainError("Agent identity not found.", 404)
        if current.status == "disabled":
            raise AgentDomainError("Disabled agent identities cannot receive new credentials.", 409)

        secret, secret_hash, fingerprint = self._credential()
        created_at = datetime.now(UTC)
        scopes = current.credential.scopes or (AGENT_AUTHENTICATE_SCOPE,)
        version = current.credential.version + 1
        agent = await self._repository.rotate_credential(
            user_id=user_id,
            organization_id=organization_id,
            agent_id=agent_id,
            credential_hash=secret_hash,
            credential_fingerprint=fingerprint,
            scopes=scopes,
            version=version,
            created_at=created_at,
        )
        return agent, AgentCredentialReveal(
            secret=secret,
            fingerprint=fingerprint,
            version=version,
            scopes=scopes,
            created_at=created_at,
            expires_at=None,
        )

    async def revoke_credential(
        self,
        user_id: UUID,
        organization_id: UUID,
        agent_id: UUID,
    ) -> AgentIdentityView:
        await self._require_permission(user_id, organization_id, "agents.manage")
        current = await self._repository.get_agent(organization_id, agent_id)
        if current is None:
            raise AgentDomainError("Agent identity not found.", 404)
        if current.credential.status == "revoked":
            return current
        return await self._repository.revoke_credential(
            user_id=user_id,
            organization_id=organization_id,
            agent_id=agent_id,
            changed_at=datetime.now(UTC),
        )
