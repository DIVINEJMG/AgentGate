from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator

import httpx
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from audoryn_gateway.app import Gateway
from audoryn_gateway.headers import caller
from audoryn_gateway.settings import GatewaySettings
from tests.conftest import ADMIN, Server


async def _send(port: int, method: str, path: str, headers: list[tuple[str, str]] | None = None,
                content: bytes | None = None) -> httpx.Response:
    """Sends exactly these headers: httpx's defaults (Accept-Encoding among them) are left out."""
    async with httpx.AsyncClient() as client:
        request = httpx.Request(method, f"http://127.0.0.1:{port}{path}", headers=headers or [], content=content)
        return await client.send(request)


async def test_request_reaches_the_core_unchanged_with_the_callers_host(gateway_port: int):
    response = await _send(gateway_port, "POST", "/api/v1/echo?page=2&q=a%20b", [("content-type", "text/plain")], b"hello")

    seen = response.json()
    assert response.status_code == 200
    assert seen["method"] == "POST" and seen["body"] == "hello"
    # The core sees the URL the caller used, so QStash signatures and OAuth callbacks still match.
    assert seen["url"] == f"http://127.0.0.1:{gateway_port}/api/v1/echo?page=2&q=a%20b"
    assert seen["headers"]["host"] == f"127.0.0.1:{gateway_port}"
    assert "accept-encoding" not in seen["headers"]
    assert seen["headers"]["x-forwarded-proto"] == "http"
    assert response.headers["x-correlation-id"] == seen["headers"]["x-correlation-id"]


async def test_callers_cannot_set_forwarding_headers(gateway_port: int):
    response = await _send(gateway_port, "GET", "/api/v1/echo", [("x-forwarded-for", "1.2.3.4"), ("x-forwarded-proto", "https")])

    headers = response.json()["headers"]
    assert headers["x-forwarded-for"] == "127.0.0.1"
    assert headers["x-forwarded-proto"] == "http"


@pytest.mark.parametrize(("given", "kept"), [("task-1234-abcd", True), ("bad id!", False), ("", False)])
async def test_correlation_id_is_kept_only_when_well_formed(gateway_port: int, given: str, kept: bool):
    response = await _send(gateway_port, "GET", "/api/v1/echo", [("x-correlation-id", given)] if given else [])

    passed = response.json()["headers"]["x-correlation-id"]
    assert (passed == given) is kept
    assert response.headers["x-correlation-id"] == passed


async def test_streamed_replies_arrive_as_they_are_sent(gateway_port: int):
    async with httpx.AsyncClient() as client, client.stream("GET", f"http://127.0.0.1:{gateway_port}/api/v1/stream") as response:
        chunks = [chunk async for chunk in response.aiter_text()]

    assert response.headers["content-type"].startswith("text/event-stream")
    assert "".join(chunks) == "data: 0\n\ndata: 1\n\ndata: 2\n\n"


async def test_every_set_cookie_header_is_kept(gateway_port: int):
    response = await _send(gateway_port, "GET", "/api/v1/cookies")

    assert {cookie.split("=")[0] for cookie in response.headers.get_list("set-cookie")} == {"first", "second"}


async def test_unreachable_core_is_a_502(closed_gateway_port: int):
    response = await _send(closed_gateway_port, "GET", "/api/v1/echo")

    assert response.status_code == 502
    assert response.json() == {"detail": "The service is unavailable."}


async def test_gateway_answers_its_own_liveness_check(gateway_port: int):
    response = await _send(gateway_port, "GET", "/health/gateway")

    assert response.json() == {"status": "ok", "service": "audoryn-gateway",
                               "pools": {"core": {"inRotation": 2, "copies": 2},
                                         "execution": {"inRotation": 2, "copies": 2}},
                               "limits": {"mode": "off", "consoleVersion": None}}


async def test_websocket_messages_and_close_code_pass_both_ways(gateway_port: int):
    async with connect(f"ws://127.0.0.1:{gateway_port}/ws/v1/echo?access_token=good") as websocket:
        await websocket.send("hi")
        assert await websocket.recv() in ("a:hi", "b:hi")
        await websocket.send("bye")
        with pytest.raises(ConnectionClosed):
            await websocket.recv()

    assert websocket.close_code == 4000
    assert websocket.close_reason == "done"


async def test_websocket_refused_by_the_core_is_refused_by_the_gateway(gateway_port: int):
    with pytest.raises(InvalidStatus) as refused:
        async with connect(f"ws://127.0.0.1:{gateway_port}/ws/v1/echo?access_token=bad"):
            pass

    assert refused.value.response.status_code == 403


async def test_access_log_never_contains_the_query_string(gateway_port: int, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.INFO, logger="audoryn.gateway"):
        await _send(gateway_port, "GET", "/api/v1/echo?access_token=secret-value", [("authorization", "Bearer secret-token")])
        lines: list[str] = []
        for _ in range(100):  # the line is written in the server's thread just after the reply ends
            lines = [record.getMessage() for record in caplog.records if record.name == "audoryn.gateway"]
            if lines:
                break
            await asyncio.sleep(0.02)

    assert any('"path":"/api/v1/echo"' in line and '"status":200' in line for line in lines)
    assert not any("secret" in line for line in lines)


@pytest.fixture
def behind_nginx(cores: list[Server]) -> Iterator[int]:
    """A gateway as on Oracle: nginx on the same machine terminates TLS and calls it on loopback."""
    server = Server(Gateway(GatewaySettings(
        core_upstreams=[cores[0].url], trusted_proxies=["127.0.0.1"], admin_token=ADMIN)))  # type: ignore[arg-type]
    yield server.port
    server.stop()


# What nginx sends: `X-Forwarded-For $proxy_add_x_forwarded_for` (whatever the caller sent, then the
# caller's real address) and `X-Forwarded-Proto $scheme`.
NGINX = [("x-forwarded-for", "6.6.6.6, 203.0.113.9"), ("x-forwarded-proto", "https"), ("x-real-ip", "203.0.113.9")]


async def test_behind_nginx_the_core_sees_the_real_caller_over_https(behind_nginx: int):
    headers = (await _send(behind_nginx, "GET", "/api/v1/echo", NGINX)).json()["headers"]

    # The address nginx appended, never the one the caller wrote in front of it; and https, so the
    # core rebuilds the https URL that QStash signed and that OAuth callbacks are built from.
    assert headers["x-forwarded-for"] == "203.0.113.9"
    assert headers["x-forwarded-proto"] == "https"


async def test_behind_nginx_a_direct_call_on_this_machine_is_still_itself(behind_nginx: int):
    headers = (await _send(behind_nginx, "GET", "/api/v1/echo")).json()["headers"]

    assert headers["x-forwarded-for"] == "127.0.0.1"
    assert headers["x-forwarded-proto"] == "http"


async def test_behind_nginx_the_admin_controls_answer_only_on_this_machine(behind_nginx: int):
    token = [("x-gateway-admin-token", ADMIN)]

    through_nginx = await _send(behind_nginx, "GET", "/gateway/admin/copies", NGINX + token)
    on_the_machine = await _send(behind_nginx, "GET", "/gateway/admin/copies", token)

    assert through_nginx.status_code == 404  # from the internet, even with the token
    assert on_the_machine.status_code == 200


async def test_behind_nginx_websockets_reach_the_core_as_the_real_caller(behind_nginx: int):
    async with connect(f"ws://127.0.0.1:{behind_nginx}/ws/v1/echo?access_token=good",
                       additional_headers=NGINX) as socket:
        await socket.send("hi")
        assert await socket.recv() == "a:hi"


@pytest.mark.parametrize(("client", "scheme", "headers", "expected"), [
    ("10.0.0.5", "http", [(b"x-forwarded-for", b"1.2.3.4"), (b"x-forwarded-proto", b"https")], ("10.0.0.5", "http")),
    ("127.0.0.1", "wss", [], ("127.0.0.1", "https")),
    ("127.0.0.1", "http", [(b"x-forwarded-for", b"203.0.113.9, 127.0.0.1"), (b"x-forwarded-proto", b"gopher")],
     ("203.0.113.9", "http")),
])
def test_only_a_trusted_proxy_is_believed(client: str, scheme: str, headers: list[tuple[bytes, bytes]],
                                          expected: tuple[str, str]):
    assert caller(client, scheme, headers, frozenset({"127.0.0.1"})) == expected
