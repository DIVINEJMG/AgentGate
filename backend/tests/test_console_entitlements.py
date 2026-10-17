"""The Console's entitlement channel (commercial plan, phase 3): signed, separate from the reads,
newest version wins, and status shows what was applied and used."""
from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.internal import console_entitlements as channel
from app.application import console_entitlements as signing
from app.application.console_reads import signing as reads_signing
from app.application.console_reads.signing import (
    ReplayStoreUnavailable,
    ServiceKey,
    ServiceRequestRejected,
)
from app.application.services import entitlements as plans
from app.infrastructure.database import models as m
from app.infrastructure.database.session import database_session
from scripts.runtime_benchmark import BenchSession, _aware_sqlite_datetimes

VECTOR = json.loads((Path(__file__).resolve().parents[2] / "docs/integration/console-entitlements/signing-vector.json")
                    .read_text(encoding="utf-8"))
SECRET = "Y29uc29sZS1lbnRpdGxlbWVudHMtY2hhbm5lbC10ZXN0LWtleS0wMTIzNDU2Nzg5"
NOW = 1_791_626_400
BASE = "/internal/console/entitlements/v1"


def settings(**overrides: Any) -> Any:
    values = {"console_entitlements_enabled": True, "console_entitlements_audience": "agentgate",
              "console_entitlements_key_id": "console-test", "console_entitlements_key_secret": SecretStr(SECRET),
              "console_entitlements_previous_key_id": None, "console_entitlements_previous_key_secret": None,
              "console_entitlements_clock_skew_seconds": 60, "console_entitlements_rate_per_minute": 120}
    return SimpleNamespace(**{**values, **overrides})


class MemoryGuard:
    def __init__(self, *, limit_after: int | None = None, down: bool = False) -> None:
        self.nonces: set[str] = set()
        self.hits, self.limit_after, self.down = 0, limit_after, down

    async def claim(self, *, key_id: str, nonce: str, ttl_seconds: int) -> bool:
        if self.down:
            raise ReplayStoreUnavailable("down")
        fresh = nonce not in self.nonces
        self.nonces.add(nonce)
        return fresh

    async def wait_seconds(self, *, key_id: str, limit: int, now: int) -> int | None:
        self.hits += 1
        return 30 if self.limit_after is not None and self.hits > self.limit_after else None


def test_the_shared_vector_signs_the_same_on_both_sides() -> None:
    secret = base64.urlsafe_b64decode(VECTOR["secretBase64url"] + "=" * (-len(VECTOR["secretBase64url"]) % 4))
    key = ServiceKey(key_id=VECTOR["keyId"], secret=secret, audience=VECTOR["audience"],
                     scopes=frozenset({VECTOR["scope"]}))
    args = {name: VECTOR[name] for name in ("scope", "method", "target", "timestamp", "nonce")}
    body = VECTOR["body"].encode()
    assert signing.canonical_bytes(key_id=key.key_id, audience=key.audience, body=body, **args).decode() == VECTOR["canonical"]
    assert signing.sign(key, body=body, **args) == VECTOR["signature"]
    assert VECTOR["signatureVersion"] == "audoryn-entitlements-v1"


def test_the_channel_is_off_unless_enabled_with_a_valid_key() -> None:
    assert not signing.load_config(settings(console_entitlements_enabled=False)).enabled
    for broken in ({"console_entitlements_key_secret": None}, {"console_entitlements_key_secret": SecretStr("short")},
                   {"console_entitlements_rate_per_minute": 0},
                   {"console_entitlements_previous_key_id": "console-test",
                    "console_entitlements_previous_key_secret": SecretStr(SECRET)}):
        config = signing.load_config(settings(**broken))
        assert not config.enabled and config.problem
    rotated = signing.load_config(settings(console_entitlements_previous_key_id="console-old",
                                           console_entitlements_previous_key_secret=SecretStr(SECRET)))
    assert rotated.enabled and set(rotated.keys) == {"console-test", "console-old"}
    assert all(key.scopes == signing.SCOPES for key in rotated.keys.values())


def signed(config: signing.EntitlementsChannelConfig, *, method: str, target: str, body: bytes = b"",
           scope: str = signing.WRITE_SCOPE, nonce: str | None = None, timestamp: int = NOW,
           key_id: str = "console-test") -> dict[str, str]:
    key = config.keys[key_id]
    nonce = nonce or uuid4().hex
    return {"x-audoryn-service-key": key.key_id, "x-audoryn-service-audience": key.audience,
            "x-audoryn-service-scope": scope, "x-audoryn-service-timestamp": str(timestamp),
            "x-audoryn-service-nonce": nonce,
            "x-audoryn-service-signature": signing.sign(key, scope=scope, method=method, target=target,
                                                        timestamp=str(timestamp), nonce=nonce, body=body)}


@pytest.mark.asyncio
async def test_only_a_fresh_correctly_signed_request_for_the_route_scope_passes() -> None:
    config, target, body = signing.load_config(settings()), f"{BASE}/snapshots", b'{"a":1}'

    async def verify(headers: dict[str, str], *, scope: str = signing.WRITE_SCOPE, sent: bytes = body,
                     now: int = NOW, guard: MemoryGuard | None = None) -> ServiceKey:
        return await signing.verify_request(config=config, scope=scope, method="POST", target=target, body=sent,
                                            headers=headers.items(), guard=cast(Any, guard or MemoryGuard()), now=now)

    good = signed(config, method="POST", target=target, body=body)
    assert (await verify(good)).key_id == "console-test"
    reads_key = config.keys["console-test"]
    reads_signature = reads_signing.sign(reads_key, scope=signing.WRITE_SCOPE, target=target,
                                         timestamp=str(NOW), nonce=good["x-audoryn-service-nonce"])
    guard = MemoryGuard()
    await verify(good, guard=guard)
    cases = {
        "replayed": lambda: verify(good, guard=guard),
        "bad_signature": lambda: verify(good, sent=b'{"a":2}'),  # the body is signed
        "wrong_scope": lambda: verify(good, scope=signing.READ_SCOPE),
        "expired": lambda: verify(good, now=NOW + 61),
        "unknown_key": lambda: verify({**good, "x-audoryn-service-key": "other"}),
        "wrong_audience": lambda: verify({**good, "x-audoryn-service-audience": "console"}),
        "malformed_header": lambda: verify({**good, "x-audoryn-service-nonce": "x"}),
        "unsupported_request": lambda: verify(good, sent=b"x" * (signing.MAX_BODY_BYTES + 1)),
    }
    for reason, attempt in cases.items():
        with pytest.raises(ServiceRequestRejected) as rejected:
            await attempt()
        assert rejected.value.reason == reason
    # A signed read (audoryn-service-v1) never authorises a write on this channel.
    with pytest.raises(ServiceRequestRejected, match="bad_signature"):
        await verify({**good, "x-audoryn-service-signature": reads_signature})


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    tables = (m.Organization, m.OrganizationEntitlement, m.EntitlementSnapshot, m.EntitlementTemplate,
              m.OrganizationUsage)
    m.Base.metadata.create_all(engine, tables=[cast(Any, table.__table__) for table in tables])

    async def session() -> Any:
        yield BenchSession(Session(engine))

    from app.infrastructure.database import session as session_module

    monkeypatch.setattr(session_module, "session_factory", lambda: BenchSession(Session(engine)))
    config, guard = signing.load_config(settings()), MemoryGuard()
    runtime = channel.ChannelRuntime(config=config, guard=cast(Any, guard), clock=lambda: NOW)
    app = FastAPI()
    app.include_router(channel.router)
    app.dependency_overrides[database_session] = session
    app.dependency_overrides[channel.channel_runtime] = lambda: runtime
    plans.forget()
    with _aware_sqlite_datetimes(), TestClient(app) as client:
        def call(method: str, path: str, document: Any = None, **kwargs: Any) -> Any:
            body = json.dumps(document).encode() if document is not None else b""
            scope = kwargs.pop("scope", signing.WRITE_SCOPE if method == "POST" else signing.READ_SCOPE)
            headers = signed(config, method=method, target=BASE + path, body=body, scope=scope, **kwargs)
            return client.request(method, BASE + path, content=body, headers=headers)

        def organization() -> UUID:
            identity = uuid4()
            with Session(engine) as db:
                db.add(m.Organization(id=identity, name="Acme", created_by=uuid4()))
                db.commit()
            return identity

        yield SimpleNamespace(call=call, organization=organization, runtime=runtime, guard=guard, client=client,
                              engine=engine)
    plans.forget()


def document(organization: UUID, version: int, **changes: Any) -> dict[str, Any]:
    start = datetime(2026, 10, 1, tzinfo=UTC)
    return {"organizationId": str(organization), "version": version, "status": "active", "planCode": "team",
            "periodStart": start.isoformat(), "periodEnd": (start + timedelta(days=31)).isoformat(),
            "validUntil": None, "entitlements": {"workers.max": 10, "browser.enabled": True}, **changes}


def test_snapshots_apply_newest_first_and_report_what_is_applied(api: Any) -> None:
    organization = api.organization()
    results = [api.call("POST", "/snapshots", document(organization, version)).json()["data"] for version in (2, 2, 1, 3)]
    assert [(item["result"], item["appliedVersion"]) for item in results] == [
        ("applied", 2), ("already_applied", 2), ("stale", 2), ("applied", 3)]
    unknown = api.call("POST", "/snapshots", document(uuid4(), 1))
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "unknown_organization"
    for bad in (document(organization, 4, entitlements={"workers.max": -1}), {**document(organization, 4), "extra": 1},
                document(organization, 0)):
        refused = api.call("POST", "/snapshots", bad)
        assert refused.status_code == 400 and refused.json()["error"]["code"] == "invalid"


def test_the_template_is_versioned_like_snapshots(api: Any) -> None:
    free = {"version": 1, "status": "active", "planCode": "free", "periodDays": 30, "entitlements": {"workers.max": 2}}
    assert api.call("POST", "/template", free).json()["data"] == {"result": "applied", "appliedVersion": 1}
    assert api.call("POST", "/template", free).json()["data"]["result"] == "already_applied"
    assert api.call("POST", "/template", {**free, "version": 2}).json()["data"] == {"result": "applied", "appliedVersion": 2}


def test_status_pages_organisations_with_their_plan_and_period_usage(api: Any) -> None:
    with_plan, without = api.organization(), api.organization()
    api.call("POST", "/snapshots", document(with_plan, 1, periodStart=datetime.now(UTC).isoformat(),
                                            periodEnd=(datetime.now(UTC) + timedelta(days=30)).isoformat()))
    with Session(api.engine) as db:
        row = db.get(m.OrganizationEntitlement, with_plan)
        assert row is not None
        db.add(m.OrganizationUsage(organization_id=with_plan, period_start=row.period_start, tasks=4, ai_tokens=900,
                                   browser_seconds=12.5, coding_seconds=0, web_research=1))
        db.commit()
    first = api.call("GET", "/status?limit=1").json()["data"]
    second = api.call("GET", f"/status?after={first['next']}&limit=1").json()["data"]
    pages = {item["organizationId"]: item for item in first["organizations"] + second["organizations"]}
    assert set(pages) == {str(with_plan), str(without)} and first["next"] is not None
    assert pages[str(without)]["plan"] is None and pages[str(without)]["usage"] is None
    assert pages[str(with_plan)]["plan"]["version"] == 1
    assert pages[str(with_plan)]["usage"]["tasks"] == 4 and pages[str(with_plan)]["usage"]["browserSeconds"] == 12.5
    for query in ("?limit=0", "?limit=201", "?after=nope", "?other=1"):
        assert api.call("GET", "/status" + query).status_code == 400


def test_refusals_never_reach_the_database(api: Any) -> None:
    organization = api.organization()
    body = json.dumps(document(organization, 1)).encode()
    unsigned = api.client.post(BASE + "/snapshots", content=body)
    assert unsigned.status_code == 401 and unsigned.json() == {
        "version": 1, "error": {"code": "unauthorized", "message": "Service request was not authorized."}}
    read_scope = api.call("POST", "/snapshots", document(organization, 1), scope=signing.READ_SCOPE)
    assert read_scope.status_code == 401
    with Session(api.engine) as db:
        assert db.get(m.OrganizationEntitlement, organization) is None
    api.guard.limit_after = api.guard.hits
    limited = api.call("GET", "/status")
    assert limited.status_code == 429 and limited.headers["Retry-After"] == "30"
    api.guard.down = True
    assert api.call("GET", "/status").status_code == 503
    api.runtime.config = signing.DISABLED
    assert api.client.get(BASE + "/status").status_code == 404


def test_an_unexpected_failure_is_the_contract_answer_not_a_bare_500(api: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    organization = api.organization()

    async def broken(*_: Any, **__: Any) -> str:
        raise RuntimeError("database fault")

    monkeypatch.setattr(plans, "apply_snapshot", broken)
    response = api.call("POST", "/snapshots", document(organization, 1))
    assert response.status_code == 503 and response.json()["error"]["code"] == "unavailable"
