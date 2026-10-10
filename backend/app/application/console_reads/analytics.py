"""Usage and AI usage analytics (phase 6).

camelCase shapes match what Console's observability adapter checks. Only aggregates
leave AgentGate: no prompts, responses, request IDs or per-call records. Cost is
reported as unavailable because AgentGate does not record prices.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, distinct, func, select
from sqlalchemy.engine import Connection

from app.application.console_reads.database import day_text, json_integer, utc_day
from app.application.console_reads.visibility import (
    _ints,
    _n,
    _values,
    ai_invocations,
    identities,
    integrations,
    organizations,
    runs,
    workers,
)

COST_NOTE = "AgentGate AI invocation telemetry does not persist pricing or estimated cost."


def _since(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


def _daily_counts(connection: Connection, table: Any, since: datetime) -> dict[str, int]:
    day = utc_day(table.c.created_at)
    rows = connection.execute(select(day, func.count()).where(table.c.created_at >= since).group_by(day))
    return {day_text(key): int(value) for key, value in rows}


def read_usage(connection: Connection, *, days: int, now: datetime) -> dict[str, object]:
    since = _since(now, days)
    new_users = _daily_counts(connection, identities, since)
    run_days = _daily_counts(connection, runs, since)
    ai_days = _daily_counts(connection, ai_invocations, since)
    calendar = [(now - timedelta(days=offset)).date().isoformat() for offset in range(days - 1, -1, -1)]
    return {
        "windowDays": days,
        "metrics": _ints(
            _values(
                connection,
                newUsers=_n(identities, identities.c.created_at >= since),
                organizations=_n(organizations),
                activeOrganizations=select(func.count(distinct(runs.c.organization_id)))
                .where(runs.c.created_at >= since)
                .scalar_subquery(),
                workersCreated=_n(workers, workers.c.created_at >= since),
                runs=_n(runs, runs.c.created_at >= since),
                aiInvocations=_n(ai_invocations, ai_invocations.c.created_at >= since),
                integrations=_n(integrations),
            )
        ),
        # One entry per UTC day in the window, oldest first, zero-filled.
        "daily": [
            {"day": day, "newUsers": new_users.get(day, 0), "runs": run_days.get(day, 0), "aiInvocations": ai_days.get(day, 0)}
            for day in calendar
        ],
    }


def _successes() -> Any:
    return func.sum(case((ai_invocations.c.success.is_(True), 1), else_=0))


def _tokens(key: str = "total_tokens") -> Any:
    return func.coalesce(func.sum(json_integer(ai_invocations.c.usage, key)), 0)


def _latency(value: Any) -> float | None:
    return None if value is None else round(float(value), 1)


def read_ai_usage(connection: Connection, *, days: int, limit: int, now: datetime) -> dict[str, object]:
    window = ai_invocations.c.created_at >= _since(now, days)
    summary = connection.execute(
        select(
            func.count(),
            _successes(),
            func.avg(ai_invocations.c.latency_ms),
            _tokens("prompt_tokens"),
            _tokens("completion_tokens"),
            _tokens(),
        ).where(window)
    ).one()
    invocations, successes = int(summary[0] or 0), int(summary[1] or 0)
    count = func.count().label("invocations")
    models = connection.execute(
        select(
            ai_invocations.c.provider,
            ai_invocations.c.model,
            ai_invocations.c.role,
            count,
            _successes(),
            func.avg(ai_invocations.c.latency_ms),
            _tokens(),
        )
        .where(window)
        .group_by(ai_invocations.c.provider, ai_invocations.c.model, ai_invocations.c.role)
        .order_by(count.desc(), ai_invocations.c.provider, ai_invocations.c.model, ai_invocations.c.role)
        .limit(limit)
    )

    def grouped(column: Any) -> list[Any]:
        label = func.count().label("invocations")
        return list(
            connection.execute(
                select(column, label, _successes(), _tokens())
                .where(window, column.is_not(None))
                .group_by(column)
                .order_by(label.desc(), column)
                .limit(limit)
            )
        )

    day = utc_day(ai_invocations.c.created_at)
    daily = connection.execute(select(day, func.count(), _successes(), _tokens()).where(window).group_by(day).order_by(day))
    return {
        "windowDays": days,
        "summary": {
            "invocations": invocations,
            "successes": successes,
            "failures": invocations - successes,
            "averageLatencyMs": _latency(summary[2]),
            "promptTokens": int(summary[3] or 0),
            "completionTokens": int(summary[4] or 0),
            "totalTokens": int(summary[5] or 0),
            "estimatedCost": None,
            "costAvailable": False,
            "costNote": COST_NOTE,
        },
        "models": [
            {
                "provider": row[0],
                "model": row[1],
                "role": row[2],
                "invocations": int(row[3]),
                "successes": int(row[4] or 0),
                "averageLatencyMs": _latency(row[5]),
                "totalTokens": int(row[6] or 0),
            }
            for row in models
        ],
        "organizations": [
            {"organizationId": str(row[0]), "invocations": int(row[1]), "successes": int(row[2] or 0), "totalTokens": int(row[3] or 0)}
            for row in grouped(ai_invocations.c.organization_id)
        ],
        "workers": [
            {"workerId": str(row[0]), "invocations": int(row[1]), "successes": int(row[2] or 0), "totalTokens": int(row[3] or 0)}
            for row in grouped(ai_invocations.c.worker_id)
        ],
        "daily": [
            {"day": day_text(row[0]), "invocations": int(row[1]), "successes": int(row[2] or 0), "totalTokens": int(row[3] or 0)}
            for row in daily
        ],
    }
