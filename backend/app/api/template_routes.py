from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import organization_principal
from app.api.jobs_routes import create_job, job_public
from app.api.product_common import not_found, require_permission
from app.api.workforce_routes import (
    create_role,
    create_worker,
    role_public,
    worker_public,
)
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.database.models import (
    CapabilityProfile,
    Integration,
    WorkforceRole,
)
from app.infrastructure.database.session import database_session

v1_router = APIRouter(tags=["templates"])
v2_router = APIRouter(tags=["templates"])

SECURITY = {
    "autoConnectIntegrations": False,
    "autoDeclareCapabilities": False,
    "autoCreatePolicies": False,
    "autoApproveActions": False,
    "activateWorker": False,
    "activateJobs": False,
}


def _job(
    name: str,
    objective: str,
    scopes: list[str],
    *,
    priority: str = "normal",
) -> dict[str, Any]:
    return {
        "name": name,
        "description": objective,
        "objective": objective,
        "instructions": "Use only connected, bounded Audoryn context and follow policy.",
        "responsibilityLinks": [],
        "requiredCapabilities": scopes,
        "priority": priority,
        "completionCriteria": ["A concise, reviewable deliverable is produced."],
    }


TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "customer-support-assistant",
        "name": "Customer Support Assistant",
        "category": "Customer Support",
        "summary": "Triage customer conversations and prepare supervised support follow-up.",
        "role": {
            "name": "Customer Support Assistant",
            "purpose": "Help a support team understand incoming customer needs.",
            "defaultInstructions": "Prioritize accuracy and escalate sensitive work.",
            "defaultResponsibilities": [
                "Review customer messages",
                "Prepare support context",
            ],
            "recommendedCapabilities": [
                "gmail.messages.recent.read",
                "slack.messages.create",
            ],
        },
        "exampleJobs": [
            _job(
                "Inbox Triage",
                "Prepare a prioritized support triage brief.",
                ["gmail.messages.recent.read"],
            )
        ],
        "requiredIntegrations": [
            {
                "provider": "gmail",
                "label": "Gmail",
                "reason": "Inbox triage uses recent-message context.",
            }
        ],
        "suggestedCapabilities": [
            "gmail.messages.recent.read",
            "slack.messages.create",
        ],
        "recommendedPolicyRules": [],
        "approvalDefaults": [
            {
                "scope": "slack.messages.create",
                "decision": "require_approval",
                "reason": "Human review before posting.",
            }
        ],
    },
    {
        "id": "marketing-coordinator",
        "name": "Marketing Coordinator",
        "category": "Marketing",
        "summary": "Review campaign material and coordinate approved team communication.",
        "role": {
            "name": "Marketing Coordinator",
            "purpose": "Support recurring marketing coordination.",
            "defaultInstructions": "Keep claims grounded and external messaging supervised.",
            "defaultResponsibilities": [
                "Review campaign assets",
                "Prepare campaign summaries",
            ],
            "recommendedCapabilities": [
                "google_drive.files.recent.read",
                "slack.messages.create",
            ],
        },
        "exampleJobs": [
            _job(
                "Campaign Asset Review",
                "Prepare a concise view of recent campaign material.",
                ["google_drive.files.recent.read"],
            )
        ],
        "requiredIntegrations": [
            {
                "provider": "google_drive",
                "label": "Google Drive",
                "reason": "Campaign review uses Drive context.",
            }
        ],
        "suggestedCapabilities": [
            "google_drive.files.recent.read",
            "slack.messages.create",
        ],
        "recommendedPolicyRules": [],
        "approvalDefaults": [],
    },
    {
        "id": "research-assistant",
        "name": "Research Assistant",
        "category": "Research",
        "summary": "Collect bounded source context and prepare research briefs.",
        "role": {
            "name": "Research Assistant",
            "purpose": "Support repeatable research using connected sources.",
            "defaultInstructions": "Separate observed facts from inference.",
            "defaultResponsibilities": ["Collect updates", "Summarize findings"],
            "recommendedCapabilities": [
                "github.repository.issues.read",
                "github.repository.pull_requests.read",
            ],
        },
        "exampleJobs": [
            _job(
                "Repository Research Brief",
                "Prepare a concise technical change brief.",
                [
                    "github.repository.issues.read",
                    "github.repository.pull_requests.read",
                ],
            )
        ],
        "requiredIntegrations": [
            {
                "provider": "github",
                "label": "GitHub",
                "reason": "Repository research uses issues and pull requests.",
            }
        ],
        "suggestedCapabilities": [
            "github.repository.issues.read",
            "github.repository.pull_requests.read",
        ],
        "recommendedPolicyRules": [],
        "approvalDefaults": [],
    },
    {
        "id": "sales-assistant",
        "name": "Sales Assistant",
        "category": "Sales",
        "summary": "Review prospect communication and upcoming meetings.",
        "role": {
            "name": "Sales Assistant",
            "purpose": "Prepare supervised sales follow-up context.",
            "defaultInstructions": "Do not send email or commit the company to terms.",
            "defaultResponsibilities": [
                "Review prospect messages",
                "Prepare meeting briefs",
            ],
            "recommendedCapabilities": [
                "gmail.messages.recent.read",
                "google_calendar.events.upcoming.read",
            ],
        },
        "exampleJobs": [
            _job(
                "Upcoming Meeting Brief",
                "Prepare a concise brief of upcoming sales meetings.",
                ["google_calendar.events.upcoming.read"],
            )
        ],
        "requiredIntegrations": [
            {
                "provider": "google_calendar",
                "label": "Google Calendar",
                "reason": "Meeting briefs use upcoming event context.",
            }
        ],
        "suggestedCapabilities": [
            "gmail.messages.recent.read",
            "google_calendar.events.upcoming.read",
        ],
        "recommendedPolicyRules": [],
        "approvalDefaults": [],
    },
    {
        "id": "operations-assistant",
        "name": "Operations Assistant",
        "category": "Operations",
        "summary": "Monitor operational context and prepare supervised internal updates.",
        "role": {
            "name": "Operations Assistant",
            "purpose": "Support recurring operations coordination.",
            "defaultInstructions": "Surface uncertainty and escalate exceptions.",
            "defaultResponsibilities": [
                "Review operational context",
                "Prepare internal updates",
            ],
            "recommendedCapabilities": [
                "google_drive.files.recent.read",
                "google_calendar.events.upcoming.read",
                "slack.messages.create",
            ],
        },
        "exampleJobs": [
            _job(
                "Daily Operations Brief",
                "Prepare a concise daily operations brief.",
                [
                    "google_drive.files.recent.read",
                    "google_calendar.events.upcoming.read",
                ],
            )
        ],
        "requiredIntegrations": [],
        "suggestedCapabilities": [
            "google_drive.files.recent.read",
            "google_calendar.events.upcoming.read",
            "slack.messages.create",
        ],
        "recommendedPolicyRules": [],
        "approvalDefaults": [],
    },
    {
        "id": "developer-assistant",
        "name": "Developer Assistant",
        "category": "Engineering",
        "summary": "Review repository work and prepare approved engineering follow-up.",
        "role": {
            "name": "Developer Assistant",
            "purpose": "Support engineering coordination with explicit write controls.",
            "defaultInstructions": "Use repository evidence and keep writes approval-gated.",
            "defaultResponsibilities": [
                "Review issues",
                "Summarize engineering changes",
            ],
            "recommendedCapabilities": [
                "github.repository.issues.read",
                "github.repository.pull_requests.read",
                "github.repository.issues.create",
            ],
        },
        "exampleJobs": [
            _job(
                "Engineering Change Review",
                "Prepare a concise engineering status brief.",
                [
                    "github.repository.issues.read",
                    "github.repository.pull_requests.read",
                ],
            )
        ],
        "requiredIntegrations": [
            {
                "provider": "github",
                "label": "GitHub",
                "reason": "Engineering review uses the connected repository.",
            }
        ],
        "suggestedCapabilities": [
            "github.repository.issues.read",
            "github.repository.pull_requests.read",
            "github.repository.issues.create",
        ],
        "recommendedPolicyRules": [],
        "approvalDefaults": [
            {
                "scope": "github.repository.issues.create",
                "decision": "require_approval",
                "reason": "Human review before repository writes.",
            }
        ],
    },
)


async def _readiness(
    session: AsyncSession, organization_id: UUID, template: dict[str, Any]
) -> dict[str, Any]:
    integrations = list(
        (
            await session.scalars(
                select(Integration).where(
                    Integration.organization_id == organization_id
                )
            )
        ).all()
    )
    connected = {
        integration.provider
        for integration in integrations
        if integration.status == "connected"
    }
    scopes = set(
        (
            await session.scalars(
                select(CapabilityProfile.scope).where(
                    CapabilityProfile.organization_id == organization_id,
                    CapabilityProfile.active.is_(True),
                )
            )
        ).all()
    )
    required = [
        {**item, "connected": item["provider"] in connected}
        for item in template["requiredIntegrations"]
    ]
    suggested = [
        {"scope": scope, "available": scope in scopes}
        for scope in template["suggestedCapabilities"]
    ]
    jobs = []
    for job in template["exampleJobs"]:
        missing = [
            scope
            for scope in job["requiredCapabilities"]
            if scope not in scopes
        ]
        jobs.append(
            {
                "name": job["name"],
                "ready": not missing,
                "missingCapabilities": missing,
            }
        )
    return {
        "requiredIntegrations": required,
        "suggestedCapabilities": suggested,
        "jobs": jobs,
        "missingIntegrations": [
            item["provider"] for item in required if not item["connected"]
        ],
        "unavailableCapabilities": [
            item["scope"] for item in suggested if not item["available"]
        ],
        "security": SECURITY,
    }


async def _items(
    session: AsyncSession, organization_id: UUID
) -> list[dict[str, Any]]:
    result = []
    for template in TEMPLATES:
        result.append(
            {
                **template,
                "readiness": await _readiness(
                    session, organization_id, template
                ),
            }
        )
    return result


def _v2(template: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "id": template["id"],
            "name": template["name"],
            "category": template["category"],
        },
        "summary": template["summary"],
        "blueprint": {
            "role": template["role"],
            "jobs": template["exampleJobs"],
            "requiredIntegrations": template["requiredIntegrations"],
            "suggestedCapabilities": template["suggestedCapabilities"],
            "recommendedPolicyRules": template["recommendedPolicyRules"],
            "approvalDefaults": template["approvalDefaults"],
        },
        "readiness": template["readiness"],
    }


@v1_router.get("/organizations/{organization_id}/workforce/templates")
async def list_templates_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "workforce.read")
    items = await _items(session, organization_id)
    return {
        "templates": items,
        "count": len(items),
        "security": SECURITY,
    }


@v2_router.get("/organizations/{organization_id}/workforce/templates")
async def list_templates_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "workforce.read")
    items = await _items(session, organization_id)
    return {
        "data": {
            "items": [_v2(item) for item in items],
            "total": len(items),
            "security": SECURITY,
        }
    }


async def _apply(
    session: AsyncSession,
    organization_id: UUID,
    template_id: str,
    principal: HumanPrincipal,
    payload: dict[str, Any],
) -> dict[str, Any]:
    require_permission(principal, "workforce.manage")
    require_permission(principal, "jobs.manage")
    template = next(
        (
            item
            for item in await _items(session, organization_id)
            if item["id"] == template_id
        ),
        None,
    )
    if template is None:
        raise not_found("Workforce template")

    role_input = template["role"]
    role = await session.scalar(
        select(WorkforceRole).where(
            WorkforceRole.organization_id == organization_id,
            WorkforceRole.name == role_input["name"],
        )
    )
    if role is None:
        role = await create_role(
            session, organization_id, principal, role_input
        )

    worker = await create_worker(
        session,
        organization_id,
        principal,
        {
            **payload,
            "roleId": str(role.id),
            "description": template["summary"],
            "responsibilities": role_input["defaultResponsibilities"],
            "instructions": role_input["defaultInstructions"],
        },
    )

    jobs: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    readiness = {
        item["name"]: item for item in template["readiness"]["jobs"]
    }
    for definition in template["exampleJobs"]:
        state = readiness.get(
            definition["name"],
            {"ready": True, "missingCapabilities": []},
        )
        if not state["ready"]:
            blocked.append(
                {
                    "name": definition["name"],
                    "reason": "Required capability is not available from a connected integration.",
                    "missingCapabilities": state["missingCapabilities"],
                }
            )
            continue
        job = await create_job(
            session,
            organization_id,
            principal,
            {"workerId": str(worker.id), **definition},
        )
        jobs.append(await job_public(session, job))

    return {
        "template": template,
        "role": role_public(role),
        "worker": worker_public(worker),
        "jobs": jobs,
        "blockedJobs": blocked,
        "security": SECURITY,
        "nextSteps": {
            "missingIntegrations": template["readiness"][
                "missingIntegrations"
            ],
            "unavailableCapabilities": template["readiness"][
                "unavailableCapabilities"
            ],
            "recommendedPolicyRules": template[
                "recommendedPolicyRules"
            ],
            "approvalDefaults": template["approvalDefaults"],
        },
    }


@v1_router.post(
    "/organizations/{organization_id}/workforce/templates/{template_id}/apply",
    status_code=201,
)
async def apply_template_v1(
    organization_id: UUID,
    template_id: str,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return await _apply(
        session, organization_id, template_id, principal, payload
    )


@v2_router.post(
    "/organizations/{organization_id}/workforce/templates/{template_id}/apply",
    status_code=201,
)
async def apply_template_v2(
    organization_id: UUID,
    template_id: str,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await _apply(
        session, organization_id, template_id, principal, payload
    )
    return {
        "data": {
            "template": _v2(result["template"]),
            "created": {
                "role": result["role"],
                "worker": result["worker"],
                "jobs": result["jobs"],
            },
            "blockedJobs": result["blockedJobs"],
            "security": result["security"],
            "nextSteps": result["nextSteps"],
        }
    }
