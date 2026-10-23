from __future__ import annotations

import httpx
import pytest
from websockets.asyncio.client import connect

from audoryn_gateway.app import Gateway
from audoryn_gateway.edge import at_the_edge
from audoryn_gateway.settings import GatewaySettings
from tests.conftest import Server

APP = "http://localhost:5173"


def _edge(cores: list[Server], **switches: object) -> Server:
    settings = GatewaySettings(core_upstreams=[core.url for core in cores], cors_allowed_origins=[APP],  # type: ignore[arg-type]
                               **switches)  # type: ignore[arg-type]
    return Server(at_the_edge(Gateway(settings), settings))


async def _get(server: Server, method: str, path: str, headers: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.request(method, f"http://127.0.0.1:{server.port}{path}", headers=headers)


async def test_with_the_edge_off_replies_carry_nothing_new(cores: list[Server]):
    server = _edge(cores)
    try:
        reply = await _get(server, "GET", "/api/v1/echo", {"Origin": APP})
        assert "access-control-allow-origin" not in reply.headers
        assert "x-content-type-options" not in reply.headers
    finally:
        server.stop()


async def test_cors_answers_like_the_core(cores: list[Server]):
    server = _edge(cores, cors_enabled=True)
    try:
        preflight = await _get(server, "OPTIONS", "/api/v1/echo", {
            "Origin": APP, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,idempotency-key"})
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == APP
        assert preflight.headers["access-control-allow-credentials"] == "true"
        refused = await _get(server, "OPTIONS", "/api/v1/echo", {
            "Origin": "https://elsewhere.example", "Access-Control-Request-Method": "GET"})
        assert refused.status_code == 400
        reply = await _get(server, "GET", "/api/v1/echo", {"Origin": APP})
        assert reply.headers.get_list("access-control-allow-origin") == [APP]  # once, never twice
        assert reply.headers["access-control-expose-headers"] == "X-Plan-Limit"
        assert "Origin" in reply.headers["vary"]
    finally:
        server.stop()


async def test_security_headers_are_added_without_replacing_a_reply_s_own(cores: list[Server]):
    server = _edge(cores, security_headers=True, hsts_seconds=31536000)
    try:
        reply = await _get(server, "GET", "/api/v1/echo", {})
        assert reply.headers["x-content-type-options"] == "nosniff"
        assert reply.headers["x-frame-options"] == "DENY"
        assert reply.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert reply.headers["strict-transport-security"].startswith("max-age=31536000")
        own = await _get(server, "GET", "/api/v1/sign-in-page", {})
        assert own.headers.get_list("referrer-policy") == ["no-referrer"]
    finally:
        server.stop()


async def test_hsts_is_sent_only_when_configured(cores: list[Server]):
    server = _edge(cores, security_headers=True)
    try:
        assert "strict-transport-security" not in (await _get(server, "GET", "/api/v1/echo", {})).headers
    finally:
        server.stop()


async def test_websockets_pass_through_the_edge(cores: list[Server]):
    server = _edge(cores, cors_enabled=True, security_headers=True)
    try:
        async with connect(f"ws://127.0.0.1:{server.port}/ws/v1/echo?access_token=good", origin=APP) as websocket:  # type: ignore[arg-type]
            await websocket.send("hi")
            assert await websocket.recv() in ("a:hi", "b:hi")
    finally:
        server.stop()


@pytest.mark.parametrize("given", ['https://app.example, https://console.example',
                                   '["https://app.example", "https://console.example"]'])
def test_cors_origins_read_like_the_core_s(monkeypatch: pytest.MonkeyPatch, given: str):
    monkeypatch.setenv("GATEWAY_CORS_ALLOWED_ORIGINS", given)
    assert GatewaySettings().cors_allowed_origins == ["https://app.example", "https://console.example"]
