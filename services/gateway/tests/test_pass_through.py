from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus


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
