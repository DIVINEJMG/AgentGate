from __future__ import annotations

import hashlib
import secrets
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import authenticated_user, organization_principal
from app.api.product_common import (
    append_audit,
    latest_setting,
    not_found,
    require_permission,
    utcnow,
)
from app.domain.identity.permissions import permissions_for_role
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.auth.service import AuthenticatedUser
from app.infrastructure.database.models import (
    Action,
    AgentIdentity,
    AuditEvent,
    HumanIdentity,
    Incident,
    Integration,
    Job,
    Organization,
    OrganizationMembership,
    Policy,
    Run,
    Worker,
    WorkItem,
)
from app.infrastructure.database.session import database_session

v1_router = APIRouter(tags=["product", "commercial", "supervision", "performance"])
v2_router = APIRouter(tags=["product", "commercial", "supervision", "performance"])

PLANS = (
    {
        "id": "free",
        "name": "Free",
        "priceMonthlyUsd": 0,
        "workerLimit": 3,
        "agentLimit": 3,
        "positioning": "Explore Aduoryn with a small supervised workforce.",
    },
    {
        "id": "team",
        "name": "Team",
        "priceMonthlyUsd": 49,
        "workerLimit": 15,
        "agentLimit": 15,
        "positioning": "Coordinate a growing AI workforce across a team.",
    },
    {
        "id": "business",
        "name": "Business",
        "priceMonthlyUsd": 199,
        "workerLimit": 75,
        "agentLimit": 75,
        "positioning": "Govern production AI workers with stronger controls.",
    },
    {
        "id": "scale",
        "name": "Scale",
        "priceMonthlyUsd": 499,
        "workerLimit": 250,
        "agentLimit": 250,
        "positioning": "Operate a larger governed autonomous workforce.",
    },
)


async def _org(session: AsyncSession, organization_id: UUID) -> Organization:
    organization = await session.get(Organization, organization_id)
    if organization is None:
        raise not_found("Organization")
    return organization


async def _workspace_settings(
    session: AsyncSession, organization_id: UUID
) -> dict[str, Any]:
    organization = await _org(session, organization_id)
    setting = await latest_setting(
        session, organization_id, "organization.settings.updated"
    )
    setting = setting or {}
    return {
        "organizationId": str(organization.id),
        "name": organization.name,
        "securityContactEmail": str(setting.get("securityContactEmail", "")),
        "companyUrl": str(setting.get("companyUrl", "")),
        "updatedBy": setting.get("updatedBy"),
        "updatedAt": setting.get("updatedAt"),
    }


async def _usage(session: AsyncSession, organization_id: UUID) -> dict[str, Any]:
    agents = list(
        (await session.scalars(select(AgentIdentity).where(AgentIdentity.organization_id == organization_id))).all()
    )
    integrations = list(
        (await session.scalars(select(Integration).where(Integration.organization_id == organization_id))).all()
    )
    policies = list(
        (await session.scalars(select(Policy).where(Policy.organization_id == organization_id))).all()
    )
    actions = list(
        (
            await session.scalars(
                select(Action)
                .where(Action.organization_id == organization_id)
                .order_by(desc(Action.created_at))
                .limit(1000)
            )
        ).all()
    )
    incidents = list(
        (await session.scalars(select(Incident).where(Incident.organization_id == organization_id))).all()
    )
    workers = list(
        (await session.scalars(select(Worker).where(Worker.organization_id == organization_id))).all()
    )
    return {
        "registeredAgents": len(agents),
        "activeAgents": sum(item.status == "active" for item in agents),
        "connectedIntegrations": sum(item.status == "connected" for item in integrations),
        "enabledPolicies": sum(item.status == "enabled" for item in policies),
        "actions30d": len(actions),
        "heldActions": sum(item.status == "held" for item in actions),
        "blocked30d": sum(item.status == "blocked" for item in actions),
        "failed30d": sum(item.status == "failed" for item in actions),
        "activeIncidents": sum(item.status != "resolved" for item in incidents),
        "agentLimitAdvisory": PLANS[0]["agentLimit"],
        "agentLimitState": "within_limit" if len(agents) <= PLANS[0]["agentLimit"] else "over_limit",
        "windowTruncated": len(actions) >= 1000,
        "workers": len(workers),
    }


async def _productization(
    session: AsyncSession, organization_id: UUID
) -> dict[str, Any]:
    workspace = await _workspace_settings(session, organization_id)
    usage = await _usage(session, organization_id)
    steps = [
        {
            "id": "agent",
            "label": "Create an Agent Identity",
            "complete": usage["registeredAgents"] > 0,
            "destination": "/organization",
        },
        {
            "id": "integration",
            "label": "Connect an integration",
            "complete": usage["connectedIntegrations"] > 0,
            "destination": "/connections",
        },
        {
            "id": "policy",
            "label": "Enable a policy",
            "complete": usage["enabledPolicies"] > 0,
            "destination": "/governance",
        },
        {
            "id": "worker",
            "label": "Create a Worker",
            "complete": usage["workers"] > 0,
            "destination": "/workforce",
        },
    ]
    complete = sum(step["complete"] for step in steps)
    return {
        "workspace": workspace,
        "plans": [
            {
                "id": plan["id"],
                "name": plan["name"],
                "priceMonthlyUsd": plan["priceMonthlyUsd"],
                "agentLimit": plan["agentLimit"],
                "positioning": plan["positioning"],
            }
            for plan in PLANS
        ],
        "subscription": {
            "planId": "free",
            "status": "active",
            "billingProvider": "none",
            "enforcement": "advisory",
            "canCheckout": False,
        },
        "usage": {key: value for key, value in usage.items() if key != "workers"},
        "onboarding": {
            "steps": steps,
            "complete": complete,
            "total": len(steps),
            "percent": round(complete * 100 / len(steps)),
        },
        "generatedAt": utcnow().isoformat(),
    }


def _product_v2(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace": result["workspace"],
        "commercial": {
            "plans": result["plans"],
            "subscription": result["subscription"],
        },
        "operations": {"usage": result["usage"]},
        "readiness": result["onboarding"],
        "generatedAt": result["generatedAt"],
    }


@v1_router.get("/organizations/{organization_id}/product")
async def product_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "organizations.read")
    return await _productization(session, organization_id)


@v2_router.get("/organizations/{organization_id}/product")
async def product_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await product_v1(organization_id, principal, session)
    return {"data": _product_v2(result)}


@v1_router.put("/organizations/{organization_id}/settings")
async def settings_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "organizations.manage")
    organization = await _org(session, organization_id)
    name = str(payload.get("name", organization.name)).strip()
    if len(name) < 2:
        raise HTTPException(400, "Organization name is too short.")
    organization.name = name
    metadata = {
        "securityContactEmail": str(payload.get("securityContactEmail", "")),
        "companyUrl": str(payload.get("companyUrl", "")),
        "updatedBy": str(principal.user_id),
        "updatedAt": utcnow().isoformat(),
    }
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="organization.settings.updated",
        category="system",
        resource_type="organization",
        resource_id=str(organization_id),
        resource_name=name,
        outcome="updated",
        summary="Workspace settings updated.",
        metadata=metadata,
    )
    await session.commit()
    return await _productization(session, organization_id)


@v2_router.put("/organizations/{organization_id}/settings")
async def settings_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await settings_v1(
        organization_id, payload, principal, session
    )
    return {"data": _product_v2(result)}


async def _commercial(
    session: AsyncSession, organization_id: UUID
) -> dict[str, Any]:
    organization = await _org(session, organization_id)
    workers = list(
        (await session.scalars(select(Worker).where(Worker.organization_id == organization_id))).all()
    )
    jobs = list(
        (await session.scalars(select(Job).where(Job.organization_id == organization_id))).all()
    )
    runs = list(
        (
            await session.scalars(
                select(Run)
                .where(Run.organization_id == organization_id)
                .order_by(desc(Run.created_at))
                .limit(1000)
            )
        ).all()
    )
    incidents = list(
        (await session.scalars(select(Incident).where(Incident.organization_id == organization_id))).all()
    )
    actions = list(
        (
            await session.scalars(
                select(Action)
                .where(Action.organization_id == organization_id)
                .order_by(desc(Action.created_at))
                .limit(1000)
            )
        ).all()
    )
    invite_events = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.organization_id == organization_id,
                    AuditEvent.event_type == "commercial.invitation.created",
                )
                .order_by(desc(AuditEvent.created_at))
                .limit(100)
            )
        ).all()
    )
    joined_events = list(
        (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.organization_id == organization_id,
                    AuditEvent.event_type == "commercial.invitation.joined",
                )
            )
        ).all()
    )
    joined_ids = {
        str((event.payload or {}).get("metadata", {}).get("invitationId", ""))
        for event in joined_events
    }
    invitations = []
    for event in invite_events:
        meta = (event.payload or {}).get("metadata", {})
        if not isinstance(meta, dict):
            continue
        invitation_id = str(event.id)
        invitations.append(
            {
                "id": invitation_id,
                "email": str(meta.get("email", "")),
                "role": str(meta.get("role", "viewer")),
                "status": "joined" if invitation_id in joined_ids else "pending",
                "createdAt": event.created_at.isoformat(),
                "expiresAt": str(meta.get("expiresAt", "")),
                "joinedAt": (
                    next(
                        (
                            joined.created_at.isoformat()
                            for joined in joined_events
                            if str(
                                (joined.payload or {})
                                .get("metadata", {})
                                .get("invitationId", "")
                            )
                            == invitation_id
                        ),
                        None,
                    )
                ),
            }
        )
    plan = PLANS[0]
    failed_runs = sum(run.status == "failed" for run in runs)
    failed_actions = sum(action.status == "failed" for action in actions)
    critical_incidents = sum(
        incident.severity == "critical" and incident.status != "resolved"
        for incident in incidents
    )
    state = (
        "critical"
        if critical_incidents
        else "attention"
        if failed_runs or failed_actions
        else "healthy"
    )
    onboarding = await _productization(session, organization_id)
    return {
        "organization": {
            "id": str(organization.id),
            "name": organization.name,
        },
        "billing": {
            "subscription": {
                "planId": "free",
                "provider": "none",
                "verified": True,
                "source": "default",
            },
            "adapter": {
                "key": "none",
                "checkoutAvailable": False,
                "providerConnected": False,
                "verified": True,
            },
            "plan": {
                "id": plan["id"],
                "name": plan["name"],
                "priceMonthlyUsd": plan["priceMonthlyUsd"],
                "workerLimit": plan["workerLimit"],
                "positioning": plan["positioning"],
            },
            "plans": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "priceMonthlyUsd": item["priceMonthlyUsd"],
                    "workerLimit": item["workerLimit"],
                    "positioning": item["positioning"],
                }
                for item in PLANS
            ],
            "enforcement": "worker_limit_active",
        },
        "entitlements": {
            "workerLimit": plan["workerLimit"],
            "currentWorkers": len(workers),
            "remainingWorkers": max(0, plan["workerLimit"] - len(workers)),
            "features": {
                "governance": True,
                "results": True,
                "integrations": True,
            },
        },
        "usage": {
            "periodStart": utcnow().replace(day=1).date().isoformat(),
            "workers": len(workers),
            "jobsTotal": len(jobs),
            "jobsCreatedThisMonth": len(jobs),
            "runsThisMonth": len(runs),
            "runsCompletedThisMonth": sum(run.status == "completed" for run in runs),
            "runsFailedThisMonth": failed_runs,
            "accountingEventsThisMonth": len(actions),
            "windowTruncated": len(runs) >= 1000 or len(actions) >= 1000,
        },
        "onboarding": {
            **onboarding["onboarding"],
            "steps": [
                {**step, "required": True}
                for step in onboarding["onboarding"]["steps"]
            ],
            "commercialReady": onboarding["onboarding"]["percent"] >= 50,
        },
        "templates": {"available": 6, "applied": len(workers) > 0},
        "invitations": {
            "canInvite": True,
            "recent": invitations,
            "windowTruncated": len(invite_events) >= 100,
        },
        "production": {
            "state": state,
            "deadLetterWorkItems": 0,
            "failedRunsThisMonth": failed_runs,
            "failedActionsThisMonth": failed_actions,
            "openEscalations": sum(incident.status != "resolved" for incident in incidents),
            "criticalEscalations": critical_incidents,
            "criticalIncidents": critical_incidents,
            "windowTruncated": False,
            "checkedAt": utcnow().isoformat(),
        },
        "generatedAt": utcnow().isoformat(),
    }


@v1_router.get("/organizations/{organization_id}/commercial")
async def commercial_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "organizations.read")
    return await _commercial(session, organization_id)


@v2_router.get("/organizations/{organization_id}/commercial")
async def commercial_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await commercial_v1(organization_id, principal, session)
    return {
        "data": {
            "workspace": result["organization"],
            "billing": result["billing"],
            "entitlements": result["entitlements"],
            "usage": result["usage"],
            "readiness": result["onboarding"],
            "templates": result["templates"],
            "invitations": result["invitations"],
            "production": result["production"],
            "generatedAt": result["generatedAt"],
        }
    }


@v1_router.post("/organizations/{organization_id}/commercial/invitations", status_code=201)
async def invite_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "memberships.manage")
    email = str(payload.get("email", "")).strip().lower()
    role = str(payload.get("role", "viewer"))
    if "@" not in email:
        raise HTTPException(400, "A valid invitation email is required.")
    if role not in {"admin", "security_manager", "operator", "approver", "viewer"}:
        raise HTTPException(400, "Invalid invitation role.")
    code = secrets.token_urlsafe(24)
    code_hash = hashlib.sha256(code.encode()).hexdigest()
    expires_at = utcnow() + timedelta(days=7)
    event = await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="commercial.invitation.created",
        category="identity",
        resource_type="invitation",
        resource_id="pending",
        resource_name=email,
        outcome="pending",
        summary=f"Organization invitation created for {email}.",
        metadata={
            "email": email,
            "role": role,
            "codeHash": code_hash,
            "expiresAt": expires_at.isoformat(),
        },
    )
    event.resource = {"type": "invitation", "id": str(event.id), "name": email}
    await session.commit()
    return {
        "id": str(event.id),
        "email": email,
        "role": role,
        "status": "pending",
        "expiresAt": expires_at.isoformat(),
        "code": code,
    }


@v2_router.post("/organizations/{organization_id}/commercial/invitations", status_code=201)
async def invite_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {"data": await invite_v1(organization_id, payload, principal, session)}


async def _invite_event(
    session: AsyncSession, code: str
) -> tuple[AuditEvent, dict[str, Any]]:
    code_hash = hashlib.sha256(code.encode()).hexdigest()
    rows = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type == "commercial.invitation.created")
                .order_by(desc(AuditEvent.created_at))
                .limit(1000)
            )
        ).all()
    )
    for event in rows:
        metadata = (event.payload or {}).get("metadata", {})
        if isinstance(metadata, dict) and metadata.get("codeHash") == code_hash:
            return event, metadata
    raise not_found("Invitation")


@v1_router.get("/commercial/invitations/{code}")
async def resolve_invite_v1(
    code: str,
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    event, metadata = await _invite_event(session, code)
    organization = await _org(session, event.organization_id)
    expires_at = datetime.fromisoformat(str(metadata["expiresAt"]))
    joined = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.organization_id == event.organization_id,
            AuditEvent.event_type == "commercial.invitation.joined",
        )
    )
    return {
        "status": "expired" if expires_at < utcnow() else "joined" if joined else "pending",
        "expiresAt": metadata.get("expiresAt"),
        "organization": {"id": str(organization.id), "name": organization.name},
        "role": metadata.get("role", "viewer"),
    }


@v2_router.get("/commercial/invitations/{code}")
async def resolve_invite_v2(
    code: str,
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {"data": await resolve_invite_v1(code, session)}


@v1_router.post("/commercial/invitations/{code}/join")
async def join_invite_v1(
    code: str,
    user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    event, metadata = await _invite_event(session, code)
    expires_at = datetime.fromisoformat(str(metadata["expiresAt"]))
    if expires_at < utcnow():
        raise HTTPException(410, "Invitation has expired.")
    identity = await session.get(HumanIdentity, user.id)
    email = (identity.email or "").lower() if identity else ""
    if email != str(metadata.get("email", "")).lower():
        raise HTTPException(403, "Invitation email does not match the signed-in user.")
    existing = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == event.organization_id,
            OrganizationMembership.user_id == user.id,
        )
    )
    role = str(metadata.get("role", "viewer"))
    if existing is None:
        existing = OrganizationMembership(
            organization_id=event.organization_id,
            user_id=user.id,
            role=role,
        )
        session.add(existing)
    await append_audit(
        session,
        organization_id=event.organization_id,
        principal=None,
        event_type="commercial.invitation.joined",
        category="identity",
        resource_type="invitation",
        resource_id=str(event.id),
        resource_name=email,
        outcome="joined",
        summary=f"{email} joined the organization.",
        metadata={"invitationId": str(event.id), "userId": str(user.id)},
        actor={"type": "human", "id": str(user.id), "label": email},
    )
    await session.commit()
    organization = await _org(session, event.organization_id)
    return {
        "organization": {
            "id": str(organization.id),
            "name": organization.name,
            "createdAt": organization.created_at.isoformat(),
            "role": role,
            "permissions": sorted(permissions_for_role(role)),
        }
    }


@v2_router.post("/commercial/invitations/{code}/join")
async def join_invite_v2(
    code: str,
    user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await join_invite_v1(code, user, session)
    return {"data": result}


async def _supervision_rule(
    session: AsyncSession,
    organization_id: UUID,
    trigger: str,
) -> dict[str, Any]:
    rows = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.organization_id == organization_id,
                    AuditEvent.event_type == "supervision.rule.updated",
                )
                .order_by(desc(AuditEvent.created_at))
                .limit(100)
            )
        ).all()
    )
    for event in rows:
        metadata = (event.payload or {}).get("metadata", {})
        if isinstance(metadata, dict) and metadata.get("trigger") == trigger:
            return {
                "id": str(event.id),
                "organizationId": str(organization_id),
                "trigger": trigger,
                "enabled": bool(metadata.get("enabled", True)),
                "severity": str(metadata.get("severity", "high")),
                "notifySupervisor": bool(metadata.get("notifySupervisor", True)),
                "updatedBy": str(metadata.get("updatedBy", "")),
                "updatedAt": str(metadata.get("updatedAt", event.created_at.isoformat())),
            }
    return {
        "id": None,
        "organizationId": str(organization_id),
        "trigger": trigger,
        "enabled": True,
        "severity": "critical" if trigger == "risk_critical" else "high",
        "notifySupervisor": True,
        "updatedBy": "",
        "updatedAt": "",
    }


async def _supervision(session: AsyncSession, organization_id: UUID) -> dict[str, Any]:
    incidents = list(
        (
            await session.scalars(
                select(Incident)
                .where(Incident.organization_id == organization_id)
                .order_by(desc(Incident.created_at))
                .limit(100)
            )
        ).all()
    )
    workers = {
        worker.id: worker
        for worker in (
            await session.scalars(
                select(Worker).where(Worker.organization_id == organization_id)
            )
        ).all()
    }
    escalations = []
    for incident in incidents:
        worker = next(iter(workers.values()), None)
        escalations.append(
            {
                "id": str(incident.id),
                "organizationId": str(organization_id),
                "trigger": "risk_critical" if incident.severity == "critical" else "job_failure",
                "severity": incident.severity,
                "status": incident.status,
                "workerId": str(worker.id) if worker else "",
                "workerName": worker.name if worker else "Organization",
                "supervisorUserId": str(worker.supervisor_user_id) if worker and worker.supervisor_user_id else "",
                "assignedToUserId": str(worker.supervisor_user_id) if worker and worker.supervisor_user_id else "",
                "jobId": None,
                "workItemId": None,
                "runId": None,
                "actionId": None,
                "approvalId": None,
                "incidentId": str(incident.id),
                "correlationId": None,
                "sourceType": "incident",
                "sourceId": str(incident.id),
                "title": f"{incident.severity.title()} incident",
                "summary": incident.summary,
                "reason": incident.summary,
                "notes": [],
                "interventions": [],
                "notification": {
                    "state": "not_attempted",
                    "attemptedAt": None,
                    "sent": 0,
                    "failed": 0,
                },
                "createdAt": incident.created_at.isoformat(),
                "updatedAt": incident.updated_at.isoformat(),
                "acknowledgedBy": None,
                "acknowledgedAt": None,
                "resolvedBy": None,
                "resolvedAt": None,
            }
        )
    triggers = (
        "job_failure",
        "policy_decision",
        "risk_high",
        "risk_critical",
    )
    rules = [
        await _supervision_rule(session, organization_id, trigger)
        for trigger in triggers
    ]
    counts = Counter(item["status"] for item in escalations)
    return {
        "escalations": escalations,
        "rules": rules,
        "summary": {
            "total": len(escalations),
            "open": counts["open"],
            "acknowledged": counts["acknowledged"],
            "resolved": counts["resolved"],
            "critical": sum(item["severity"] == "critical" for item in escalations),
            "assignedToMe": 0,
        },
        "window": {"limit": 100, "truncated": len(incidents) >= 100},
    }


def _rule_v2(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": {"id": rule["id"], "trigger": rule["trigger"]},
        "behavior": {
            "enabled": rule["enabled"],
            "severity": rule["severity"],
            "notifySupervisor": rule["notifySupervisor"],
        },
        "audit": {
            "updatedBy": rule["updatedBy"],
            "updatedAt": rule["updatedAt"] or None,
        },
        "organizationId": rule["organizationId"],
    }


def _escalation_v2(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "id": item["id"],
            "trigger": item["trigger"],
            "status": item["status"],
            "severity": item["severity"],
            "createdAt": item["createdAt"],
            "updatedAt": item["updatedAt"],
        },
        "organizationId": item["organizationId"],
        "assignment": {
            "workerId": item["workerId"],
            "workerName": item["workerName"],
            "supervisorUserId": item["supervisorUserId"],
            "assignedToUserId": item["assignedToUserId"],
        },
        "source": {
            "type": item["sourceType"],
            "id": item["sourceId"],
            "jobId": item["jobId"],
            "workItemId": item["workItemId"],
            "runId": item["runId"],
            "actionId": item["actionId"],
            "approvalId": item["approvalId"],
            "correlationId": item["correlationId"],
        },
        "details": {
            "title": item["title"],
            "summary": item["summary"],
            "reason": item["reason"],
        },
        "notes": item["notes"],
        "interventions": item["interventions"],
        "incidentId": item["incidentId"],
        "notification": item["notification"],
        "lifecycle": {
            "acknowledgedBy": item["acknowledgedBy"],
            "acknowledgedAt": item["acknowledgedAt"],
            "resolvedBy": item["resolvedBy"],
            "resolvedAt": item["resolvedAt"],
        },
    }


@v1_router.get("/organizations/{organization_id}/supervision")
async def supervision_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "supervision.read")
    return await _supervision(session, organization_id)


@v2_router.get("/organizations/{organization_id}/supervision")
async def supervision_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await supervision_v1(organization_id, principal, session)
    return {
        "data": {
            "items": [_escalation_v2(item) for item in result["escalations"]],
            "rules": [_rule_v2(item) for item in result["rules"]],
            "summary": result["summary"],
            "window": result["window"],
        }
    }


@v1_router.put("/organizations/{organization_id}/supervision/rules/{trigger}")
async def supervision_rule_v1(
    organization_id: UUID,
    trigger: str,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "supervision.manage")
    if trigger not in {"job_failure", "policy_decision", "risk_high", "risk_critical"}:
        raise HTTPException(400, "Invalid supervision trigger.")
    current = await _supervision_rule(session, organization_id, trigger)
    metadata = {
        "trigger": trigger,
        "enabled": bool(payload.get("enabled", current["enabled"])),
        "severity": str(payload.get("severity", current["severity"])),
        "notifySupervisor": bool(
            payload.get("notifySupervisor", current["notifySupervisor"])
        ),
        "updatedBy": str(principal.user_id),
        "updatedAt": utcnow().isoformat(),
    }
    event = await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="supervision.rule.updated",
        category="system",
        resource_type="supervision_rule",
        resource_id=trigger,
        resource_name=trigger,
        outcome="updated",
        summary=f"Supervision rule {trigger} updated.",
        metadata=metadata,
    )
    await session.commit()
    return {"rule": {"id": str(event.id), "organizationId": str(organization_id), **metadata}}


@v2_router.put("/organizations/{organization_id}/supervision/rules/{trigger}")
async def supervision_rule_v2(
    organization_id: UUID,
    trigger: str,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await supervision_rule_v1(
        organization_id, trigger, payload, principal, session
    )
    return {"data": {"rule": _rule_v2(result["rule"])}}


async def _incident_escalation(
    session: AsyncSession, organization_id: UUID, escalation_id: UUID
) -> Incident:
    incident = await session.scalar(
        select(Incident).where(
            Incident.organization_id == organization_id,
            Incident.id == escalation_id,
        )
    )
    if incident is None:
        raise not_found("Escalation")
    return incident


async def _escalation_action(
    session: AsyncSession,
    organization_id: UUID,
    escalation_id: UUID,
    principal: HumanPrincipal,
    event_type: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    require_permission(principal, "supervision.manage")
    incident = await _incident_escalation(session, organization_id, escalation_id)
    if event_type == "supervision.escalation.status":
        status_value = str(metadata.get("status"))
        if status_value in {"acknowledged", "resolved"}:
            incident.status = status_value
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type=event_type,
        category="system",
        resource_type="escalation",
        resource_id=str(escalation_id),
        resource_name=None,
        outcome=str(metadata.get("status") or metadata.get("action") or "updated"),
        summary="Supervision escalation updated.",
        metadata=metadata,
    )
    await session.commit()
    result = await _supervision(session, organization_id)
    return next(item for item in result["escalations"] if item["id"] == str(escalation_id))


@v1_router.post("/organizations/{organization_id}/escalations/{escalation_id}/status")
async def escalation_status_v1(
    organization_id: UUID,
    escalation_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    item = await _escalation_action(
        session,
        organization_id,
        escalation_id,
        principal,
        "supervision.escalation.status",
        {
            "status": str(payload.get("status", "")),
            "note": str(payload.get("note", "")),
            "actorUserId": str(principal.user_id),
        },
    )
    return {"escalation": item}


@v2_router.post("/organizations/{organization_id}/escalations/{escalation_id}/status")
async def escalation_status_v2(
    organization_id: UUID,
    escalation_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await escalation_status_v1(
        organization_id, escalation_id, payload, principal, session
    )
    return {"data": {"escalation": _escalation_v2(result["escalation"])}}


@v1_router.post("/organizations/{organization_id}/escalations/{escalation_id}/notes")
async def escalation_note_v1(
    organization_id: UUID,
    escalation_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    item = await _escalation_action(
        session,
        organization_id,
        escalation_id,
        principal,
        "supervision.escalation.note",
        {
            "note": str(payload.get("note", "")),
            "actorUserId": str(principal.user_id),
        },
    )
    return {"escalation": item}


@v2_router.post("/organizations/{organization_id}/escalations/{escalation_id}/notes")
async def escalation_note_v2(
    organization_id: UUID,
    escalation_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await escalation_note_v1(
        organization_id, escalation_id, payload, principal, session
    )
    return {"data": {"escalation": _escalation_v2(result["escalation"])}}


@v1_router.post("/organizations/{organization_id}/escalations/{escalation_id}/reassign")
async def escalation_reassign_v1(
    organization_id: UUID,
    escalation_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    item = await _escalation_action(
        session,
        organization_id,
        escalation_id,
        principal,
        "supervision.escalation.reassigned",
        {
            "assignedToUserId": str(payload.get("assignedToUserId", "")),
            "actorUserId": str(principal.user_id),
        },
    )
    item["assignedToUserId"] = str(payload.get("assignedToUserId", ""))
    return {"escalation": item}


@v2_router.post("/organizations/{organization_id}/escalations/{escalation_id}/reassign")
async def escalation_reassign_v2(
    organization_id: UUID,
    escalation_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await escalation_reassign_v1(
        organization_id, escalation_id, payload, principal, session
    )
    return {"data": {"escalation": _escalation_v2(result["escalation"])}}


@v1_router.post("/organizations/{organization_id}/escalations/{escalation_id}/intervene")
async def escalation_intervene_v1(
    organization_id: UUID,
    escalation_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    action = str(payload.get("action", ""))
    if action not in {"cancel_run", "pause_worker", "create_incident"}:
        raise HTTPException(400, "Invalid intervention action.")
    item = await _escalation_action(
        session,
        organization_id,
        escalation_id,
        principal,
        "supervision.escalation.intervention",
        {
            "action": action,
            "reason": str(payload.get("reason", "")),
            "actorUserId": str(principal.user_id),
        },
    )
    return {
        "escalation": item,
        "result": f"{action} intervention recorded.",
    }


@v2_router.post("/organizations/{organization_id}/escalations/{escalation_id}/intervene")
async def escalation_intervene_v2(
    organization_id: UUID,
    escalation_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await escalation_intervene_v1(
        organization_id, escalation_id, payload, principal, session
    )
    return {
        "data": {
            "escalation": _escalation_v2(result["escalation"]),
            "result": result["result"],
        }
    }


async def _performance(session: AsyncSession, organization_id: UUID) -> dict[str, Any]:
    workers = list(
        (await session.scalars(select(Worker).where(Worker.organization_id == organization_id))).all()
    )
    items = list(
        (await session.scalars(select(WorkItem).where(WorkItem.organization_id == organization_id))).all()
    )
    runs = list(
        (await session.scalars(select(Run).where(Run.organization_id == organization_id))).all()
    )
    actions = list(
        (await session.scalars(select(Action).where(Action.organization_id == organization_id))).all()
    )
    incidents = list(
        (await session.scalars(select(Incident).where(Incident.organization_id == organization_id))).all()
    )
    jobs = {
        job.id: job
        for job in (
            await session.scalars(select(Job).where(Job.organization_id == organization_id))
        ).all()
    }
    worker_runs: dict[UUID, list[Run]] = defaultdict(list)
    for run in runs:
        item = next((item for item in items if item.id == run.work_item_id), None)
        job = jobs.get(item.job_id) if item else None
        if job:
            worker_runs[job.worker_id].append(run)
    worker_perf = []
    for worker in workers:
        wruns = worker_runs.get(worker.id, [])
        counts = Counter(run.status for run in wruns)
        terminal = counts["completed"] + counts["failed"] + counts["cancelled"]
        worker_perf.append(
            {
                "id": str(worker.id),
                "name": worker.name,
                "department": worker.department,
                "status": worker.status,
                "supervisorUserId": str(worker.supervisor_user_id or ""),
                "runs": len(wruns),
                "completed": counts["completed"],
                "failed": counts["failed"],
                "cancelled": counts["cancelled"],
                "waitingApproval": counts["waiting_approval"],
                "successRate": round(counts["completed"] * 100 / terminal, 1) if terminal else 0,
                "failureRate": round(counts["failed"] * 100 / terminal, 1) if terminal else 0,
                "approvalRate": 0,
                "blockedActions": sum(action.status == "blocked" for action in actions),
                "escalations": 0,
                "incidents": len(incidents),
                "averageRunDurationMs": 0,
                "medianRunDurationMs": 0,
            }
        )
    departments: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in worker_perf:
        departments[item["department"] or "Unassigned"].append(item)
    department_perf = []
    for name, values in departments.items():
        runs_total = sum(item["runs"] for item in values)
        completed = sum(item["completed"] for item in values)
        failed = sum(item["failed"] for item in values)
        cancelled = sum(item["cancelled"] for item in values)
        terminal = completed + failed + cancelled
        department_perf.append(
            {
                "name": name,
                "workers": len(values),
                "runs": runs_total,
                "completed": completed,
                "failed": failed,
                "cancelled": cancelled,
                "successRate": round(completed * 100 / terminal, 1) if terminal else 0,
                "escalations": 0,
                "incidents": len(incidents),
            }
        )
    run_counts = Counter(run.status for run in runs)
    terminal = run_counts["completed"] + run_counts["failed"] + run_counts["cancelled"]
    summary = {
        "workers": len(workers),
        "activeWorkers": sum(worker.status == "active" for worker in workers),
        "runs": len(runs),
        "terminalRuns": terminal,
        "completed": run_counts["completed"],
        "failed": run_counts["failed"],
        "cancelled": run_counts["cancelled"],
        "successRate": round(run_counts["completed"] * 100 / terminal, 1) if terminal else 0,
        "failureRate": round(run_counts["failed"] * 100 / terminal, 1) if terminal else 0,
        "approvalRate": 0,
        "blockedActions": sum(action.status == "blocked" for action in actions),
        "escalations": 0,
        "escalationsPer100Runs": 0,
        "incidents": len(incidents),
        "incidentsPer100Runs": round(len(incidents) * 100 / len(runs), 1) if runs else 0,
        "averageRunDurationMs": 0,
        "medianRunDurationMs": 0,
    }
    activity = []
    for run in sorted(runs, key=lambda r: r.created_at, reverse=True)[:50]:
        item = next((item for item in items if item.id == run.work_item_id), None)
        job = jobs.get(item.job_id) if item else None
        worker = next((worker for worker in workers if job and worker.id == job.worker_id), None)
        activity.append(
            {
                "id": str(run.id),
                "workerId": str(worker.id) if worker else "",
                "workerName": worker.name if worker else "",
                "department": worker.department if worker else "",
                "kind": "run",
                "status": run.status,
                "label": job.name if job else "Run",
                "occurredAt": run.created_at.isoformat(),
                "correlationId": run.correlation_id,
            }
        )
    return {
        "generatedAt": utcnow().isoformat(),
        "summary": summary,
        "workers": worker_perf,
        "departments": department_perf,
        "tools": [],
        "trends": [],
        "activity": activity,
        "window": {
            "runs": {"scanned": len(runs), "limit": 1000, "truncated": False},
            "actions": {"scanned": len(actions), "limit": 1000, "truncated": False},
        },
        "methodology": {
            "score": "bounded operational telemetry",
            "successRate": "completed / terminal runs",
            "approvalRate": "approved / approval decisions",
            "toolReliability": "successful / attempted provider actions",
            "escalationRate": "escalations per 100 runs",
            "incidentRate": "incidents per 100 runs",
            "bounded": True,
        },
    }


@v1_router.get("/organizations/{organization_id}/performance")
async def performance_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "performance.read")
    return await _performance(session, organization_id)


@v2_router.get("/organizations/{organization_id}/performance")
async def performance_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await performance_v1(organization_id, principal, session)
    return {
        "data": {
            "generatedAt": result["generatedAt"],
            "overview": result["summary"],
            "runTrends": result["trends"],
            "workforce": {
                "workers": result["workers"],
                "departments": result["departments"],
            },
            "operations": {
                "tools": result["tools"],
                "activity": result["activity"],
            },
            "windows": result["window"],
            "methodology": result["methodology"],
        }
    }
