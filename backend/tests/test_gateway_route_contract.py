"""The gateway's route table covers every route the backend serves, each in the right pool
(docs/plans/architecture-scale-plan.md, sections 3 and 5.3). A new route outside the table would
get the gateway's 404, so this fails first."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "gateway"))

from audoryn_gateway.routes import pool_for

from app.main import app

EXECUTION = ("/internal/v1/runtime/", "/internal/v1/integrations/", "/internal/v1/web-research/",
             "/internal/v1/outbox/")


def _paths(route) -> list[str]:
    nested = getattr(route, "effective_candidates", None)  # an included router
    if nested is not None:
        return [path for child in nested() for path in _paths(child)]
    return [route.path] if route.path else []


def test_every_backend_route_has_its_pool():
    paths = sorted({path for route in app.router.routes for path in _paths(route)})
    assert len(paths) > 250  # the walk found the included routers, not only the top level

    unrouted = [path for path in paths if pool_for(path) is None]
    misplaced = [path for path in paths if pool_for(path) != ("execution" if path.startswith(EXECUTION) else "core")]
    assert not unrouted, f"Add these to services/gateway/audoryn_gateway/routes.py: {unrouted}"
    assert not misplaced, misplaced


def test_the_realtime_websocket_is_routed_to_the_core():
    assert pool_for("/ws/v1/organizations/00000000-0000-0000-0000-000000000001") == "core"


def test_the_gateway_answers_cors_with_the_core_s_rules():
    from audoryn_gateway.edge import CORS_RULES as GATEWAY

    from app.main import CORS_RULES as CORE

    assert GATEWAY == CORE


def test_the_core_s_cors_is_on_by_default_and_can_be_left_to_the_gateway(monkeypatch):
    from fastapi.middleware.cors import CORSMiddleware

    from app.main import create_application
    from audoryn_shared.bootstrap.settings import settings

    def has_cors() -> bool:
        return any(middleware.cls is CORSMiddleware for middleware in create_application().user_middleware)

    assert settings.cors_enabled is True and has_cors()
    monkeypatch.setattr(settings, "cors_enabled", False)
    assert not has_cors()


def test_the_console_traffic_limits_document_is_the_one_the_gateway_reads():
    from audoryn_gateway.limits import PolicyDocument

    from app.application.gateway_limits import TrafficLimitsDocument

    assert set(TrafficLimitsDocument.model_fields) == set(PolicyDocument.model_fields)


@pytest.mark.skipif(not os.environ.get("GATEWAY_TEST_REDIS_URL"), reason="needs a real Redis (GATEWAY_TEST_REDIS_URL)")
async def test_what_agentgate_stores_on_a_real_redis_the_gateway_applies():
    from audoryn_gateway.limits import DEFAULT_POLICIES, Limiter
    from redis.asyncio import Redis

    from app.application import gateway_limits

    prefix = f"audoryn:gateway-test:{uuid.uuid4().hex[:16]}"
    client = Redis.from_url(os.environ["GATEWAY_TEST_REDIS_URL"], decode_responses=True, socket_timeout=3)
    try:
        def body(version: int) -> bytes:
            return json.dumps({"version": version, "mode": "enforce", "policies": {
                "auth": {"rules": [{"key": "ip", "limit": 7, "window_seconds": 300}], "fail_closed": True}}}).encode()

        assert await gateway_limits.apply(client, prefix, body(2)) == {"result": "applied", "appliedVersion": 2}
        assert await gateway_limits.apply(client, prefix, body(1)) == {"result": "stale", "appliedVersion": 2}
        assert await gateway_limits.applied_version(client, prefix) == 2
        limiter = Limiter(client, b"s", DEFAULT_POLICIES, "off", prefix=prefix)
        await limiter.refresh()
        assert limiter.console_version == 2 and limiter.mode == "enforce"
        assert limiter.policies["auth"].rules[0].limit == 7
    finally:
        await client.delete(gateway_limits.key(prefix))
        await client.aclose()
