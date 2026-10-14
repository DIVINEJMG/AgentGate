from __future__ import annotations

import fnmatch
import hashlib
import json
from collections import Counter, defaultdict
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select, tuple_
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
    Run,
    Worker,
    WorkItem,
)
from app.infrastructure.database.outbox import TransactionalOutbox
from app.infrastructure.database.session import database_session
from app.runtime.qstash_trigger import request_runtime_execution

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


async def _policies_public(
    session: AsyncSession, policies: list[Policy]
) -> list[dict[str, Any]]:
    """Public policy records with their current revisions, loaded in one query."""
    if not policies:
        return []
    revisions = {
        (revision.policy_id, revision.revision): revision
        for revision in await session.scalars(
            select(PolicyRevision).where(
                tuple_(PolicyRevision.policy_id, PolicyRevision.revision).in_(
                    [(policy.id, policy.current_revision) for policy in policies]
                )
            )
        )
    }
    records = []
    for policy in policies:
        revision = revisions.get((policy.id, policy.current_revision))
        if revision is None:
            raise HTTPException(500, "Policy revision is unavailable.")
        records.append(_policy_record(policy, revision))
    return records


async def _policy_public(
    session: AsyncSession, policy: Policy
) -> dict[str, Any]:
    return (await _policies_public(session, [policy]))[0]


def _policy_record(policy: Policy, revision: PolicyRevision) -> dict[str, Any]:
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
            "providers": list(selectors.get("providers", [])),
            "resourceNames": list(selectors.get("resourceNames", [])),
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
        "providers": [str(value).strip().lower() for value in raw.get("providers", []) if str(value).strip()],
        "resourceNames": [str(value).strip() for value in raw.get("resourceNames", []) if str(value).strip()],
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
    provider: str = "",
    resource_name: str = "",
) -> bool:
    selectors = policy["selectors"]
    dimensions = (
        ("agentIds", agent_id),
        ("resourceIds", resource_id),
        ("actions", action),
        ("scopes", scope),
        ("risks", risk),
        ("providers", provider.lower()),
    )
    for key, value in dimensions:
        values = selectors.get(key, [])
        if values and value not in values:
            return False
    # Resource names by pattern, ignoring case: "acme/*" covers every acme repository.
    patterns = selectors.get("resourceNames") or []
    return not patterns or any(fnmatch.fnmatchcase(resource_name.lower(), str(pattern).lower()) for pattern in patterns)


def explain_decision(decision: dict[str, Any]) -> str:
    """One plain sentence saying why a policy decision came out as it did."""
    request = decision.get("request") or {}
    agent = request.get("agentName") or "This worker"
    action = request.get("action") or request.get("scope") or "this action"
    resource = request.get("resourceName") or "this resource"
    winner = decision.get("winningPolicy")
    if winner:
        name = f"\u201c{winner.get('name')}\u201d"
        ties = [item for item in decision.get("matchedPolicies") or [] if item.get("priority") == winner.get("priority")]
        tie_note = " Several policies matched at the same priority; the strictest one applies." if len(ties) > 1 else ""
        return {
            "ALLOW": f"Allowed by the policy {name}.",
            "REQUIRE_APPROVAL": f"Needs a person's approval because of the policy {name}.",
            "DENY": f"Blocked by the policy {name}.",
        }.get(str(decision.get("outcome")), f"Decided by the policy {name}.") + tie_note
    preconditions = decision.get("preconditions") or {}
    if request.get("agentName") is None:
        return "The worker's identity could not be found, so nothing is allowed."
    if request.get("resourceName") is None:
        return f"The resource isn't connected or visible to {agent}, so {agent} can't use it."
    if not preconditions.get("scopeAvailable"):
        return f"{resource} doesn't offer that action."
    if not preconditions.get("agentActive"):
        return f"{agent} is paused or not active, so nothing is allowed."
    if not preconditions.get("resourceLive"):
        return f"{resource} isn't fully connected right now, so actions on it are held back."
    if not preconditions.get("scopeDeclared"):
        return f"{agent} hasn't been given the ability to {action}."
    return (f"No policy allows {agent} to {action} on {resource}, so it is blocked by default. "
            "Add a policy, or use \u201cAlways allow\u201d on an approval, to permit it.")


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


async def _live_policy_resource(
    session: AsyncSession, organization_id: UUID, resource_id: str,
) -> dict[str, Any] | None:
    """Resolve the resource identity used by both planning and execution.

    Catalog presence is not a grant. Task authority is still checked by the
    foundation before execution, and this evaluator still applies org policy.
    """
    from app.bootstrap.settings import settings

    if settings.integration_foundation_enabled:
        from app.application.services.integration_foundation import IntegrationFoundation
        from app.execution.bootstrap import execution_provider_registry
        from app.infrastructure.database.models import Integration, IntegrationResource

        try:
            identity = UUID(resource_id)
        except ValueError:
            identity = None
        row = await session.get(IntegrationResource, identity) if identity else None
        if row is not None:
            if row.organization_id != organization_id:
                return None
            # Browser and public research retain their existing catalog contracts.
            if row.provider not in {"browser", "web_research"}:
                connection = await session.get(Integration, row.connection_id)
                if not connection or connection.organization_id != organization_id or connection.provider != row.provider:
                    return None
                try:
                    provider = execution_provider_registry().get(row.provider)
                except KeyError:
                    return None
                scopes = await IntegrationFoundation(session).resource_capabilities(row)
                connected = row.health == "healthy" and connection.status == "connected"
                return {"id": str(row.id), "displayName": row.display_name,
                    "provider": row.provider, "status": "connected" if connected else "degraded",
                    "actions": [{"scope": c.scope, "action": c.operation.rsplit(".", 1)[-1],
                        "target": c.target, "risk": c.risk, "providerOperation": c.operation,
                        "sideEffect": c.side_effect, "approvalRecommendation": c.approval_recommendation}
                        for c in provider.manifest.capabilities if c.scope in scopes]}
    catalog = await _catalog(session, organization_id)
    return next((item for item in catalog["resources"] if item["id"] == resource_id), None)


async def _evaluate(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    *,
    agent_id: UUID,
    resource_id: str,
    scope: str,
    preparation_cache: dict[Any, Any] | None = None,
) -> dict[str, Any]:
    decision = await _evaluate_policies(session, organization_id, principal, agent_id=agent_id,
        resource_id=resource_id, scope=scope, preparation_cache=preparation_cache)
    return {**decision, "explanation": explain_decision(decision)}


async def _evaluate_policies(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    *,
    agent_id: UUID,
    resource_id: str,
    scope: str,
    preparation_cache: dict[Any, Any] | None = None,
) -> dict[str, Any]:
    require_permission(principal, "policies.read")
    # Only the tool-preparation caller supplies this pass-local read snapshot.
    # Execution and approval revalidation continue to read current policy data.
    cache = preparation_cache if preparation_cache is not None else {}
    agent_key = (organization_id, agent_id, "agent")
    if agent_key not in cache:
        cache[agent_key] = await session.scalar(
            select(AgentIdentity).where(
                AgentIdentity.organization_id == organization_id,
                AgentIdentity.id == agent_id,
            )
        )
    agent = cache[agent_key]
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

    resource_key = (organization_id, resource_id, "resource")
    if resource_key not in cache:
        cache[resource_key] = await _live_policy_resource(session, organization_id, resource_id)
    resource = cache[resource_key]
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

    profile_key = (organization_id, agent_id, "profile")
    if profile_key not in cache:
        cache[profile_key] = await _profile(session, organization_id, agent_id)
    profile = cache[profile_key]
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

    policies_key = (organization_id, "policies")
    if policies_key not in cache:
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
        cache[policies_key] = await _policies_public(session, policies)
    public = cache[policies_key]
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
            provider=str(resource.get("provider") or ""),
            resource_name=str(resource.get("displayName") or ""),
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
            "explanation": decision.get("explanation", ""),
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
    items = await _policies_public(session, policies)
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


async def _simulate(
    session: AsyncSession, organization_id: UUID, principal: HumanPrincipal, payload: dict[str, Any],
) -> dict[str, Any]:
    """What would happen if this worker tried this action on this resource, under the current
    policies, and optionally with one unsaved draft policy added. Nothing is saved or run."""
    require_permission(principal, "policies.read")
    agent_id: Any = payload.get("agentId")
    if not agent_id and payload.get("workerId"):
        worker = await session.scalar(select(Worker).where(
            Worker.organization_id == organization_id, Worker.id == UUID(str(payload["workerId"]))))
        if worker is None:
            raise not_found("Worker")
        agent_id = worker.agent_identity_id
    resource_id, scope = str(payload.get("resourceId") or ""), str(payload.get("scope") or "")
    if not agent_id or not resource_id or not scope:
        raise HTTPException(400, "Choose a worker, a resource and an action to simulate.")
    cache: dict[Any, Any] = {}
    draft = payload.get("draftPolicy")
    if isinstance(draft, dict):
        effect = str(draft.get("effect", "deny"))
        if effect not in EFFECT_WEIGHT:
            raise HTTPException(400, "Invalid policy effect.")
        current = await _policies_public(session, list((await session.scalars(select(Policy).where(
            Policy.organization_id == organization_id, Policy.status == "enabled"))).all()))
        cache[(organization_id, "policies")] = [*current, {
            "id": "draft", "name": str(draft.get("name") or "Draft policy"), "effect": effect,
            "priority": int(draft.get("priority", 100)), "revision": 0, "selectors": _selectors(draft, {})}]
    decision = await _evaluate(session, organization_id, principal, agent_id=UUID(str(agent_id)),
                               resource_id=resource_id, scope=scope, preparation_cache=cache)
    return {"decision": decision, "explanation": decision["explanation"], "withDraft": isinstance(draft, dict)}


@v1_router.post("/organizations/{organization_id}/policies/simulate")
async def simulate_policy_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return await _simulate(session, organization_id, principal, payload)


@v2_router.post("/organizations/{organization_id}/policies/simulate")
async def simulate_policy_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {"data": await _simulate(session, organization_id, principal, payload)}


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
    resource = await _live_policy_resource(session, organization_id, resource_id)
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


async def _actions_public(
    session: AsyncSession, actions: list[Action]
) -> list[dict[str, Any]]:
    """Public action records with agent names and approval IDs, in two queries."""
    if not actions:
        return []
    names = {
        row.id: row.name
        for row in await session.execute(
            select(AgentIdentity.id, AgentIdentity.name)
            .where(AgentIdentity.id.in_({action.agent_id for action in actions}))
        )
    }
    approval_ids: dict[Any, str] = {}
    for row in await session.execute(
        select(Approval.action_id, Approval.id).where(Approval.action_id.in_([action.id for action in actions]))
    ):
        approval_ids.setdefault(row.action_id, str(row.id))
    return [_action_record(action, names.get(action.agent_id, ""), approval_ids.get(action.id)) for action in actions]


async def _action_public(
    session: AsyncSession, action: Action
) -> dict[str, Any]:
    return (await _actions_public(session, [action]))[0]


def _action_record(action: Action, agent_name: str, approval_id: str | None) -> dict[str, Any]:
    data: dict[str, Any] = (
        dict(action.payload) if isinstance(action.payload, dict) else {}
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
            "name": agent_name,
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
        "approvalId": approval_id,
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
    public = await _actions_public(session, actions)
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
    approval: Approval | None = None
    if outcome == "REQUIRE_APPROVAL":
        approval = Approval(
            organization_id=organization_id,
            action_id=action.id,
            status="pending",
            decided_by=None,
            decision_reason=None,
        )
        session.add(approval)
        await session.flush()
    outbox = TransactionalOutbox(session)
    await outbox.enqueue(
        topic="action.proposed",
        aggregate_type="action",
        aggregate_id=str(action.id),
        payload={
            "organization_id": str(organization_id),
            "action_id": str(action.id),
            "resource_id": resource_id,
            "correlation_id": correlation_id,
        },
    )
    if approval is not None:
        await outbox.enqueue(
            topic="approval.created",
            aggregate_type="approval",
            aggregate_id=str(approval.id),
            payload={
                "organization_id": str(organization_id),
                "approval_id": str(approval.id),
                "action_id": str(action.id),
                "resource_id": resource_id,
                "correlation_id": correlation_id,
            },
        )
    elif status_value == "blocked":
        await outbox.enqueue(
            topic="action.blocked",
            aggregate_type="action",
            aggregate_id=str(action.id),
            payload={
                "organization_id": str(organization_id),
                "action_id": str(action.id),
                "resource_id": resource_id,
                "correlation_id": correlation_id,
            },
        )
    else:
        await outbox.enqueue(
            topic="action.executed",
            aggregate_type="action",
            aggregate_id=str(action.id),
            payload={
                "organization_id": str(organization_id),
                "action_id": str(action.id),
                "resource_id": resource_id,
                "correlation_id": correlation_id,
            },
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


async def _approvals_public(
    session: AsyncSession, approvals: list[Approval]
) -> list[dict[str, Any]]:
    """Public approval records for many approvals in a handful of queries, whatever their number.

    Each record needs its action, the agent's name and the conversation thread. Loading those
    one approval at a time read about six rows (two whole JSON documents among them) per approval.
    """
    if not approvals:
        return []
    from app.application.services.approval_conversation import approval_conversation_ids

    actions = {
        action.id: action
        for action in await session.scalars(
            select(Action).where(Action.id.in_({approval.action_id for approval in approvals}))
        )
    }
    if any(approval.action_id not in actions for approval in approvals):
        raise HTTPException(500, "Approval action is unavailable.")
    names = {
        row.id: row.name
        for row in await session.execute(
            select(AgentIdentity.id, AgentIdentity.name)
            .where(AgentIdentity.id.in_({action.agent_id for action in actions.values()}))
        )
    }
    threads = await approval_conversation_ids(session, list(actions.values()))
    return [
        _approval_record(
            approval,
            actions[approval.action_id],
            # An approval's own record never shows the action's approvalId, so it is not looked up.
            _action_record(actions[approval.action_id], names.get(actions[approval.action_id].agent_id, ""), None),
            threads[approval.action_id],
        )
        for approval in approvals
    ]


async def _approval_public(
    session: AsyncSession, approval: Approval
) -> dict[str, Any]:
    return (await _approvals_public(session, [approval]))[0]


def _approval_record(
    approval: Approval, action: Action, action_public: dict[str, Any], conversation_thread_id: Any
) -> dict[str, Any]:
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
        "runId": str(action.run_id) if action.run_id else None,
        "conversationThreadId": conversation_thread_id,
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
        "notification": _approval_notification(action),
        "requestedAt": approval.created_at.isoformat(),
        **_approval_lifecycle(approval, action),
    }


def _approval_notification(action: Action) -> dict[str, Any]:
    data = action.payload if isinstance(action.payload, dict) else {}
    reminded = data.get("approvalRemindedAt")
    if not reminded:
        return {"state": "not_attempted", "attemptedAt": None, "sent": 0, "failed": 0}
    posted = data.get("approvalReminder") == "posted"
    return {"state": "sent" if posted else "unavailable", "attemptedAt": reminded, "sent": int(posted), "failed": 0}


def _approval_lifecycle(approval: Approval, action: Action) -> dict[str, Any]:
    """When the approval expires, whether its action was edited, and the action's input fields
    (so the workspace can offer to edit them before approving)."""
    from datetime import timedelta

    from app.bootstrap.settings import settings
    from app.execution.bootstrap import execution_provider_registry

    data = action.payload if isinstance(action.payload, dict) else {}
    request: dict[str, Any] = data["request"] if isinstance(data.get("request"), dict) else {}
    schema = None
    try:
        provider = execution_provider_registry().get(str(request.get("provider", "")))
        capability = next((c for c in provider.manifest.capabilities if c.scope == action.scope), None)
        schema = capability.input_schema if capability is not None else None
    except KeyError:
        schema = None
    return {
        "expiresAt": (approval.created_at + timedelta(hours=settings.approval_expiry_hours)).isoformat(),
        "edited": isinstance(data.get("approvedEdit"), dict),
        "inputSchema": schema,
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
            "runId": public["runId"],
            "conversationThreadId": public.get("conversationThreadId"),
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
        "lifecycle": {key: public.get(key) for key in ("expiresAt", "edited", "inputSchema")},
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
    public = await _approvals_public(session, approvals)
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


MAX_BATCH_DECISIONS = 20


def input_hash(value: dict[str, Any]) -> str:
    """The identity of an action input a person approved after editing it."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


async def _edited_input(action: Action, edited: Any) -> dict[str, Any] | None:
    """Check an edit against the tool's own input rules. Only the input can change: the tool,
    resource and policy stay as approved. Returns the normalised input, or None when unchanged."""
    from jsonschema import ValidationError, validate

    from app.execution.bootstrap import execution_provider_registry

    if not isinstance(edited, dict):
        raise HTTPException(400, "The edited action input must be an object.")
    request = dict((action.payload or {}).get("request") or {})
    try:
        provider = execution_provider_registry().get(str(request.get("provider", "")))
    except KeyError as error:
        raise HTTPException(409, "This action's tool is no longer available, so it cannot be edited.") from error
    capability = next((c for c in provider.manifest.capabilities if c.scope == action.scope), None)
    if capability is None:
        raise HTTPException(409, "This action's tool is no longer available, so it cannot be edited.")
    try:
        validate(edited, capability.input_schema)
        normalized = await provider.normalize_input(operation=capability.operation, input=edited)
    except (ValidationError, ValueError) as error:
        message = error.message if isinstance(error, ValidationError) else str(error)
        raise HTTPException(400, f"The edited input is not valid for this action: {message}") from error
    return None if normalized == request.get("input") else dict(normalized)


async def _always_allow(
    session: AsyncSession, organization_id: UUID, principal: HumanPrincipal, action: Action,
) -> Policy:
    """A narrow ALLOW policy for this worker, this resource and this kind of action, placed just
    above the policy that required approval. Shown to the person before they confirm."""
    from app.execution.bootstrap import execution_provider_registry

    require_permission(principal, "policies.manage")
    request = dict((action.payload or {}).get("request") or {})
    try:
        capability = next((c for c in execution_provider_registry().get(str(request.get("provider", ""))).manifest.capabilities
                           if c.scope == action.scope), None)
    except KeyError:
        capability = None
    if capability is not None and capability.approval_recommendation == "required":
        raise HTTPException(409, "This kind of action always needs approval, so it cannot be always allowed.")
    reader = HumanPrincipal(user_id=principal.user_id, organization_id=organization_id, membership_id=principal.membership_id,
                            role=principal.role, permissions=frozenset({"policies.read"}))
    current = await _evaluate(session, organization_id, reader, agent_id=action.agent_id,
                              resource_id=action.resource_id, scope=action.scope)
    if current["outcome"] == "DENY":
        raise HTTPException(409, "Organisation policy now denies this action, so it cannot be always allowed.")
    winner = current.get("winningPolicy") or {}
    priority = min(1000, int(winner.get("priority", 100)) + 1)
    agent = await session.get(AgentIdentity, action.agent_id)
    details = current.get("request") or {}
    name = f"Always allow: {agent.name if agent else 'worker'} · {details.get('action') or action.scope} on {details.get('resourceName') or action.resource_id}"
    return await _create_policy(session, organization_id, principal, {
        "name": name[:120], "effect": "allow", "priority": priority,
        "description": "Created from an approval with \"Always allow this kind\". Applies only to this worker, "
                       "this resource and this kind of action.",
        "selectors": {"agentIds": [str(action.agent_id)], "resourceIds": [action.resource_id], "scopes": [action.scope]},
    })


async def _decide_approval(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    approval_id: UUID,
    payload: dict[str, Any],
) -> Approval:
    """Approve or decline one approval. An approval may carry an edited input (what then runs)
    and a request to always allow this kind of action."""
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
        raise HTTPException(409, "Approval has already been decided." if approval.status != "expired"
                            else "This approval expired; the action was not performed.")
    decision = str(payload.get("decision", ""))
    if decision not in {"approve", "reject"}:
        raise HTTPException(400, "Decision must be approve or reject.")
    action = await session.get(Action, approval.action_id)
    edited = None
    if payload.get("input") is not None:
        if decision != "approve" or action is None:
            raise HTTPException(400, "Only an approval can carry an edited action.")
        edited = await _edited_input(action, payload["input"])
    approval.status = "approved" if decision == "approve" else "rejected"
    approval.decided_by = principal.user_id
    approval.decision_reason = str(payload.get("note", ""))
    if action is not None:
        action.status = "held" if decision == "approve" else "blocked"
        data = dict(action.payload or {})
        if decision == "reject":
            data["error"] = "Human approval rejected the action."
            data["completedAt"] = utcnow().isoformat()
        if edited is not None:
            request = dict(data.get("request") or {})
            # The runtime runs the edited input only while it still matches this hash.
            data["approvedEdit"] = {"inputHash": input_hash(edited), "previousInput": request.get("input"),
                                    "editedBy": str(principal.user_id), "editedAt": utcnow().isoformat()}
            data["request"] = {**request, "input": edited}
        action.payload = data
        if edited is not None:
            await append_audit(
                session, organization_id=organization_id, principal=principal,
                event_type="approval.action_edited", category="approval", severity="warning",
                resource_type="approval", resource_id=str(approval.id), resource_name=None, outcome="edited",
                summary="The action was edited before approval; the edited version is what runs.",
                metadata={"actionId": str(action.id), "fields": sorted(set(edited) | set(data["approvedEdit"]["previousInput"] or {}))},
            )
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
    outbox = TransactionalOutbox(session)
    await outbox.enqueue(
        topic="approval.decided",
        aggregate_type="approval",
        aggregate_id=str(approval.id),
        payload={
            "organization_id": str(organization_id),
            "approval_id": str(approval.id),
            "action_id": str(approval.action_id),
            "correlation_id": str((action.payload or {}).get("correlationId", "")) if action else "",
            "decision": decision,
        },
    )
    if action is not None:
        await outbox.enqueue(
            topic="action.approved" if decision == "approve" else "action.blocked",
            aggregate_type="action",
            aggregate_id=str(action.id),
            payload={
                "organization_id": str(organization_id),
                "action_id": str(action.id),
                "resource_id": action.resource_id,
                "correlation_id": str((action.payload or {}).get("correlationId", "")),
            },
        )
    if decision == "approve" and payload.get("alwaysAllow") and action is not None:
        await _always_allow(session, organization_id, principal, action)
    await session.commit()

    if decision == "approve" and action is not None and action.run_id is not None:
        run = await session.get(Run, action.run_id)
        item = (
            await session.get(WorkItem, run.work_item_id)
            if run is not None
            else None
        )
        if item is not None:
            payload_data = item.payload if isinstance(item.payload, dict) else {}
            runtime_data = payload_data.get("runtime")
            runtime_data = runtime_data if isinstance(runtime_data, dict) else {}
            await request_runtime_execution(
                organization_id=organization_id,
                work_item_id=item.id,
                expected_step=max(0, int(runtime_data.get("currentStep", 0))),
                reason="approval",
            )

    await session.refresh(approval)
    return approval


@v1_router.post("/organizations/{organization_id}/approvals/{approval_id}/decision")
async def approval_decision_v1(
    organization_id: UUID,
    approval_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    approval = await _decide_approval(session, organization_id, principal, approval_id, payload)
    return {"approval": await _approval_public(session, approval)}


@v1_router.post("/organizations/{organization_id}/approvals/decisions")
async def approval_decisions_v1(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    """Decide several approvals at once, for example every action a task is waiting on. Each is
    decided exactly as on its own; one that cannot be decided is reported, the rest proceed."""
    raw_ids = payload.get("approvalIds")
    if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > MAX_BATCH_DECISIONS:
        raise HTTPException(400, f"Choose between 1 and {MAX_BATCH_DECISIONS} approvals.")
    single = {"decision": payload.get("decision"), "note": payload.get("note", "")}
    decided, failed = [], []
    for raw in dict.fromkeys(str(value) for value in raw_ids):
        try:
            approval = await _decide_approval(session, organization_id, principal, UUID(raw), single)
            decided.append(await _approval_public(session, approval))
        except (HTTPException, ValueError) as error:
            await session.rollback()
            failed.append({"approvalId": raw, "reason": error.detail if isinstance(error, HTTPException) else "Invalid approval ID."})
    return {"approvals": decided, "failed": failed}


@v2_router.post("/organizations/{organization_id}/approvals/decisions")
async def approval_decisions_v2(
    organization_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await approval_decisions_v1(organization_id, payload, principal, session)
    return {"data": {"approvals": [_approval_v2(item) for item in result["approvals"]], "failed": result["failed"]}}


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


async def _incidents_metadata(
    session: AsyncSession, incidents: list[Incident]
) -> dict[Any, dict[str, Any]]:
    """Each incident's metadata from its audit events, for many incidents in one query.

    Reads only the events about these incidents, and only their type and ``metadata`` part.
    Previously every incident loaded every incident event of its organisation, as whole rows.
    """
    if not incidents:
        return {}
    events: dict[tuple[Any, str], list[Any]] = defaultdict(list)
    for row in await session.execute(
        select(
            AuditEvent.organization_id,
            AuditEvent.event_type,
            AuditEvent.resource["id"].astext.label("resource_id"),
            AuditEvent.payload["metadata"].label("event_metadata"),
        )
        .where(
            AuditEvent.organization_id.in_({incident.organization_id for incident in incidents}),
            AuditEvent.event_type.in_(["incident.created", "incident.status.changed"]),
            AuditEvent.resource["id"].astext.in_([str(incident.id) for incident in incidents]),
        )
        .order_by(AuditEvent.created_at)
    ):
        events[(row.organization_id, str(row.resource_id))].append(row)
    result = {}
    for incident in incidents:
        relevant = events.get((incident.organization_id, str(incident.id)), [])
        created = next((event for event in relevant if event.event_type == "incident.created"), None)
        latest = relevant[-1] if relevant else created
        created_meta = created.event_metadata if created is not None else {}
        latest_meta = latest.event_metadata if latest is not None else {}
        result[incident.id] = {
            **(created_meta if isinstance(created_meta, dict) else {}),
            **(latest_meta if isinstance(latest_meta, dict) else {}),
        }
    return result


async def _incident_metadata(
    session: AsyncSession, incident: Incident
) -> dict[str, Any]:
    return (await _incidents_metadata(session, [incident]))[incident.id]


async def _incidents_public(
    session: AsyncSession, incidents: list[Incident]
) -> list[dict[str, Any]]:
    metadata = await _incidents_metadata(session, incidents)
    return [_incident_record(incident, metadata[incident.id]) for incident in incidents]


async def _incident_public(
    session: AsyncSession, incident: Incident
) -> dict[str, Any]:
    return _incident_record(incident, await _incident_metadata(session, incident))


def _incident_record(incident: Incident, meta: dict[str, Any]) -> dict[str, Any]:
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
    public = await _incidents_public(session, rows)
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
    await TransactionalOutbox(session).enqueue(
        topic="incident.created",
        aggregate_type="incident",
        aggregate_id=str(incident.id),
        payload={
            "organization_id": str(organization_id),
            "incident_id": str(incident.id),
            "resource_id": target_id,
            "correlation_id": str(payload.get("correlationId", "")),
            "severity": severity,
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
    await TransactionalOutbox(session).enqueue(
        topic="incident.updated",
        aggregate_type="incident",
        aggregate_id=str(incident.id),
        payload={
            "organization_id": str(organization_id),
            "incident_id": str(incident.id),
            "status": new_status,
            "correlation_id": str((await _incident_public(session, incident)).get("correlationId", "")),
        },
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
    await TransactionalOutbox(session).enqueue(
        topic="incident.updated",
        aggregate_type="execution_control",
        aggregate_id=f"{target_type}:{target_id}",
        payload={
            "organization_id": str(organization_id),
            "resource_id": target_id,
            "state": state,
            "incident_id": payload.get("incidentId"),
            "correlation_id": str(payload.get("correlationId", "")),
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
