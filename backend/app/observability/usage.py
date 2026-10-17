"""Compute used by one unit of work (a runtime step or a chat message), for before-and-after
comparisons (improvement plan, phase 0).

A meter is bound to the current context with `metered()`. Database statements, QStash
messages, Redis commands and AI calls made inside that context, including in tasks it
starts, are added to it. Nothing is counted when no meter is bound, so the hooks cost one
context lookup outside measured work.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from collections import Counter
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import greenlet
from redis.asyncio import Redis
from sqlalchemy import event

_CURRENT: ContextVar[Usage | None] = ContextVar("audoryn_usage_meter", default=None)
# Development aid: with AUDORYN_TRACE_STATEMENTS=1 each meter also counts its statements by table and
# the AgentGate line that issued them, and the usage log lists the most frequent ones.
TRACE_STATEMENTS = os.environ.get("AUDORYN_TRACE_STATEMENTS") == "1"
_TABLE = re.compile(r"^\s*(?:SELECT\b.*?\bFROM|INSERT INTO|UPDATE|DELETE FROM)\s+([\w.]+)", re.IGNORECASE | re.DOTALL)


@dataclass(slots=True)
class Usage:
    db_statements: int = 0
    # Rows returned by SELECT statements, or changed by writes, where the driver reports them.
    db_rows: int = 0
    qstash_messages: int = 0
    redis_commands: int = 0
    ai_calls: int = 0
    ai_failed_calls: int = 0
    ai_tokens_in: int = 0
    ai_tokens_out: int = 0
    ai_models: Counter[str] = field(default_factory=Counter)
    # Characters sent to models, per prompt section ("planner.RECORDED OBSERVATIONS").
    prompt_chars: Counter[str] = field(default_factory=Counter)
    # Time spent in browser actions and in running coding sandboxes, the two metered tools.
    browser_seconds: float = 0.0
    coding_seconds: float = 0.0
    # Counted against the organisation's plan: new tasks admitted, web searches made.
    tasks: int = 0
    web_searches: int = 0
    seconds: float = 0.0
    # Set by the measured code, for example the runtime phase and run.
    labels: dict[str, Any] = field(default_factory=dict)
    # Statements by "verb table @ file:line function" (only with TRACE_STATEMENTS).
    statements: Counter[str] = field(default_factory=Counter)

    def merge(self, other: Usage) -> None:
        self.db_statements += other.db_statements
        self.db_rows += other.db_rows
        self.qstash_messages += other.qstash_messages
        self.redis_commands += other.redis_commands
        self.ai_calls += other.ai_calls
        self.ai_failed_calls += other.ai_failed_calls
        self.ai_tokens_in += other.ai_tokens_in
        self.ai_tokens_out += other.ai_tokens_out
        self.ai_models.update(other.ai_models)
        self.prompt_chars.update(other.prompt_chars)
        self.browser_seconds += other.browser_seconds
        self.coding_seconds += other.coding_seconds
        self.tasks += other.tasks
        self.web_searches += other.web_searches
        self.seconds += other.seconds

    def as_dict(self) -> dict[str, Any]:
        return {
            "dbStatements": self.db_statements,
            "dbRows": self.db_rows,
            "qstashMessages": self.qstash_messages,
            "redisCommands": self.redis_commands,
            "aiCalls": self.ai_calls,
            "aiFailedCalls": self.ai_failed_calls,
            "aiTokensIn": self.ai_tokens_in,
            "aiTokensOut": self.ai_tokens_out,
            "aiModels": dict(sorted(self.ai_models.items())),
            "promptChars": dict(sorted(self.prompt_chars.items())),
            "browserSeconds": round(self.browser_seconds, 3),
            "codingSeconds": round(self.coding_seconds, 3),
            "tasks": self.tasks,
            "webSearches": self.web_searches,
            "seconds": round(self.seconds, 3),
            **({"statementSources": dict(self.statements.most_common(60))} if self.statements else {}),
        }


def current_usage() -> Usage | None:
    return _CURRENT.get()


@contextmanager
def metered(usage: Usage | None = None) -> Iterator[Usage]:
    """Count the work done inside this block, including tasks it starts, into one meter.
    A nested block counts only into its own meter: background work started from a chat
    message, for example, is recorded on its own."""
    meter = usage or Usage()
    token = _CURRENT.set(meter)
    started = time.monotonic()
    try:
        yield meter
    finally:
        meter.seconds += time.monotonic() - started
        _CURRENT.reset(token)


def label_usage(**labels: Any) -> None:
    if (meter := _CURRENT.get()) is not None:
        meter.labels.update(labels)


def count_qstash_message(destination: str | None = None) -> None:
    if (meter := _CURRENT.get()) is not None:
        meter.qstash_messages += 1
        return
    # Every QStash message is billed; one sent outside measured work is still reported.
    path = urlsplit(destination).path if destination else None
    logging.getLogger("uvicorn.error").info("QStash message outside a meter destination=%s", path)


def count_redis_commands(count: int = 1) -> None:
    if (meter := _CURRENT.get()) is not None:
        meter.redis_commands += count


def count_ai_call(*, model: str, usage: dict[str, Any] | None, success: bool) -> None:
    if (meter := _CURRENT.get()) is None:
        return
    meter.ai_calls += 1
    meter.ai_models[model] += 1
    if not success:
        meter.ai_failed_calls += 1
    usage = usage or {}
    for key, attribute in (("prompt_tokens", "ai_tokens_in"), ("completion_tokens", "ai_tokens_out")):
        value = usage.get(key)
        if type(value) is int and value > 0:
            setattr(meter, attribute, getattr(meter, attribute) + value)


def count_prompt(role: str, sections: dict[str, int]) -> None:
    if (meter := _CURRENT.get()) is not None:
        meter.prompt_chars.update({f"{role}.{name}": size for name, size in sections.items()})


def count_plan_usage(*, tasks: int = 0, web_searches: int = 0) -> None:
    if (meter := _CURRENT.get()) is not None:
        meter.tasks += tasks
        meter.web_searches += web_searches


def count_tool_seconds(*, browser: float = 0.0, coding: float = 0.0) -> None:
    if (meter := _CURRENT.get()) is not None:
        meter.browser_seconds += max(0.0, browser)
        meter.coding_seconds += max(0.0, coding)


@asynccontextmanager
async def browser_time() -> AsyncIterator[None]:
    """Count the time spent inside this block, or a call of the async function it decorates, as
    browser time."""
    started = time.monotonic()
    try:
        yield
    finally:
        count_tool_seconds(browser=time.monotonic() - started)


def _after_cursor_execute(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
    if (meter := _CURRENT.get()) is None:
        return
    meter.db_statements += 1
    if TRACE_STATEMENTS:
        meter.statements[_statement_source(statement)] += 1
    rowcount = getattr(cursor, "rowcount", -1)
    if isinstance(rowcount, int) and rowcount > 0:
        meter.db_rows += rowcount


def _statement_source(statement: str) -> str:
    """"select work_items @ runtime/managed.py:443 execute_step": the table and the innermost AgentGate
    line outside the meter that issued the statement."""
    verb = statement.split(None, 1)[0].lower()
    target = f"{verb} {match.group(1)}" if (match := _TABLE.match(statement)) else verb
    # An async session runs the statement in a greenlet; the awaiting code is on its parent's stack.
    frame, parent = sys._getframe(), greenlet.getcurrent().parent  # pyright: ignore[reportPrivateUsage]
    while frame is not None or parent is not None:
        if frame is None:
            frame, parent = parent.gr_frame, parent.parent  # pyright: ignore[reportOptionalMemberAccess]
            continue
        path = frame.f_code.co_filename.replace("\\", "/")
        if "/app/" in path and not path.endswith(("/observability/usage.py", "/database/session.py")):
            return f"{target} @ {path.rsplit('/app/', 1)[-1]}:{frame.f_lineno} {frame.f_code.co_name}"
        frame = frame.f_back
    return target


def meter_database(engine: Any) -> None:
    """Count statements on this engine (sync or async). Safe to call more than once."""
    target = getattr(engine, "sync_engine", engine)
    if not event.contains(target, "after_cursor_execute", _after_cursor_execute):
        event.listen(target, "after_cursor_execute", _after_cursor_execute)


class MeteredRedis(Redis):
    """A Redis client whose commands count towards the current meter."""

    async def execute_command(self, *args: Any, **options: Any) -> Any:
        count_redis_commands()
        return await super().execute_command(*args, **options)

    def pipeline(self, transaction: bool = True, shard_hint: Any = None) -> Any:
        pipeline = super().pipeline(transaction, shard_hint)
        execute = pipeline.execute

        async def counted(*args: Any, **kwargs: Any) -> Any:
            count_redis_commands(len(pipeline.command_stack))
            return await execute(*args, **kwargs)

        pipeline.execute = counted  # type: ignore[method-assign]
        return pipeline
