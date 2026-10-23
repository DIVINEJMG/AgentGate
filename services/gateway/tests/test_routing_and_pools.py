from __future__ import annotations

import asyncio
from collections import Counter

import httpx
import pytest
from websockets.asyncio.client import connect

from audoryn_gateway.app import Gateway, _may_retry
from audoryn_gateway.routes import pool_for
from audoryn_gateway.settings import GatewaySettings
from tests.conftest import ADMIN, Server

DEAD = "http://127.0.0.1:9"


async def _copies_seen(port: int, path: str = "/api/v1/echo", method: str = "GET", count: int = 12) -> Counter[str]:
    async with httpx.AsyncClient() as client:
        replies = [await client.request(method, f"http://127.0.0.1:{port}{path}") for _ in range(count)]
    assert all(reply.status_code == 200 for reply in replies)
    return Counter(reply.json()["copy"] for reply in replies)


async def _admin(port: int, method: str, path: str, token: str | None = ADMIN) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.request(method, f"http://127.0.0.1:{port}/gateway/admin/{path}",
                                    headers={"x-gateway-admin-token": token} if token else {})


@pytest.mark.parametrize(("path", "pool"), [
    ("/internal/v1/runtime/execute", "execution"), ("/internal/v1/outbox/drain", "execution"),
    ("/internal/v1/integrations/process", "execution"), ("/internal/v1/web-research/execute", "execution"),
    ("/internal/console/v1/organizations", "core"), ("/internal/console/entitlements/v1/plans", "core"),
    ("/api/v2/organizations/1/news", "core"), ("/ws/v1/organizations/1", "core"), ("/health/ready", "core"),
    ("/integrations/github/callback", "core"), ("/docs/oauth2-redirect", "core"), ("/openapi.json", "core"),
    ("/integrations/github/callbackx", None), ("/gateway/anything", None), ("/", None), ("/api/v3/x", None),
])
def test_route_table(path: str, pool: str | None):
    assert pool_for(path) == pool


async def test_a_path_no_service_serves_is_a_404_from_the_gateway(gateway_port: int):
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://127.0.0.1:{gateway_port}/wp-admin/login.php")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


async def test_requests_are_spread_over_the_copies(gateway_port: int):
    assert set(await _copies_seen(gateway_port)) == {"a", "b"}


async def test_a_copy_that_fails_its_checks_leaves_rotation_and_comes_back(gateway_port: int, cores: list[Server]):
    cores[0].app.state.ready = False
    await asyncio.sleep(0.5)
    assert set(await _copies_seen(gateway_port)) == {"b"}

    cores[0].app.state.ready = True
    await asyncio.sleep(0.5)
    assert set(await _copies_seen(gateway_port)) == {"a", "b"}


async def test_a_drained_copy_gets_no_new_requests_until_restored(gateway_port: int):
    drained = await _admin(gateway_port, "POST", "copies/core-0/drain")
    assert drained.json()["draining"] is True
    assert set(await _copies_seen(gateway_port)) == {"b"}
    async with connect(f"ws://127.0.0.1:{gateway_port}/ws/v1/echo?access_token=good") as websocket:
        await websocket.send("hi")
        assert await websocket.recv() == "b:hi"

    listed = (await _admin(gateway_port, "GET", "copies")).json()["copies"]
    assert {copy["name"]: copy["draining"] for copy in listed} == {"core-0": True, "core-1": False}

    await _admin(gateway_port, "POST", "copies/core-0/restore")
    assert set(await _copies_seen(gateway_port)) == {"a", "b"}


async def test_the_last_copy_in_rotation_cannot_be_drained(gateway_port: int):
    assert (await _admin(gateway_port, "POST", "copies/core-0/drain")).status_code == 200
    refused = await _admin(gateway_port, "POST", "copies/core-1/drain")

    assert refused.status_code == 409
    assert set(await _copies_seen(gateway_port)) == {"b"}
    await _admin(gateway_port, "POST", "copies/core-0/restore")


@pytest.mark.parametrize("token", [None, "wrong-token"])
async def test_admin_controls_need_the_token_and_look_absent_without_it(gateway_port: int, token: str | None):
    response = await _admin(gateway_port, "POST", "copies/core-0/drain", token=token)

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert set(await _copies_seen(gateway_port)) == {"a", "b"}


async def test_admin_controls_are_off_without_a_configured_token(cores: list[Server]):
    server = Server(Gateway(GatewaySettings(core_upstreams=[cores[0].url])))  # type: ignore[arg-type]
    try:
        assert (await _admin(server.port, "GET", "copies")).status_code == 404
    finally:
        server.stop()


async def test_a_request_that_never_reached_a_copy_goes_to_another(cores: list[Server]):
    server = Server(Gateway(GatewaySettings(core_upstreams=[DEAD, cores[1].url], connect_timeout_seconds=1)))  # type: ignore[arg-type]
    try:
        # A POST is retried only because it was never sent: the first copy refused the connection.
        assert set(await _copies_seen(server.port, "/internal/v1/runtime/execute", "POST", count=4)) == {"b"}
        async with httpx.AsyncClient() as client:
            health = (await client.get(f"http://127.0.0.1:{server.port}/health/gateway")).json()
        assert health["pools"]["core"] == {"inRotation": 1, "copies": 2}
    finally:
        server.stop()


async def test_the_execution_pool_can_hold_its_own_copies(cores: list[Server]):
    server = Server(Gateway(GatewaySettings(core_upstreams=[cores[0].url], execution_upstreams=[cores[1].url])))  # type: ignore[arg-type]
    try:
        assert set(await _copies_seen(server.port, "/internal/v1/runtime/execute", "POST")) == {"b"}
        assert set(await _copies_seen(server.port)) == {"a"}
    finally:
        server.stop()


@pytest.mark.parametrize(("error", "method", "has_body", "retried"), [
    (httpx.ConnectError("refused"), "POST", True, True),  # never sent
    (httpx.ReadError("reset"), "POST", True, False),  # may have arrived
    (httpx.ReadError("reset"), "GET", False, True),  # no side effects
    (httpx.ReadError("reset"), "OPTIONS", True, False),  # its body can't be sent again
    (httpx.ReadTimeout("slow"), "GET", False, True),
])
def test_retry_rule(error: httpx.HTTPError, method: str, has_body: bool, retried: bool):
    assert _may_retry(error, method, has_body) is retried


class DroppingCopy:
    """A copy that closes the first echo connection without answering, as a copy does with an
    idle connection it is closing just as the gateway reuses it. Health checks are answered."""

    def __init__(self) -> None:
        self.echo_requests: list[str] = []

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            head = await reader.readuntil(b"\r\n\r\n")
            line = head.split(b"\r\n", 1)[0].decode()
            length = next((int(h.split(b":")[1]) for h in head.split(b"\r\n") if h.lower().startswith(b"content-length")), 0)
            await reader.readexactly(length)
            if "/api/v1/echo" in line:
                self.echo_requests.append(line.split()[0])
                if len(self.echo_requests) == 1 or line.startswith("POST"):
                    writer.close()
                    return
            writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: 2\r\n\r\n{}")
            await writer.drain()


async def test_a_dropped_connection_is_retried_on_the_only_copy_but_a_post_never_twice():
    copy = DroppingCopy()
    server = await asyncio.start_server(copy.serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    gateway = Server(Gateway(GatewaySettings(core_upstreams=[f"http://127.0.0.1:{port}"],  # type: ignore[list-item]
                                             health_interval_seconds=300)))
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            read = await client.get(f"{gateway.url}/api/v1/echo")  # no other copy: the same one, afresh
            written = await client.post(f"{gateway.url}/api/v1/echo", content=b"x")
        assert read.status_code == 200 and written.status_code == 502
        assert copy.echo_requests == ["GET", "GET", "POST"]  # the POST that may have arrived was not resent
    finally:
        gateway.stop()
        server.close()


def test_idle_connections_to_the_copies_end_before_the_copies_close_them():
    import uvicorn

    copies_keep_alive = uvicorn.Config(app="app.main:app").timeout_keep_alive  # the copies run uvicorn's default
    settings = GatewaySettings()
    assert settings.upstream_keepalive_seconds < copies_keep_alive < settings.keep_alive_seconds
