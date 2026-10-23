"""The AgentGate process for SERVICE_ROLE (architecture plan, Phases 3 and 4): the core API ("api"),
the execution service ("execution"), or both in one process ("all", the single service).

The core loads the execution service by name, and only when this process runs steps, so the core
API's image needs neither the step runtime nor Playwright."""
import importlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.github_auth_routes import GitHubOAuthQueryMiddleware
from app.api.router import CORE_ROUTERS
from audoryn_shared.bootstrap.application import build_application
from audoryn_shared.bootstrap.settings import settings

EXECUTION_SERVICE = "audoryn_execution.service"
# Kept identical to the gateway's (services/gateway, tests/test_gateway_route_contract.py), which
# answers CORS instead once it fronts this service.
CORS_RULES = {
    "allow_credentials": True,
    "allow_methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    "allow_headers": ["Authorization", "Content-Type", "Idempotency-Key", "X-Correlation-ID"],
    # Which plan limit refused new work (402), so the app can show its upgrade prompt.
    "expose_headers": ["X-Plan-Limit"],
}


def create_application(role: str | None = None) -> FastAPI:
    role = role or settings.service_role
    if role == "execution":
        return importlib.import_module(EXECUTION_SERVICE).create_application()
    execution = importlib.import_module(EXECUTION_SERVICE) if role == "all" else None
    cors = [(CORSMiddleware, {"allow_origins": list(settings.cors_allowed_origins), **CORS_RULES})]
    return build_application(
        (*CORE_ROUTERS, *(execution.ROUTERS if execution else ())),
        middleware=[*(cors if settings.cors_enabled else []), (GitHubOAuthQueryMiddleware, {})],
        execution=execution,
    )


app = create_application()
