from __future__ import annotations

import hashlib
from collections import Counter
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import organization_principal
from app.api.integration_capability_routes import _catalog, _profile
from app.api.product_common import (
    append_audit,
    audit_public,
    not_found,
    require_permission,
    utcnow,
)
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.auth.agent_credentials import AgentCredentialAuthenticator
from app.infrastructure.database.models import (
    Action,
    AgentIdentity,
    Approval,
    AuditEvent,
    ExecutionControl,
    Incident,
    Policy,
    PolicyRevision,
    RiskEvent,
)
from app.infrastructure.database.session import database_session

v1_router = APIRouter(tags=["governance"])
v2_router = APIRouter(tags=["governance"])

EFFECT_WEIGHT = {"allow": 1, "require_approval": 2, "deny": 3}
OUTCOME = {
    "allow": "ALLOW",
    "require_approval": "REQUIRE_APPROVAL",
    "deny": "DENY",
}
RISK_ORDER = ("low", "medium", "high", "critical")


async def _policy_revision(session: AsyncSession, policy: Policy) -> PolicyRevision:
    revision = await session.scalar(
        select(PolicyRevision).where(
            PolicyRevision.policy_id == policy.id,
            PolicyRevision.revision == policy.current_revision,
        )
    )
    if revision is None:
        raise HTTPException(500, "Policy revision is unavailable.")
    return revision


async def _policy_public(
    session: AsyncSession, policy: Policy
) -> dict[str, Any]:
    revision = await _policy_revision(session, policy)
    selectors = dict(revision.selectors or {})
    meta = selectors.pop("_meta", {})
    if not isinstance(meta, dict):
        meta = {}
    return {
        "id": str(policy.id),
        "organizationId": str(policy.organization_id),
        "name": policy.name,
        "description": str(meta.get("description", "")),
        "effect": revision.effect,
        "priority": revision.priority,
        "status": policy.status,
        "selectors": {
            "agentIds": list(selectors.get("agentIds", [])),
            "resourceIds": list(selectors.get("resourceIds", [])),
            "actions": list(selectors.get("actions", [])),
            "scopes": list(selectors.get("scopes", [])),
            "risks": list(selectors.get("risks", [])),
        },
        "revision": policy.current_revision,
        "createdBy": str(meta.get("createdBy", "")),
        "createdAt": str(
            meta.get("createdAt") or policy.created_at.isoformat()
        ),
        "updatedBy": str(meta.get("updatedBy", "")),
        "updatedAt": policy.updated_at.isoformat(),
    }


def _policy_v2(public: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "id": public["id"],
            "name": public["name"],
            "status": public["status"],
            "revision": public["revision"],
        },
        "decision": {
            "effect": public["effect"],
            "priority": public["priority"],
        },
        "selectors": public["selectors"],
        "description": public["description"],
        "ownership": {
            "organizationId": public["organizationId"],
            "createdBy": public["createdBy"],
            "updatedBy": public["updatedBy"],
        },
        "timestamps": {
            "createdAt": public["createdAt"],
            "updatedAt": public["updatedAt"],
        },
    }


def _selectors(payload: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("selectors")
    raw = raw if isinstance(raw, dict) else {}
    return {
        "agentIds": list(raw.get("agentIds", [])),
        "resourceIds": list(raw.get("resourceIds", [])),
        "actions": list(raw.get("actions", [])),
        "scopes": list(raw.get("scopes", [])),
        "risks": list(raw.get("risks", [])),
        "_meta": meta,
    }


async def _create_policy(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    payload: dict[str, Any],
) -> Policy:
    require_permission(principal, "policies.manage")
    name = str(payload.get("name", "")).strip()
    effect = str(payload.get("effect", "deny"))
    priority = int(payload.get("priority", 100))
    if len(name) < 2:
        raise HTTPException(400, "Policy name must be at least 2 characters.")
    if effect not in EFFECT_WEIGHT:
        raise HTTPException(400, "Invalid policy effect.")
    if priority < 0 or priority > 1000:
        raise HTTPException(400, "Policy priority must be between 0 and 1000.")

    now = utcnow()
    policy = Policy(
        organization_id=organization_id,
        name=name,
        status="enabled",
        current_revision=1,
        created_at=now,
        updated_at=now,
    )
    session.add(policy)
    await session.flush()
    meta = {
        "description": str(payload.get("description", "")),
        "createdBy": str(principal.user_id),
        "updatedBy": str(principal.user_id),
        "createdAt": now.isoformat(),
    }
    session.add(
        PolicyRevision(
            policy_id=policy.id,
            revision=1,
            effect=effect,
            priority=priority,
            selectors=_selectors(payload, meta),
            created_at=now,
        )
    )
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="policy.created",
        category="policy",
        resource_type="policy",
        resource_id=str(policy.id),
        resource_name=policy.name,
        outcome="enabled",
        summary=f"Policy {policy.name} created.",
        metadata={"effect": effect, "priority": priority},
    )
    await session.commit()
    await session.refresh(policy)
    return policy


async def _update_policy(
    session: AsyncSession,
    organization_id: UUID,
    policy_id: UUID,
    principal: HumanPrincipal,
    payload: dict[str, Any],
) -> Policy:
    require_permission(principal, "policies.manage")
    policy = await session.scalar(
        select(Policy).where(
            Policy.organization_id == organization_id,
            Policy.id == policy_id,
        )
    )
    if policy is None:
        raise not_found("Policy")
    previous = await _policy_revision(session, policy)
    previous_selectors = dict(previous.selectors or {})
    previous_meta = previous_selectors.get("_meta")
    previous_meta = (
        previous_meta if isinstance(previous_meta, dict) else {}
    )
    effect = str(payload.get("effect", previous.effect))
    priority = int(payload.get("priority", previous.priority))
    if effect not in EFFECT_WEIGHT:
        raise HTTPException(400, "Invalid policy effect.")
    if payload.get("name"):
        policy.name = str(payload["name"]).strip() or policy.name
    policy.current_revision += 1
    now = utcnow()
    meta = {
        **previous_meta,
        "description": str(
            payload.get("description", previous_meta.get("description", ""))
        ),
        "updatedBy": str(principal.user_id),
    }
    if "createdBy" not in meta:
        meta["createdBy"] = str(principal.user_id)
    if "createdAt" not in meta:
        meta["createdAt"] = policy.created_at.isoformat()
    effective_payload = dict(payload)
    if "selectors" not in effective_payload:
        effective_payload["selectors"] = {
            key: previous_selectors.get(key, [])
            for key in ("agentIds", "resourceIds", "actions", "scopes", "risks")
        }
    session.add(
        PolicyRevision(
            policy_id=policy.id,
            revision=policy.current_revision,
            effect=effect,
            priority=priority,
            selectors=_selectors(effective_payload, meta),
            created_at=now,
        )
    )
    policy.updated_at = now
    await session.commit()
    await session.refresh(policy)
    return policy


def _matches(
    policy: dict[str, Any],
    *,
    agent_id: str,
    resource_id: str,
    action: str,
    scope: str,
    risk: str,
) -> bool:
    selectors = policy["selectors"]
    dimensions = (
        ("agentIds", agent_id),
        ("resourceIds", resource_id),
        ("actions", action),
        ("scopes", scope),
        ("risks", risk),
    )
    for key, value in dimensions:
        values = selectors.get(key, [])
        if values and value not in values:
            return False
    return True


def _risk_for_capability(
    capability: dict[str, Any],
    *,
    agent_id: str,
    agent_name: str,
    resource_id: str,
    resource_name: str,
) -> dict[str, Any]:
    baseline = str(capability.get("risk", "low"))
    now = utcnow().isoformat()
    return {
        "baselineRisk": baseline,
        "effectiveRisk": baseline,
        "elevationSteps": 0,
        "evaluatedAt": now,
        "signals": [],
        "counters": {
            "requests10m": 0,
            "blocked30m": 0,
            "failed30m": 0,
            "approval30m": 0,
        },
        "window": {
            "scanned": 0,
            "agentMatches": 0,
            "limit": 100,
            "truncated": False,
        },
        "context": {
            "agentId": agent_id,
            "agentName": agent_name,
            "resourceId": resource_id,
            "resourceName": resource_name,
            "scope": capability["scope"],
            "action": capability["action"],
        },
    }


def _risk_v2(assessment: dict[str, Any]) -> dict[str, Any]:
    return {
        "context": assessment["context"],
        "risk": {
            "baseline": assessment["baselineRisk"],
            "effective": assessment["effectiveRisk"],
            "elevationSteps": assessment["elevationSteps"],
            "evaluatedAt": assessment["evaluatedAt"],
        },
        "signals": assessment["signals"],
        "counters": assessment["counters"],
        "window": assessment["window"],
    }


async def _evaluate(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    *,
    agent_id: UUID,
    resource_id: str,
    scope: str,
) -> dict[str, Any]:
    require_permission(principal, "policies.read")
    agent = await session.scalar(
        select(AgentIdentity).where(
            AgentIdentity.organization_id == organization_id,
            AgentIdentity.id == agent_id,
        )
    )
    request = {
        "agentId": str(agent_id),
        "resourceId": resource_id,
        "scope": scope,
    }
    default_preconditions = {
        "agentActive": bool(agent and agent.status == "active"),
        "resourceLive": False,
        "scopeAvailable": False,
        "scopeDeclared": False,
    }
    if agent is None:
        return {
            "outcome": "DENY",
            "reason": "Agent identity is unavailable. Default deny applies.",
            "evaluatedAt": utcnow().isoformat(),
            "request": {
                **request,
                "agentName": None,
                "resourceName": None,
                "action": None,
                "target": None,
                "risk": None,
            },
            "preconditions": default_preconditions,
            "winningPolicy": None,
            "matchedPolicies": [],
            "riskAssessment": None,
        }

    catalog = await _catalog(session, organization_id)
    resource = next(
        (item for item in catalog["resources"] if item["id"] == resource_id),
        None,
    )
    if resource is None:
        return {
            "outcome": "DENY",
            "reason": "Resource is not present in the live capability catalog. Default deny applies.",
            "evaluatedAt": utcnow().isoformat(),
            "request": {
                **request,
                "agentName": agent.name,
                "resourceName": None,
                "action": None,
                "target": None,
                "risk": None,
            },
            "preconditions": default_preconditions,
            "winningPolicy": None,
            "matchedPolicies": [],
            "riskAssessment": None,
        }
    capability = next(
        (item for item in resource["actions"] if item["scope"] == scope),
        None,
    )
    if capability is None:
        return {
            "outcome": "DENY",
            "reason": "Requested scope is not available on this resource. Default deny applies.",
            "evaluatedAt": utcnow().isoformat(),
            "request": {
                **request,
                "agentName": agent.name,
                "resourceName": resource["displayName"],
                "action": None,
                "target": None,
                "risk": None,
            },
            "preconditions": {
                **default_preconditions,
                "resourceLive": resource["status"] == "connected",
            },
            "winningPolicy": None,
            "matchedPolicies": [],
            "riskAssessment": None,
        }

    profile = await _profile(session, organization_id, agent_id)
    declared = scope in profile["activeScopes"]
    assessment = _risk_for_capability(
        capability,
        agent_id=str(agent_id),
        agent_name=agent.name,
        resource_id=resource_id,
        resource_name=resource["displayName"],
    )
    preconditions = {
        "agentActive": agent.status == "active",
        "resourceLive": resource["status"] == "connected",
        "scopeAvailable": True,
        "scopeDeclared": declared,
    }
    details = {
        "agentName": agent.name,
        "resourceName": resource["displayName"],
        "action": capability["action"],
        "target": capability["target"],
        "risk": assessment["effectiveRisk"],
    }
    if not all(preconditions.values()):
        reason = (
            "Agent identity is not active. Default deny applies."
            if not preconditions["agentActive"]
            else "Resource health is not fully connected. Audoryn fails closed."
            if not preconditions["resourceLive"]
            else "Agent has not declared this live capability scope. Default deny applies."
        )
        return {
            "outcome": "DENY",
            "reason": reason,
            "evaluatedAt": utcnow().isoformat(),
            "request": {**request, **details},
            "preconditions": preconditions,
            "winningPolicy": None,
            "matchedPolicies": [],
            "riskAssessment": assessment,
        }

    policies = list(
        (
            await session.scalars(
                select(Policy).where(
                    Policy.organization_id == organization_id,
                    Policy.status == "enabled",
                )
            )
        ).all()
    )
    public = [await _policy_public(session, item) for item in policies]
    matched = [
        item
        for item in public
        if _matches(
            item,
            agent_id=str(agent_id),
            resource_id=resource_id,
            action=capability["action"],
            scope=scope,
            risk=assessment["effectiveRisk"],
        )
    ]
    if not matched:
        return {
            "outcome": "DENY",
            "reason": "No enabled policy matches this request. Default deny applies.",
            "evaluatedAt": utcnow().isoformat(),
            "request": {**request, **details},
            "preconditions": preconditions,
            "winningPolicy": None,
            "matchedPolicies": [],
            "riskAssessment": assessment,
        }

    highest = max(item["priority"] for item in matched)
    contenders = [item for item in matched if item["priority"] == highest]
    contenders.sort(
        key=lambda item: (
            -EFFECT_WEIGHT[item["effect"]],
            item["name"],
            item["id"],
        )
    )
    winner = contenders[0]
    summaries = [
        {
            "id": item["id"],
            "name": item["name"],
            "effect": item["effect"],
            "priority": item["priority"],
            "revision": item["revision"],
        }
        for item in matched
    ]
    return {
        "outcome": OUTCOME[winner["effect"]],
        "reason": (
            f"Matched {len(contenders)} policy"
            f"{'' if len(contenders) == 1 else 'ies'} at highest priority {highest}. "
            "Safety precedence resolves ties as DENY > REQUIRE_APPROVAL > ALLOW."
        ),
        "evaluatedAt": utcnow().isoformat(),
        "request": {**request, **details},
        "preconditions": preconditions,
        "winningPolicy": {
            "id": winner["id"],
            "name": winner["name"],
            "effect": winner["effect"],
            "priority": winner["priority"],
            "revision": winner["revision"],
        },
        "matchedPolicies": summaries,
        "riskAssessment": assessment,
    }


def _decision_v2(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision": {
            "outcome": decision["outcome"],
            "reason": decision["reason"],
            "evaluatedAt": decision["evaluatedAt"],
        },
        "request": decision["request"],
        "riskAssessment": decision["riskAssessment"],
        "preconditions": decision["preconditions"],
        "resolution": {
            "winningPolicy": decision["winningPolicy"],
            "matchedPolicies": decision["matchedPolicies"],
        },
    }


@v1_router.get("/organizations/{organization_id}/policies")
async def policies_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "policies.read")
    policies = list(
        (
            await session.scalars(
                select(Policy)
                .where(Policy.organization_id == organization_id)
                .order_by(desc(Policy.created_at))
            )
        ).all()
    )
    items = [await _policy_public(session, item) for item in policies]
    return {"policies": items, "count": len(items)}


@v2_router.get("/organizations/{organization_id}/policies")
async def policies_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await policies_v1(organization_id, principal, session)
    return {
        "items": [_policy_v2(item) for item in result["policies"]],
        "total": result["count"],
    }


@v1_router.post("/organizations/{organization_id}/policies", status_code=201)
async def create_policy_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "policy": await _policy_public(
            session,
            await _create_policy(session, organization_id, principal, payload),
        )
    }


@v2_router.post("/organizations/{organization_id}/policies", status_code=201)
async def create_policy_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    policy = await _create_policy(session, organization_id, principal, payload)
    return {"data": {"policy": _policy_v2(await _policy_public(session, policy))}}


@v1_router.put("/organizations/{organization_id}/policies/{policy_id}")
async def update_policy_v1(
    organization_id: UUID,
    policy_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    policy = await _update_policy(
        session, organization_id, policy_id, principal, payload
    )
    return {"policy": await _policy_public(session, policy)}


@v2_router.put("/organizations/{organization_id}/policies/{policy_id}")
async def update_policy_v2(
    organization_id: UUID,
    policy_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    policy = await _update_policy(
        session, organization_id, policy_id, principal, payload
    )
    return {"data": {"policy": _policy_v2(await _policy_public(session, policy))}}


@v1_router.post("/organizations/{organization_id}/policies/{policy_id}/status")
async def policy_status_v1(
    organization_id: UUID,
    policy_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "policies.manage")
    policy = await session.scalar(
        select(Policy).where(
            Policy.organization_id == organization_id, Policy.id == policy_id
        )
    )
    if policy is None:
        raise not_found("Policy")
    new_status = str(payload.get("status", ""))
    if new_status not in {"enabled", "disabled"}:
        raise HTTPException(400, "Invalid policy status.")
    policy.status = new_status
    await session.commit()
    await session.refresh(policy)
    return {"policy": await _policy_public(session, policy)}


@v2_router.post("/organizations/{organization_id}/policies/{policy_id}/status")
async def policy_status_v2(
    organization_id: UUID,
    policy_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await policy_status_v1(
        organization_id, policy_id, payload, principal, session
    )
    return {"data": {"policy": _policy_v2(result["policy"])}}


@v1_router.post("/organizations/{organization_id}/policy-evaluations")
async def evaluate_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    try:
        agent_id = UUID(str(payload.get("agentId")))
    except ValueError as error:
        raise HTTPException(400, "Policy evaluation requires a valid agentId.") from error
    resource_id = str(payload.get("resourceId", "")).strip()
    scope = str(payload.get("scope", "")).strip()
    if not resource_id or not scope:
        raise HTTPException(400, "Policy evaluation requires resourceId and scope.")
    return {
        "decision": await _evaluate(
            session,
            organization_id,
            principal,
            agent_id=agent_id,
            resource_id=resource_id,
            scope=scope,
        )
    }


@v2_router.post("/organizations/{organization_id}/policy-evaluations")
async def evaluate_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await evaluate_v1(
        organization_id, payload, principal, session
    )
    return {"data": _decision_v2(result["decision"])}


async def _assess_risk(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    payload: dict[str, Any],
    *,
    persist: bool = True,
) -> dict[str, Any]:
    require_permission(principal, "risk.read")
    try:
        agent_id = UUID(str(payload.get("agentId")))
    except ValueError as error:
        raise HTTPException(400, "Risk assessment requires a valid agentId.") from error
    resource_id = str(payload.get("resourceId", "")).strip()
    scope = str(payload.get("scope", "")).strip()
    catalog = await _catalog(session, organization_id)
    resource = next(
        (item for item in catalog["resources"] if item["id"] == resource_id),
        None,
    )
    if resource is None:
        raise not_found("Capability resource")
    capability = next(
        (item for item in resource["actions"] if item["scope"] == scope),
        None,
    )
    if capability is None:
        raise not_found("Capability scope")
    agent = await session.scalar(
        select(AgentIdentity).where(
            AgentIdentity.organization_id == organization_id,
            AgentIdentity.id == agent_id,
        )
    )
    if agent is None:
        raise not_found("Agent Identity")
    assessment = _risk_for_capability(
        capability,
        agent_id=str(agent.id),
        agent_name=agent.name,
        resource_id=resource_id,
        resource_name=resource["displayName"],
    )
    if persist:
        event = RiskEvent(
            organization_id=organization_id,
            agent_id=agent.id,
            effective_risk=assessment["effectiveRisk"],
            factors=assessment,
        )
        session.add(event)
        await session.commit()
    return assessment


@v1_router.post("/organizations/{organization_id}/risk-assessments")
async def assess_risk_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "assessment": await _assess_risk(
            session, organization_id, principal, payload
        )
    }


@v2_router.post("/organizations/{organization_id}/risk-assessments")
async def assess_risk_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": _risk_v2(
            await _assess_risk(
                session, organization_id, principal, payload
            )
        )
    }


@v1_router.get("/organizations/{organization_id}/risk")
async def risk_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "risk.read")
    events = list(
        (
            await session.scalars(
                select(RiskEvent)
                .where(RiskEvent.organization_id == organization_id)
                .order_by(desc(RiskEvent.created_at))
                .limit(100)
            )
        ).all()
    )
    assessments: list[dict[str, Any]] = []
    for event in events:
        factors: dict[str, Any] = (
            dict(event.factors) if isinstance(event.factors, dict) else {}
        )
        raw_context = factors.get("context")
        context: dict[str, Any] = (
            dict(raw_context) if isinstance(raw_context, dict) else {}
        )
        assessments.append(
            {
                "actionId": str(factors.get("actionId", event.id)),
                "correlationId": str(factors.get("correlationId", "")),
                "status": str(factors.get("status", "assessed")),
                "agent": {
                    "id": str(event.agent_id),
                    "name": str(context.get("agentName", "")),
                },
                "resource": {
                    "id": str(context.get("resourceId", "")),
                    "name": context.get("resourceName"),
                },
                "scope": str(context.get("scope", "")),
                "assessment": factors,
            }
        )
    counts = Counter(event.effective_risk for event in events)
    return {
        "assessments": assessments,
        "summary": {
            "assessedActions": len(events),
            "elevated": sum(event.effective_risk in {"medium", "high", "critical"} for event in events),
            "activeSignalHits": sum(
                len(dict(event.factors).get("signals", []))
                if isinstance(event.factors, dict)
                else 0
                for event in events
            ),
            "highOrCritical": counts["high"] + counts["critical"],
            "byEffectiveRisk": {
                risk: counts[risk] for risk in RISK_ORDER
            },
            "latestAt": events[0].created_at.isoformat() if events else None,
        },
        "window": {
            "scanned": len(events),
            "limit": 100,
            "truncated": False,
        },
        "thresholds": {
            "burst": {"count": 10, "minutes": 10},
            "blocks": {"count": 5, "minutes": 30},
            "failures": {"count": 5, "minutes": 30},
            "approvals": {"count": 5, "minutes": 30},
            "truncatedHistoryElevation": True,
        },
    }


@v2_router.get("/organizations/{organization_id}/risk")
async def risk_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await risk_v1(organization_id, principal, session)
    return {
        "data": {
            "items": [
                {
                    "identity": {
                        "actionId": item["actionId"],
                        "correlationId": item["correlationId"],
                        "status": item["status"],
                    },
                    "actor": item["agent"],
                    "resource": item["resource"],
                    "scope": item["scope"],
                    "assessment": _risk_v2(item["assessment"]),
                }
                for item in result["assessments"]
            ],
            "summary": result["summary"],
            "window": result["window"],
            "thresholds": result["thresholds"],
        }
    }


async def _action_public(
    session: AsyncSession, action: Action
) -> dict[str, Any]:
    data: dict[str, Any] = (
        dict(action.payload) if isinstance(action.payload, dict) else {}
    )
    agent = await session.get(AgentIdentity, action.agent_id)
    approval = await session.scalar(
        select(Approval).where(Approval.action_id == action.id)
    )
    raw_request = data.get("request")
    request: dict[str, Any] = (
        dict(raw_request) if isinstance(raw_request, dict) else {}
    )
    return {
        "id": str(action.id),
        "organizationId": str(action.organization_id),
        "agent": {
            "id": str(action.agent_id),
            "name": agent.name if agent else "",
            "credentialFingerprint": str(data.get("credentialFingerprint", "")),
        },
        "request": {
            "resourceId": action.resource_id,
            "resourceName": request.get("resourceName"),
            "integrationId": request.get("integrationId"),
            "provider": request.get("provider"),
            "scope": action.scope,
            "action": request.get("action"),
            "target": request.get("target"),
            "risk": request.get("risk"),
            "providerOperation": request.get("providerOperation"),
            "input": request.get("input") if isinstance(request.get("input"), dict) else {},
        },
        "status": action.status,
        "correlationId": str(data.get("correlationId", "")),
        "idempotencyHash": str(
            data.get(
                "idempotencyHash",
                hashlib.sha256(action.idempotency_key.encode()).hexdigest(),
            )
        ),
        "policy": data.get("policy")
        if isinstance(data.get("policy"), dict)
        else {
            "outcome": "DENY",
            "reason": "No policy decision recorded.",
            "winningPolicy": None,
            "matchedPolicies": [],
        },
        "riskAssessment": data.get("riskAssessment"),
        "result": data.get("result"),
        "error": data.get("error"),
        "requestedAt": str(
            data.get("requestedAt") or action.created_at.isoformat()
        ),
        "completedAt": data.get("completedAt"),
        "approvalId": str(approval.id) if approval else None,
    }


def _action_v2(public: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "id": public["id"],
            "status": public["status"],
            "correlationId": public["correlationId"],
            "requestedAt": public["requestedAt"],
            "completedAt": public["completedAt"],
        },
        "actor": public["agent"],
        "request": public["request"],
        "risk": public["riskAssessment"],
        "authorization": public["policy"],
        "execution": {
            "result": public["result"],
            "error": public["error"],
            "idempotencyHash": public["idempotencyHash"],
            "approvalId": public["approvalId"],
        },
        "organizationId": public["organizationId"],
    }


@v1_router.get("/organizations/{organization_id}/actions")
async def actions_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "actions.read")
    actions = list(
        (
            await session.scalars(
                select(Action)
                .where(Action.organization_id == organization_id)
                .order_by(desc(Action.created_at))
                .limit(100)
            )
        ).all()
    )
    public = [await _action_public(session, item) for item in actions]
    return {"actions": public, "count": len(public)}


@v2_router.get("/organizations/{organization_id}/actions")
async def actions_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await actions_v1(organization_id, principal, session)
    return {
        "items": [_action_v2(item) for item in result["actions"]],
        "total": result["count"],
    }


async def _gateway_test(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    payload: dict[str, Any],
) -> Action:
    require_permission(principal, "actions.manage")
    try:
        agent_id = UUID(str(payload.get("agentId")))
    except ValueError as error:
        raise HTTPException(400, "Test action requires a valid agentId.") from error
    credential = str(payload.get("credential", ""))
    if not credential:
        raise HTTPException(400, "Test action requires an agent credential.")
    try:
        agent_principal = await AgentCredentialAuthenticator(session).authenticate(
            organization_id=organization_id,
            agent_id=agent_id,
            secret=credential,
        )
    except Exception as error:
        raise HTTPException(401, "Agent credential authentication failed.") from error

    resource_id = str(payload.get("resourceId", "")).strip()
    scope = str(payload.get("scope", "")).strip()
    idempotency_key = str(payload.get("idempotencyKey", "")).strip()
    if not resource_id or not scope or not idempotency_key:
        raise HTTPException(
            400, "resourceId, scope, and idempotencyKey are required."
        )
    existing = await session.scalar(
        select(Action).where(
            Action.organization_id == organization_id,
            Action.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing

    decision = await _evaluate(
        session,
        organization_id,
        principal,
        agent_id=agent_id,
        resource_id=resource_id,
        scope=scope,
    )
    request = decision["request"]
    assessment = decision["riskAssessment"]
    now = utcnow()
    correlation_id = str(payload.get("correlationId") or uuid4())
    outcome = decision["outcome"]
    status_value = (
        "blocked"
        if outcome == "DENY"
        else "held"
        if outcome == "REQUIRE_APPROVAL"
        else "executed"
    )
    result = (
        {
            "providerOperation": request.get("action") or "authorization.test",
            "summary": "Authorization test passed; no external side effect was executed.",
            "executedAt": now.isoformat(),
            "providerRequestId": None,
            "data": {"dryRun": True},
        }
        if status_value == "executed"
        else None
    )
    action = Action(
        organization_id=organization_id,
        agent_id=agent_principal.agent_id,
        run_id=None,
        status=status_value,
        resource_id=resource_id,
        scope=scope,
        idempotency_key=idempotency_key,
        payload={
            "credentialFingerprint": agent_principal.credential_fingerprint,
            "correlationId": correlation_id,
            "idempotencyHash": hashlib.sha256(idempotency_key.encode()).hexdigest(),
            "request": {
                **request,
                "integrationId": resource_id,
                "provider": None,
                "providerOperation": request.get("action"),
                "input": {},
            },
            "policy": {
                "outcome": decision["outcome"],
                "reason": decision["reason"],
                "winningPolicy": decision["winningPolicy"],
                "matchedPolicies": decision["matchedPolicies"],
            },
            "riskAssessment": assessment,
            "result": result,
            "error": decision["reason"] if status_value == "blocked" else None,
            "requestedAt": now.isoformat(),
            "completedAt": now.isoformat() if status_value in {"executed", "blocked"} else None,
        },
    )
    session.add(action)
    await session.flush()
    if outcome == "REQUIRE_APPROVAL":
        session.add(
            Approval(
                organization_id=organization_id,
                action_id=action.id,
                status="pending",
                decided_by=None,
                decision_reason=None,
            )
        )
    await append_audit(
        session,
        organization_id=organization_id,
        principal=None,
        actor={
            "type": "agent",
            "id": str(agent_id),
            "label": request.get("agentName"),
        },
        event_type="action.authorization.test",
        category="action",
        severity="warning" if status_value != "executed" else "info",
        resource_type="action",
        resource_id=str(action.id),
        resource_name=request.get("resourceName"),
        correlation_id=correlation_id,
        outcome=status_value,
        summary=f"Action authorization test {status_value}.",
        metadata={"scope": scope, "dryRun": True},
    )
    await session.commit()
    await session.refresh(action)
    return action


@v1_router.post("/organizations/{organization_id}/action-gateway/tests")
async def gateway_test_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    action = await _gateway_test(
        session, organization_id, principal, payload
    )
    return {"action": await _action_public(session, action)}


@v2_router.post("/organizations/{organization_id}/action-gateway/tests")
async def gateway_test_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    action = await _gateway_test(
        session, organization_id, principal, payload
    )
    return {"data": {"action": _action_v2(await _action_public(session, action))}}


async def _approval_public(
    session: AsyncSession, approval: Approval
) -> dict[str, Any]:
    action = await session.get(Action, approval.action_id)
    if action is None:
        raise HTTPException(500, "Approval action is unavailable.")
    action_public = await _action_public(session, action)
    decision = None
    if approval.decided_by is not None:
        decision = {
            "decision": "approve" if approval.status == "approved" else "reject",
            "note": approval.decision_reason or "",
            "decidedBy": str(approval.decided_by),
            "decidedAt": approval.updated_at.isoformat(),
        }
    return {
        "id": str(approval.id),
        "organizationId": str(approval.organization_id),
        "actionId": str(action.id),
        "agentId": action_public["agent"]["id"],
        "agentName": action_public["agent"]["name"],
        "request": action_public["request"],
        "policy": action_public["policy"],
        "status": approval.status,
        "decision": decision,
        "execution": {
            "state": (
                "executed"
                if action.status == "executed"
                else "blocked"
                if action.status == "blocked"
                else "not_started"
            ),
            "actionStatus": action.status,
            "summary": (
                action_public["result"].get("summary")
                if isinstance(action_public["result"], dict)
                else None
            ),
            "completedAt": action_public["completedAt"],
            "reauthorization": None,
        },
        "notification": {
            "state": "not_attempted",
            "attemptedAt": None,
            "sent": 0,
            "failed": 0,
        },
        "requestedAt": approval.created_at.isoformat(),
    }


def _approval_v2(public: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "id": public["id"],
            "status": public["status"],
            "requestedAt": public["requestedAt"],
        },
        "action": {
            "id": public["actionId"],
            "agent": {
                "id": public["agentId"],
                "name": public["agentName"],
            },
            "request": public["request"],
            "policy": public["policy"],
        },
        "decision": public["decision"],
        "execution": public["execution"],
        "notification": public["notification"],
        "organizationId": public["organizationId"],
    }


@v1_router.get("/organizations/{organization_id}/approvals")
async def approvals_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "approvals.review")
    approvals = list(
        (
            await session.scalars(
                select(Approval)
                .where(Approval.organization_id == organization_id)
                .order_by(desc(Approval.created_at))
                .limit(100)
            )
        ).all()
    )
    public = [await _approval_public(session, item) for item in approvals]
    return {"approvals": public, "count": len(public)}


@v2_router.get("/organizations/{organization_id}/approvals")
async def approvals_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await approvals_v1(organization_id, principal, session)
    return {
        "items": [_approval_v2(item) for item in result["approvals"]],
        "total": result["count"],
    }


@v1_router.post("/organizations/{organization_id}/approvals/{approval_id}/decision")
async def approval_decision_v1(
    organization_id: UUID,
    approval_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "approvals.review")
    approval = await session.scalar(
        select(Approval).where(
            Approval.organization_id == organization_id,
            Approval.id == approval_id,
        )
    )
    if approval is None:
        raise not_found("Approval")
    if approval.status != "pending":
        raise HTTPException(409, "Approval has already been decided.")
    decision = str(payload.get("decision", ""))
    if decision not in {"approve", "reject"}:
        raise HTTPException(400, "Decision must be approve or reject.")
    approval.status = "approved" if decision == "approve" else "rejected"
    approval.decided_by = principal.user_id
    approval.decision_reason = str(payload.get("note", ""))
    action = await session.get(Action, approval.action_id)
    if action is not None:
        action.status = "held" if decision == "approve" else "blocked"
        data = dict(action.payload or {})
        if decision == "reject":
            data["error"] = "Human approval rejected the action."
            data["completedAt"] = utcnow().isoformat()
        action.payload = data
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="approval.decided",
        category="approval",
        severity="warning",
        resource_type="approval",
        resource_id=str(approval.id),
        resource_name=None,
        outcome=approval.status,
        summary=f"Approval {approval.status}.",
        metadata={"actionId": str(approval.action_id)},
    )
    await session.commit()
    await session.refresh(approval)
    return {"approval": await _approval_public(session, approval)}


@v2_router.post("/organizations/{organization_id}/approvals/{approval_id}/decision")
async def approval_decision_v2(
    organization_id: UUID,
    approval_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await approval_decision_v1(
        organization_id, approval_id, payload, principal, session
    )
    return {"data": {"approval": _approval_v2(result["approval"])}}


async def _incident_metadata(
    session: AsyncSession, incident: Incident
) -> dict[str, Any]:
    events = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.organization_id == incident.organization_id,
                    AuditEvent.event_type.in_(
                        ["incident.created", "incident.status.changed"]
                    ),
                )
                .order_by(AuditEvent.created_at)
            )
        ).all()
    )
    relevant = [
        event
        for event in events
        if str((event.resource or {}).get("id", "")) == str(incident.id)
    ]
    created = next(
        (event for event in relevant if event.event_type == "incident.created"),
        None,
    )
    latest = relevant[-1] if relevant else created
    created_meta = (
        (created.payload or {}).get("metadata", {})
        if created is not None
        else {}
    )
    latest_meta = (
        (latest.payload or {}).get("metadata", {})
        if latest is not None
        else {}
    )
    return {
        **(created_meta if isinstance(created_meta, dict) else {}),
        **(latest_meta if isinstance(latest_meta, dict) else {}),
    }


async def _incident_public(
    session: AsyncSession, incident: Incident
) -> dict[str, Any]:
    meta = await _incident_metadata(session, incident)
    return {
        "id": str(incident.id),
        "organizationId": str(incident.organization_id),
        "title": str(meta.get("title", "Incident")),
        "description": incident.summary,
        "severity": incident.severity,
        "status": incident.status,
        "target": {
            "type": str(meta.get("targetType", "organization")),
            "id": str(meta.get("targetId", incident.organization_id)),
            "name": str(meta.get("targetName", "")),
        },
        "correlationId": str(meta.get("correlationId", "")),
        "createdBy": str(meta.get("createdBy", "")),
        "createdAt": incident.created_at.isoformat(),
        "acknowledgedBy": meta.get("acknowledgedBy"),
        "acknowledgedAt": meta.get("acknowledgedAt"),
        "resolvedBy": meta.get("resolvedBy"),
        "resolvedAt": meta.get("resolvedAt"),
        "resolutionNote": meta.get("resolutionNote"),
        "updatedAt": incident.updated_at.isoformat(),
    }


def _incident_v2(public: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "id": public["id"],
            "status": public["status"],
            "severity": public["severity"],
            "correlationId": public["correlationId"],
        },
        "summary": {
            "title": public["title"],
            "description": public["description"],
        },
        "target": public["target"],
        "ownership": {
            "organizationId": public["organizationId"],
            "createdBy": public["createdBy"],
            "acknowledgedBy": public["acknowledgedBy"],
            "resolvedBy": public["resolvedBy"],
        },
        "timestamps": {
            "createdAt": public["createdAt"],
            "acknowledgedAt": public["acknowledgedAt"],
            "resolvedAt": public["resolvedAt"],
            "updatedAt": public["updatedAt"],
        },
        "resolutionNote": public["resolutionNote"],
    }


@v1_router.get("/organizations/{organization_id}/incidents")
async def incidents_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "incidents.read")
    rows = list(
        (
            await session.scalars(
                select(Incident)
                .where(Incident.organization_id == organization_id)
                .order_by(desc(Incident.created_at))
                .limit(100)
            )
        ).all()
    )
    public = [await _incident_public(session, row) for row in rows]
    return {
        "incidents": public,
        "summary": {
            "open": sum(row.status == "open" for row in rows),
            "acknowledged": sum(row.status == "acknowledged" for row in rows),
            "resolved": sum(row.status == "resolved" for row in rows),
            "critical": sum(row.severity == "critical" for row in rows),
        },
        "window": {"limit": 100, "truncated": False},
    }


@v2_router.get("/organizations/{organization_id}/incidents")
async def incidents_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await incidents_v1(organization_id, principal, session)
    return {
        "data": {
            "items": [_incident_v2(item) for item in result["incidents"]],
            "summary": result["summary"],
            "window": result["window"],
        }
    }


@v1_router.post("/organizations/{organization_id}/incidents", status_code=201)
async def create_incident_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "incidents.manage")
    severity = str(payload.get("severity", "medium"))
    if severity not in RISK_ORDER:
        raise HTTPException(400, "Invalid incident severity.")
    incident = Incident(
        organization_id=organization_id,
        status="open",
        severity=severity,
        summary=str(payload.get("description", "")).strip()
        or str(payload.get("title", "Incident")),
    )
    session.add(incident)
    await session.flush()
    target_type = str(payload.get("targetType", "organization"))
    target_id = str(payload.get("targetId") or organization_id)
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="incident.created",
        category="incident",
        severity="critical" if severity == "critical" else "warning",
        resource_type="incident",
        resource_id=str(incident.id),
        resource_name=str(payload.get("title", "Incident")),
        outcome="open",
        summary=f"Incident {payload.get('title', 'Incident')} created.",
        metadata={
            "title": str(payload.get("title", "Incident")),
            "targetType": target_type,
            "targetId": target_id,
            "targetName": str(payload.get("targetName", target_id)),
            "correlationId": str(payload.get("correlationId", "")),
            "createdBy": str(principal.user_id),
        },
    )
    await session.commit()
    await session.refresh(incident)
    return {"incident": await _incident_public(session, incident)}


@v2_router.post("/organizations/{organization_id}/incidents", status_code=201)
async def create_incident_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await create_incident_v1(
        organization_id, payload, principal, session
    )
    return {"data": {"incident": _incident_v2(result["incident"])}}


@v1_router.post("/organizations/{organization_id}/incidents/{incident_id}/status")
async def incident_status_v1(
    organization_id: UUID,
    incident_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "incidents.manage")
    incident = await session.scalar(
        select(Incident).where(
            Incident.organization_id == organization_id,
            Incident.id == incident_id,
        )
    )
    if incident is None:
        raise not_found("Incident")
    new_status = str(payload.get("status", ""))
    if new_status not in {"acknowledged", "resolved"}:
        raise HTTPException(400, "Invalid incident status.")
    incident.status = new_status
    now = utcnow().isoformat()
    metadata: dict[str, Any] = {
        "resolutionNote": str(payload.get("note", "")),
    }
    if new_status == "acknowledged":
        metadata.update(
            {
                "acknowledgedBy": str(principal.user_id),
                "acknowledgedAt": now,
            }
        )
    else:
        metadata.update(
            {
                "resolvedBy": str(principal.user_id),
                "resolvedAt": now,
            }
        )
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="incident.status.changed",
        category="incident",
        severity="warning",
        resource_type="incident",
        resource_id=str(incident.id),
        resource_name=None,
        outcome=new_status,
        summary=f"Incident {new_status}.",
        metadata=metadata,
    )
    await session.commit()
    await session.refresh(incident)
    return {"incident": await _incident_public(session, incident)}


@v2_router.post("/organizations/{organization_id}/incidents/{incident_id}/status")
async def incident_status_v2(
    organization_id: UUID,
    incident_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await incident_status_v1(
        organization_id, incident_id, payload, principal, session
    )
    return {"data": {"incident": _incident_v2(result["incident"])}}


async def _control_public(
    session: AsyncSession,
    organization_id: UUID,
    target_type: str,
    target_id: str,
) -> dict[str, Any]:
    control = await session.scalar(
        select(ExecutionControl).where(
            ExecutionControl.organization_id == organization_id,
            ExecutionControl.target_type == target_type,
            ExecutionControl.target_id == target_id,
        )
    )
    events = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.organization_id == organization_id,
                    AuditEvent.event_type == "execution_control.changed",
                )
                .order_by(desc(AuditEvent.created_at))
                .limit(100)
            )
        ).all()
    )
    metadata: dict[str, Any] = {}
    for event in events:
        resource = event.resource or {}
        if str(resource.get("id", "")) == f"{target_type}:{target_id}":
            raw = (event.payload or {}).get("metadata", {})
            if isinstance(raw, dict):
                metadata = raw
            break
    return {
        "organizationId": str(organization_id),
        "targetType": target_type,
        "targetId": target_id,
        "targetName": str(metadata.get("targetName", target_id)),
        "state": (
            "suspended"
            if control is not None and control.suspended
            else "active"
        ),
        "reason": control.reason if control and control.reason else "",
        "incidentId": metadata.get("incidentId"),
        "changedBy": str(metadata.get("changedBy", "")),
        "changedAt": metadata.get("changedAt"),
    }


def _control_v2(public: dict[str, Any]) -> dict[str, Any]:
    return {
        "target": {
            "type": public["targetType"],
            "id": public["targetId"],
            "name": public["targetName"],
        },
        "execution": {
            "state": public["state"],
            "reason": public["reason"],
            "incidentId": public["incidentId"],
        },
        "changed": {
            "by": public["changedBy"],
            "at": public["changedAt"],
        },
        "organizationId": public["organizationId"],
    }


@v1_router.get("/organizations/{organization_id}/execution-controls/{target_type}/{target_id}")
async def control_v1(
    organization_id: UUID,
    target_type: str,
    target_id: str,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "incidents.read")
    return {
        "control": await _control_public(
            session, organization_id, target_type, target_id
        )
    }


@v2_router.get("/organizations/{organization_id}/execution-controls/{target_type}/{target_id}")
async def control_v2(
    organization_id: UUID,
    target_type: str,
    target_id: str,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    public = await _control_public(
        session, organization_id, target_type, target_id
    )
    return {"data": {"control": _control_v2(public)}}


@v1_router.post("/organizations/{organization_id}/execution-controls/{target_type}/{target_id}")
async def set_control_v1(
    organization_id: UUID,
    target_type: str,
    target_id: str,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "incidents.manage")
    state = str(payload.get("state", ""))
    if state not in {"active", "suspended"}:
        raise HTTPException(400, "Execution state must be active or suspended.")
    control = await session.scalar(
        select(ExecutionControl).where(
            ExecutionControl.organization_id == organization_id,
            ExecutionControl.target_type == target_type,
            ExecutionControl.target_id == target_id,
        )
    )
    if control is None:
        control = ExecutionControl(
            organization_id=organization_id,
            target_type=target_type,
            target_id=target_id,
            suspended=state == "suspended",
            reason=str(payload.get("reason", "")),
        )
        session.add(control)
    else:
        control.suspended = state == "suspended"
        control.reason = str(payload.get("reason", ""))
    now = utcnow().isoformat()
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="execution_control.changed",
        category="incident",
        severity="warning",
        resource_type="execution_control",
        resource_id=f"{target_type}:{target_id}",
        resource_name=target_id,
        outcome=state,
        summary=f"Execution control changed to {state}.",
        metadata={
            "targetName": str(payload.get("targetName", target_id)),
            "incidentId": payload.get("incidentId"),
            "changedBy": str(principal.user_id),
            "changedAt": now,
        },
    )
    await session.commit()
    return {
        "control": await _control_public(
            session, organization_id, target_type, target_id
        )
    }


@v2_router.post("/organizations/{organization_id}/execution-controls/{target_type}/{target_id}")
async def set_control_v2(
    organization_id: UUID,
    target_type: str,
    target_id: str,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await set_control_v1(
        organization_id,
        target_type,
        target_id,
        payload,
        principal,
        session,
    )
    return {"data": {"control": _control_v2(result["control"])}}


@v1_router.get("/organizations/{organization_id}/audit")
async def audit_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
    q: str | None = Query(default=None),
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    actorType: str | None = Query(default=None),
    correlationId: str | None = Query(default=None),
) -> dict[str, Any]:
    require_permission(principal, "audit.read")
    rows = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.organization_id == organization_id)
                .order_by(desc(AuditEvent.created_at))
                .limit(500)
            )
        ).all()
    )
    public = [audit_public(row) for row in rows]
    if category:
        public = [item for item in public if item["category"] == category]
    if severity:
        public = [item for item in public if item["severity"] == severity]
    if actorType:
        public = [
            item for item in public if item["actor"]["type"] == actorType
        ]
    if correlationId:
        public = [
            item
            for item in public
            if item["correlationId"] == correlationId
        ]
    if q:
        needle = q.lower()
        public = [
            item
            for item in public
            if needle
            in (
                item["summary"]
                + " "
                + item["eventType"]
                + " "
                + str(item["resource"]["name"] or "")
            ).lower()
        ]
    severities = Counter(item["severity"] for item in public)
    categories = Counter(item["category"] for item in public)
    return {
        "events": public[:200],
        "summary": {
            "visible": len(public[:200]),
            "bySeverity": {
                "info": severities["info"],
                "warning": severities["warning"],
                "critical": severities["critical"],
            },
            "byCategory": dict(categories),
            "uniqueCorrelations": len(
                {
                    item["correlationId"]
                    for item in public
                    if item["correlationId"]
                }
            ),
            "latestAt": public[0]["occurredAt"] if public else None,
        },
        "window": {
            "scanned": len(rows),
            "limit": 500,
            "truncated": len(rows) >= 500,
        },
    }


@v2_router.get("/organizations/{organization_id}/audit")
async def audit_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
    q: str | None = Query(default=None),
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    actorType: str | None = Query(default=None),
    correlationId: str | None = Query(default=None),
) -> dict[str, Any]:
    result = await audit_v1(
        organization_id,
        principal,
        session,
        q,
        category,
        severity,
        actorType,
        correlationId,
    )
    return {
        "data": {
            "items": [
                {
                    "identity": {
                        "id": item["id"],
                        "eventType": item["eventType"],
                        "category": item["category"],
                        "severity": item["severity"],
                        "occurredAt": item["occurredAt"],
                    },
                    "actor": item["actor"],
                    "resource": item["resource"],
                    "trace": {"correlationId": item["correlationId"]},
                    "outcome": item["outcome"],
                    "summary": item["summary"],
                    "metadata": item["metadata"],
                }
                for item in result["events"]
            ],
            "summary": result["summary"],
            "window": result["window"],
        }
    }
