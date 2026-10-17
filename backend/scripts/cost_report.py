"""What each organisation used, and what it cost, over a period (commercial plan, phase 1). Read-only.

Per organisation: tasks, chat messages, AI tokens by model, browser and coding seconds, and
stored bytes, from `usage_records`, `ai_invocations` and `artifacts`. Prices are never built in:
pass them in a JSON file, and the report adds a cost column. Without one it shows quantities only.

Prices file (all optional; model prices per million tokens, keyed by model name):
    {"models": {"<model>": {"input": 0.30, "output": 1.20}},
     "browserHour": 0.02, "codingHour": 0.10, "storageGbMonth": 0.02, "currency": "USD"}

Usage: python scripts/cost_report.py [--since 2026-10-01] [--until 2026-11-01] [--prices prices.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import Integer, func, select

from app.infrastructure.database.models import AIInvocation, Artifact, UsageRecord

GB = 1024**3


@dataclass
class OrganizationUsage:
    tasks: int = 0
    messages: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    browser_seconds: float = 0.0
    coding_seconds: float = 0.0
    storage_bytes: int = 0
    # model -> [input tokens, output tokens]
    models: dict[str, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))


def _token(column: str) -> Any:
    return func.coalesce(func.sum(AIInvocation.usage[column].as_string().cast(Integer)), 0)


async def collect(since: datetime, until: datetime) -> dict[str, OrganizationUsage]:
    from app.infrastructure.database.session import session_factory

    in_period = (UsageRecord.created_at >= since, UsageRecord.created_at < until)
    async with session_factory() as session:
        records = (await session.execute(
            select(UsageRecord.organization_id, func.count(UsageRecord.work_item_id.distinct()),
                   func.count().filter(UsageRecord.kind == "conversation.message"),
                   func.sum(UsageRecord.ai_tokens_in), func.sum(UsageRecord.ai_tokens_out),
                   func.sum(UsageRecord.browser_seconds), func.sum(UsageRecord.coding_seconds))
            .where(*in_period).group_by(UsageRecord.organization_id))).all()
        tokens = (await session.execute(
            select(AIInvocation.organization_id, AIInvocation.model, _token("prompt_tokens"),
                   _token("completion_tokens"))
            .where(AIInvocation.created_at >= since, AIInvocation.created_at < until)
            .group_by(AIInvocation.organization_id, AIInvocation.model))).all()
        # Storage is what is held at the end of the period.
        storage = (await session.execute(
            select(Artifact.organization_id, func.coalesce(func.sum(Artifact.size_bytes), 0))
            .where(Artifact.created_at < until).group_by(Artifact.organization_id))).all()
    usage: dict[str, OrganizationUsage] = defaultdict(OrganizationUsage)
    for organization, tasks, messages, tokens_in, tokens_out, browser, coding in records:
        row = usage[str(organization)]
        row.tasks, row.messages = tasks, messages
        row.tokens_in, row.tokens_out = int(tokens_in or 0), int(tokens_out or 0)
        row.browser_seconds, row.coding_seconds = float(browser or 0), float(coding or 0)
    for organization, model, tokens_in, tokens_out in tokens:
        name = str(organization) if organization else "(no organisation)"
        usage[name].models[model] = [int(tokens_in), int(tokens_out)]
    for organization, stored in storage:
        usage[str(organization)].storage_bytes = int(stored or 0)
    return dict(usage)


def cost(row: OrganizationUsage, prices: dict[str, Any], days: float) -> tuple[float, list[str]]:
    """The priced cost of one organisation's usage, and the models that have no price."""
    total, unpriced = 0.0, []
    for model, (tokens_in, tokens_out) in row.models.items():
        price = prices.get("models", {}).get(model)
        if price is None:
            unpriced.append(model)
            continue
        total += (tokens_in * float(price.get("input", 0)) + tokens_out * float(price.get("output", 0))) / 1e6
    total += row.browser_seconds / 3600 * float(prices.get("browserHour", 0))
    total += row.coding_seconds / 3600 * float(prices.get("codingHour", 0))
    total += row.storage_bytes / GB * float(prices.get("storageGbMonth", 0)) * days / 30
    return total, unpriced


def _table(header: list[str], rows: list[list[object]]) -> str:
    widths = [max(len(str(row[i])) for row in [header, *rows]) for i in range(len(header))]
    return "\n".join("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths, strict=True))
                     for row in [header, *rows])


def render(usage: dict[str, OrganizationUsage], since: datetime, until: datetime,
           prices: dict[str, Any] | None) -> str:
    days = (until - since).total_seconds() / 86400
    header = ["organisation", "tasks", "messages", "tokens in", "tokens out", "browser s", "coding s", "storage MB"]
    if prices is not None:
        header.append(f"cost {prices.get('currency', '')}".strip())
    rows, models, unpriced = [], [], set()
    for organization, row in sorted(usage.items(), key=lambda item: -(item[1].tokens_in + item[1].tokens_out)):
        line: list[object] = [organization, row.tasks, row.messages, row.tokens_in, row.tokens_out,
                              round(row.browser_seconds), round(row.coding_seconds),
                              round(row.storage_bytes / 1024**2, 1)]
        if prices is not None:
            total, missing = cost(row, prices, days)
            unpriced.update(missing)
            line.append(f"{total:.4f}")
        rows.append(line)
        models += [[organization, model, tokens_in, tokens_out] for model, (tokens_in, tokens_out)
                   in sorted(row.models.items())]
    parts = [f"Usage from {since:%Y-%m-%d %H:%M} to {until:%Y-%m-%d %H:%M} UTC ({days:g} days)",
             _table(header, rows), "AI tokens by model",
             _table(["organisation", "model", "tokens in", "tokens out"], models)]
    if unpriced:
        parts.append("No price given (not in the cost): " + ", ".join(sorted(unpriced)))
    return "\n\n".join(parts)


def _day(value: str) -> datetime:
    return datetime.combine(date.fromisoformat(value), time(), UTC)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--since", type=_day, help="first day, YYYY-MM-DD (default: 30 days ago)")
    parser.add_argument("--until", type=_day, help="day after the last, YYYY-MM-DD (default: now)")
    parser.add_argument("--prices", type=Path, help="JSON file of prices (see above)")
    args = parser.parse_args()
    until = args.until or datetime.now(UTC)
    since = args.since or until - timedelta(days=30)
    prices = json.loads(args.prices.read_text(encoding="utf-8")) if args.prices else None
    print(render(asyncio.run(collect(since, until)), since, until, prices))


if __name__ == "__main__":
    main()
