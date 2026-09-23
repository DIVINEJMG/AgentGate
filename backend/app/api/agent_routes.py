from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import authenticated_user
from app.application.services.agent_identity import AgentIdentityService
from app.domain.identity.agents import (
    AgentCredentialReveal,
    AgentDomainError,
    AgentIdentityView,
)
from app.infrastructure.auth.service import AuthenticatedUser
from app.infrastructure.database.agent_repository import SQLAlchemyAgentRepository
from app.infrastructure.database.session import database_session


class AgentCreateRequest(BaseModel):
    name: str
    description: str = ""


class AgentLifecycleRequest(BaseModel):
    status: str


def _iso(value) -> str | None:
    return None if value is None else value.isoformat()


def _credential(agent: AgentIdentityView) -> dict[str, object]:
    return {
        "status": agent.credential.status,
        "fingerprint": agent.credential.fingerprint,
        "version": agent.credential.version,
        "scopes": list(agent.credential.scopes),
        "expiresAt": _iso(agent.credential.expires_at),
        "lastUsedAt": _iso(agent.credential.last_used_at),
    }


def _v1(agent: AgentIdentityView) -> dict[str, object]:
    return {
        "id": str(agent.id),
        "organizationId": str(agent.organization_id),
        "name": agent.name,
        "description": agent.description,
        "ownerUserId": str(agent.owner_user_id),
        "status": agent.status,
        "credential": _credential(agent),
        "createdAt": agent.created_at.isoformat(),
        "updatedAt": agent.updated_at.isoformat(),
    }


def _v2(agent: AgentIdentityView) -> dict[str, object]:
    return {
        "identity": {
            "id": str(agent.id),
            "name": agent.name,
            "status": agent.status,
            "createdAt": agent.created_at.isoformat(),
            "updatedAt": agent.updated_at.isoformat(),
        },
        "profile": {"description": agent.description},
        "ownership": {
            "organizationId": str(agent.organization_id),
            "ownerUserId": str(agent.owner_user_id),
        },
        "credential": _credential(agent),
    }


def _reveal(value: AgentCredentialReveal) -> dict[str, object]:
    return {
        "secret": value.secret,
        "fingerprint": value.fingerprint,
        "version": value.version,
        "scopes": list(value.scopes),
        "createdAt": value.created_at.isoformat(),
        "expiresAt": _iso(value.expires_at),
    }


def _service(session: AsyncSession) -> AgentIdentityService:
    return AgentIdentityService(SQLAlchemyAgentRepository(session))


def _raise(error: AgentDomainError) -> None:
    raise HTTPException(status_code=error.status_code, detail=str(error)) from error


v1_router = APIRouter(tags=["agents"])
v2_router = APIRouter(tags=["agents"])


@v1_router.get("/organizations/{organization_id}/agents")
async def list_agents_v1(
    organization_id: UUID,
    user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, object]:
    try:
        agents = await _service(session).list_agents(user.id, organization_id)
    except AgentDomainError as error:
        _raise(error)
    return {"agents": [_v1(agent) for agent in agents], "count": len(agents)}


@v2_router.get("/organizations/{organization_id}/agents")
async def list_agents_v2(
    organization_id: UUID,
    user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, object]:
    try:
        agents = await _service(session).list_agents(user.id, organization_id)
    except AgentDomainError as error:
        _raise(error)
    return {"items": [_v2(agent) for agent in agents], "total": len(agents)}


@v1_router.post("/organizations/{organization_id}/agents", status_code=201)
async def create_agent_v1(
    organization_id: UUID,
    payload: AgentCreateRequest,
    user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, object]:
    try:
        agent, credential = await _service(session).register_agent(
            user.id, organization_id, payload.name, payload.description
        )
    except AgentDomainError as error:
        _raise(error)
    return {"agent": _v1(agent), "credential": _reveal(credential)}


@v2_router.post("/organizations/{organization_id}/agents", status_code=201)
async def create_agent_v2(
    organization_id: UUID,
    payload: AgentCreateRequest,
    user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, object]:
    try:
        agent, credential = await _service(session).register_agent(
            user.id, organization_id, payload.name, payload.description
        )
    except AgentDomainError as error:
        _raise(error)
    return {"data": {"agent": _v2(agent), "credential": _reveal(credential)}}


async def _lifecycle(
    organization_id: UUID,
    agent_id: UUID,
    payload: AgentLifecycleRequest,
    user: AuthenticatedUser,
    session: AsyncSession,
) -> AgentIdentityView:
    try:
        return await _service(session).set_lifecycle(
            user.id, organization_id, agent_id, payload.status
        )
    except AgentDomainError as error:
        _raise(error)


@v1_router.put("/organizations/{organization_id}/agents/{agent_id}/lifecycle")
async def lifecycle_v1(
    organization_id: UUID,
    agent_id: UUID,
    payload: AgentLifecycleRequest,
    user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, object]:
    return {"agent": _v1(await _lifecycle(organization_id, agent_id, payload, user, session))}


@v2_router.put("/organizations/{organization_id}/agents/{agent_id}/lifecycle")
async def lifecycle_v2(
    organization_id: UUID,
    agent_id: UUID,
    payload: AgentLifecycleRequest,
    user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, object]:
    return {"data": {"agent": _v2(await _lifecycle(organization_id, agent_id, payload, user, session))}}


async def _rotate(
    organization_id: UUID,
    agent_id: UUID,
    user: AuthenticatedUser,
    session: AsyncSession,
) -> tuple[AgentIdentityView, AgentCredentialReveal]:
    try:
        return await _service(session).rotate_credential(user.id, organization_id, agent_id)
    except AgentDomainError as error:
        _raise(error)


@v1_router.post("/organizations/{organization_id}/agents/{agent_id}/credentials/rotate")
async def rotate_v1(
    organization_id: UUID,
    agent_id: UUID,
    user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, object]:
    agent, credential = await _rotate(organization_id, agent_id, user, session)
    return {"agent": _v1(agent), "credential": _reveal(credential)}


@v2_router.post("/organizations/{organization_id}/agents/{agent_id}/credentials/rotate")
async def rotate_v2(
    organization_id: UUID,
    agent_id: UUID,
    user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, object]:
    agent, credential = await _rotate(organization_id, agent_id, user, session)
    return {"data": {"agent": _v2(agent), "credential": _reveal(credential)}}


async def _revoke(
    organization_id: UUID,
    agent_id: UUID,
    user: AuthenticatedUser,
    session: AsyncSession,
) -> AgentIdentityView:
    try:
        return await _service(session).revoke_credential(user.id, organization_id, agent_id)
    except AgentDomainError as error:
        _raise(error)


@v1_router.post("/organizations/{organization_id}/agents/{agent_id}/credentials/revoke")
async def revoke_v1(
    organization_id: UUID,
    agent_id: UUID,
    user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, object]:
    return {"agent": _v1(await _revoke(organization_id, agent_id, user, session))}


@v2_router.post("/organizations/{organization_id}/agents/{agent_id}/credentials/revoke")
async def revoke_v2(
    organization_id: UUID,
    agent_id: UUID,
    user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, object]:
    return {"data": {"agent": _v2(await _revoke(organization_id, agent_id, user, session))}}
