"""Console reads phase 2: switch, signed-request verification, replay, rate limit, envelope."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import SecretStr
from referencing import Registry, Resource

from app.application.console_reads.config import load_config
from app.application.console_reads.guard import (
    ConsoleReadsRuntime,
    InvalidParameters,
    console_reads_runtime,
    serve,
)
from app.application.console_reads.signing import (
    ReplayStoreUnavailable,
    ServiceKey,
    ServiceRequestRejected,
    sign,
    verify_read_request,
)
from app.main import app

CONTRACT_DIR = Path(__file__).resolve().parents[2] / "docs" / "integration" / "console-reads"
SCHEMA = json.loads((CONTRACT_DIR / "contract.v1.json").read_text(encoding="utf-8"))
VECTOR = json.loads((CONTRACT_DIR / "signing-vector.json").read_text(encoding="utf-8"))
NOW = 1_791_626_400
SECRET = b"k" * 32
SECRET_TEXT = base64.urlsafe_b64encode(SECRET).decode()
HEALTH = "/internal/console/v1/health"


def _schema(definition: str) -> Draft202012Validator:
    registry = Registry().with_resource(SCHEMA["$id"], Resource.from_contents(SCHEMA))
    return Draft202012Validator({"$ref": f"{SCHEMA['$id']}#/$defs/{definition}"}, registry=registry)


class MemoryReplay:
    def __init__(self, *, down: bool = False) -> None:
        self.used: set[tuple[str, str]] = set()
        self.down = down
        self.ttls: list[int] = []

    async def claim(self, *, key_id: str, nonce: str, ttl_seconds: int) -> bool:
        if self.down:
            raise ReplayStoreUnavailable("ConnectionError")
        self.ttls.append(ttl_seconds)
        if (key_id, nonce) in self.used:
            return False
        self.used.add((key_id, nonce))
        return True


class MemoryRate:
    def __init__(self) -> None:
        self.counts: dict[tuple[str, int], int] = {}

    async def hit(self, *, key_id: str, limit: int, now: int) -> int | None:
        window = now // 60
        self.counts[(key_id, window)] = self.counts.get((key_id, window), 0) + 1
        return None if self.counts[(key_id, window)] <= limit else (window + 1) * 60 - now


def _settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "console_reads_enabled": True,
        "console_reads_audience": "agentgate",
        "console_reads_key_id": "console-reads-1",
        "console_reads_key_secret": SecretStr(SECRET_TEXT),
        "console_reads_key_scopes": "visibility.read,observability.read",
        "console_reads_previous_key_id": None,
        "console_reads_previous_key_secret": None,
        "console_reads_previous_key_scopes": "",
        "console_reads_clock_skew_seconds": 60,
        "console_reads_rate_per_minute": 120,
        "console_reads_statement_timeout_ms": 3000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _runtime(**overrides: object) -> ConsoleReadsRuntime:
    replay = overrides.pop("replay", MemoryReplay())
    rate = overrides.pop("rate", MemoryRate())
    return ConsoleReadsRuntime(config=load_config(_settings(**overrides)), replay=replay, rate_limiter=rate, clock=lambda: NOW)  # type: ignore[arg-type]


KEY = ServiceKey(key_id="console-reads-1", secret=SECRET, audience="agentgate", scopes=frozenset({"visibility.read", "observability.read"}))


def _headers(target: str, *, key: ServiceKey = KEY, scope: str = "observability.read", timestamp: int = NOW, nonce: str = "a" * 32, **replace: str) -> dict[str, str]:
    headers = {
        "X-Audoryn-Service-Key": key.key_id,
        "X-Audoryn-Service-Audience": key.audience,
        "X-Audoryn-Service-Scope": scope,
        "X-Audoryn-Service-Timestamp": str(timestamp),
        "X-Audoryn-Service-Nonce": nonce,
        "X-Audoryn-Service-Signature": sign(key, scope=scope, target=target, timestamp=str(timestamp), nonce=nonce),
    }
    for name, value in replace.items():
        headers["X-Audoryn-Service-" + name] = value
    return headers


@pytest.fixture
def client():
    runtime = _runtime()
    app.dependency_overrides[console_reads_runtime] = lambda: runtime
    try:
        yield TestClient(app), runtime
    finally:
        app.dependency_overrides.pop(console_reads_runtime, None)


# ---------- shared vector ----------

def test_shared_signing_vector_matches() -> None:
    secret = base64.urlsafe_b64decode(VECTOR["secretBase64url"] + "=" * (-len(VECTOR["secretBase64url"]) % 4))
    key = ServiceKey(key_id=VECTOR["keyId"], secret=secret, audience=VECTOR["audience"], scopes=frozenset({VECTOR["scope"]}))
    assert sign(key, scope=VECTOR["scope"], target=VECTOR["target"], timestamp=VECTOR["timestamp"], nonce=VECTOR["nonce"]) == VECTOR["signature"]


async def test_shared_signing_vector_verifies() -> None:
    secret = base64.urlsafe_b64decode(VECTOR["secretBase64url"] + "=" * (-len(VECTOR["secretBase64url"]) % 4))
    key = ServiceKey(key_id=VECTOR["keyId"], secret=secret, audience=VECTOR["audience"], scopes=frozenset({VECTOR["scope"]}))
    headers = [
        ("x-audoryn-service-key", VECTOR["keyId"]),
        ("x-audoryn-service-audience", VECTOR["audience"]),
        ("x-audoryn-service-scope", VECTOR["scope"]),
        ("x-audoryn-service-timestamp", VECTOR["timestamp"]),
        ("x-audoryn-service-nonce", VECTOR["nonce"]),
        ("x-audoryn-service-signature", VECTOR["signature"]),
    ]
    verified = await verify_read_request(
        keys={key.key_id: key}, audience="agentgate", required_scope=VECTOR["scope"], method="GET",
        target=VECTOR["target"], body=b"", headers=headers, replay=MemoryReplay(),
        now=int(VECTOR["timestamp"]), max_clock_skew_seconds=60,
    )
    assert verified.key_id == VECTOR["keyId"]


# ---------- switch ----------

def test_default_settings_keep_every_route_hidden() -> None:
    app.dependency_overrides.pop(console_reads_runtime, None)
    response = TestClient(app).get(HEALTH, headers=_headers(HEALTH))
    assert response.status_code == 404
    assert response.json() == {"version": 1, "error": {"code": "not_found", "message": "Not found."}}


def test_enabled_without_valid_key_stays_hidden() -> None:
    runtime = ConsoleReadsRuntime(config=load_config(_settings(console_reads_key_secret=SecretStr("short"))), clock=lambda: NOW)
    app.dependency_overrides[console_reads_runtime] = lambda: runtime
    try:
        assert TestClient(app).get(HEALTH, headers=_headers(HEALTH)).status_code == 404
    finally:
        app.dependency_overrides.pop(console_reads_runtime, None)


# ---------- success ----------

def test_signed_health_returns_contract_envelope(client) -> None:
    http, runtime = client
    response = http.get(HEALTH, headers={**_headers(HEALTH), "X-Request-ID": "req-123"})
    assert response.status_code == 200
    body = response.json()
    assert not list(_schema("envelope").iter_errors(body))
    assert body["operation"] == "health"
    assert body["requestTarget"] == HEALTH
    assert body["requestId"] == "req-123"
    assert body["data"] == {"compatible": True}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"] == "req-123"
    assert runtime.replay.ttls == [121]


def test_invalid_request_id_is_replaced(client) -> None:
    http, _ = client
    response = http.get(HEALTH, headers={**_headers(HEALTH), "X-Request-ID": "bad id with spaces"})
    assert response.status_code == 200
    assert response.json()["requestId"] != "bad id with spaces"


def test_previous_key_is_accepted_during_rotation() -> None:
    old_secret = b"o" * 32
    runtime = ConsoleReadsRuntime(
        config=load_config(_settings(
            console_reads_previous_key_id="console-reads-0",
            console_reads_previous_key_secret=SecretStr(base64.urlsafe_b64encode(old_secret).decode()),
            console_reads_previous_key_scopes="observability.read",
        )),
        replay=MemoryReplay(), rate_limiter=MemoryRate(), clock=lambda: NOW,
    )
    old = ServiceKey(key_id="console-reads-0", secret=old_secret, audience="agentgate", scopes=frozenset({"observability.read"}))
    app.dependency_overrides[console_reads_runtime] = lambda: runtime
    try:
        assert TestClient(app).get(HEALTH, headers=_headers(HEALTH, key=old)).status_code == 200
    finally:
        app.dependency_overrides.pop(console_reads_runtime, None)


# ---------- rejection ----------

OTHER = ServiceKey(key_id="console-reads-1", secret=b"x" * 32, audience="agentgate", scopes=frozenset({"observability.read"}))


@pytest.mark.parametrize(
    ("label", "reason", "make_request"),
    [
        ("unknown key", "unknown_key", lambda: (HEALTH, _headers(HEALTH, Key="someone-else"))),
        ("wrong audience header", "wrong_audience", lambda: (HEALTH, _headers(HEALTH, Audience="console"))),
        ("scope from header not route", "wrong_scope", lambda: (HEALTH, _headers(HEALTH, scope="visibility.read"))),
        ("stale timestamp", "expired", lambda: (HEALTH, _headers(HEALTH, timestamp=NOW - 61))),
        ("future timestamp", "expired", lambda: (HEALTH, _headers(HEALTH, timestamp=NOW + 61))),
        ("malformed nonce", "malformed_header", lambda: (HEALTH, _headers(HEALTH, nonce="Z" * 32))),
        ("missing signature", "malformed_header", lambda: (HEALTH, {k: v for k, v in _headers(HEALTH).items() if not k.endswith("Signature")})),
        ("wrong secret", "bad_signature", lambda: (HEALTH, _headers(HEALTH, key=OTHER))),
        ("query added after signing", "bad_signature", lambda: (HEALTH + "?limit=5", _headers(HEALTH))),
        ("path changed after signing", "bad_signature", lambda: (HEALTH, _headers("/internal/console/v1/overview"))),
        ("no headers", "unknown_key", lambda: (HEALTH, {})),
    ],
)
def test_rejected_requests_are_unauthorized(client, caplog, label: str, reason: str, make_request) -> None:
    http, _ = client
    caplog.set_level(logging.INFO, logger="audoryn.console_reads")
    target, headers = make_request()
    response = http.get(target, headers=headers)
    assert response.status_code == 401, label
    assert response.json() == {"version": 1, "error": {"code": "unauthorized", "message": "Service request was not authorized."}}
    assert not list(_schema("error").iter_errors(response.json()))
    assert [r.reason for r in caplog.records if r.name == "audoryn.console_reads"] == [reason]  # type: ignore[attr-defined]


def test_key_without_route_scope_is_unauthorized() -> None:
    runtime = _runtime(console_reads_key_scopes="visibility.read")
    narrow = ServiceKey(key_id="console-reads-1", secret=SECRET, audience="agentgate", scopes=frozenset({"visibility.read"}))
    app.dependency_overrides[console_reads_runtime] = lambda: runtime
    try:
        headers = _headers(HEALTH, key=narrow, scope="observability.read")
        assert TestClient(app).get(HEALTH, headers=headers).status_code == 401
    finally:
        app.dependency_overrides.pop(console_reads_runtime, None)


def test_reused_nonce_is_rejected(client) -> None:
    http, _ = client
    headers = _headers(HEALTH)
    assert http.get(HEALTH, headers=headers).status_code == 200
    assert http.get(HEALTH, headers=headers).status_code == 401


def test_forged_requests_do_not_burn_nonces(client) -> None:
    http, runtime = client
    assert http.get(HEALTH, headers=_headers(HEALTH, key=OTHER)).status_code == 401
    assert runtime.replay.used == set()
    assert http.get(HEALTH, headers=_headers(HEALTH)).status_code == 200


def test_duplicate_service_headers_are_rejected(client) -> None:
    http, _ = client
    pairs = list(_headers(HEALTH).items()) + [("X-Audoryn-Service-Scope", "observability.read")]
    assert http.get(HEALTH, headers=pairs).status_code == 401


def test_body_on_get_is_rejected(client) -> None:
    http, _ = client
    assert http.request("GET", HEALTH, headers=_headers(HEALTH), content=b"{}").status_code == 401


def test_other_methods_are_not_served(client) -> None:
    http, _ = client
    assert http.post(HEALTH, headers=_headers(HEALTH)).status_code == 405


def test_replay_store_failure_fails_closed() -> None:
    runtime = _runtime(replay=MemoryReplay(down=True))
    app.dependency_overrides[console_reads_runtime] = lambda: runtime
    try:
        response = TestClient(app).get(HEALTH, headers=_headers(HEALTH))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "unavailable"
    finally:
        app.dependency_overrides.pop(console_reads_runtime, None)


def test_rate_limit_returns_429_with_retry_after() -> None:
    runtime = _runtime(console_reads_rate_per_minute=2)
    app.dependency_overrides[console_reads_runtime] = lambda: runtime
    try:
        http = TestClient(app)
        for index in range(2):
            assert http.get(HEALTH, headers=_headers(HEALTH, nonce=f"{index:032x}")).status_code == 200
        response = http.get(HEALTH, headers=_headers(HEALTH, nonce="f" * 32))
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "rate_limited"
        assert 1 <= int(response.headers["retry-after"]) <= 60
    finally:
        app.dependency_overrides.pop(console_reads_runtime, None)


def test_unexpected_parameter_after_valid_signature_is_400(client) -> None:
    http, _ = client
    target = HEALTH + "?limit=5"
    response = http.get(target, headers=_headers(target))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_parameters"


# ---------- producer failures and parameters (test-only router) ----------

def _probe_app(producer, parameters=frozenset({"limit", "query"})) -> TestClient:
    probe = FastAPI()
    runtime = _runtime()

    @probe.get("/internal/console/v1/probe")
    async def route(request: Request):
        return await serve(request, runtime, operation="health", scope="observability.read", producer=producer, parameters=parameters)

    return TestClient(probe)


def test_parameters_reach_the_producer_decoded() -> None:
    seen = {}

    async def producer(context):
        seen.update(context.parameters)
        return {"compatible": True}

    target = "/internal/console/v1/probe?limit=5&query=a%20b%2Bc"
    assert _probe_app(producer).get(target, headers=_headers(target)).status_code == 200
    assert seen == {"limit": "5", "query": "a b+c"}


def test_repeated_parameter_is_400() -> None:
    async def producer(context):
        return {}

    target = "/internal/console/v1/probe?limit=5&limit=6"
    assert _probe_app(producer).get(target, headers=_headers(target)).status_code == 400


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [(InvalidParameters("limit"), 400, "invalid_parameters"), (TimeoutError(), 504, "timeout"), (RuntimeError("db exploded: secret"), 503, "unavailable")],
)
def test_producer_failures_map_to_contract_errors(error, status, code) -> None:
    async def producer(context):
        raise error

    target = "/internal/console/v1/probe"
    response = _probe_app(producer).get(target, headers=_headers(target))
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "secret" not in response.text


def test_oversized_response_is_refused() -> None:
    async def producer(context):
        return {"blob": "x" * (1024 * 1024)}

    target = "/internal/console/v1/probe"
    assert _probe_app(producer).get(target, headers=_headers(target)).status_code == 503


# ---------- logging ----------

def test_logs_never_contain_secrets_signatures_or_query_values(caplog) -> None:
    async def producer(context):
        return {"compatible": True}

    caplog.set_level(logging.INFO, logger="audoryn.console_reads")
    target = "/internal/console/v1/probe?query=ada%40example.com"
    headers = _headers(target)
    _probe_app(producer).get(target, headers=headers)
    _probe_app(producer).get(target, headers=_headers(target, key=OTHER))
    records = [record for record in caplog.records if record.name == "audoryn.console_reads"]
    assert [record.outcome for record in records] == ["ok", "unauthorized"]  # type: ignore[attr-defined]
    assert records[1].reason == "bad_signature"  # type: ignore[attr-defined]
    assert [record.levelno for record in records] == [logging.INFO, logging.WARNING]  # refusals are visible by default
    assert "reason=bad_signature" in records[1].getMessage()
    dumped = json.dumps([record.__dict__ for record in records], default=str)
    for forbidden in (SECRET_TEXT, headers["X-Audoryn-Service-Signature"][3:], "ada%40example.com", "ada@example.com"):
        assert forbidden not in dumped


# ---------- configuration ----------

def test_config_disabled_by_default() -> None:
    assert load_config(SimpleNamespace()).enabled is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"console_reads_key_id": None},
        {"console_reads_key_secret": SecretStr(base64.urlsafe_b64encode(b"s" * 31).decode())},
        {"console_reads_key_secret": SecretStr("not base64 !!")},
        {"console_reads_key_scopes": ""},
        {"console_reads_key_scopes": "visibility.read,admin.write"},
        {"console_reads_clock_skew_seconds": 0},
        {"console_reads_rate_per_minute": 0},
        {"console_reads_statement_timeout_ms": 50},
        {"console_reads_audience": "has spaces"},
        {"console_reads_previous_key_id": "console-reads-1", "console_reads_previous_key_secret": SecretStr(SECRET_TEXT), "console_reads_previous_key_scopes": "visibility.read"},
    ],
)
def test_invalid_config_turns_the_feature_off_without_crashing(overrides) -> None:
    config = load_config(_settings(**overrides))
    assert config.enabled is False
    assert config.problem
    assert SECRET_TEXT not in config.problem


def test_empty_rotation_placeholders_are_ignored() -> None:
    config = load_config(_settings(console_reads_previous_key_id="", console_reads_previous_key_secret=SecretStr(""), console_reads_previous_key_scopes=""))
    assert config.enabled is True
    assert list(config.keys) == ["console-reads-1"]
    assert "k" * 32 not in repr(config)


async def test_verifier_rejects_non_get_and_bad_targets() -> None:
    for method, target, body in (("POST", HEALTH, b""), ("GET", HEALTH, b"x"), ("GET", "//evil", b""), ("GET", "/a b", b"")):
        with pytest.raises(ServiceRequestRejected):
            await verify_read_request(
                keys={KEY.key_id: KEY}, audience="agentgate", required_scope="observability.read", method=method,
                target=target, body=body, headers=[], replay=MemoryReplay(), now=NOW, max_clock_skew_seconds=60,
            )
