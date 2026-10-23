"""Summarise saved usage records (improvement plan, phase 0). Read-only.

Shows what runtime steps, chat messages and preparations cost over a period, from the
`usage_records` table of the configured database: totals by kind and phase, then the most
expensive work items.

Usage: python scripts/usage_report.py [--hours 24] [--top 10]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from audoryn_shared.infrastructure.database.models import UsageRecord

COUNTERS = ("qstash_messages", "db_statements", "db_rows", "redis_commands", "ai_calls", "ai_failed_calls",
            "ai_tokens_in", "ai_tokens_out", "duration_ms")


def _table(header: list[str], rows: list[list[object]]) -> str:
    widths = [max(len(str(row[i])) for row in [header, *rows]) for i in range(len(header))]
    return "\n".join("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths, strict=True))
                     for row in [header, *rows])


async def report(hours: float, top: int) -> str:
    from audoryn_shared.infrastructure.database.session import session_factory

    since = datetime.now(UTC) - timedelta(hours=hours)
    sums = [func.sum(getattr(UsageRecord, name)) for name in COUNTERS]
    async with session_factory() as session:
        by_kind = (await session.execute(
            select(UsageRecord.kind, UsageRecord.phase, func.count(), *sums)
            .where(UsageRecord.created_at >= since)
            .group_by(UsageRecord.kind, UsageRecord.phase)
            .order_by(UsageRecord.kind, UsageRecord.phase))).all()
        by_item = (await session.execute(
            select(UsageRecord.work_item_id, func.count(), *sums)
            .where(UsageRecord.created_at >= since, UsageRecord.work_item_id.is_not(None))
            .group_by(UsageRecord.work_item_id)
            .order_by(func.sum(UsageRecord.db_rows).desc())
            .limit(top))).all()
    header = ["records", *COUNTERS]
    return "\n\n".join([
        f"Usage since {since:%Y-%m-%d %H:%M} UTC ({hours:g} h)",
        _table(["kind", "phase", *header], [[kind, phase or "-", *values] for kind, phase, *values in by_kind]),
        f"Top {top} work items by rows",
        _table(["work item", *header], [[str(item), *values] for item, *values in by_item]),
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--hours", type=float, default=24, help="how far back to look (default 24)")
    parser.add_argument("--top", type=int, default=10, help="how many work items to list (default 10)")
    args = parser.parse_args()
    print(asyncio.run(report(args.hours, args.top)))


if __name__ == "__main__":
    main()
