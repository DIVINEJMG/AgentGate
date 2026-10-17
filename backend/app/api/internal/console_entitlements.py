"""The Console's entitlement channel (commercial plan, phase 3), under
``/internal/console/entitlements/v1``. Off unless enabled.

- ``POST /snapshots``: apply one organisation's plan (newer versions only).
- ``POST /template``: replace the plan new workspaces start on.
- ``POST /usage-resets``: give workspaces a usage reset, applied now or banked (phase 6).
- ``GET /status``: organisations, paged by id, with the plan applied and this period's usage, so
  the Console can confirm delivery, show usage and find new workspaces.

Every response is ``{"version": 1, "data": ...}`` or ``{"version": 1, "error": {"code", "message"}}``.
Bodies, headers and plan values are never logged.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.console_entitlements import (
    READ_SCOPE,
    WRITE_SCOPE,
    ChannelGuard,
    EntitlementsChannelConfig,
    load_config,
    verify_request,
)
from app.application.console_reads.signing import ReplayStoreUnavailable, ServiceRequestRejected
from app.application.services import entitlements as plans
from app.application.services import usage_resets
from app.infrastructure.database.models import (
    EntitlementTemplate,
    Organization,
    OrganizationEntitlement,
    OrganizationUsage,
)
from app.infrastructure.database.session import database_session

router = APIRouter(prefix="/internal/console/entitlements/v1", include_in_schema=False)
logger = logging.getLogger("audoryn.console_entitlements")

_ERRORS = {
    "not_found": (404, "Not found."),
    "unauthorized": (401, "Service request was not authorized."),
    "invalid": (400, "The request is not a valid document for this contract."),
    "unknown_organization": (404, "No organization has this id."),
    "rate_limited": (429, "Too many requests."),
    "unavailable": (503, "Service temporarily unavailable."),
}
STATUS_PAGE_MAX = 200


class _Refused(Exception):
    def __init__(self, code: str, reason: str | None = None, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code, self.reason, self.retry_after = code, reason or code, retry_after


@dataclass
class ChannelRuntime:
    config: EntitlementsChannelConfig
    guard: ChannelGuard | None = None
    clock: Callable[[], float] = time.time


@lru_cache
def _runtime_from_settings() -> ChannelRuntime:
    from app.bootstrap.settings import settings

    config = load_config(settings)
    if not config.enabled:
        return ChannelRuntime(config=config)
    from redis.asyncio import Redis

    return ChannelRuntime(config=config, guard=ChannelGuard(Redis.from_url(settings.redis_dsn, decode_responses=True)))


def channel_runtime() -> ChannelRuntime:
    """FastAPI dependency; tests override it through ``app.dependency_overrides``."""
    return _runtime_from_settings()


Runtime = Annotated[ChannelRuntime, Depends(channel_runtime)]
Session = Annotated[AsyncSession, Depends(database_session)]
Handler = Callable[[bytes, dict[str, str]], Awaitable[object]]


def _target(request: Request) -> str:
    raw_path = request.scope.get("raw_path") or request.scope["path"].encode()
    query = request.scope.get("query_string", b"")
    try:
        return raw_path.decode("ascii") + ("?" + query.decode("ascii") if query else "")
    except UnicodeDecodeError as exc:
        raise _Refused("unauthorized", "invalid_target") from exc


async def _serve(request: Request, runtime: ChannelRuntime, *, operation: str, scope: str, handle: Handler) -> JSONResponse:
    started, key_id = time.perf_counter(), "-"
    try:
        if not runtime.config.enabled or runtime.guard is None:
            raise _Refused("not_found", "disabled")
        body, now = await request.body(), int(runtime.clock())
        try:
            key = await verify_request(config=runtime.config, scope=scope, method=request.method,
                                       target=_target(request), body=body, headers=request.headers.items(),
                                       guard=runtime.guard, now=now)
        except ServiceRequestRejected as exc:
            raise _Refused("unauthorized", exc.reason) from exc
        key_id = key.key_id
        wait = await runtime.guard.wait_seconds(key_id=key_id, limit=runtime.config.rate_per_minute, now=now)
        if wait is not None:
            raise _Refused("rate_limited", retry_after=wait)
        try:
            data = await handle(body, dict(request.query_params))
        except (ValidationError, ValueError) as exc:
            raise _Refused("invalid", type(exc).__name__) from exc
        response = JSONResponse({"version": 1, "data": data}, headers={"Cache-Control": "no-store"})
        outcome, reason = "ok", None
    except _Refused as exc:
        status, message = _ERRORS[exc.code]
        headers = {"Cache-Control": "no-store", **({"Retry-After": str(exc.retry_after)} if exc.retry_after else {})}
        response = JSONResponse({"version": 1, "error": {"code": exc.code, "message": message}},
                                status_code=status, headers=headers)
        outcome, reason = exc.code, exc.reason
    except Exception as exc:  # noqa: BLE001 - never a bare 500: the contract's answer, the type in the log
        status, message = _ERRORS["unavailable"]
        response = JSONResponse({"version": 1, "error": {"code": "unavailable", "message": message}}, status_code=status)
        outcome, reason = "unavailable", "redis" if isinstance(exc, ReplayStoreUnavailable) else type(exc).__name__
    logger.log(logging.INFO if outcome == "ok" else logging.WARNING,
               "console_entitlements.request operation=%s key=%s outcome=%s reason=%s status=%s duration_ms=%s",
               operation, key_id, outcome, reason, response.status_code, round((time.perf_counter() - started) * 1000, 1))
    return response


@router.post("/snapshots")
async def apply_snapshot(request: Request, runtime: Runtime, session: Session) -> JSONResponse:
    async def handle(body: bytes, _: dict[str, str]) -> object:
        document = plans.SnapshotDocument.model_validate_json(body)
        if await session.get(Organization, document.organization_id) is None:
            raise _Refused("unknown_organization")
        result = await plans.apply_snapshot(session, document)
        await session.commit()
        applied = await session.scalar(select(OrganizationEntitlement.version).where(
            OrganizationEntitlement.organization_id == document.organization_id))
        return {"result": result, "organizationId": str(document.organization_id), "appliedVersion": applied}

    return await _serve(request, runtime, operation="snapshot", scope=WRITE_SCOPE, handle=handle)


@router.post("/template")
async def apply_template(request: Request, runtime: Runtime, session: Session) -> JSONResponse:
    async def handle(body: bytes, _: dict[str, str]) -> object:
        result = await plans.apply_template(session, plans.TemplateDocument.model_validate_json(body))
        await session.commit()
        return {"result": result, "appliedVersion": await _template_version(session)}

    return await _serve(request, runtime, operation="template", scope=WRITE_SCOPE, handle=handle)


@router.post("/usage-resets")
async def grant_usage_reset(request: Request, runtime: Runtime, session: Session) -> JSONResponse:
    async def handle(body: bytes, _: dict[str, str]) -> object:
        result = await usage_resets.grant(session, usage_resets.GrantDocument.model_validate_json(body))
        await session.commit()
        return result

    return await _serve(request, runtime, operation="usage_reset", scope=WRITE_SCOPE, handle=handle)


@router.get("/status")
async def status(request: Request, runtime: Runtime, session: Session) -> JSONResponse:
    async def handle(_: bytes, query: dict[str, str]) -> object:
        if set(query) - {"after", "limit"}:
            raise ValueError("unexpected parameter")
        after = UUID(query["after"]) if query.get("after") else None
        limit = int(query.get("limit") or 100)
        if not 1 <= limit <= STATUS_PAGE_MAX:
            raise ValueError("limit out of range")
        return await _status_page(session, after, limit)

    return await _serve(request, runtime, operation="status", scope=READ_SCOPE, handle=handle)


async def _template_version(session: AsyncSession) -> int | None:
    return await session.scalar(select(EntitlementTemplate.version).order_by(EntitlementTemplate.version.desc()).limit(1))


async def _status_page(session: AsyncSession, after: UUID | None, limit: int) -> dict[str, Any]:
    query = (select(Organization.id, Organization.created_at, OrganizationEntitlement)
             .outerjoin(OrganizationEntitlement, OrganizationEntitlement.organization_id == Organization.id)
             .order_by(Organization.id).limit(limit))
    if after is not None:
        query = query.where(Organization.id > after)
    rows = (await session.execute(query)).all()
    now = datetime.now(UTC)
    periods = {organization: _plan(row).period_at(now) for organization, _, row in rows if row is not None}
    usage = {}
    if periods:
        keys = [(organization, start) for organization, (start, _) in periods.items()]
        usage = {row.organization_id: row for row in (await session.scalars(select(OrganizationUsage).where(
            tuple_(OrganizationUsage.organization_id, OrganizationUsage.period_start).in_(keys)))).all()}
    return {
        "templateVersion": await _template_version(session),
        "organizations": [_organization_status(organization, created, row, periods.get(organization),
                                               usage.get(organization)) for organization, created, row in rows],
        "next": str(rows[-1][0]) if len(rows) == limit else None,
    }


def _plan(row: OrganizationEntitlement) -> plans.Plan:
    return plans.Plan(row.version, row.status, row.period_start, row.period_end, row.valid_until, {})


def _organization_status(organization: UUID, created: datetime, row: OrganizationEntitlement | None,
                         period: tuple[datetime, datetime | None] | None, used: OrganizationUsage | None) -> dict[str, Any]:
    status: dict[str, Any] = {"organizationId": str(organization), "createdAt": created.isoformat(), "plan": None,
                              "usage": None}
    if row is None or period is None:
        return status
    status["plan"] = {"version": row.version, "status": row.status, "planCode": row.plan_code,
                      "validUntil": row.valid_until.isoformat() if row.valid_until else None}
    status["usage"] = {
        "periodStart": period[0].isoformat(), "periodEnd": period[1].isoformat() if period[1] else None,
        "tasks": used.tasks if used else 0, "aiTokens": used.ai_tokens if used else 0,
        "browserSeconds": round(used.browser_seconds, 1) if used else 0.0,
        "codingSeconds": round(used.coding_seconds, 1) if used else 0.0,
        "webResearch": used.web_research if used else 0,
    }
    return status
