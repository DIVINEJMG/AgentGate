from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Header, HTTPException, status
from sqlalchemy import select

from app.domain.identity.agents import AgentDomainError
from app.domain.identity.permissions import permissions_for_role
from app.domain.identity.principals import AgentPrincipal, HumanPrincipal
from app.infrastructure.auth.agent_credentials import AgentCredentialAuthenticator
from app.infrastructure.auth.service import AuthenticatedUser, LocalAuthenticationService
from app.infrastructure.auth.session_store import RedisSessionStore
from app.infrastructure.database.models import OrganizationMembership
from app.infrastructure.database.session import session_factory


def bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required.")
    return token


async def authenticated_user(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedUser:
    token = bearer_token(authorization)
    async with session_factory() as session:
        service = LocalAuthenticationService(session, RedisSessionStore.from_settings())
        user = await service.current_user(token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session.")
    return user


async def organization_principal(
    organization_id: UUID,
    authorization: Annotated[str | None, Header()] = None,
) -> HumanPrincipal:
    token = bearer_token(authorization)
    async with session_factory() as session:
        service = LocalAuthenticationService(session, RedisSessionStore.from_settings())
        user = await service.current_user(token)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired session.",
            )
        membership = await session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user.id,
            )
        )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-tenant access denied.",
        )
    return HumanPrincipal(
        user_id=user.id,
        organization_id=organization_id,
        membership_id=membership.id,
        role=membership.role,
        permissions=permissions_for_role(membership.role),
    )


def agent_secret(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Agent "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent credential required.",
        )
    secret = authorization.removeprefix("Agent ").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent credential required.",
        )
    return secret


async def authenticated_agent(
    organization_id: UUID,
    agent_id: UUID,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentPrincipal:
    secret = agent_secret(authorization)
    async with session_factory() as session:
        try:
            return await AgentCredentialAuthenticator(session).authenticate(
                organization_id=organization_id,
                agent_id=agent_id,
                secret=secret,
            )
        except AgentDomainError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail=str(error),
            ) from error
