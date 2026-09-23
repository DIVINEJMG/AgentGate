from __future__ import annotations

import hashlib
import hmac
import re
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.agents import AgentDomainError
from app.domain.identity.principals import AgentPrincipal
from app.infrastructure.database.models import (
    AgentCredential,
    AgentIdentity,
    CapabilityProfile,
    RiskEvent,
)

_AGENT_SECRET = re.compile(r"^agt_sk_[A-Za-z0-9_-]{20,120}$")


class AgentCredentialAuthenticator:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def authenticate(
        self,
        *,
        organization_id: UUID,
        agent_id: UUID,
        secret: str,
    ) -> AgentPrincipal:
        normalized = secret.strip()
        if not _AGENT_SECRET.fullmatch(normalized):
            raise AgentDomainError("Invalid agent credential.", 401)

        agent = await self._session.scalar(
            select(AgentIdentity).where(
                AgentIdentity.id == agent_id,
                AgentIdentity.organization_id == organization_id,
            )
        )
        if agent is None:
            raise AgentDomainError("Agent identity not found.", 404)
        if agent.status != "active":
            raise AgentDomainError("Agent identity is not permitted to execute actions.", 403)

        credential = await self._session.scalar(
            select(AgentCredential)
            .where(AgentCredential.agent_id == agent_id)
            .order_by(desc(AgentCredential.version))
            .limit(1)
        )
        if credential is None or credential.status != "active":
            raise AgentDomainError("Agent credential is unavailable or revoked.", 401)

        supplied = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(supplied, credential.secret_hash):
            raise AgentDomainError("Invalid agent credential.", 401)

        capabilities = frozenset(
            (
                await self._session.scalars(
                    select(CapabilityProfile.scope).where(
                        CapabilityProfile.organization_id == organization_id,
                        CapabilityProfile.agent_id == agent_id,
                        CapabilityProfile.active.is_(True),
                    )
                )
            ).all()
        )
        risk = await self._session.scalar(
            select(RiskEvent.effective_risk)
            .where(
                RiskEvent.organization_id == organization_id,
                RiskEvent.agent_id == agent_id,
            )
            .order_by(desc(RiskEvent.created_at))
            .limit(1)
        )

        return AgentPrincipal(
            agent_id=agent_id,
            organization_id=organization_id,
            credential_fingerprint=credential.fingerprint,
            capabilities=capabilities,
            risk_level=risk or "low",
            policy_context=(),
        )
