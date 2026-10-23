"""Phase 3 (architecture plan): one image, three roles. The api process answers people and sends
due steps straight to the execution process, signed; QStash stays the fallback and the delayed path."""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from audoryn_execution import service as execution_service
from audoryn_execution.api.internal import runtime as runtime_routes
from audoryn_shared.application import internal_calls
from audoryn_shared.application.console_entitlements import ChannelGuard
from audoryn_shared.application.console_reads.signing import ServiceRequestRejected
from audoryn_shared.runtime import outbox_trigger, qstash_trigger
from tests.test_runtime_run_local import _Coordinator

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "gateway"))
from audoryn_gateway.routes import pool_for

HEALTH = {"/health/live", "/health/ready"}
Client = httpx.AsyncClient  # the real one, while a test replaces the class the caller uses


def _paths(role: str) -> set[str]:
    from app.main import create_application

    def walk(route) -> list[str]:
        nested = getattr(route, "effective_candidates", None)  # an included router
        return [path for child in nested() for path in walk(child)] if nested else [route.path] if route.path else []
    return {path for route in create_application(role).routes for path in walk(route)} - {
        "/openapi.json", "/docs", "/docs/oauth2-redirect"}


def test_each_role_mounts_exactly_its_routes():
    api, execution, every = _paths("api"), _paths("execution"), _paths("all")
    assert api & execution == HEALTH and api | execution == every
    assert all(pool_for(path) == "execution" for path in execution - HEALTH)  # what the gateway sends there
    assert all(pool_for(path) == "core" for path in api - HEALTH)
    assert "/internal/v1/runtime/execute" in execution and "/api/v1/organizations" in api


def _secret() -> SecretStr:
    return SecretStr(base64.urlsafe_b64encode(bytes(range(32))).decode())


def _config(*, current: str = "k2", previous: str | None = "k1"):
    return internal_calls.load_config(SimpleNamespace(
        internal_call_key_id=current, internal_call_key_secret=_secret(),
        internal_call_previous_key_id=previous, internal_call_previous_key_secret=_secret() if previous else None))


class Replays:
    """The Redis the replay records go to."""

    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        if key in self.keys:
            return False
        self.keys.add(key)
        return True


async def _verify(headers: dict[str, str], body: bytes = b"{}", **options) -> None:
    await internal_calls.verify(scope=internal_calls.EXECUTE_SCOPE, method="POST", target=internal_calls.EXECUTE_PATH,
                                body=body, headers=headers.items(), config=options.get("config", _config()),
                                guard=options.get("guard") or ChannelGuard(Replays(), namespace="internal-calls"))


def _signed(config=None, body: bytes = b"{}", now: int | None = None, target: str = internal_calls.EXECUTE_PATH):
    return internal_calls.signed_headers(config or _config(), scope=internal_calls.EXECUTE_SCOPE, method="POST",
                                         target=target, body=body, now=now)


@pytest.mark.asyncio
async def test_a_signed_call_is_accepted_once_and_the_previous_key_still_works():
    guard = ChannelGuard(Replays(), namespace="internal-calls")
    headers = _signed()
    await _verify(headers, guard=guard)
    with pytest.raises(ServiceRequestRejected, match="replayed"):
        await _verify(headers, guard=guard)
    await _verify(_signed(_config(current="k1", previous=None)))  # a process not yet given the new key


@pytest.mark.asyncio
@pytest.mark.parametrize("case, reason", [
    ("unsigned", "unknown_key"), ("retired key", "unknown_key"), ("old", "expired"),
    ("other body", "bad_signature"), ("other path", "bad_signature"), ("calls off", "internal_calls_off")])
async def test_anything_else_is_refused(case: str, reason: str):
    headers, body, config = _signed(), b"{}", _config()
    if case == "unsigned":
        headers = {}
    if case == "retired key":
        headers = _signed(_config(current="k0", previous=None))
    if case == "old":
        headers = _signed(now=int(time.time()) - 600)
    if case == "other body":
        body = b'{"expected_step":9}'
    if case == "other path":
        headers = _signed(target="/internal/v1/runtime/sweep")
    if case == "calls off":
        config = internal_calls.load_config(SimpleNamespace(internal_call_key_id=None, internal_call_key_secret=None,
                                                            internal_call_previous_key_id=None,
                                                            internal_call_previous_key_secret=None))
    with pytest.raises(ServiceRequestRejected, match=reason):
        await _verify(headers, body, config=config)


def test_a_console_entitlements_signature_never_authorises_a_step():
    from audoryn_shared.application.console_entitlements import sign

    key = next(iter(_config().keys.values()))
    ours = _signed(now=1_700_000_000)
    theirs = sign(key, scope=internal_calls.EXECUTE_SCOPE, method="POST", target=internal_calls.EXECUTE_PATH,
                  timestamp="1700000000", nonce=ours["x-audoryn-service-nonce"], body=b"{}")
    assert theirs != ours["x-audoryn-service-signature"]


@pytest.fixture
def execution(monkeypatch: pytest.MonkeyPatch):
    """Two execution processes (their own apps) sharing one Redis; started steps are recorded."""
    replays, coordinator, started = Replays(), _Coordinator(set()), []
    monkeypatch.setattr(internal_calls, "_config", _config)
    monkeypatch.setattr(internal_calls, "_guard", lambda: ChannelGuard(replays, namespace="internal-calls"))
    monkeypatch.setattr(runtime_routes.settings, "runtime_execution_enabled", True)
    monkeypatch.setattr(runtime_routes.CutoverController, "require_authoritative", lambda self, part: None)
    monkeypatch.setattr(runtime_routes.RedisCoordinator, "from_settings", classmethod(lambda cls: coordinator))
    monkeypatch.setattr(runtime_routes, "record_usage", lambda *args, **kwargs: _nothing())

    async def start(message, *args, **kwargs):
        started.append(message.expected_step)

    monkeypatch.setattr(runtime_routes, "_start_runtime_task", start)
    apps = []
    for _ in range(2):
        app = FastAPI()
        for router in execution_service.ROUTERS:
            app.include_router(router)
        apps.append(app)

    def client(index: int = 0) -> httpx.AsyncClient:
        return Client(transport=httpx.ASGITransport(app=apps[index]), base_url="http://execution")
    return SimpleNamespace(client=client, started=started)


async def _nothing() -> None:
    return None


def _step(step: int = 3) -> bytes:
    return json.dumps({"organization_id": str(uuid4()), "work_item_id": str(uuid4()), "expected_step": step,
                       "reason": "continuation-plan"}, separators=(",", ":"), sort_keys=True).encode()


@pytest.mark.asyncio
async def test_the_same_step_sent_to_two_execution_processes_runs_once(execution):
    body = _step()
    answers = []
    for index in (0, 1):
        async with execution.client(index) as client:
            answers.append((await client.post(internal_calls.EXECUTE_PATH, content=body,
                                              headers=_signed(body=body))).json()["status"])
    assert answers == ["accepted", "busy"] and execution.started == [3]
    async with execution.client() as client:
        unsigned = await client.post(internal_calls.EXECUTE_PATH, content=_step())
    assert unsigned.status_code == 401  # neither QStash's signature nor ours


@pytest.mark.asyncio
async def test_the_api_process_sends_a_due_step_straight_to_execution(execution, monkeypatch: pytest.MonkeyPatch):
    published = []

    class _Provider:
        async def publish(self, **kwargs):
            published.append(kwargs)
            return SimpleNamespace(id="qstash-message")

    async def ran_here(*args):
        raise AssertionError("an api process never runs a step itself")

    monkeypatch.setattr(qstash_trigger.settings, "service_role", "api")
    monkeypatch.setattr(qstash_trigger.settings, "execution_call_url", "http://execution")
    monkeypatch.setattr(qstash_trigger.settings, "qstash_runtime_execute_url", "https://agentgate.test/internal/v1/runtime/execute")
    monkeypatch.setattr(qstash_trigger.UpstashQStashProvider, "from_settings", classmethod(lambda cls: _Provider()))
    monkeypatch.setattr(qstash_trigger, "_clear_dispatch_health", _nothing)
    monkeypatch.setattr(qstash_trigger, "local_runner", ran_here)
    monkeypatch.setattr(outbox_trigger, "serving", True)
    monkeypatch.setattr(internal_calls.httpx, "AsyncClient", lambda timeout: execution.client())

    async def signal(**options):
        return await qstash_trigger.request_runtime_execution_detailed(
            organization_id=uuid4(), work_item_id=uuid4(), expected_step=5, reason="continuation-plan", **options)

    assert (await signal()).message_id == "direct:accepted" and execution.started == [5] and published == []
    assert (await signal(delay_seconds=30)).message_id == "qstash-message"  # a delay is QStash's job

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("execution is down")

    monkeypatch.setattr(internal_calls.httpx, "AsyncClient",
                        lambda timeout: Client(transport=httpx.MockTransport(down)))
    assert (await signal()).message_id == "qstash-message" and len(published) == 2  # the fallback


@pytest.mark.asyncio
@pytest.mark.parametrize("role, recovers", [("api", False), ("execution", True), ("all", True)])
async def test_only_a_process_that_runs_preparations_recovers_them(monkeypatch: pytest.MonkeyPatch, role: str,
                                                                     recovers: bool):
    import asyncio

    from app.main import create_application
    from audoryn_execution.runtime import preparation_worker
    from audoryn_shared.bootstrap import lifecycle

    started = []

    async def recovery() -> None:
        started.append(role)
        await asyncio.Event().wait()

    monkeypatch.setattr(lifecycle.settings, "database_migrate_on_startup", False)
    monkeypatch.setattr(lifecycle.settings, "smart_planner_enabled", True)
    monkeypatch.setattr(lifecycle.settings, "integration_foundation_enabled", True)
    monkeypatch.setattr(lifecycle.settings, "delivery_watchdog_seconds", 0)
    monkeypatch.setattr(preparation_worker, "preparation_recovery_loop", recovery)
    monkeypatch.setattr(preparation_worker, "stop_preparations", _nothing)
    monkeypatch.setattr(execution_service, "stop_runtime_tasks", lambda seconds: _nothing())
    monkeypatch.setattr(execution_service, "execution_provider_registry", lambda: SimpleNamespace(providers=list))
    application = create_application(role)
    assert (application.state.execution is execution_service) is recovers
    async with lifecycle.lifespan(application):
        await asyncio.sleep(0)
    assert started == ([role] if recovers else [])


def test_the_core_runs_without_playwright_or_e2b():
    """The core image installs neither (Phase 4): the api app must build, and list every provider's
    capabilities, without them. Run in a fresh interpreter, where both imports fail."""
    import subprocess

    script = ("import sys; sys.modules['playwright'] = None; sys.modules['e2b'] = None\n"
              "from app.main import create_application; create_application('api')\n"
              "from audoryn_shared.execution.bootstrap import execution_provider_registry as r\n"
              "print(sorted(p.manifest.provider for p in r().providers()))")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False,
                            cwd=Path(__file__).resolve().parents[1], env={**__import__("os").environ, "SERVICE_ROLE": "api"})
    assert result.returncode == 0, result.stderr[-2000:]
    assert "'browser'" in result.stdout and "'github'" in result.stdout


def test_the_shared_migration_runner_finds_the_core_s_migrations():
    from audoryn_shared.infrastructure.database.migration_runner import _BACKEND_ROOT

    assert (_BACKEND_ROOT / "alembic.ini").is_file() and (_BACKEND_ROOT / "migrations").is_dir()
