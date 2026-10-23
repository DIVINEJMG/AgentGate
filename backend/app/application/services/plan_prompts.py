"""When the workspace app suggests a bigger plan (plans, news and upgrade prompts plan, part 2).

The frequency rules live here so every browser follows them: a limit at 80% or more shows one
quiet banner, which stays away for a week after the person dismisses it; members who cannot buy
a plan may ask the workspace's administrators, at most once a day per workspace and limit. The
request is an audit record, as invitations are, and administrators see recent ones in the app.
Nothing here starts, stops or slows any work.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services import content_state
from audoryn_shared.application.services import entitlements
from audoryn_shared.domain.identity.principals import HumanPrincipal
from audoryn_shared.infrastructure.database.models import AuditEvent

DISMISSED_FOR = timedelta(days=7)
REQUEST_EVERY = timedelta(days=1)
# How long administrators see a request from their team.
REQUESTS_SHOWN_FOR = timedelta(days=7)
REQUESTED = "commercial.upgrade.requested"
# What a prompt may be about: a limit or feature of the plan, or the plan's status.
PROMPT_KEYS = frozenset(row["key"] for row in entitlements.comparison()) | {"plan.status"}
REQUEST_PREFIX = "request:"


def valid_key(key: str) -> bool:
    """A prompt a person may dismiss: one about a limit, or an administrator's notice of a request."""
    if key in PROMPT_KEYS:
        return True
    try:
        return key.startswith(REQUEST_PREFIX) and bool(UUID(key.removeprefix(REQUEST_PREFIX)))
    except ValueError:
        return False


def _item(organization_id: UUID, key: str) -> str:
    return f"prompt:{organization_id}:{key}"


async def status(session: AsyncSession, organization_id: UUID, principal: HumanPrincipal) -> dict[str, Any]:
    """What the app may show this person now: limits at or near their end, the one banner the
    rules allow, the plan's features, and (for administrators) recent requests to upgrade."""
    plan = await entitlements.summary(session, organization_id)
    can_upgrade = "organizations.manage" in principal.permissions
    now = datetime.now(UTC)
    pressing = [] if plan is None or not plan["enforced"] else sorted(
        (limit for limit in plan["limits"] if limit["state"] != "ok"),
        key=lambda limit: (limit["state"] != "reached", -(limit["used"] / limit["limit"] if limit["limit"] else 1)))
    requests = await _requests(session, organization_id, now) if can_upgrade else []
    keys = [limit["key"] for limit in pressing] + [REQUEST_PREFIX + request["id"] for request in requests]
    dismissed = {item: at for item, (_, at) in (await content_state.states(
        session, principal.user_id, keys=[_item(organization_id, key) for key in keys])).items() if at}

    def showing(key: str) -> bool:
        at = dismissed.get(_item(organization_id, key))
        return at is None or now - at >= DISMISSED_FOR
    return {
        "plan": None if plan is None else {"code": plan["code"], "name": plan["name"], "status": plan["status"],
                                           "periodEnd": plan["periodEnd"], "enforced": plan["enforced"]},
        "canUpgrade": can_upgrade,
        "limits": pressing,
        "banner": next((limit["key"] for limit in pressing if showing(limit["key"])), None),
        # Only an enforced plan turns features away, so only then does the app say so.
        "features": {row["key"]: row["included"] for row in plan["included"]} if plan and plan["enforced"] else {},
        # A request stays dismissed once an administrator has seen it.
        "requests": [request for request in requests
                     if _item(organization_id, REQUEST_PREFIX + request["id"]) not in dismissed],
    }


async def dismiss(session: AsyncSession, organization_id: UUID, user_id: UUID, key: str) -> None:
    await content_state.mark(session, user_id, [_item(organization_id, key)], "dismissed")


async def last_request(session: AsyncSession, organization_id: UUID, key: str) -> datetime | None:
    """When someone last asked the administrators to upgrade for this limit, within a day."""
    return await session.scalar(select(AuditEvent.created_at).where(
        AuditEvent.organization_id == organization_id, AuditEvent.event_type == REQUESTED,
        AuditEvent.created_at >= datetime.now(UTC) - REQUEST_EVERY,
        AuditEvent.payload["metadata"]["limit"].as_string() == key).limit(1))


async def _requests(session: AsyncSession, organization_id: UUID, now: datetime) -> list[dict[str, Any]]:
    events = (await session.scalars(select(AuditEvent).where(
        AuditEvent.organization_id == organization_id, AuditEvent.event_type == REQUESTED,
        AuditEvent.created_at >= now - REQUESTS_SHOWN_FOR).order_by(desc(AuditEvent.created_at)).limit(20))).all()
    return [{"id": str(event.id), "limit": meta.get("limit"), "name": meta.get("name"),
             "requestedBy": meta.get("requestedBy"), "requestedAt": event.created_at.isoformat()}
            for event in events if isinstance(meta := (event.payload or {}).get("metadata"), dict)]
