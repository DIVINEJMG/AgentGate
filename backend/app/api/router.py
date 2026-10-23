from fastapi import APIRouter

from app.api.github_routes import callback_router as github_callback_router
from app.api.internal.ai import router as internal_ai_router
from app.api.internal.console_entitlements import router as internal_console_entitlements_router
from app.api.internal.console_reads import router as internal_console_reads_router
from app.api.internal.migration import router as internal_migration_router
from app.api.realtime_routes import ws_router as realtime_ws_router
from app.api.v1.router import router as v1_router
from app.api.v2.router import router as v2_router
from audoryn_shared.api.integration_foundation_routes import (
    event_router as integration_event_router,
)

# The core API's routes (architecture plan, section 3.2). The execution service's are in
# app/execution_service.py; the gateway routes each group to its pool (services/gateway, routes.py).
CORE_ROUTERS: tuple[tuple[APIRouter, str], ...] = (
    (integration_event_router, ""),
    (github_callback_router, ""),
    (realtime_ws_router, ""),
    (internal_ai_router, ""),
    (internal_console_reads_router, ""),
    (internal_console_entitlements_router, ""),
    (internal_migration_router, ""),
    (v1_router, "/api/v1"),
    (v2_router, "/api/v2"),
)
