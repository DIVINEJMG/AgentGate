"""Compute used by one unit of work (a runtime step or a chat message), for before-and-after
comparisons (improvement plan, phase 0).

A meter is bound to the current context with `metered()`. Database statements, QStash
messages, Redis commands and AI calls made inside that context, including in tasks it
starts, are added to it. Nothing is counted when no meter is bound, so the hooks cost one
context lookup outside measured work.
"""
from __future__ import annotations

import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import event

_CURRENT: ContextVar[Usage | None] = ContextVar("audoryn_usage_meter", default=None)


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
    seconds: float = 0.0
    # Set by the measured code, for example the runtime phase and run.
    labels: dict[str, Any] = field(default_factory=dict)

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
            "seconds": round(self.seconds, 3),
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


def count_qstash_message() -> None:
    if (meter := _CURRENT.get()) is not None:
        meter.qstash_messages += 1


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


def _after_cursor_execute(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
    if (meter := _CURRENT.get()) is None:
        return
    meter.db_statements += 1
    rowcount = getattr(cursor, "rowcount", -1)
    if isinstance(rowcount, int) and rowcount > 0:
        meter.db_rows += rowcount


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
