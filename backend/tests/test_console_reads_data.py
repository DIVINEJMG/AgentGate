"""Console reads phases 3-5: real queries over a seeded database, through signed HTTP.

Tables come from AgentGate's own models (SQLite in memory; JSONB rendered as JSON). Every
statement is also compiled for PostgreSQL, every response is validated against contract v1
and Console's response limits, and secrets planted in excluded columns must never appear.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import SecretStr
from referencing import Registry, Resource
from sqlalchemy import Boolean, DateTime, Integer, String, Table, Text, create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.application.console_reads import operations, visibility
from app.application.console_reads.config import load_config
from app.application.console_reads.database import like_pattern, run_read_only
from app.application.console_reads.guard import ConsoleReadsRuntime, console_reads_runtime
from app.application.console_reads.signing import ServiceKey, sign
from app.infrastructure.database import models as m
from app.main import app


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    return "JSON"


CONTRACT_DIR = Path(__file__).resolve().parents[2] / "docs" / "integration" / "console-reads"
SCHEMA = json.loads((CONTRACT_DIR / "contract.v1.json").read_text(encoding="utf-8"))
LIMITS = SCHEMA["x-limits"]
SECRET = b"d" * 32
SCOPES = ("visibility.read", "observability.read", "security.read", "notifications.read")
KEY = ServiceKey(key_id="console-reads-1", secret=SECRET, audience="agentgate", scopes=frozenset(SCOPES))
NOW = datetime.now(UTC)
PLANTED = ("PLANTED_PASSWORD_HASH", "PLANTED_INTEGRATION_SECRET", "PLANTED_FAILURE_PAYLOAD", "PLANTED_URL_PASSWORD", "PLANTED_URL_TOKEN", "PLANTED_AUDIT_ACTOR", "PLANTED_AUDIT_PAYLOAD", "PLANTED_PROMPT")

TABLES = [
    m.HumanIdentity, m.LocalAuthCredential, m.Organization, m.OrganizationMembership, m.Worker, m.Job, m.Run,
    m.WorkItem, m.Integration, m.Incident, m.QueueDeliveryFailure, m.AIInvocation, m.AuditEvent,
    m.ConversationThread, m.ResultExport, m.Approval, m.OutboxEvent,
]


def _filler(column: Any) -> Any:
    kind = column.type
    if isinstance(kind, JSONB):
        return {}
    if isinstance(kind, DateTime):
        return NOW
    if isinstance(kind, Boolean):
        return False
    if isinstance(kind, Integer):
        return 0
    if isinstance(kind, String | Text):
        return "x" + uuid4().hex[:10]  # unique, for unique columns
    if column.type.python_type is UUID:
        return uuid4()
    raise AssertionError(f"no filler for {column}")


class Database:
    """Seeded SQLite database built from AgentGate's own model tables."""

    def __init__(self) -> None:
        # One shared connection: TestClient runs the app on another thread.
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        for model in TABLES:
            model.__table__.create(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))

    def add(self, model: Any, /, **values: Any) -> dict[str, Any]:
        table: Table = model.__table__
        row: dict[str, Any] = {}
        for column in table.columns:
            if column.name in values:
                row[column.name] = values[column.name]
            elif getattr(column.default, "is_callable", False):
                row[column.name] = column.default.arg(None) if column.name != "id" else uuid4()  # type: ignore[union-attr]
            elif getattr(column.default, "is_scalar", False):
                row[column.name] = column.default.arg  # type: ignore[union-attr]
            elif not column.nullable and column.server_default is None:
                row[column.name] = _filler(column)
        with self.engine.begin() as connection:
            connection.execute(table.insert(), row)
        return row

    def sql(self, statement: str, **parameters: Any) -> None:
        with self.engine.begin() as connection:
            connection.execute(text(statement), parameters)


class PostgresCompilingConnection:
    """Delegates to SQLite but first compiles every statement for PostgreSQL."""

    def __init__(self, connection: Connection, compiled: list[str]) -> None:
        self._connection = connection
        self._compiled = compiled

    def _check(self, statement: Any) -> None:
        self._compiled.append(str(statement.compile(dialect=postgresql.dialect())))

    def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self._check(statement)
        return self._connection.execute(statement, *args, **kwargs)

    def scalar(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self._check(statement)
        return self._connection.scalar(statement, *args, **kwargs)

    def scalars(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self._check(statement)
        return self._connection.scalars(statement, *args, **kwargs)


@pytest.fixture
def db() -> Database:
    return Database()


@pytest.fixture
def http(db: Database):
    compiled: list[str] = []

    async def runner(query: Callable[[Connection], Any], *, timeout_ms: int) -> Any:
        assert timeout_ms == 3000
        with db.engine.connect() as connection:
            return query(PostgresCompilingConnection(connection, compiled))  # type: ignore[arg-type]

    settings = SimpleNamespace(
        console_reads_enabled=True,
        console_reads_key_id=KEY.key_id,
        console_reads_key_secret=SecretStr(base64.urlsafe_b64encode(SECRET).decode()),
        console_reads_key_scopes=",".join(SCOPES),
    )

    class Replay:
        async def claim(self, **kwargs: Any) -> bool:
            return True

    class Rate:
        async def hit(self, **kwargs: Any) -> None:
            return None

    runtime = ConsoleReadsRuntime(config=load_config(settings), replay=Replay(), rate_limiter=Rate(), query_runner=runner)
    app.dependency_overrides[console_reads_runtime] = lambda: runtime
    client = TestClient(app)
    nonce = iter(range(10**6))

    def get(target: str, scope: str) -> Any:
        timestamp = str(int(datetime.now(UTC).timestamp()))
        value = f"{next(nonce):032x}"
        headers = {
            "X-Audoryn-Service-Key": KEY.key_id,
            "X-Audoryn-Service-Audience": "agentgate",
            "X-Audoryn-Service-Scope": scope,
            "X-Audoryn-Service-Timestamp": timestamp,
            "X-Audoryn-Service-Nonce": value,
            "X-Audoryn-Service-Signature": sign(KEY, scope=scope, target=target, timestamp=timestamp, nonce=value),
        }
        return client.get(target, headers=headers)

    try:
        yield SimpleNamespace(get=get, compiled=compiled)
    finally:
        app.dependency_overrides.pop(console_reads_runtime, None)


def _validator(definition: str) -> Draft202012Validator:
    registry = Registry().with_resource(SCHEMA["$id"], Resource.from_contents(SCHEMA))
    return Draft202012Validator({"$ref": f"{SCHEMA['$id']}#/$defs/{definition}"}, registry=registry)


def _limit_violations(value: object, depth: int = 0) -> list[str]:
    if depth > LIMITS["maxDepth"]:
        return ["depth"]
    problems: list[str] = []
    if isinstance(value, dict):
        problems += ["keys"] if len(value) > LIMITS["maxObjectKeys"] else []
        for key, item in value.items():
            problems += [f"forbidden {key}"] if key.lower() in LIMITS["forbiddenKeys"] else []
            problems += _limit_violations(item, depth + 1)
    elif isinstance(value, list):
        problems += ["items"] if len(value) > LIMITS["maxListItems"] else []
        for item in value:
            problems += _limit_violations(item, depth + 1)
    elif isinstance(value, str) and len(value) > LIMITS["maxStringLength"]:
        problems.append("string")
    return problems


def _read(http: Any, target: str, operation: str, scope: str = "visibility.read") -> Any:
    response = http.get(target, scope)
    assert response.status_code == 200, response.text
    body = response.json()
    assert not list(_validator("envelope").iter_errors(body))
    assert body["operation"] == operation and body["requestTarget"] == target
    errors = list(_validator(SCHEMA["x-operations"][operation]["data"].removeprefix("#/$defs/")).iter_errors(body["data"]))
    assert not errors, [error.message for error in errors]
    assert _limit_violations(body["data"]) == []
    for planted in PLANTED:
        assert planted not in response.text
    assert all("INSERT" not in statement and "UPDATE" not in statement and "DELETE" not in statement for statement in http.compiled)
    return body["data"]


def _days(n: float) -> datetime:
    return NOW - timedelta(days=n)


# ---------- seed ----------

@pytest.fixture
def world(db: Database) -> SimpleNamespace:
    ada = db.add(m.HumanIdentity, subject="local:ada@example.com", email="ada@example.com", display_name="Ada Lovelace", created_at=_days(2), updated_at=_days(1))
    bob = db.add(m.HumanIdentity, subject="github:1001", email="bob@example.com", display_name="Bob 100% Real", created_at=_days(5), updated_at=_days(5))
    cy = db.add(m.HumanIdentity, subject="github:1002", email=None, display_name=None, created_at=_days(40), updated_at=_days(40))
    db.add(m.LocalAuthCredential, user_id=ada["id"], password_hash="PLANTED_PASSWORD_HASH")
    acme = db.add(m.Organization, name="Acme", created_by=ada["id"], created_at=_days(40), updated_at=_days(3))
    beta = db.add(m.Organization, name="Beta_Labs", created_by=bob["id"], created_at=_days(5), updated_at=_days(5))
    db.add(m.OrganizationMembership, organization_id=acme["id"], user_id=ada["id"], role="owner", created_at=_days(40))
    db.add(m.OrganizationMembership, organization_id=acme["id"], user_id=bob["id"], role="viewer", created_at=_days(4))
    db.add(m.OrganizationMembership, organization_id=beta["id"], user_id=bob["id"], role="owner", created_at=_days(5))
    worker = db.add(m.Worker, organization_id=acme["id"], name="Reviewer", department="Engineering", status="active", created_at=_days(10))
    db.add(m.Worker, organization_id=beta["id"], name="Writer", department="Content", status="paused", created_at=_days(3))
    job = db.add(m.Job, organization_id=acme["id"], worker_id=worker["id"], name="Weekly review", status="active", current_revision=3)
    for days, status in ((1, "completed"), (0.5, "FAILED"), (3, "completed"), (40, "failed")):
        db.add(m.Run, organization_id=acme["id"], status=status, created_at=_days(days))
    db.add(m.Integration, organization_id=acme["id"], provider="github", display_name="Repo", status="active", config={"token": "PLANTED_INTEGRATION_SECRET"})
    db.add(m.Incident, organization_id=acme["id"], status="open", severity="medium", summary="Slow runs", created_at=_days(1))
    db.add(m.Incident, organization_id=acme["id"], status="resolved", severity="critical", summary="Old outage", created_at=_days(20))
    db.add(m.Incident, organization_id=beta["id"], status="Closed", severity="high", summary="Closed one", created_at=_days(2))
    db.add(
        m.QueueDeliveryFailure, organization_id=acme["id"], source_message_id="msg-1", status_code=502, retried=3, max_retries=3,
        destination_url="https://user:PLANTED_URL_PASSWORD@api.example.com:8443/internal/v1/runtime/execute?token=PLANTED_URL_TOKEN#frag",
        failure_payload={"body": "PLANTED_FAILURE_PAYLOAD"}, created_at=_days(1),
    )
    db.add(m.QueueDeliveryFailure, organization_id=acme["id"], source_message_id="msg-0", destination_url="https://api.example.com/x", resolved_at=_days(2), created_at=_days(3))
    db.add(m.ConversationThread, organization_id=acme["id"], worker_id=worker["id"], title="t", status="open", created_by=ada["id"], created_at=_days(1))
    db.add(m.ResultExport, organization_id=acme["id"], exported_by=cy["id"], created_at=_days(35))
    db.add(m.Approval, organization_id=acme["id"], status="approved", decided_by=bob["id"], updated_at=_days(2))
    db.add(m.AIInvocation, organization_id=acme["id"], created_at=_days(1))
    db.add(m.AIInvocation, organization_id=acme["id"], created_at=_days(50))
    db.add(m.AuditEvent, organization_id=acme["id"], event_type="e", category="c", severity="HIGH", payload={"secret": "x"}, created_at=_days(1))
    db.add(m.AuditEvent, organization_id=acme["id"], event_type="e", category="c", severity="low", created_at=_days(2))
    for status in ("queued", "Queued", "waiting_ai", "completed"):
        db.add(m.WorkItem, organization_id=acme["id"], status=status)
    db.add(m.OutboxEvent, published_at=None)
    db.add(m.OutboxEvent, published_at=NOW)
    db.sql("INSERT INTO alembic_version VALUES ('0099_b'), ('0098_a')")
    return SimpleNamespace(db=db, ada=ada, bob=bob, cy=cy, acme=acme, beta=beta, worker=worker, job=job)


# ---------- phase 3 ----------

def test_snapshot(http, world) -> None:
    assert _read(http, "/internal/console/v1/snapshot", "snapshot") == {"human_identities": 3, "organizations": 2}


def test_overview_counts_match_seeded_data(http, world) -> None:
    data = _read(http, "/internal/console/v1/overview", "overview")
    assert data == {
        "total_users": 3,
        "active_users_30d": 2,  # Bob created Beta, Ada started a thread, Bob decided; Cy's export is old
        "users_with_memberships": 2,
        "organizations": 2,
        "active_organizations_30d": 1,
        "workers": 2,
        "runs_30d": 3,
        "failed_runs_30d": 1,  # status matching is case-insensitive
        "new_registrations_30d": 2,
        "integrations": 1,
        "open_incidents": 1,  # resolved and Closed are excluded
        "unresolved_queue_failures": 1,
        "agentgate_migration_heads": ["0098_a", "0099_b"],
    }


# ---------- phase 4: users ----------

def test_users_directory_orders_newest_first_with_memberships(http, world) -> None:
    items, total = _read(http, "/internal/console/v1/users?limit=50&offset=0", "users")
    assert total == 3
    assert [item["email"] for item in items] == ["ada@example.com", "bob@example.com", None]
    bob = items[1]
    assert bob["organization_count"] == 2 and bob["membership_roles"] == ["owner", "viewer"]
    assert items[2]["organization_count"] == 0 and items[2]["membership_roles"] == []


def test_users_paging_and_filters(http, world) -> None:
    items, total = _read(http, "/internal/console/v1/users?limit=1&offset=1", "users")
    assert total == 3 and [item["email"] for item in items] == ["bob@example.com"]
    items, total = _read(http, "/internal/console/v1/users?query=LOVELACE", "users")
    assert total == 1 and items[0]["display_name"] == "Ada Lovelace"
    items, total = _read(http, "/internal/console/v1/users?query=github%3A", "users")
    assert total == 2
    items, total = _read(http, "/internal/console/v1/users?email=BOB%40", "users")
    assert [item["email"] for item in items] == ["bob@example.com"]
    since = (NOW - timedelta(days=3)).isoformat().replace("+00:00", "Z")
    items, total = _read(http, f"/internal/console/v1/users?registered_from={since.replace(':', '%3A')}", "users")
    assert [item["email"] for item in items] == ["ada@example.com"]
    until = (NOW - timedelta(days=30)).isoformat().replace("+", "%2B").replace(":", "%3A")
    items, total = _read(http, f"/internal/console/v1/users?registered_to={until}", "users")
    assert total == 1 and items[0]["id"] == str(world.cy["id"])


def test_like_wildcards_in_search_are_literal(http, world) -> None:
    _, total = _read(http, "/internal/console/v1/users?query=100%25", "users")
    assert total == 1  # only "Bob 100% Real"
    _, total = _read(http, "/internal/console/v1/users?query=%25", "users")
    assert total == 1  # a bare % matches the literal character, not everything
    _, total = _read(http, "/internal/console/v1/organizations?query=a_l", "organizations")
    assert total == 1  # Beta_Labs; "_" is not a single-character wildcard
    assert like_pattern("50%_off\\") == "%50\\%\\_off\\\\%"


def test_user_detail(http, world) -> None:
    data = _read(http, f"/internal/console/v1/user?user_id={world.bob['id']}", "user")
    assert data["user"]["id"] == str(world.bob["id"])
    assert [item["organization_name"] for item in data["memberships"]] == ["Acme", "Beta_Labs"]
    assert data["activity"]["organizations_created_30d"] == 1
    assert data["activity"]["approval_decisions_30d"] == 1
    assert data["activity"]["conversations_started_30d"] == 0
    assert data["activity"]["last_activity_at"] is not None
    assert data["local_password_configured"] is False
    ada = _read(http, f"/internal/console/v1/user?user_id={world.ada['id']}", "user")
    assert ada["local_password_configured"] is True
    assert ada["activity"]["conversations_started_30d"] == 1
    cy = _read(http, f"/internal/console/v1/user?user_id={world.cy['id']}", "user")
    assert cy["activity"]["result_exports_30d"] == 0 and cy["activity"]["last_activity_at"] is not None


def test_unknown_user_and_organization_return_null(http, world) -> None:
    missing = "00000000-0000-4000-8000-000000000000"
    assert _read(http, f"/internal/console/v1/user?user_id={missing}", "user") is None
    assert _read(http, f"/internal/console/v1/organization?organization_id={missing}", "organization") is None


@pytest.mark.parametrize(
    "target",
    [
        "/internal/console/v1/users?limit=0",
        "/internal/console/v1/users?limit=101",
        "/internal/console/v1/users?offset=-1",
        "/internal/console/v1/users?limit=abc",
        "/internal/console/v1/users?registered_from=yesterday",
        "/internal/console/v1/users?query=" + "a" * 161,
        "/internal/console/v1/users?query=%00",
        "/internal/console/v1/users?role=owner",
        "/internal/console/v1/user",
        "/internal/console/v1/user?user_id=not-a-uuid",
        "/internal/console/v1/organization?organization_id=12345",
        "/internal/console/v1/organizations?limit=1000",
        "/internal/console/v1/queues?limit=101",
    ],
)
def test_out_of_bounds_parameters_are_400(http, world, target: str) -> None:
    scope = "observability.read" if "/queues" in target else "visibility.read"
    response = http.get(target, scope)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_parameters"


# ---------- phase 4: organisations ----------

def test_organizations_directory(http, world) -> None:
    items, total = _read(http, "/internal/console/v1/organizations?limit=50&offset=0", "organizations")
    assert total == 2
    assert [item["name"] for item in items] == ["Beta_Labs", "Acme"]
    acme = items[1]
    assert (acme["member_count"], acme["worker_count"], acme["job_count"], acme["runs_30d"]) == (2, 1, 1, 3)
    assert acme["created_by"] == str(world.ada["id"])


def test_organization_detail(http, world) -> None:
    data = _read(http, f"/internal/console/v1/organization?organization_id={world.acme['id']}", "organization")
    assert data["organization"]["id"] == str(world.acme["id"])
    assert [member["role"] for member in data["members"]] == ["owner", "viewer"]
    assert data["workers"][0]["name"] == "Reviewer"
    assert data["jobs"][0]["current_revision"] == 3
    assert data["integrations"][0] == {**data["integrations"][0], "provider": "github", "display_name": "Repo"}
    assert "config" not in data["integrations"][0]
    assert {incident["summary"] for incident in data["incidents"]} == {"Slow runs", "Old outage"}
    assert data["usage"] == {"runs_30d": 3, "failed_runs_30d": 1, "ai_invocations_30d": 1}
    assert data["operational_state"] == {"state": "attention", "open_incidents": 1, "unresolved_queue_failures": 1, "failed_runs_24h": 1}
    assert data["audit_summary"] == {"events_30d": 2, "high_or_critical_30d": 1}
    beta = _read(http, f"/internal/console/v1/organization?organization_id={world.beta['id']}", "organization")
    assert beta["operational_state"]["state"] == "healthy"


def test_organization_lists_are_capped_but_counts_are_full(http, world) -> None:
    for index in range(205):
        person = world.db.add(m.HumanIdentity, subject=f"bulk:{index}", email=f"bulk{index}@example.com", created_at=_days(1))
        world.db.add(m.OrganizationMembership, organization_id=world.beta["id"], user_id=person["id"], role="viewer")
    world.db.add(m.Incident, organization_id=world.beta["id"], status="open", severity="low", summary="s" * 5000)
    data = _read(http, f"/internal/console/v1/organization?organization_id={world.beta['id']}", "organization")
    assert len(data["members"]) == visibility.MEMBER_CAP
    assert data["organization"]["member_count"] == 206
    long = next(item for item in data["incidents"] if item["summary"].startswith("sss"))
    assert len(long["summary"]) == visibility.SUMMARY_CAP and long["summary"].endswith("…")


def test_organization_state_rules() -> None:
    assert visibility.organization_state(open_incidents=1, unresolved_queue_failures=0, failed_runs_24h=0) == "attention"
    assert visibility.organization_state(open_incidents=0, unresolved_queue_failures=1, failed_runs_24h=0) == "degraded"
    assert visibility.organization_state(open_incidents=0, unresolved_queue_failures=0, failed_runs_24h=2) == "degraded"
    assert visibility.organization_state(open_incidents=0, unresolved_queue_failures=0, failed_runs_24h=0) == "healthy"


# ---------- phase 5 ----------

def test_operations(http, world) -> None:
    data = _read(http, "/internal/console/v1/operations", "operations", scope="observability.read")
    assert {key: value for key, value in data.items() if key != "recentIncidents"} == {
        "state": "degraded",
        "openIncidents": 1,
        "criticalIncidents": 0,
        "unresolvedQueueFailures": 1,
        "failedRuns24h": 1,
        "queuedWorkItems": 3,  # queued, Queued, waiting_ai
        "unpublishedOutbox": 1,
    }
    assert [item["summary"] for item in data["recentIncidents"]] == ["Slow runs"]


def test_operations_critical_state_and_severity_order(http, world) -> None:
    for severity in ("low", "critical", "HIGH"):
        world.db.add(m.Incident, organization_id=world.acme["id"], status="open", severity=severity, summary=severity)
    data = _read(http, "/internal/console/v1/operations", "operations", scope="observability.read")
    assert data["state"] == "critical" and data["criticalIncidents"] == 2
    assert [item["severity"] for item in data["recentIncidents"]] == ["critical", "HIGH", "medium", "low"]


def test_operations_scope_is_observability(http, world) -> None:
    assert http.get("/internal/console/v1/operations", "visibility.read").status_code == 401


def test_queues(http, world) -> None:
    data = _read(http, "/internal/console/v1/queues?limit=100", "queues", scope="observability.read")
    assert data["audorynWorkItems"] == {"queued": 2, "waiting_ai": 1, "completed": 1}
    first, second = data["deliveryFailures"]
    assert first["sourceMessageId"] == "msg-1" and second["sourceMessageId"] == "msg-0"
    assert first["destinationUrl"] == "https://api.example.com:8443/internal/v1/runtime/execute"
    assert first["resolvedAt"] is None and second["resolvedAt"] is not None
    assert (first["statusCode"], first["retried"], first["maxRetries"]) == (502, 3, 3)
    assert "failurePayload" not in first
    data = _read(http, "/internal/console/v1/queues?limit=1", "queues", scope="observability.read")
    assert len(data["deliveryFailures"]) == 1


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://user:pass@host.example/path?q=1#f", "https://host.example/path"),
        ("http://host:8080", "http://host:8080"),
        ("not a url", ""),
        ("", ""),
        (None, ""),
        ("https://[::1", ""),
    ],
)
def test_safe_destination(url: str | None, expected: str) -> None:
    assert operations.safe_destination(url) == expected


# ---------- phases 6-8: extra seed ----------

@pytest.fixture
def rich(world: SimpleNamespace) -> SimpleNamespace:
    db = world.db
    common = {"request_id": "PLANTED_PROMPT", "transport_identity": {"prompt": "PLANTED_PROMPT"}}
    db.add(m.AIInvocation, organization_id=world.acme["id"], worker_id=world.worker["id"], provider="nvidia_nim", model="m1",
           role="planner", success=True, latency_ms=100, usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
           created_at=_days(1), **common)
    db.add(m.AIInvocation, organization_id=world.acme["id"], worker_id=world.worker["id"], provider="nvidia_nim", model="m1",
           role="planner", success=False, latency_ms=300, usage={"total_tokens": "12a"}, created_at=_days(2), **common)
    db.add(m.AIInvocation, organization_id=None, worker_id=None, provider="openrouter", model="m2", role="coordinator",
           success=True, latency_ms=None, usage={"total_tokens": 7.9}, created_at=_days(3), **common)
    for category, severity, days in (("Authentication", "low", 0.2), ("security.policy", "medium", 0.3), ("billing", "low", 0.1)):
        db.add(m.AuditEvent, organization_id=world.beta["id"], event_type="e." + category, category=category, severity=severity,
               actor={"email": "PLANTED_AUDIT_ACTOR"}, payload={"token": "PLANTED_AUDIT_PAYLOAD"}, created_at=_days(days))
    db.add(m.Incident, organization_id=world.beta["id"], status="open", severity="critical", summary="Database down", created_at=_days(0.1))
    return world


# ---------- phase 6 ----------

def test_usage(http, rich) -> None:
    data = _read(http, "/internal/console/v1/usage?days=30", "usage", scope="observability.read")
    assert data["windowDays"] == 30
    assert data["metrics"] == {
        "newUsers": 2, "organizations": 2, "activeOrganizations": 1, "workersCreated": 2,
        "runs": 3, "aiInvocations": 4, "integrations": 1,
    }
    assert len(data["daily"]) == 30
    assert data["daily"][-1]["day"] == NOW.date().isoformat()
    assert [entry["day"] for entry in data["daily"]] == sorted(entry["day"] for entry in data["daily"])
    assert sum(entry["runs"] for entry in data["daily"]) == 3
    assert sum(entry["newUsers"] for entry in data["daily"]) == 2
    one = _read(http, "/internal/console/v1/usage?days=1", "usage", scope="observability.read")
    assert one["windowDays"] == 1 and len(one["daily"]) == 1


def test_ai_usage(http, rich) -> None:
    data = _read(http, "/internal/console/v1/ai-usage?days=30&limit=50", "ai-usage", scope="observability.read")
    summary = data["summary"]
    assert (summary["invocations"], summary["successes"], summary["failures"]) == (4, 2, 2)
    assert summary["averageLatencyMs"] == 200.0  # rows without latency are ignored
    # "12a" counts as 0 and 7.9 is rounded down: only real numbers are summed.
    assert (summary["promptTokens"], summary["completionTokens"], summary["totalTokens"]) == (10, 5, 22)
    assert summary["estimatedCost"] is None and summary["costAvailable"] is False
    planner = next(row for row in data["models"] if row["model"] == "m1")
    assert planner == {"provider": "nvidia_nim", "model": "m1", "role": "planner", "invocations": 2, "successes": 1,
                       "averageLatencyMs": 200.0, "totalTokens": 15}
    assert data["models"][0]["model"] == "m1"  # most used first
    assert data["organizations"] == [{"organizationId": str(rich.acme["id"]), "invocations": 3, "successes": 1, "totalTokens": 15}]
    assert data["workers"] == [{"workerId": str(rich.worker["id"]), "invocations": 2, "successes": 1, "totalTokens": 15}]
    assert sum(day["invocations"] for day in data["daily"]) == 4
    limited = _read(http, "/internal/console/v1/ai-usage?days=30&limit=1", "ai-usage", scope="observability.read")
    assert len(limited["models"]) == 1


@pytest.mark.parametrize("target", ["/internal/console/v1/usage?days=0", "/internal/console/v1/usage?days=91",
                                    "/internal/console/v1/ai-usage?limit=101", "/internal/console/v1/ai-usage?days=7&model=m1"])
def test_analytics_parameter_bounds(http, rich, target: str) -> None:
    assert http.get(target, "observability.read").status_code == 400


# ---------- phase 7 ----------

def test_security_events_carry_only_summary_fields(http, rich) -> None:
    data = _read(http, "/internal/console/v1/security?limit=100", "security", scope="security.read")
    assert [event["category"] for event in data] == ["Authentication", "security.policy", "c"]
    assert set(data[0]) == {"id", "organizationId", "eventType", "category", "severity", "correlationId", "createdAt"}
    assert len(_read(http, "/internal/console/v1/security?limit=1", "security", scope="security.read")) == 1
    assert http.get("/internal/console/v1/security?limit=201", "security.read").status_code == 400
    assert http.get("/internal/console/v1/security", "visibility.read").status_code == 401


def test_notifications_follow_console_product_notification_model(http, rich) -> None:
    data = _read(http, "/internal/console/v1/notifications?limit=40", "notifications", scope="notifications.read")
    assert [item["source"] for item in data] == ["audoryn.incident", "audoryn.queue"]
    incident, queue = data
    assert incident["body"] == "Database down" and incident["severity"] == "critical"
    assert incident["href"] == "/operations/system-health"
    assert queue["body"] == "Message msg-1 is unresolved." and queue["href"] == "/operations/queues"
    assert incident["fingerprint"].startswith("audoryn-incident:") and queue["fingerprint"].startswith("audoryn-queue:")
    again = _read(http, "/internal/console/v1/notifications?limit=40", "notifications", scope="notifications.read")
    assert [item["fingerprint"] for item in again] == [item["fingerprint"] for item in data]  # stable for de-duplication
    assert len(_read(http, "/internal/console/v1/notifications?limit=1", "notifications", scope="notifications.read")) == 1
    assert http.get("/internal/console/v1/notifications?limit=41", "notifications.read").status_code == 400


# ---------- phase 8 ----------

def test_search_users_and_organizations(http, rich) -> None:
    items = _read(http, "/internal/console/v1/search?query=ada&limit_per_type=6", "search")["items"]
    assert items == [{"type": "user", "id": str(rich.ada["id"]), "title": "Ada Lovelace", "subtitle": "ada@example.com",
                      "href": f"/users/{rich.ada['id']}"}]
    items = _read(http, "/internal/console/v1/search?query=be", "search")["items"]
    assert [(item["type"], item["title"]) for item in items] == [("organization", "Beta_Labs")]
    assert items[0]["href"] == f"/organizations/{rich.beta['id']}"
    items = _read(http, "/internal/console/v1/search?query=github%3A1002", "search")["items"]
    assert items[0]["title"] == "github:1002" and items[0]["subtitle"] == "github:1002"  # no name or email
    items = _read(http, f"/internal/console/v1/search?query={rich.acme['id']}", "search")["items"]
    assert [item["title"] for item in items] == ["Acme"]  # a pasted full ID finds its record
    items = _read(http, "/internal/console/v1/search?query=github&limit_per_type=1", "search")["items"]
    assert len(items) == 1 and items[0]["subtitle"] == "bob@example.com"
    assert _read(http, "/internal/console/v1/search?query=%25%25", "search")["items"] == []


@pytest.mark.parametrize("target", ["/internal/console/v1/search", "/internal/console/v1/search?query=a",
                                    "/internal/console/v1/search?query=" + "a" * 181,
                                    "/internal/console/v1/search?query=ab&limit_per_type=21"])
def test_search_parameter_bounds(http, rich, target: str) -> None:
    assert http.get(target, "visibility.read").status_code == 400


def test_support_user(http, rich) -> None:
    data = _read(http, f"/internal/console/v1/support-user?user_id={rich.bob['id']}", "support-user")
    assert data["identity"] == {**data["identity"], "id": str(rich.bob["id"]), "email": "bob@example.com", "displayName": "Bob 100% Real"}
    assert [item["organizationName"] for item in data["memberships"]] == ["Acme", "Beta_Labs"]
    missing = "00000000-0000-4000-8000-000000000000"
    assert _read(http, f"/internal/console/v1/support-user?user_id={missing}", "support-user") is None


def test_support_organization(http, rich) -> None:
    data = _read(http, f"/internal/console/v1/support-organization?organization_id={rich.acme['id']}", "support-organization")
    assert data["organization"]["id"] == str(rich.acme["id"]) and data["organization"]["name"] == "Acme"
    assert data["state"] == {"members": 2, "workers": 1, "runs30d": 3, "failedRuns30d": 1, "openIncidents": 1, "queueFailures": 1}
    assert [item["summary"] for item in data["incidents"]] == ["Slow runs", "Old outage"]
    missing = "00000000-0000-4000-8000-000000000000"
    assert _read(http, f"/internal/console/v1/support-organization?organization_id={missing}", "support-organization") is None


def test_phase_6_to_8_statements_compile_for_postgresql(http, rich) -> None:
    for target, scope in (
        ("/internal/console/v1/usage?days=7", "observability.read"),
        ("/internal/console/v1/ai-usage?days=7&limit=10", "observability.read"),
        ("/internal/console/v1/security", "security.read"),
        ("/internal/console/v1/notifications", "notifications.read"),
        ("/internal/console/v1/search?query=ada", "visibility.read"),
        (f"/internal/console/v1/support-user?user_id={rich.ada['id']}", "visibility.read"),
        (f"/internal/console/v1/support-organization?organization_id={rich.acme['id']}", "visibility.read"),
    ):
        assert http.get(target, scope).status_code == 200
    joined = "\n".join(http.compiled)
    assert "jsonb_typeof" in joined and "timezone('UTC'" in joined
    for excluded in ("actor", "payload", "transport_identity", "request_id", "failure_payload"):
        assert excluded not in joined


# ---------- round trips ----------

# Statements per read (each is one database round trip). Counts are batched into single
# SELECTs; the list reads are separate statements by design.
ROUND_TRIP_BUDGET = {
    "/internal/console/v1/snapshot": 1,
    "/internal/console/v1/overview": 2,
    "/internal/console/v1/users?limit=50": 3,
    "/internal/console/v1/user?user_id={ada}": 4,
    "/internal/console/v1/organizations?limit=50": 2,
    "/internal/console/v1/organization?organization_id={acme}": 7,
    "/internal/console/v1/operations": 2,
    "/internal/console/v1/queues": 2,
    "/internal/console/v1/usage?days=30": 4,
    "/internal/console/v1/ai-usage?days=30": 5,
    "/internal/console/v1/security": 1,
    "/internal/console/v1/notifications": 2,
    "/internal/console/v1/search?query=ada": 2,
    "/internal/console/v1/support-user?user_id={ada}": 2,
    "/internal/console/v1/support-organization?organization_id={acme}": 3,
}
SCOPE_FOR = {"operations": "observability.read", "queues": "observability.read", "usage": "observability.read",
             "ai-usage": "observability.read", "security": "security.read", "notifications": "notifications.read"}


@pytest.mark.parametrize(("template", "budget"), list(ROUND_TRIP_BUDGET.items()))
def test_round_trip_budget(http, rich, template: str, budget: int) -> None:
    target = template.format(ada=rich.ada["id"], acme=rich.acme["id"])
    operation = target.split("/")[-1].split("?")[0]
    before = len(http.compiled)
    assert http.get(target, SCOPE_FOR.get(operation, "visibility.read")).status_code == 200
    assert len(http.compiled) - before == budget


# ---------- read-only runner ----------

class _Recorder:
    def __init__(self, dialect: str, error: Exception | None = None) -> None:
        self.statements: list[str] = []
        self.rolled_back = False
        self.dialect = SimpleNamespace(name=dialect)
        self.error = error

    def connect(self) -> Self:
        return self

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def begin(self) -> Self:
        return self

    async def rollback(self) -> None:
        self.rolled_back = True

    async def execute(self, statement: Any) -> None:
        self.statements.append(str(statement))

    async def run_sync(self, query: Callable[[Any], Any]) -> Any:
        if self.error:
            raise self.error
        return query("sync-connection")


async def test_runner_sets_read_only_and_timeout_then_rolls_back() -> None:
    recorder = _Recorder("postgresql")
    result = await run_read_only(lambda connection: f"ran on {connection}", timeout_ms=2500, engine=recorder)  # type: ignore[arg-type]
    assert result == "ran on sync-connection"
    assert recorder.statements == ["SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = 2500"]
    assert recorder.rolled_back


async def test_runner_maps_statement_timeout_and_still_rolls_back() -> None:
    original = SimpleNamespace(sqlstate="57014")
    recorder = _Recorder("postgresql", DBAPIError("SELECT 1", {}, original))  # type: ignore[arg-type]
    with pytest.raises(TimeoutError):
        await run_read_only(lambda connection: None, timeout_ms=100, engine=recorder)  # type: ignore[arg-type]
    assert recorder.rolled_back


async def test_runner_passes_other_database_errors_through() -> None:
    recorder = _Recorder("postgresql", DBAPIError("SELECT 1", {}, SimpleNamespace(sqlstate="08006")))  # type: ignore[arg-type]
    with pytest.raises(DBAPIError):
        await run_read_only(lambda connection: None, timeout_ms=100, engine=recorder)  # type: ignore[arg-type]
    assert recorder.rolled_back


def test_timeouts_and_database_failures_map_to_contract_errors(http, world) -> None:
    runtime = app.dependency_overrides[console_reads_runtime]()
    runtime.query_runner = AsyncMock(side_effect=TimeoutError())
    assert http.get("/internal/console/v1/overview", "visibility.read").status_code == 504
    runtime.query_runner = AsyncMock(side_effect=DBAPIError("SELECT", {}, SimpleNamespace(sqlstate="08006")))  # type: ignore[arg-type]
    response = http.get("/internal/console/v1/overview", "visibility.read")
    assert response.status_code == 503 and "SELECT" not in response.text


def test_every_statement_compiles_for_postgresql(http, world) -> None:
    for target, scope in (
        ("/internal/console/v1/overview", "visibility.read"),
        ("/internal/console/v1/users?query=a&email=b&registered_from=2026-01-01T00%3A00%3A00Z", "visibility.read"),
        (f"/internal/console/v1/user?user_id={world.ada['id']}", "visibility.read"),
        ("/internal/console/v1/organizations?query=a", "visibility.read"),
        (f"/internal/console/v1/organization?organization_id={world.acme['id']}", "visibility.read"),
        ("/internal/console/v1/operations", "observability.read"),
        ("/internal/console/v1/queues", "observability.read"),
    ):
        assert http.get(target, scope).status_code == 200
    joined = "\n".join(http.compiled)
    assert "ILIKE" in joined and "ESCAPE" in joined
    for excluded in ("password_hash", "failure_payload", "integrations.config", "payload"):
        assert excluded not in joined
