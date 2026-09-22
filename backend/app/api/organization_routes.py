from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import bearer_token
from app.domain.identity.permissions import permissions_for_role
from app.infrastructure.auth.service import LocalAuthenticationService
from app.infrastructure.auth.session_store import RedisSessionStore
from app.infrastructure.database.models import Organization, OrganizationMembership
from app.infrastructure.database.session import database_session


class OrganizationCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)


@dataclass(frozen=True, slots=True)
class OrganizationAccess:
    id: UUID
    name: str
    created_at: datetime
    role: str
    permissions: frozenset[str]


async def _user_id(
    authorization: str | None,
    session: AsyncSession,
) -> UUID:
    token = bearer_token(authorization)
    service = LocalAuthenticationService(session, RedisSessionStore.from_settings())
    user = await service.current_user(token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session.")
    return user.id


async def _list(user_id: UUID, session: AsyncSession) -> list[OrganizationAccess]:
    rows = (
        await session.execute(
            select(Organization, OrganizationMembership)
            .join(
                OrganizationMembership,
                OrganizationMembership.organization_id == Organization.id,
            )
            .where(OrganizationMembership.user_id == user_id)
            .order_by(Organization.created_at, Organization.id)
        )
    ).all()
    return [
        OrganizationAccess(
            id=organization.id,
            name=organization.name,
            created_at=organization.created_at,
            role=membership.role,
            permissions=permissions_for_role(membership.role),
        )
        for organization, membership in rows
    ]


async def _create(name: str, user_id: UUID, session: AsyncSession) -> OrganizationAccess:
    cleaned = name.strip()
    if len(cleaned) < 2:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Organization name is too short.")
    organization = Organization(name=cleaned, created_by=user_id)
    session.add(organization)
    await session.flush()
    membership = OrganizationMembership(
        organization_id=organization.id,
        user_id=user_id,
        role="owner",
    )
    session.add(membership)
    await session.commit()
    return OrganizationAccess(
        id=organization.id,
        name=organization.name,
        created_at=organization.created_at,
        role="owner",
        permissions=permissions_for_role("owner"),
    )


def _v1(item: OrganizationAccess) -> dict[str, object]:
    return {
        "id": str(item.id),
        "name": item.name,
        "createdAt": item.created_at.isoformat(),
        "role": item.role,
        "permissions": sorted(item.permissions),
    }


def _v2(item: OrganizationAccess) -> dict[str, object]:
    return {
        "organization": {
            "id": str(item.id),
            "name": item.name,
            "createdAt": item.created_at.isoformat(),
        },
        "membership": {
            "role": item.role,
            "permissions": sorted(item.permissions),
        },
    }


v1_router = APIRouter(tags=["organizations"])
v2_router = APIRouter(tags=["organizations"])


@v1_router.get("/organizations")
async def list_v1(
    session: Annotated[AsyncSession, Depends(database_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = await _user_id(authorization, session)
    return {"organizations": [_v1(item) for item in await _list(user_id, session)]}


@v1_router.post("/organizations", status_code=status.HTTP_201_CREATED)
async def create_v1(
    payload: OrganizationCreateRequest,
    session: Annotated[AsyncSession, Depends(database_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = await _user_id(authorization, session)
    return {"organization": _v1(await _create(payload.name, user_id, session))}


@v2_router.get("/organizations")
async def list_v2(
    session: Annotated[AsyncSession, Depends(database_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = await _user_id(authorization, session)
    return {"items": [_v2(item) for item in await _list(user_id, session)]}


@v2_router.post("/organizations", status_code=status.HTTP_201_CREATED)
async def create_v2(
    payload: OrganizationCreateRequest,
    session: Annotated[AsyncSession, Depends(database_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = await _user_id(authorization, session)
    item = await _create(payload.name, user_id, session)
    return {"data": _v2(item)}
