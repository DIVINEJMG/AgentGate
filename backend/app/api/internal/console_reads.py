"""Signed read-only routes for Audoryn Console (contract v1).

Every operation is its own declared GET route. Nothing here is reachable unless
CONSOLE_READS_ENABLED is true and a valid key is configured; otherwise each route
answers 404. See docs/integration/console-admin-reads-plan.md.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.engine import Connection

from app.application.console_reads import analytics, operations, security, support, visibility
from app.application.console_reads.database import (
    datetime_param,
    int_param,
    run_read_only,
    text_param,
    uuid_param,
)
from app.application.console_reads.guard import (
    ConsoleReadsRuntime,
    InvalidParameters,
    ReadContext,
    console_reads_runtime,
    serve,
)

router = APIRouter(prefix="/internal/console/v1", tags=["internal-console-reads"], include_in_schema=False)

Runtime = Annotated[ConsoleReadsRuntime, Depends(console_reads_runtime)]
VISIBILITY = "visibility.read"
OBSERVABILITY = "observability.read"
SECURITY = "security.read"
NOTIFICATIONS = "notifications.read"


def _now() -> datetime:
    return datetime.now(UTC)


async def _query(runtime: ConsoleReadsRuntime, context: ReadContext, query: Callable[[Connection], Any]) -> Any:
    runner = runtime.query_runner or run_read_only
    return await runner(query, timeout_ms=context.config.statement_timeout_ms)


@router.get("/health")
async def health(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        del context
        return {"compatible": True}

    return await serve(request, runtime, operation="health", scope=OBSERVABILITY, producer=produce)


# ---------- phase 3: snapshot and overview ----------

@router.get("/snapshot")
async def snapshot(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        return await _query(runtime, context, visibility.read_snapshot)

    return await serve(request, runtime, operation="snapshot", scope=VISIBILITY, producer=produce)


@router.get("/overview")
async def overview(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        now = _now()
        return await _query(runtime, context, lambda connection: visibility.read_overview(connection, now=now))

    return await serve(request, runtime, operation="overview", scope=VISIBILITY, producer=produce)


# ---------- phase 4: users and organisations ----------

@router.get("/users")
async def users(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        parameters = context.parameters
        query = text_param(parameters, "query", max_length=160)
        email = text_param(parameters, "email", max_length=320)
        registered_from = datetime_param(parameters, "registered_from")
        registered_to = datetime_param(parameters, "registered_to")
        limit = int_param(parameters, "limit", default=50, minimum=1, maximum=100)
        offset = int_param(parameters, "offset", default=0, minimum=0, maximum=1_000_000)
        return await _query(
            runtime,
            context,
            lambda connection: visibility.read_users(
                connection,
                query=query,
                email=email,
                registered_from=registered_from,
                registered_to=registered_to,
                limit=limit,
                offset=offset,
            ),
        )

    return await serve(
        request,
        runtime,
        operation="users",
        scope=VISIBILITY,
        producer=produce,
        parameters=frozenset({"query", "email", "registered_from", "registered_to", "limit", "offset"}),
    )


@router.get("/user")
async def user(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        user_id = uuid_param(context.parameters, "user_id")
        now = _now()
        return await _query(runtime, context, lambda connection: visibility.read_user(connection, user_id=user_id, now=now))

    return await serve(
        request, runtime, operation="user", scope=VISIBILITY, producer=produce, parameters=frozenset({"user_id"})
    )


@router.get("/organizations")
async def organizations(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        parameters = context.parameters
        query = text_param(parameters, "query", max_length=160)
        limit = int_param(parameters, "limit", default=50, minimum=1, maximum=100)
        offset = int_param(parameters, "offset", default=0, minimum=0, maximum=1_000_000)
        now = _now()
        return await _query(
            runtime,
            context,
            lambda connection: visibility.read_organizations(connection, query=query, limit=limit, offset=offset, now=now),
        )

    return await serve(
        request,
        runtime,
        operation="organizations",
        scope=VISIBILITY,
        producer=produce,
        parameters=frozenset({"query", "limit", "offset"}),
    )


@router.get("/organization")
async def organization(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        organization_id = uuid_param(context.parameters, "organization_id")
        now = _now()
        return await _query(
            runtime,
            context,
            lambda connection: visibility.read_organization(connection, organization_id=organization_id, now=now),
        )

    return await serve(
        request,
        runtime,
        operation="organization",
        scope=VISIBILITY,
        producer=produce,
        parameters=frozenset({"organization_id"}),
    )


# ---------- phase 5: operations and queues ----------

@router.get("/operations")
async def product_operations(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        now = _now()
        return await _query(runtime, context, lambda connection: operations.read_operations(connection, now=now))

    return await serve(request, runtime, operation="operations", scope=OBSERVABILITY, producer=produce)


@router.get("/queues")
async def queues(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        limit = int_param(context.parameters, "limit", default=50, minimum=1, maximum=100)
        return await _query(runtime, context, lambda connection: operations.read_queues(connection, limit=limit))

    return await serve(
        request, runtime, operation="queues", scope=OBSERVABILITY, producer=produce, parameters=frozenset({"limit"})
    )


# ---------- phase 6: usage and AI usage ----------

@router.get("/usage")
async def usage(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        days = int_param(context.parameters, "days", default=30, minimum=1, maximum=90)
        now = _now()
        return await _query(runtime, context, lambda connection: analytics.read_usage(connection, days=days, now=now))

    return await serve(
        request, runtime, operation="usage", scope=OBSERVABILITY, producer=produce, parameters=frozenset({"days"})
    )


@router.get("/ai-usage")
async def ai_usage(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        days = int_param(context.parameters, "days", default=30, minimum=1, maximum=90)
        limit = int_param(context.parameters, "limit", default=50, minimum=1, maximum=100)
        now = _now()
        return await _query(
            runtime, context, lambda connection: analytics.read_ai_usage(connection, days=days, limit=limit, now=now)
        )

    return await serve(
        request,
        runtime,
        operation="ai-usage",
        scope=OBSERVABILITY,
        producer=produce,
        parameters=frozenset({"days", "limit"}),
    )


# ---------- phase 7: security events and notifications ----------

@router.get("/security")
async def security_events(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        limit = int_param(context.parameters, "limit", default=100, minimum=1, maximum=200)
        return await _query(runtime, context, lambda connection: security.read_security_events(connection, limit=limit))

    return await serve(
        request, runtime, operation="security", scope=SECURITY, producer=produce, parameters=frozenset({"limit"})
    )


@router.get("/notifications")
async def notifications(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        limit = int_param(context.parameters, "limit", default=40, minimum=1, maximum=40)
        return await _query(runtime, context, lambda connection: security.read_notifications(connection, limit=limit))

    return await serve(
        request,
        runtime,
        operation="notifications",
        scope=NOTIFICATIONS,
        producer=produce,
        parameters=frozenset({"limit"}),
    )


# ---------- phase 8: search and support ----------

@router.get("/search")
async def search(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        query = text_param(context.parameters, "query", max_length=180)
        if query is None or len(query) < 2:
            raise InvalidParameters("query")
        limit = int_param(context.parameters, "limit_per_type", default=6, minimum=1, maximum=20)
        return await _query(
            runtime, context, lambda connection: support.read_search(connection, query=query, limit_per_type=limit)
        )

    return await serve(
        request,
        runtime,
        operation="search",
        scope=VISIBILITY,
        producer=produce,
        parameters=frozenset({"query", "limit_per_type"}),
    )


@router.get("/support-user")
async def support_user(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        user_id = uuid_param(context.parameters, "user_id")
        return await _query(runtime, context, lambda connection: support.read_support_user(connection, user_id=user_id))

    return await serve(
        request,
        runtime,
        operation="support-user",
        scope=VISIBILITY,
        producer=produce,
        parameters=frozenset({"user_id"}),
    )


@router.get("/support-organization")
async def support_organization(request: Request, runtime: Runtime) -> Response:
    async def produce(context: ReadContext) -> object:
        organization_id = uuid_param(context.parameters, "organization_id")
        now = _now()
        return await _query(
            runtime,
            context,
            lambda connection: support.read_support_organization(connection, organization_id=organization_id, now=now),
        )

    return await serve(
        request,
        runtime,
        operation="support-organization",
        scope=VISIBILITY,
        producer=produce,
        parameters=frozenset({"organization_id"}),
    )
