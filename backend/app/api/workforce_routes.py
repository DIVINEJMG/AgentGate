from __future__ import annotations

import hashlib
import secrets
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import organization_principal
from app.api.product_common import append_audit, not_found, require_permission, utcnow
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.database.models import (
    AgentCredential,
    AgentIdentity,
    Job,
    Worker,
    WorkforceRole,
)
from app.infrastructure.database.session import database_session

v1_router = APIRouter(tags=["workforce"])
v2_router = APIRouter(tags=["workforce"])

DEFAULT_HOURS = {
    "timezone": "UTC",
    "days": {
        day: {
            "enabled": day not in {"saturday", "sunday"},
            "start": "09:00",
            "end": "17:00",
        }
        for day in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    },
}


def _profile(worker: Worker) -> dict[str, Any]:
    return worker.profile if isinstance(worker.profile, dict) else {}


def role_public(role: WorkforceRole) -> dict[str, Any]:
    defaults = role.defaults if isinstance(role.defaults, dict) else {}
    return {
        "id": str(role.id),
        "organizationId": str(role.organization_id),
        "name": role.name,
        "purpose": role.purpose,
        "defaultInstructions": str(defaults.get("defaultInstructions", "")),
        "defaultResponsibilities": list(defaults.get("defaultResponsibilities", [])),
        "recommendedCapabilities": list(defaults.get("recommendedCapabilities", [])),
        "createdBy": str(defaults.get("createdBy", "")),
        "createdAt": role.created_at.isoformat(),
        "updatedAt": role.updated_at.isoformat(),
    }


def role_v2(role: WorkforceRole) -> dict[str, Any]:
    public = role_public(role)
    return {
        "identity": {
            "id": public["id"],
            "name": public["name"],
            "createdAt": public["createdAt"],
            "updatedAt": public["updatedAt"],
        },
        "organizationId": public["organizationId"],
        "profile": {
            "purpose": public["purpose"],
            "defaultInstructions": public["defaultInstructions"],
            "defaultResponsibilities": public["defaultResponsibilities"],
            "recommendedCapabilities": public["recommendedCapabilities"],
        },
        "createdBy": public["createdBy"],
    }


def worker_public(worker: Worker) -> dict[str, Any]:
    profile = _profile(worker)
    return {
        "id": str(worker.id),
        "organizationId": str(worker.organization_id),
        "agentIdentityId": str(worker.agent_identity_id),
        "agentIdentityProvisioning": str(
            profile.get("agentIdentityProvisioning", "existing")
        ),
        "name": worker.name,
        "roleId": str(worker.role_id) if worker.role_id else "",
        "department": worker.department,
        "supervisorUserId": (
            str(worker.supervisor_user_id) if worker.supervisor_user_id else ""
        ),
        "description": str(profile.get("description", "")),
        "responsibilities": list(profile.get("responsibilities", [])),
        "instructions": str(profile.get("instructions", "")),
        "status": worker.status,
        "workingHours": profile.get("workingHours") or DEFAULT_HOURS,
        "createdBy": str(profile.get("createdBy", "")),
        "createdAt": worker.created_at.isoformat(),
        "updatedAt": worker.updated_at.isoformat(),
    }


def worker_v2(worker: Worker) -> dict[str, Any]:
    public = worker_public(worker)
    return {
        "identity": {
            "id": public["id"],
            "name": public["name"],
            "status": public["status"],
            "createdAt": public["createdAt"],
            "updatedAt": public["updatedAt"],
        },
        "organizationId": public["organizationId"],
        "employment": {
            "roleId": public["roleId"],
            "department": public["department"],
            "supervisorUserId": public["supervisorUserId"],
        },
        "securityBinding": {
            "agentIdentityId": public["agentIdentityId"],
            "provisioning": public["agentIdentityProvisioning"],
        },
        "profile": {
            "description": public["description"],
            "responsibilities": public["responsibilities"],
            "instructions": public["instructions"],
        },
        "workingHours": public["workingHours"],
        "createdBy": public["createdBy"],
    }


async def security_identities(
    session: AsyncSession, organization_id: UUID
) -> list[dict[str, Any]]:
    agents = list(
        (
            await session.scalars(
                select(AgentIdentity).where(
                    AgentIdentity.organization_id == organization_id
                )
            )
        ).all()
    )
    workers = list(
        (
            await session.scalars(
                select(Worker).where(Worker.organization_id == organization_id)
            )
        ).all()
    )
    bound = {worker.agent_identity_id: str(worker.id) for worker in workers}
    results: list[dict[str, Any]] = []
    for agent in agents:
        credential = await session.scalar(
            select(AgentCredential)
            .where(AgentCredential.agent_id == agent.id)
            .order_by(desc(AgentCredential.version))
            .limit(1)
        )
        results.append(
            {
                "id": str(agent.id),
                "name": agent.name,
                "status": agent.status,
                "credentialStatus": credential.status if credential else "missing",
                "boundWorkerId": bound.get(agent.id),
            }
        )
    return results


async def list_workforce(
    session: AsyncSession, organization_id: UUID
) -> dict[str, Any]:
    workers = list(
        (
            await session.scalars(
                select(Worker)
                .where(Worker.organization_id == organization_id)
                .order_by(desc(Worker.created_at))
            )
        ).all()
    )
    roles = list(
        (
            await session.scalars(
                select(WorkforceRole)
                .where(WorkforceRole.organization_id == organization_id)
                .order_by(WorkforceRole.name)
            )
        ).all()
    )
    departments = {worker.department for worker in workers if worker.department}
    return {
        "workers": workers,
        "roles": roles,
        "securityIdentities": await security_identities(session, organization_id),
        "summary": {
            "workers": len(workers),
            "active": sum(worker.status == "active" for worker in workers),
            "paused": sum(worker.status == "paused" for worker in workers),
            "suspended": sum(worker.status == "suspended" for worker in workers),
            "roles": len(roles),
            "departments": len(departments),
        },
        "window": {"workersTruncated": False, "rolesTruncated": False},
    }


async def create_role(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    payload: dict[str, Any],
) -> WorkforceRole:
    require_permission(principal, "workforce.manage")
    name = str(payload.get("name", "")).strip()
    if len(name) < 2:
        raise HTTPException(400, "Role name must be at least 2 characters.")
    role = WorkforceRole(
        organization_id=organization_id,
        name=name,
        purpose=str(payload.get("purpose", "")).strip(),
        defaults={
            "defaultInstructions": str(payload.get("defaultInstructions", "")),
            "defaultResponsibilities": list(
                payload.get("defaultResponsibilities", [])
            ),
            "recommendedCapabilities": list(
                payload.get("recommendedCapabilities", [])
            ),
            "createdBy": str(principal.user_id),
        },
    )
    session.add(role)
    await session.flush()
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="workforce.role.created",
        category="workforce",
        resource_type="workforce_role",
        resource_id=str(role.id),
        resource_name=name,
        outcome="created",
        summary=f"Workforce role {name} created.",
    )
    await session.commit()
    await session.refresh(role)
    return role


async def update_role(
    session: AsyncSession,
    organization_id: UUID,
    role_id: UUID,
    principal: HumanPrincipal,
    payload: dict[str, Any],
) -> WorkforceRole:
    require_permission(principal, "workforce.manage")
    role = await session.scalar(
        select(WorkforceRole).where(
            WorkforceRole.organization_id == organization_id,
            WorkforceRole.id == role_id,
        )
    )
    if role is None:
        raise not_found("Workforce role")
    role.name = str(payload.get("name", role.name)).strip() or role.name
    role.purpose = str(payload.get("purpose", role.purpose))
    defaults = dict(role.defaults or {})
    defaults.update(
        {
            "defaultInstructions": str(
                payload.get(
                    "defaultInstructions", defaults.get("defaultInstructions", "")
                )
            ),
            "defaultResponsibilities": list(
                payload.get(
                    "defaultResponsibilities",
                    defaults.get("defaultResponsibilities", []),
                )
            ),
            "recommendedCapabilities": list(
                payload.get(
                    "recommendedCapabilities",
                    defaults.get("recommendedCapabilities", []),
                )
            ),
        }
    )
    role.defaults = defaults
    await session.commit()
    await session.refresh(role)
    return role


async def create_worker(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    payload: dict[str, Any],
    *,
    provisioning: str = "existing",
) -> Worker:
    require_permission(principal, "workforce.manage")
    try:
        agent_id = UUID(str(payload.get("agentIdentityId")))
    except ValueError as error:
        raise HTTPException(400, "A valid Agent Identity is required.") from error
    agent = await session.scalar(
        select(AgentIdentity).where(
            AgentIdentity.organization_id == organization_id,
            AgentIdentity.id == agent_id,
        )
    )
    if agent is None:
        raise not_found("Agent Identity")
    already = await session.scalar(
        select(Worker).where(Worker.agent_identity_id == agent_id)
    )
    if already is not None:
        raise HTTPException(
            409, "This Agent Identity is already bound to a managed Worker."
        )

    role_raw = payload.get("roleId")
    role_id = UUID(str(role_raw)) if role_raw else None
    if role_id:
        role = await session.scalar(
            select(WorkforceRole).where(
                WorkforceRole.organization_id == organization_id,
                WorkforceRole.id == role_id,
            )
        )
        if role is None:
            raise not_found("Workforce role")

    supervisor_raw = payload.get("supervisorUserId")
    supervisor_id = (
        UUID(str(supervisor_raw)) if supervisor_raw else principal.user_id
    )
    worker = Worker(
        organization_id=organization_id,
        agent_identity_id=agent_id,
        role_id=role_id,
        supervisor_user_id=supervisor_id,
        name=str(payload.get("name", "")).strip() or agent.name,
        department=str(payload.get("department", "")).strip(),
        status="draft",
        profile={
            "description": str(payload.get("description", "")),
            "responsibilities": list(payload.get("responsibilities", [])),
            "instructions": str(payload.get("instructions", "")),
            "workingHours": payload.get("workingHours") or DEFAULT_HOURS,
            "createdBy": str(principal.user_id),
            "agentIdentityProvisioning": provisioning,
        },
    )
    session.add(worker)
    await session.flush()
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="worker.created",
        category="workforce",
        resource_type="worker",
        resource_id=str(worker.id),
        resource_name=worker.name,
        outcome="draft",
        summary=f"Worker {worker.name} created.",
    )
    await session.commit()
    await session.refresh(worker)
    return worker


async def update_worker(
    session: AsyncSession,
    organization_id: UUID,
    worker_id: UUID,
    principal: HumanPrincipal,
    payload: dict[str, Any],
) -> Worker:
    require_permission(principal, "workforce.manage")
    worker = await session.scalar(
        select(Worker).where(
            Worker.organization_id == organization_id, Worker.id == worker_id
        )
    )
    if worker is None:
        raise not_found("Worker")
    if payload.get("agentIdentityId"):
        agent_id = UUID(str(payload["agentIdentityId"]))
        if agent_id != worker.agent_identity_id:
            conflict = await session.scalar(
                select(Worker).where(Worker.agent_identity_id == agent_id)
            )
            if conflict is not None:
                raise HTTPException(
                    409, "This Agent Identity is already bound to another Worker."
                )
            worker.agent_identity_id = agent_id
    if payload.get("roleId"):
        worker.role_id = UUID(str(payload["roleId"]))
    if payload.get("supervisorUserId"):
        worker.supervisor_user_id = UUID(str(payload["supervisorUserId"]))
    worker.name = str(payload.get("name", worker.name)).strip() or worker.name
    worker.department = str(payload.get("department", worker.department))
    profile = dict(_profile(worker))
    for key in ("description", "responsibilities", "instructions", "workingHours"):
        if key in payload:
            profile[key] = payload[key]
    worker.profile = profile
    await session.commit()
    await session.refresh(worker)
    return worker


async def set_worker_status(
    session: AsyncSession,
    organization_id: UUID,
    worker_id: UUID,
    principal: HumanPrincipal,
    new_status: str,
) -> Worker:
    require_permission(principal, "workforce.manage")
    if new_status not in {"draft", "active", "paused", "suspended", "archived"}:
        raise HTTPException(400, "Invalid Worker status.")
    worker = await session.scalar(
        select(Worker).where(
            Worker.organization_id == organization_id, Worker.id == worker_id
        )
    )
    if worker is None:
        raise not_found("Worker")
    worker.status = new_status
    await session.commit()
    await session.refresh(worker)
    return worker


async def automatic_agent(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    name: str,
) -> tuple[AgentIdentity, dict[str, Any]]:
    secret = f"agt_sk_{secrets.token_urlsafe(32)}"
    digest = hashlib.sha256(secret.encode()).hexdigest()
    now = utcnow()
    agent = AgentIdentity(
        organization_id=organization_id,
        name=name,
        status="active",
        created_by=principal.user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(agent)
    await session.flush()
    credential = AgentCredential(
        agent_id=agent.id,
        fingerprint=f"sha256:{digest[:12]}",
        secret_hash=digest,
        status="active",
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(credential)
    await session.commit()
    return agent, {
        "secret": secret,
        "fingerprint": credential.fingerprint,
        "version": 1,
        "scopes": ["agent.authenticate"],
        "createdAt": now.isoformat(),
        "expiresAt": None,
    }


@v1_router.get("/organizations/{organization_id}/workforce")
async def workforce_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "workforce.read")
    result = await list_workforce(session, organization_id)
    return {
        "workers": [worker_public(x) for x in result["workers"]],
        "roles": [role_public(x) for x in result["roles"]],
        "securityIdentities": result["securityIdentities"],
        "summary": result["summary"],
        "window": result["window"],
    }


@v2_router.get("/organizations/{organization_id}/workforce")
async def workforce_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "workforce.read")
    result = await list_workforce(session, organization_id)
    return {
        "data": {
            "workers": [worker_v2(x) for x in result["workers"]],
            "roles": [role_v2(x) for x in result["roles"]],
            "security": {"identities": result["securityIdentities"]},
            "summary": result["summary"],
            "window": result["window"],
        }
    }


@v1_router.post("/organizations/{organization_id}/workforce/roles", status_code=201)
async def create_role_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "role": role_public(
            await create_role(session, organization_id, principal, payload)
        )
    }


@v2_router.post("/organizations/{organization_id}/workforce/roles", status_code=201)
async def create_role_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": {
            "role": role_v2(
                await create_role(session, organization_id, principal, payload)
            )
        }
    }


@v1_router.put("/organizations/{organization_id}/workforce/roles/{role_id}")
async def update_role_v1(
    organization_id: UUID,
    role_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "role": role_public(
            await update_role(
                session, organization_id, role_id, principal, payload
            )
        )
    }


@v2_router.put("/organizations/{organization_id}/workforce/roles/{role_id}")
async def update_role_v2(
    organization_id: UUID,
    role_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": {
            "role": role_v2(
                await update_role(
                    session, organization_id, role_id, principal, payload
                )
            )
        }
    }


@v1_router.post("/organizations/{organization_id}/workforce/workers", status_code=201)
async def create_worker_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "worker": worker_public(
            await create_worker(session, organization_id, principal, payload)
        )
    }


@v2_router.post("/organizations/{organization_id}/workforce/workers", status_code=201)
async def create_worker_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": {
            "worker": worker_v2(
                await create_worker(session, organization_id, principal, payload)
            )
        }
    }


@v1_router.put("/organizations/{organization_id}/workforce/workers/{worker_id}")
async def update_worker_v1(
    organization_id: UUID,
    worker_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "worker": worker_public(
            await update_worker(
                session, organization_id, worker_id, principal, payload
            )
        )
    }


@v2_router.put("/organizations/{organization_id}/workforce/workers/{worker_id}")
async def update_worker_v2(
    organization_id: UUID,
    worker_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": {
            "worker": worker_v2(
                await update_worker(
                    session, organization_id, worker_id, principal, payload
                )
            )
        }
    }


@v1_router.post(
    "/organizations/{organization_id}/workforce/workers/{worker_id}/status"
)
async def worker_status_v1(
    organization_id: UUID,
    worker_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "worker": worker_public(
            await set_worker_status(
                session,
                organization_id,
                worker_id,
                principal,
                str(payload.get("status", "")),
            )
        )
    }


@v2_router.post(
    "/organizations/{organization_id}/workforce/workers/{worker_id}/status"
)
async def worker_status_v2(
    organization_id: UUID,
    worker_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": {
            "worker": worker_v2(
                await set_worker_status(
                    session,
                    organization_id,
                    worker_id,
                    principal,
                    str(payload.get("status", "")),
                )
            )
        }
    }


@v1_router.delete("/organizations/{organization_id}/workforce/workers/{worker_id}")
async def delete_worker_v1(
    organization_id: UUID,
    worker_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "workforce.manage")
    worker = await session.scalar(
        select(Worker).where(
            Worker.organization_id == organization_id, Worker.id == worker_id
        )
    )
    if worker is None:
        raise not_found("Worker")
    jobs = list(
        (await session.scalars(select(Job).where(Job.worker_id == worker.id))).all()
    )
    # Jobs are removed through the Jobs router before worker deletion in normal UI flows.
    if jobs:
        raise HTTPException(
            409,
            "Delete this Worker's Jobs first so runtime/result lineage is preserved.",
        )
    agent = await session.get(AgentIdentity, worker.agent_identity_id)
    if agent is not None:
        agent.status = "disabled"
        await session.execute(
            delete(AgentCredential).where(AgentCredential.agent_id == agent.id)
        )
    await session.delete(worker)
    await session.commit()
    return {
        "complete": True,
        "workerId": str(worker_id),
        "jobsRemoved": 0,
        "workItemsRemoved": 0,
        "runsTerminated": 0,
        "nextToken": None,
    }


@v2_router.delete("/organizations/{organization_id}/workforce/workers/{worker_id}")
async def delete_worker_v2(
    organization_id: UUID,
    worker_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": await delete_worker_v1(
            organization_id, worker_id, principal, session
        )
    }
