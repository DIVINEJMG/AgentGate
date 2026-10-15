"""Offline runtime benchmark (improvement plan, phase 0).

Runs fixed tasks through the real runtime: the `/execute` and outbox drain handlers, the
managed step loop, the smart planner, policies, approvals and the outbox. Only the outer
edges are replaced:

- the database is SQLite in a temporary file (a few PostgreSQL functions are emulated);
- QStash is an in-memory queue, delivered in due-time order, one delivery at a time;
- Redis is in memory (the coordinator and the realtime bus);
- the AI answers from a script, one answer per planning call;
- the only integration is a `bench` provider that returns canned results.

Each task reports its outcome (statuses, steps, provider calls, result) and its compute (QStash
messages, database statements and rows, Redis commands, AI calls and estimated tokens), read from
`usage_records` like production. Later phases must keep every outcome identical while reducing
the compute; `tests/test_runtime_benchmark.py` enforces that against `BASELINE`.

Usage: python scripts/runtime_benchmark.py [--json]
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import SecretStr
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite.base import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.api import governance_routes
from app.api.internal import outbox as outbox_routes
from app.api.internal import runtime as runtime_routes
from app.bootstrap.settings import settings
from app.domain.ai.providers import AIResponse, ModelProviderCapabilities
from app.domain.identity.principals import HumanPrincipal
from app.domain.queue.provider import QueueMessage
from app.execution.authorization import CredentialReference, ProviderPermissionSnapshot
from app.execution.contracts import (
    CapabilityDescriptor,
    ExecutionResult,
    ProviderHealth,
    ProviderManifest,
    ResourceDescriptor,
    VerificationResult,
)
from app.execution.providers.registry import ProviderRegistry
from app.infrastructure.database import models as m
from app.infrastructure.database.base import Base
from app.infrastructure.qstash.provider import UpstashQStashProvider
from app.infrastructure.redis.coordination import Lease, RedisCoordinator
from app.observability.usage import count_redis_commands, current_usage, meter_database
from app.realtime.bus import RedisRealtimeBus
from app.runtime import managed, smart_planner
from app.runtime.qstash_trigger import request_runtime_execution
from app.runtime.schedule_wake import arm_schedule

EXECUTE_URL = "https://bench.local/internal/v1/runtime/execute"
DRAIN_URL = "https://bench.local/internal/v1/outbox/drain"
SWEEP_URL = "https://bench.local/internal/v1/runtime/sweep"
MAX_DELIVERIES = 400


# ---------- database: SQLite with the PostgreSQL pieces the runtime uses ----------

@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    return "JSON"


@contextlib.contextmanager
def _aware_sqlite_datetimes() -> Iterator[None]:
    """PostgreSQL returns timezone-aware datetimes; SQLite returns naive ones. All stored values
    are UTC, so attach UTC on the way out."""
    original = SQLITE_DATETIME.result_processor

    def result_processor(self: Any, dialect: Any, coltype: Any) -> Any:
        process = original(self, dialect, coltype)

        def aware(value: Any) -> Any:
            value = process(value) if process else value
            return value.replace(tzinfo=UTC) if isinstance(value, datetime) and value.tzinfo is None else value
        return aware

    SQLITE_DATETIME.result_processor = result_processor  # type: ignore[method-assign]
    try:
        yield
    finally:
        SQLITE_DATETIME.result_processor = original  # type: ignore[method-assign]


class BenchSession:
    """The AsyncSession interface the app uses, over a synchronous SQLite session."""

    def __init__(self, session: Session) -> None:
        self.sync_session = session

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.sync_session.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.sync_session, name)

    async def get(self, *a: Any, **k: Any) -> Any:
        return self.sync_session.get(*a, **k)

    async def scalar(self, *a: Any, **k: Any) -> Any:
        return self.sync_session.scalar(*a, **k)

    async def scalars(self, *a: Any, **k: Any) -> Any:
        return self.sync_session.scalars(*a, **k)

    async def execute(self, *a: Any, **k: Any) -> Any:
        return self.sync_session.execute(*a, **k)

    async def flush(self, *a: Any, **k: Any) -> None:
        self.sync_session.flush(*a, **k)

    async def commit(self) -> None:
        self.sync_session.commit()

    async def rollback(self) -> None:
        self.sync_session.rollback()

    async def refresh(self, *a: Any, **k: Any) -> None:
        self.sync_session.refresh(*a, **k)

    async def delete(self, instance: Any) -> None:
        self.sync_session.delete(instance)

    async def merge(self, *a: Any, **k: Any) -> Any:
        return self.sync_session.merge(*a, **k)

    async def close(self) -> None:
        self.sync_session.close()


class TaskTotals:
    """Everything a task costs while counting is on, including work no usage record covers (the
    outbox drain, the approval request). Rows and bytes returned by the database are the offline
    stand-in for Neon transfer; rows also count towards the current usage meter, as PostgreSQL
    row counts do."""

    def __init__(self) -> None:
        self.reset(on=False)

    def reset(self, *, on: bool) -> None:
        self.on, self.rows, self.bytes, self.statements, self.qstash, self.redis = on, 0, 0, 0, 0, 0

    def add(self, rows: list[Any]) -> list[Any]:
        if self.on and rows:
            self.rows += len(rows)
            self.bytes += sum(len(value) if isinstance(value, str | bytes) else 8
                              for row in rows for value in row if value is not None)
            if (meter := current_usage()) is not None:
                meter.db_rows += len(rows)
        return rows


READS = TaskTotals()


class CountingCursor(sqlite3.Cursor):
    def fetchone(self) -> Any:
        row = super().fetchone()
        if row is not None:
            READS.add([row])
        return row

    def fetchmany(self, size: int | None = None) -> list[Any]:
        return READS.add(super().fetchmany(size if size is not None else self.arraysize))

    def fetchall(self) -> list[Any]:
        return READS.add(super().fetchall())


class CountingConnection(sqlite3.Connection):
    def cursor(self, factory: Any = CountingCursor) -> Any:  # type: ignore[override]
        return super().cursor(factory)


def bench_engine(path: Path) -> Any:
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 2, "factory": CountingConnection})

    @event.listens_for(engine, "connect")
    def _connect(connection: Any, record: Any) -> None:
        connection.execute("PRAGMA journal_mode=WAL")
        # Advisory locks serialise admission across processes; the benchmark has one.
        connection.create_function("pg_advisory_xact_lock", 1, lambda key: None)
        # Admission's fair-order expression. SQLite cannot subtract timestamps, so the age term is
        # NULL here; that only matters with several queued items, which the tasks do not have.
        connection.create_function("least", 2, lambda a, b: None if a is None or b is None else min(a, b))
        connection.create_function("greatest", 2, lambda a, b: None if a is None or b is None else max(a, b))
        connection.create_function("floor", 1, lambda a: None if a is None else math.floor(a))

    @event.listens_for(engine, "after_cursor_execute")
    def _statement(*_: Any) -> None:
        READS.statements += 1 if READS.on else 0

    Base.metadata.create_all(engine)
    meter_database(engine)
    return engine


# ---------- QStash, Redis and the AI ----------

@dataclass(order=True)
class Delivery:
    due: datetime
    sequence: int
    destination: str = field(compare=False)
    body: str = field(compare=False)


class FakeQStash:
    def __init__(self) -> None:
        self.queue: list[Delivery] = []
        self.seen: set[str] = set()
        self.sequence = 0

    @classmethod
    def from_settings(cls) -> FakeQStash:
        raise AssertionError("replaced per benchmark")

    async def publish(self, *, destination: str, body: str, idempotency_key: str, delay_seconds: int | None = None,
                      **_: Any) -> QueueMessage:
        from app.observability.usage import count_qstash_message

        count_qstash_message()
        READS.qstash += 1 if READS.on else 0
        if idempotency_key in self.seen:
            return QueueMessage(id=idempotency_key, deduplicated=True)
        self.seen.add(idempotency_key)
        self.sequence += 1
        due = datetime.now(UTC) + timedelta(seconds=delay_seconds or 0)
        self.queue.append(Delivery(due, self.sequence, destination, body))
        return QueueMessage(id=f"bench-{self.sequence}", deduplicated=False)

    def pop(self) -> Delivery | None:
        if not self.queue:
            return None
        self.queue.sort()
        return self.queue.pop(0)


class FakeCoordinator(RedisCoordinator):
    """In-memory Redis coordination. Each method counts the Redis commands the real one sends."""

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    def _cmd(self, count: int = 1) -> None:
        count_redis_commands(count)
        READS.redis += count if READS.on else 0

    async def ping(self) -> bool:
        self._cmd()
        return True

    async def acquire_lock(self, key: str, *, ttl_seconds: int = 30) -> Lease | None:
        self._cmd()
        if f"lock:{key}" in self._state:
            return None
        token = uuid4().hex
        self._state[f"lock:{key}"] = token
        return Lease(key=key, token=token)

    async def active_locks(self, keys: list[str]) -> dict[str, bool]:
        if not keys:
            return {}
        self._cmd()
        return {key: f"lock:{key}" in self._state for key in keys}

    async def release_lock(self, lease: Lease) -> bool:
        self._cmd()
        if self._state.get(f"lock:{lease.key}") == lease.token:
            del self._state[f"lock:{lease.key}"]
            return True
        return False

    async def renew_lock(self, lease: Lease, *, ttl_seconds: int) -> bool:
        self._cmd()
        return self._state.get(f"lock:{lease.key}") == lease.token

    async def next_fencing_token(self, key: str) -> int:
        self._cmd()
        self._state[f"fence:{key}"] = int(self._state.get(f"fence:{key}", 0)) + 1
        return self._state[f"fence:{key}"]

    async def current_fencing_token(self, key: str) -> int:
        self._cmd()
        return int(self._state.get(f"fence:{key}", 0))

    async def heartbeat(self, key: str, value: str, *, ttl_seconds: int = 60) -> None:
        self._cmd()

    async def claim_flag(self, key: str, *, ttl_seconds: int) -> bool:
        self._cmd()
        if f"flag:{key}" in self._state:
            return False
        self._state[f"flag:{key}"] = "1"
        return True

    async def clear_flag(self, key: str) -> None:
        self._cmd()
        self._state.pop(f"flag:{key}", None)

    async def cache_set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        self._cmd()
        self._state[f"cache:{key}"] = value

    async def cache_get(self, key: str) -> str | None:
        self._cmd()
        return self._state.get(f"cache:{key}")

    async def cache_delete(self, key: str) -> None:
        self._cmd()
        self._state.pop(f"cache:{key}", None)

    async def increment_counter(self, key: str, *, ttl_seconds: int) -> int:
        self._cmd()
        self._state[f"cache:{key}"] = int(self._state.get(f"cache:{key}", 0)) + 1
        return self._state[f"cache:{key}"]

    async def reserve_units(self, key: str, *, units: int, limit: int, ttl_seconds: int) -> bool:
        self._cmd()
        return True

    async def reserve_limit(self, key: str, *, limit: int, ttl_seconds: int) -> bool:
        self._cmd()
        return True

    async def reserve_ai_budget(self, identity: str, entries: list[dict]) -> int:
        self._cmd()
        return -1

    async def settle_ai_budget(self, identity: str, entries: list[dict], *, actual_tokens: int, called: bool) -> bool:
        self._cmd()
        return True

    async def acquire_slot(self, key: str, *, limit: int, ttl_seconds: int = 120) -> Lease | None:
        self._cmd()
        slots = self._state.setdefault(f"slots:{key}", set())
        if len(slots) >= limit:
            return None
        token = uuid4().hex
        slots.add(token)
        return Lease(key=key, token=token)

    async def release_slot(self, lease: Lease) -> None:
        self._cmd()
        self._state.setdefault(f"slots:{lease.key}", set()).discard(lease.token)

    async def publish(self, channel: str, payload: str) -> int:
        self._cmd()
        return 0

    async def close(self) -> None:
        return None


def _redis() -> None:
    count_redis_commands()
    READS.redis += 1 if READS.on else 0


class FakeRedisClient:
    """The commands the realtime bus sends, counted, in memory."""

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        _redis()
        if nx and key in self._state:
            return False
        self._state[key] = value
        return True

    async def get(self, key: str) -> Any:
        _redis()
        return self._state.get(key)

    async def delete(self, key: str) -> None:
        _redis()
        self._state.pop(key, None)

    async def xadd(self, stream: str, fields: dict[str, Any], **_: Any) -> str:
        _redis()
        entries = self._state.setdefault(stream, [])
        entries.append(fields)
        return f"{len(entries)}-0"

    async def publish(self, channel: str, payload: str) -> int:
        _redis()
        return 0

    async def aclose(self) -> None:
        return None


class ScriptedAI:
    """The planner's model: returns the next scripted answer for each planning call. Tokens are
    estimated as characters / 4, so prompt size changes show up in the report."""

    name = "bench"
    capabilities = ModelProviderCapabilities(structured_json=True)

    def __init__(self, answers: list[dict[str, Any]], resource_id: str, fail_on_call: int | None = None) -> None:
        self.answers, self.resource_id, self.calls = answers, resource_id, 0
        self.prompt_chars: list[int] = []
        # Planning call (1-based) on which this model fails once, so the next model takes over.
        self.fail_on_call, self.failed = fail_on_call, False
        # Per planning call: whether the prompt carried the saved plan.
        self.saw_plan: list[bool] = []

    async def generate_text(self, *, model: str, request: Any) -> AIResponse:
        if self.calls >= len(self.answers):
            raise AssertionError(f"The script has no answer for planning call {self.calls + 1}.")
        if self.fail_on_call == self.calls + 1 and not self.failed:
            from app.domain.ai.providers import AIProviderError

            self.failed = True
            raise AIProviderError("provider_unavailable", "Scripted outage of the first model.", retryable=True)
        self.saw_plan.append("SAVED PLAN" in request.prompt)
        answer = dict(self.answers[self.calls])
        self.calls += 1
        acts = [entry for entry in self.answers if entry.get("decision") == "act"]
        if self.calls == 1 and acts:
            # Like a real model, the first answer writes the run's plan: one step per scripted action.
            answer.setdefault("plan", [{"title": entry["title"], "goal": entry["instruction"], "scope": entry["scope"],
                                        "target": str((entry.get("input") or {}).get("key", "")), "dependsOn": [],
                                        "check": entry["summary"]} for entry in acts])
        if answer.get("decision") == "act":
            answer.setdefault("resourceId", self.resource_id)
            # Each scripted action completes its own plan step.
            answer.setdefault("planStep", 1 + sum(1 for entry in self.answers[:self.calls - 1]
                                                   if entry.get("decision") == "act"))
            answer.setdefault("planStepDone", True)
        answer = {"resultStatus": "completed", "summary": "", "title": "", "instruction": "", "resourceId": "",
                  "scope": "", "input": {}, **answer}
        text = json.dumps(answer)
        prompt = request.system + request.prompt
        self.prompt_chars.append(len(prompt))
        return AIResponse(text=text, provider=self.name, model=model,
                          usage={"prompt_tokens": len(prompt) // 4, "completion_tokens": len(text) // 4})

    async def analyze_media(self, **_: Any) -> AIResponse:
        raise AssertionError("not used")


# ---------- the bench provider ----------

def _capability(scope: str, operation: str, mode: str, risk: str) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        scope=scope, provider="bench", resource_type="record_store", operation=operation, mode=mode,  # type: ignore[arg-type]
        risk=risk, requires_credential=False, side_effect=mode != "read",  # type: ignore[arg-type]
        approval_recommendation="never" if mode == "read" else "recommended",  # type: ignore[arg-type]
        input_schema={"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                      "additionalProperties": False},
        output_schema={"type": "object"}, description=f"{mode.title()} a record in the bench store.",
        target="record_store",
    )


class BenchProvider:
    """A record store. Reads return canned text of a chosen size; writes are recorded."""

    manifest = ProviderManifest(
        provider="bench", display_name="Bench records", kind="native_api", version="1",
        credential_strategy="none",
        capabilities=(_capability("bench.record.read", "bench.record.read", "read", "low"),
                      _capability("bench.record.write", "bench.record.write", "write", "medium")),
    )

    def __init__(self, read_size: int = 400) -> None:
        self.read_size = read_size
        self.calls: list[dict[str, Any]] = []

    def resource(self, resource_id: str = "bench-store") -> ResourceDescriptor:
        return ResourceDescriptor(
            id=resource_id, provider="bench", resource_type="record_store", external_id="bench-store",
            display_name="Bench store", metadata={}, health="healthy",
            available_capabilities=tuple(c.scope for c in self.manifest.capabilities))

    async def discover_resources(self, **_: Any) -> tuple[ResourceDescriptor, ...]:
        return (self.resource(),)

    async def discover_capabilities(self, **_: Any) -> tuple[CapabilityDescriptor, ...]:
        return self.manifest.capabilities

    async def check_health(self, **_: Any) -> ProviderHealth:
        return ProviderHealth(state="healthy", message="ok", checked_at=datetime.now(UTC))

    async def discover_permissions(self, *, resource: ResourceDescriptor, **_: Any) -> ProviderPermissionSnapshot:
        return ProviderPermissionSnapshot(
            provider="bench", resource_id=resource.id, capability_scopes=resource.available_capabilities,
            credential=CredentialReference(strategy="none", reference=None), checked_at=datetime.now(UTC))

    async def normalize_input(self, *, operation: str, input: dict[str, object]) -> dict[str, object]:
        return {str(key): value for key, value in input.items() if key in {"key", "value"}}

    async def execute(self, *, request: Any, configuration: dict[str, str], credential: str | None) -> ExecutionResult:
        started = datetime.now(UTC)
        self.calls.append({"operation": request.operation, "input": dict(request.input)})
        key = str(request.input.get("key", ""))
        output: dict[str, Any] = {"key": key, "summary": f"{request.operation} {key}"}
        if request.operation == "bench.record.read":
            output["text"] = (f"Record {key}: " + "lorem ipsum " * self.read_size)[: self.read_size]
        else:
            output["written"] = str(request.input.get("value", ""))
        return ExecutionResult.successful(
            provider="bench", adapter="native_api", adapter_version="1", operation=request.operation,
            output=output, provider_request_id=f"bench-{len(self.calls)}", started_at=started,
            verification=VerificationResult(verified=True, summary="Bench result recorded."))

    async def verify(self, *, request: Any, result: ExecutionResult, **_: Any) -> VerificationResult:
        return VerificationResult(verified=True, summary="Bench result recorded.")


# ---------- tasks ----------

def read(key: str) -> dict[str, Any]:
    return {"decision": "act", "scope": "bench.record.read", "summary": f"Read {key}", "title": f"Read {key}",
            "instruction": f"Read record {key}", "input": {"key": key}}


def write(key: str, value: str) -> dict[str, Any]:
    return {"decision": "act", "scope": "bench.record.write", "summary": f"Write {key}", "title": f"Write {key}",
            "instruction": f"Write {value} to record {key}", "input": {"key": key, "value": value}}


CRITERION = "The requested records were handled."
# A compliant model assesses the job's criterion when it finishes, citing the steps it took (steps
# that do not exist are ignored by the evidence check).
ASSESSED = [{"criterion": CRITERION, "status": "met", "evidenceSteps": list(range(1, 21))}]


def finish(summary: str, status: str = "completed", criteria: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"decision": "finish", "summary": summary, "title": "Done", "instruction": "Report the result.",
            "resultStatus": status, "criteria": ASSESSED if criteria is None else criteria}


@dataclass(frozen=True)
class Task:
    name: str
    description: str
    answers: list[dict[str, Any]]
    # Policy effect per scope; a scope with no policy is denied by default.
    policies: dict[str, str]
    read_size: int = 400
    approve: bool = False
    # Started by its daily schedule, due now, instead of by hand.
    scheduled: bool = False
    # How the person decides each pending approval: extra decision fields (an edited input,
    # "always allow"); or, with expire, nobody decides and the approval expires.
    decision: dict[str, Any] = field(default_factory=dict)
    expire: bool = False
    # The first model fails on this planning call; a second model continues the same plan.
    fail_on_call: int | None = None
    # A one-off task (as chat creates): its job is archived once its run is done.
    one_shot: bool = False


TASKS = [
    Task("single-read", "Read one record and report it.",
         [read("a"), finish("Record a was read.")], {"bench.record.read": "allow"}),
    Task("read-then-write", "Read a record, then write a derived value.",
         [read("a"), write("b", "from a"), finish("Record b was updated from a.")],
         {"bench.record.read": "allow", "bench.record.write": "allow"}),
    Task("approved-write", "A write that needs approval, approved by a person.",
         [write("b", "approved"), finish("Record b was updated after approval.")],
         {"bench.record.read": "allow", "bench.record.write": "require_approval"}, approve=True),
    Task("denied-write", "Writing is denied by policy, so the worker reports what it could not do.",
         [read("a"), finish("Record a was read; writing is not permitted.", "attention")],
         {"bench.record.read": "allow", "bench.record.write": "deny"}),
    Task("long-run", "Eight reads in a row, then a report: shows how cost grows with run length.",
         [*(read(str(index)) for index in range(8)), finish("Eight records were read.")],
         {"bench.record.read": "allow"}),
    Task("wide-results", "Five reads with large results (20 KB each): shows prompt and transfer growth.",
         [*(read(str(index)) for index in range(5)), finish("Five large records were read.")],
         {"bench.record.read": "allow"}, read_size=20_000),
    Task("scheduled-read", "A daily scheduled job, due now: started by its on-time wake-up.",
         [read("a"), finish("Record a was read on schedule.")], {"bench.record.read": "allow"}, scheduled=True),
    Task("approve-with-edit", "The person corrects the value before approving: the corrected value is written.",
         [write("b", "draft"), finish("Record b was updated after review.")],
         {"bench.record.read": "allow", "bench.record.write": "require_approval"}, approve=True,
         decision={"input": {"key": "b", "value": "corrected"}}),
    Task("approval-expires", "Nobody decides within 24 hours: nothing is written and the task needs attention.",
         [write("b", "late"), finish("unused")],
         {"bench.record.read": "allow", "bench.record.write": "require_approval"}, expire=True),
    Task("always-allow", "Approving with \"always allow\" lets the next write of the same kind run without asking.",
         [write("b", "first"), write("c", "second"), finish("Records b and c were updated.")],
         {"bench.record.read": "allow", "bench.record.write": "require_approval"}, approve=True,
         decision={"alwaysAllow": True}),
    Task("criteria-proven", "The AI checks the completion criterion against the step that proves it.",
         [read("a"), finish("Record a was read.", criteria=[{"criterion": CRITERION, "status": "met", "evidenceSteps": [1]}])],
         {"bench.record.read": "allow"}),
    Task("criteria-unproven", "The AI claims the criterion met but cites no real step: the result needs attention.",
         [read("a"), finish("Record a was read.", criteria=[{"criterion": CRITERION, "status": "met", "evidenceSteps": [7]}])],
         {"bench.record.read": "allow"}),
    Task("criteria-missing", "The AI finishes without assessing the criterion: it is asked once, then assesses it.",
         [read("a"), finish("Record a was read.", criteria=[]), finish("Record a was read.")],
         {"bench.record.read": "allow"}),
    Task("planned-30-steps", "A 30-step job (more than the old 20-action limit) followed through its saved plan.",
         [*(read(str(index)) for index in range(30)), finish("Thirty records were read.")],
         {"bench.record.read": "allow"}),
    Task("model-switch", "The first model fails mid-run; the next model continues the same saved plan.",
         [*(read(str(index)) for index in range(5)), finish("Five records were read.")],
         {"bench.record.read": "allow"}, fail_on_call=3),
]


# ---------- running a task ----------

class FakeRequest:
    def __init__(self, body: str) -> None:
        self._body = body.encode()

    async def body(self) -> bytes:
        return self._body


@contextlib.contextmanager
def _patched(targets: list[tuple[Any, str, Any]]) -> Iterator[None]:
    saved = [(owner, name, getattr(owner, name)) for owner, name, _ in targets]
    for owner, name, value in targets:
        setattr(owner, name, value)
    try:
        yield
    finally:
        for owner, name, value in reversed(saved):
            setattr(owner, name, value)


def _modules_with(name: str, value: Any) -> list[Any]:
    return [module for key, module in list(sys.modules.items())
            if key.startswith("app.") and module is not None and getattr(module, name, None) is value]


def _seed(session: Session, task: Task) -> dict[str, Any]:
    now = datetime.now(UTC)
    human = m.HumanIdentity(subject=f"bench-{uuid4().hex[:8]}", email=None, display_name="Bench owner")
    session.add(human)
    session.flush()
    org = m.Organization(name="Bench organisation", created_by=human.id)
    session.add(org)
    session.flush()
    agent = m.AgentIdentity(organization_id=org.id, name="Bench agent", status="active", created_by=human.id)
    session.add(agent)
    session.flush()
    worker = m.Worker(organization_id=org.id, agent_identity_id=agent.id, name="Ada", department="Operations",
                      status="active", profile={"role": "Records clerk", "instructions": "Be precise."})
    integration = m.Integration(organization_id=org.id, provider="bench", display_name="Bench store",
                                status="connected", config={})
    session.add_all([worker, integration])
    session.flush()
    for scope in ("bench.record.read", "bench.record.write"):
        session.add(m.CapabilityProfile(organization_id=org.id, agent_id=agent.id, scope=scope, active=True))
    for priority, (scope, effect) in enumerate(task.policies.items()):
        policy = m.Policy(organization_id=org.id, name=f"{effect} {scope}", status="enabled", current_revision=1)
        session.add(policy)
        session.flush()
        session.add(m.PolicyRevision(policy_id=policy.id, revision=1, effect=effect, priority=priority,
                                     selectors={"scopes": [scope]}, created_at=now))
    job = m.Job(organization_id=org.id, worker_id=worker.id, name=task.name, status="active", current_revision=1)
    session.add(job)
    session.flush()
    revision = m.JobRevision(job_id=job.id, revision=1, created_by=human.id, created_at=now, definition={
        "objective": task.description, "instructions": "Use only the bench store.",
        "completionCriteria": ["The requested records were handled."],
        "requiredCapabilities": ["bench.record.read", "bench.record.write"], "priority": "normal",
        **({"autonomy": {"oneShot": True}} if task.one_shot else {})})
    if task.scheduled:
        due = now - timedelta(seconds=1)
        revision.definition = {**revision.definition, "triggerConfig": {"id": "bench-schedule", "schedule": {
            "enabled": True, "cadence": "daily", "timezone": "UTC", "localTime": due.strftime("%H:%M"),
            "missedRunPolicy": "queue_once", "outsideWorkingHoursPolicy": "run", "nextDueAt": due.isoformat()}}}
        session.add(revision)
        session.commit()
        return {"organization_id": org.id, "human_id": human.id, "job_id": job.id, "item_id": None,
                "resource_id": str(integration.id), "due": due}
    session.add(revision)
    session.flush()
    item = m.WorkItem(organization_id=org.id, job_id=job.id, job_revision_id=revision.id, status="queued",
                      priority="normal", correlation_id=str(uuid4()), idempotency_key=f"bench:{uuid4()}",
                      scheduled_at=now, payload={"trigger": {"type": "manual", "requestedBy": str(human.id)}})
    session.add(item)
    session.commit()
    return {"organization_id": org.id, "human_id": human.id, "item_id": item.id, "resource_id": str(integration.id)}


async def _settle() -> None:
    """Let tasks started by the last delivery (post-commit QStash signals, runtime steps) finish."""
    for _ in range(50):
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]
        if not pending:
            return
        await asyncio.wait(pending, timeout=5)


async def _deliver(qstash: FakeQStash, log: list[str]) -> None:
    from fastapi import BackgroundTasks, HTTPException

    for _ in range(MAX_DELIVERIES):
        delivery = qstash.pop()
        if delivery is None:
            return
        wait = (delivery.due - datetime.now(UTC)).total_seconds()
        if wait > 60:
            # A wake-up for a later scheduled run: the task is over.
            qstash.queue.append(delivery)
            return
        if wait > 0:
            await asyncio.sleep(min(wait, 6))
        if delivery.destination.endswith("/runtime/execute"):
            tasks = BackgroundTasks()
            try:
                answer = await runtime_routes.execute(FakeRequest(delivery.body), tasks, "bench")  # type: ignore[arg-type]
            except HTTPException as error:
                log.append(f"execute:{error.status_code}")
                await qstash.publish(destination=delivery.destination, body=delivery.body,
                                     idempotency_key=f"retry:{uuid4()}", delay_seconds=1)
                continue
            log.append(f"execute:{answer['status']}")
            await tasks()
        elif delivery.destination.endswith("/outbox/drain"):
            answer = await outbox_routes.drain(FakeRequest(delivery.body), "bench")  # type: ignore[arg-type]
            log.append(f"drain:{answer['published']}")
        elif delivery.destination.endswith("/schedule-due"):
            answer = await runtime_routes.schedule_due(FakeRequest(delivery.body), "bench")  # type: ignore[arg-type]
            log.append(f"schedule:{answer['queued']}")
        else:
            log.append(f"ignored:{delivery.destination.rsplit('/', 1)[-1]}")
        await _settle()
    raise AssertionError(f"More than {MAX_DELIVERIES} deliveries; the run is not converging.")


def _tool_recorder(resource_id: str, seen: list[Any]) -> Any:
    """Wraps the planner call to record the exact tools and exclusions the AI is given, with the
    random resource id replaced so runs compare equal."""
    original = managed.ManagedRuntimeExecutor._choose_planner_decision

    async def record(self: Any, **kwargs: Any) -> Any:
        given = {"tools": kwargs["tools"], "exclusions": getattr(self, "_capability_exclusions", [])}
        seen.append(json.loads(json.dumps(given, default=str).replace(resource_id, "<resource>")))
        return await original(self, **kwargs)

    return record


def _schedule_outcome(session: Session, ids: dict[str, Any], qstash: FakeQStash) -> dict[str, Any]:
    revision = session.scalar(select(m.JobRevision).where(m.JobRevision.job_id == ids["job_id"]))
    assert revision is not None
    schedule = revision.definition["triggerConfig"]["schedule"]
    item = session.scalar(select(m.WorkItem).where(m.WorkItem.job_id == ids["job_id"]))
    return {
        "queuedRuns": len(list(session.scalars(select(m.WorkItem.id).where(m.WorkItem.job_id == ids["job_id"])))),
        "trigger": (item.payload or {}).get("trigger", {}).get("type") if item else None,
        "nextDueIsTomorrowAtTheSameTime": datetime.fromisoformat(schedule["nextDueAt"])
            == (ids["due"] + timedelta(days=1)).replace(second=0, microsecond=0),
        "nextWakeUpsArmed": len([d for d in qstash.queue if d.destination.endswith("/schedule-due")]),
    }


def _outcome(session: Session, ids: dict[str, Any], provider: BenchProvider) -> dict[str, Any]:
    item = session.get(m.WorkItem, ids["item_id"])
    assert item is not None
    run = session.scalar(select(m.Run).where(m.Run.work_item_id == item.id))
    steps = list(session.scalars(select(m.RunStep).where(m.RunStep.run_id == run.id).order_by(m.RunStep.step_index))) if run else []
    approvals = list(session.scalars(select(m.Approval.status).where(m.Approval.organization_id == item.organization_id)))
    result = session.scalar(select(m.Result).where(m.Result.organization_id == item.organization_id))
    version = session.scalar(select(m.ResultVersion).where(m.ResultVersion.result_id == result.id)) if result else None
    return {
        "workItem": item.status,
        "run": run.status if run else None,
        "summary": run.result_summary if run else None,
        "steps": [{"kind": s.kind, "status": s.status, "scope": (s.input or {}).get("scope"),
                   "input": (s.input or {}).get("actionInput")} for s in steps],
        "providerCalls": provider.calls,
        "approvals": approvals,
        "plannerTools": ids.get("planner_tools", []),
        # Planning calls after the first that were given the saved plan (any model).
        "planInLaterPrompts": f"{sum(ids.get('saw_plan', [])[1:])} of {max(0, len(ids.get('saw_plan', [])) - 1)}",
        # The saved plan as it ended: each step done, or skipped when the run finished without it.
        "plan": [f"{row.position}. {row.title}: {row.status}" for row in session.scalars(
            select(m.RunPlanStep).where(m.RunPlanStep.run_id == run.id).order_by(m.RunPlanStep.position))] if run else [],
        **({"policiesCreated": policies} if (policies := sorted(
            name for name in session.scalars(select(m.Policy.name).where(m.Policy.organization_id == item.organization_id))
            if name.startswith("Always allow"))) else {}),
        **({"schedule": ids["schedule"]} if "schedule" in ids else {}),
        "result": {"status": result.status, "summary": (version.body or {}).get("summary") if version else None,
                   **({"completionCheck": version.body["completionCheck"]}
                      if version and "completionCheck" in (version.body or {}) else {})} if result else None,
    }


def _compute(session: Session, item_id: UUID | None, ai: ScriptedAI, deliveries: list[str]) -> dict[str, Any]:
    rows = list(session.scalars(select(m.UsageRecord).where(m.UsageRecord.work_item_id == item_id)))
    total = {name: sum(getattr(row, name) for row in rows) for name in (
        "qstash_messages", "db_statements", "db_rows", "redis_commands", "ai_calls", "ai_tokens_in", "ai_tokens_out")}
    return {
        **total,
        "deliveries": len([entry for entry in deliveries if entry.startswith("execute:")]),
        # Deliveries that did no work: duplicates, stale steps, or after the run finished.
        "idleDeliveries": len([row for row in rows if row.kind == "runtime.step" and row.phase is None]),
        "drains": len([entry for entry in deliveries if entry.startswith("drain:")]),
        "byOutcome": _count(f"{row.kind}:{row.phase or '-'}:{row.outcome}" for row in rows),
        # Whole-task totals, including the outbox drain and the approval request.
        "qstashTotal": READS.qstash,
        "dbStatementsTotal": READS.statements,
        "dbRowsTotal": READS.rows,
        "dbBytesRead": READS.bytes,
        "redisTotal": READS.redis,
        "largestPromptChars": max(ai.prompt_chars, default=0),
    }


def _count(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


async def run_task(task: Task, directory: Path) -> dict[str, Any]:
    # Type result processors are cached per engine on first use, so the datetime fix must be in
    # place before the engine runs anything.
    with _aware_sqlite_datetimes():
        return await _run_task(task, directory)


async def _run_task(task: Task, directory: Path) -> dict[str, Any]:
    engine = bench_engine(directory / f"{task.name}.db")

    def factory() -> BenchSession:
        return BenchSession(Session(engine, expire_on_commit=False))

    with Session(engine) as session:
        ids = _seed(session, task)
    qstash, redis_state = FakeQStash(), {}
    provider = BenchProvider(task.read_size)
    registry = ProviderRegistry((provider,))
    ai = ScriptedAI(task.answers, ids["resource_id"], task.fail_on_call)
    ids["planner_tools"] = []
    route = {"provider": "bench", "model": "scripted", "endpoint": "bench"}
    second = {"provider": "bench", "model": "scripted-second", "endpoint": "bench-second"}
    from app.infrastructure.database import session as session_module
    from app.runtime import outbox_publisher

    targets: list[tuple[Any, str, Any]] = [
        *[(module, "session_factory", factory) for module in _modules_with("session_factory", session_module.session_factory)],
        *[(module, "execution_provider_registry", lambda: registry)
          for module in _modules_with("execution_provider_registry", managed.execution_provider_registry)],
        (UpstashQStashProvider, "from_settings", classmethod(lambda cls: qstash)),
        (RedisCoordinator, "from_settings", classmethod(lambda cls: FakeCoordinator(redis_state))),
        (RedisRealtimeBus, "from_settings", classmethod(lambda cls: RedisRealtimeBus(FakeRedisClient(redis_state)))),  # type: ignore[arg-type]
        (smart_planner, "configured_routes", lambda: [route, second] if task.fail_on_call else [route]),
        (smart_planner, "provider_for", lambda _route: ai),
        (smart_planner, "route_credential", lambda _route, _settings: "bench"),
        (smart_planner, "route_account_name", lambda _route, _settings: "bench"),
        (smart_planner, "transport_identity", lambda _route: {"endpoint": "bench"}),
        (managed.ManagedRuntimeExecutor, "_choose_planner_decision", _tool_recorder(ids["resource_id"], ids["planner_tools"])),
        (runtime_routes, "_verify_qstash", lambda *a: None),
        (outbox_routes, "_verify_qstash", lambda *a: None),
        (outbox_publisher, "web_research_destination", lambda: None),
        *[(settings, name, value) for name, value in {
            "environment": "bench", "ai_enabled": True, "smart_planner_enabled": True,
            "integration_foundation_enabled": False, "runtime_execution_enabled": True, "cutover_stage": "runtime",
            "coding_execution_enabled": False, "usage_records_enabled": True,
            "qstash_url": "https://qstash.bench", "qstash_token": SecretStr("bench"),
            "qstash_runtime_execute_url": EXECUTE_URL, "qstash_outbox_drain_url": DRAIN_URL,
            "qstash_runtime_sweep_url": SWEEP_URL, "qstash_failure_callback_url": None,
        }.items()],
    ]
    deliveries: list[str] = []
    READS.reset(on=True)
    with _patched(targets):
        if task.scheduled:
            await arm_schedule(organization_id=ids["organization_id"], job_id=ids["job_id"], due=ids["due"])
        else:
            await request_runtime_execution(organization_id=ids["organization_id"], work_item_id=ids["item_id"],
                                            expected_step=0, reason="dispatch")
        await _settle()
        await _deliver(qstash, deliveries)
        if task.scheduled:
            with Session(engine) as session:
                ids["item_id"] = session.scalar(select(m.WorkItem.id).where(m.WorkItem.job_id == ids["job_id"]))
                ids["schedule"] = _schedule_outcome(session, ids, qstash)
        if task.approve:
            await _approve_pending(factory, ids, task.decision)
            await _settle()
            await _deliver(qstash, deliveries)
        if task.expire:
            await _expire_pending(factory, engine)
            await _settle()
            await _deliver(qstash, deliveries)
        READS.on = False
        ids["saw_plan"] = ai.saw_plan
        with Session(engine) as session:
            outcome = _outcome(session, ids, provider)
            compute = _compute(session, ids["item_id"], ai, deliveries)
    engine.dispose()
    return {"task": task.name, "outcome": outcome, "compute": compute}


async def _approve_pending(factory: Callable[[], BenchSession], ids: dict[str, Any], decision: dict[str, Any]) -> None:
    principal = HumanPrincipal(user_id=ids["human_id"], organization_id=ids["organization_id"], membership_id=uuid4(),
                               role="owner", permissions=frozenset({"approvals.review", "policies.manage"}))
    async with factory() as session:
        approval = await session.scalar(select(m.Approval).where(m.Approval.status == "pending"))
        assert approval is not None, "The task expected a pending approval."
        await governance_routes.approval_decision_v1(ids["organization_id"], approval.id,
                                                     {"decision": "approve", **decision}, principal, session)  # type: ignore[arg-type]


async def _expire_pending(factory: Callable[[], BenchSession], engine: Any) -> None:
    """Nobody decides: move the pending approval 25 hours into the past, then run the sweep's
    expiry pass and wake the run, as the 10-minute sweep would."""
    from app.application.services.approval_expiry import expire_and_remind, wake_expired

    with Session(engine) as session:
        for approval in session.scalars(select(m.Approval).where(m.Approval.status == "pending")):
            approval.created_at = approval.created_at - timedelta(hours=25)
        session.commit()
    async with factory() as session:
        wake = await expire_and_remind(session, now=datetime.now(UTC))  # type: ignore[arg-type]
    await wake_expired(wake)


async def run_all(tasks: list[Task] | None = None) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="agentgate-bench-") as directory:
        return [await run_task(task, Path(directory)) for task in tasks or TASKS]


def _table(results: list[dict[str, Any]]) -> str:
    columns = ("qstashTotal", "deliveries", "idleDeliveries", "drains", "dbStatementsTotal", "dbRowsTotal",
               "dbBytesRead", "redisTotal", "ai_calls", "ai_tokens_in", "largestPromptChars")
    header = ["task", "result", *columns]
    rows = [[r["task"], f"{r['outcome']['run']}/{len(r['outcome']['steps'])} steps",
             *[str(r["compute"][c]) for c in columns]] for r in results]
    widths = [max(len(str(row[i])) for row in [header, *rows]) for i in range(len(header))]
    return "\n".join("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths, strict=True))
                     for row in [header, *rows])


BASELINE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "runtime_benchmark_baseline.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--json", action="store_true", help="print the full results as JSON")
    parser.add_argument("--write-baseline", action="store_true",
                        help=f"save these results as the baseline the tests hold to ({BASELINE.name})")
    args = parser.parse_args()
    import logging

    logging.disable(logging.WARNING)
    results = asyncio.run(run_all())
    if args.write_baseline:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2, default=str) if args.json else _table(results))


if __name__ == "__main__":
    main()
