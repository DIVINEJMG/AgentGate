from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.errors import AuthenticationError, AuthorizationError
from app.domain.identity.permissions import permissions_for_role
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.auth.service import LocalAuthenticationService
from app.infrastructure.auth.session_store import RedisSessionStore
from app.infrastructure.database.models import OrganizationMembership


class RedisSessionIdentityProvider:
    """Human identity adapter backed by Neon identities and Upstash sessions."""

    def __init__(
        self,
        session: AsyncSession,
        sessions: RedisSessionStore,
        *,
        organization_id: UUID,
    ) -> None:
        self._session = session
        self._sessions = sessions
        self._organization_id = organization_id

    async def authenticate(self, token: str) -> HumanPrincipal:
        user = await LocalAuthenticationService(
            self._session,
            self._sessions,
        ).current_user(token)
        if user is None:
            raise AuthenticationError("Invalid or expired session.")

        membership = await self._session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == self._organization_id,
                OrganizationMembership.user_id == user.id,
            )
        )
        if membership is None:
            raise AuthorizationError("Cross-tenant access denied.")

        return HumanPrincipal(
            user_id=user.id,
            organization_id=self._organization_id,
            membership_id=membership.id,
            role=membership.role,
            permissions=permissions_for_role(membership.role),
        )
