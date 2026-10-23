"""Real servers on free ports: two stand-in core copies and the gateway in front of them, so
streaming, WebSockets and failover are tested over real connections."""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse

from audoryn_gateway.app import Gateway
from audoryn_gateway.settings import GatewaySettings

ADMIN = "local-admin-token-for-tests"


def make_core(name: str) -> FastAPI:
    core = FastAPI()
    core.state.ready = True

    @core.get("/health/live")
    async def live(response: Response) -> dict[str, str]:
        response.status_code = 200 if core.state.ready else 503
        return {"copy": name}

    @core.api_route("/api/v1/echo", methods=["GET", "POST"])
    async def echo(request: Request) -> dict[str, Any]:
        return {"copy": name, "method": request.method, "url": str(request.url),
                "body": (await request.body()).decode(), "headers": dict(request.headers)}

    @core.post("/internal/v1/runtime/execute")
    async def execute() -> dict[str, str]:
        return {"copy": name}

    @core.get("/api/v1/stream")
    async def stream() -> StreamingResponse:
        async def events():
            for number in range(3):
                yield f"data: {number}\n\n"
                await asyncio.sleep(0.05)
        return StreamingResponse(events(), media_type="text/event-stream")

    @core.get("/api/v1/sign-in-page")
    async def sign_in_page() -> JSONResponse:  # like the GitHub sign-in pages, which set their own
        return JSONResponse({"ok": True}, headers={"Referrer-Policy": "no-referrer"})

    @core.get("/api/v1/cookies")
    async def cookies() -> JSONResponse:
        response = JSONResponse({"ok": True})
        response.set_cookie("first", "1")
        response.set_cookie("second", "2")
        return response

    @core.websocket("/ws/v1/echo")
    async def websocket_echo(websocket: WebSocket) -> None:
        if websocket.query_params.get("access_token") != "good":
            await websocket.close(code=4401)  # before accepting, like the core's auth check
            return
        await websocket.accept()
        while True:
            message = await websocket.receive_text()
            if message == "bye":
                await websocket.close(code=4000, reason="done")
                return
            await websocket.send_text(f"{name}:{message}")

    return core


class Server:
    def __init__(self, app: Any, port: int = 0) -> None:
        self.app = app
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                                    proxy_headers=False, lifespan="on"))
        self.thread = threading.Thread(target=lambda: asyncio.run(self.server.serve()), daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("test server did not start")
            time.sleep(0.02)
        self.port: int = self.server.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(5)


@pytest.fixture(scope="session")
def cores() -> Iterator[list[Server]]:
    servers = [Server(make_core("a")), Server(make_core("b"))]
    yield servers
    for server in servers:
        server.stop()


@pytest.fixture
def gateway(cores: list[Server]) -> Iterator[Server]:
    """A gateway over both copies, with fast health checks."""
    for core in cores:
        core.app.state.ready = True
    server = Server(Gateway(GatewaySettings(
        core_upstreams=[core.url for core in cores], health_interval_seconds=0.1,  # type: ignore[arg-type]
        unhealthy_after=1, healthy_after=1, admin_token=ADMIN)))  # type: ignore[arg-type]
    yield server
    server.stop()


@pytest.fixture
def gateway_port(gateway: Server) -> int:
    return gateway.port


@pytest.fixture(scope="session")
def closed_gateway_port() -> Iterator[int]:
    """A gateway whose only copy is not running."""
    server = Server(Gateway(GatewaySettings(core_upstreams=["http://127.0.0.1:9"], connect_timeout_seconds=1)))  # type: ignore[arg-type]
    yield server.port
    server.stop()
