"""Read-only database access and parameter parsing for Console reads.

Queries are plain synchronous SQLAlchemy Core functions run through ``run_sync`` inside
a transaction that PostgreSQL itself enforces as READ ONLY with a statement timeout, and
that is always rolled back. They select named columns only; nothing is ever written.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, String, literal, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement

from app.application.console_reads.guard import InvalidParameters

_STATEMENT_TIMEOUT_SQLSTATE = "57014"  # query_canceled
_JSON_KEY = re.compile(r"[a-z_]{1,40}")


def _engine() -> AsyncEngine:
    from app.infrastructure.database.session import engine

    return engine


def _is_statement_timeout(error: DBAPIError) -> bool:
    original = error.orig
    codes = {getattr(original, "sqlstate", None), getattr(original, "pgcode", None)}
    return _STATEMENT_TIMEOUT_SQLSTATE in codes or "statement timeout" in str(original).lower()


async def run_read_only[T](
    query: Callable[[Connection], T], *, timeout_ms: int, engine: AsyncEngine | None = None
) -> T:
    """Run ``query`` in a rolled-back READ ONLY transaction; a statement timeout becomes TimeoutError."""
    async with (engine or _engine()).connect() as connection:
        transaction = await connection.begin()
        try:
            if connection.dialect.name == "postgresql":
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await connection.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
            return await connection.run_sync(query)
        except DBAPIError as exc:
            if _is_statement_timeout(exc):
                raise TimeoutError("statement timeout") from exc
            raise
        finally:
            await transaction.rollback()


# ---------- dialect-aware expressions ----------

class json_integer(FunctionElement[int]):
    """A JSON object's numeric field as an integer, or 0 when absent or not a number.

    Guarded like Console's former SQL: a string such as "12a" never breaks the aggregate.
    """

    type = BigInteger()
    inherit_cache = True
    name = "json_integer"

    def __init__(self, column: Any, key: str) -> None:
        if not _JSON_KEY.fullmatch(key):
            raise ValueError("JSON keys are fixed identifiers")
        super().__init__(column, literal(key, String()))


@compiles(json_integer, "postgresql")
def _json_integer_postgresql(element: json_integer, compiler: Any, **kw: Any) -> str:
    column, key = (compiler.process(clause, **kw) for clause in element.clauses)
    value = f"({column} -> CAST({key} AS TEXT))"
    return (
        f"CASE WHEN jsonb_typeof({value}) = 'number' "
        f"THEN floor(CAST(({column} ->> CAST({key} AS TEXT)) AS NUMERIC))::bigint ELSE 0 END"
    )


@compiles(json_integer, "sqlite")
def _json_integer_sqlite(element: json_integer, compiler: Any, **kw: Any) -> str:
    column, key = (compiler.process(clause, **kw) for clause in element.clauses)
    path = f"('$.' || {key})"
    return (
        f"CASE WHEN json_type({column}, {path}) IN ('integer', 'real') "
        f"THEN CAST(json_extract({column}, {path}) AS INTEGER) ELSE 0 END"
    )


class utc_day(FunctionElement[str]):
    """The UTC calendar day of a timestamp (a date on PostgreSQL, 'YYYY-MM-DD' on SQLite)."""

    type = String()
    inherit_cache = True
    name = "utc_day"


@compiles(utc_day, "postgresql")
def _utc_day_postgresql(element: utc_day, compiler: Any, **kw: Any) -> str:
    return f"CAST(timezone('UTC', {compiler.process(element.clauses, **kw)}) AS DATE)"


@compiles(utc_day, "sqlite")
def _utc_day_sqlite(element: utc_day, compiler: Any, **kw: Any) -> str:
    return f"date({compiler.process(element.clauses, **kw)})"


def day_text(value: Any) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)[:10]


# ---------- output ----------

def iso(value: datetime) -> str:
    """UTC ISO-8601 with offset. Naive values (SQLite in tests) are treated as UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def iso_or_none(value: datetime | None) -> str | None:
    return None if value is None else iso(value)


def text_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def clip(value: str | None, limit: int) -> str:
    """Free text bounded for the contract; marks the cut with an ellipsis."""
    text_value = value or ""
    return text_value if len(text_value) <= limit else text_value[: limit - 1] + "…"


# ---------- parameters ----------

def int_param(parameters: Mapping[str, str], name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = parameters.get(name)
    if raw is None:
        return default
    if not raw.isascii() or not raw.isdigit() or len(raw) > 9:
        raise InvalidParameters(name)
    value = int(raw)
    if not minimum <= value <= maximum:
        raise InvalidParameters(name)
    return value


def text_param(parameters: Mapping[str, str], name: str, *, max_length: int) -> str | None:
    raw = parameters.get(name)
    if raw is None:
        return None
    value = raw.strip()
    if len(value) > max_length or any(ord(char) < 32 for char in value):
        raise InvalidParameters(name)
    return value or None


def uuid_param(parameters: Mapping[str, str], name: str) -> UUID:
    raw = parameters.get(name)
    if raw is None or len(raw) != 36:
        raise InvalidParameters(name)
    try:
        return UUID(raw)
    except ValueError as exc:
        raise InvalidParameters(name) from exc


def datetime_param(parameters: Mapping[str, str], name: str) -> datetime | None:
    raw = parameters.get(name)
    if raw is None:
        return None
    if len(raw) > 40:
        raise InvalidParameters(name)
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise InvalidParameters(name) from exc
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def like_pattern(value: str) -> str:
    """Contains-match pattern with LIKE wildcards in user input escaped (escape char ``\\``)."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
