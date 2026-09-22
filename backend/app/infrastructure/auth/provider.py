from sqlalchemy import select

from app.domain.identity.errors import AuthenticationError
from app.domain.identity.permissions import permissions_for_role
from app.domain.identity.principals import HumanPrincipal
from app.domain.identity.providers import IdentityProvider
from app.infrastructure.auth.session_store import RedisSessionStore
from app.infrastructure.database.models import OrganizationMembership
from app.infrastructure.database.session import session_factory


class SessionIdentityProvider(IdentityProvider):
    """Validates opaque bearer sessions from Upstash Redis and resolves membership in Neon."""

    async def authenticate(self, token: str) -> HumanPrincipal:
        sessions = RedisSessionStore.from_settings()
        record = await sessions.get(token)
        if record is None:
            raise AuthenticationError("Invalid or expired session.")

        async with session_factory() as session:
            membership = await session.scalar(
                select(OrganizationMembership)
                .where(OrganizationMembership.user_id == record.user_id)
                .order_by(OrganizationMembership.created_at, OrganizationMembership.id)
            )
        if membership is None:
            raise AuthenticationError("Authenticated user does not have an organization membership.")

        return HumanPrincipal(
            user_id=record.user_id,
            organization_id=membership.organization_id,
            membership_id=membership.id,
            role=membership.role,
            permissions=permissions_for_role(membership.role),
        )


class UnconfiguredIdentityProvider(IdentityProvider):
    """Legacy fail-closed provider retained only for explicit tests and migrations."""

    async def authenticate(self, token: str) -> HumanPrincipal:
        raise AuthenticationError("Human identity provider is not configured.")
