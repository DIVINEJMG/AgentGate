"""Capture a live run of the task set (improvement plan, phase 0 and 3). Read-only.

Saves, for a recent period: every chat turn (the message, the reply, the chosen command family
and its status), every work item started in the period (status, steps, result summary, AI calls,
tokens and time from usage records), and the usage totals. Run once before AI behaviour changes
("before") and once after on the same tasks ("after"), then compare.

The file holds the conversation text, so it is written under `backend/.local/` (not committed).

Usage: python scripts/live_baseline.py --label before [--hours 3]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from audoryn_shared.infrastructure.database.models import (
    ConversationCommand,
    ConversationMessage,
    Job,
    Run,
    RunStep,
    UsageRecord,
    WorkItem,
)

OUTPUT = Path(__file__).resolve().parents[1] / ".local" / "live-baseline"


async def capture(hours: float) -> dict[str, object]:
    from audoryn_shared.infrastructure.database.session import session_factory

    since = datetime.now(UTC) - timedelta(hours=hours)
    async with session_factory() as session:
        messages = list((await session.scalars(
            select(ConversationMessage).where(ConversationMessage.created_at >= since)
            .order_by(ConversationMessage.thread_id, ConversationMessage.created_at))).all())
        commands = {str(c.source_message_id): c for c in (await session.scalars(
            select(ConversationCommand).where(ConversationCommand.created_at >= since))).all()}
        usage = {str(row.thread_id): row for row in (await session.execute(
            select(UsageRecord.thread_id, func.count().label("messages"), func.sum(UsageRecord.ai_calls).label("ai_calls"),
                   func.sum(UsageRecord.ai_tokens_in).label("tokens_in"), func.sum(UsageRecord.duration_ms).label("ms"))
            .where(UsageRecord.created_at >= since, UsageRecord.kind == "conversation.message")
            .group_by(UsageRecord.thread_id))).all()}
        turns = []
        for message in messages:
            if message.role != "human":
                continue
            command = commands.get(str(message.id))
            reply = next((m for m in messages if m.thread_id == message.thread_id and m.role != "human"
                          and m.created_at >= message.created_at), None)
            turns.append({
                "thread": str(message.thread_id), "at": message.created_at.isoformat(), "message": message.content,
                "family": command.family if command else None, "status": command.status if command else None,
                "reply": reply.content if reply else None,
            })
        items = list((await session.scalars(select(WorkItem).where(WorkItem.created_at >= since)
                                            .order_by(WorkItem.created_at))).all())
        work = []
        for item in items:
            job = await session.get(Job, item.job_id)
            run = await session.scalar(select(Run).where(Run.work_item_id == item.id).order_by(Run.created_at.desc()).limit(1))
            steps = list((await session.execute(
                select(RunStep.kind, RunStep.status, RunStep.input["scope"].as_string())
                .where(RunStep.run_id == run.id).order_by(RunStep.step_index))).all()) if run else []
            cost = (await session.execute(
                select(func.count(), func.sum(UsageRecord.ai_calls), func.sum(UsageRecord.ai_tokens_in),
                       func.sum(UsageRecord.qstash_messages), func.sum(UsageRecord.db_statements), func.sum(UsageRecord.duration_ms))
                .where(UsageRecord.work_item_id == item.id))).one()
            work.append({
                "job": job.name if job else None, "trigger": (item.payload or {}).get("trigger", {}).get("type"),
                "status": item.status, "run": run.status if run else None,
                "summary": run.result_summary if run else None,
                "steps": [{"kind": kind, "status": status, "scope": scope} for kind, status, scope in steps],
                "usage": dict(zip(("records", "aiCalls", "tokensIn", "qstash", "statements", "ms"), cost, strict=True)),
            })
    return {"capturedAt": datetime.now(UTC).isoformat(), "since": since.isoformat(), "turns": turns, "work": work,
            "chatUsageByThread": {thread: dict(row._mapping) for thread, row in usage.items()}}


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--label", required=True, help="for example before or after")
    parser.add_argument("--hours", type=float, default=3, help="how far back to capture (default 3)")
    args = parser.parse_args()
    result = asyncio.run(capture(args.hours))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / f"{args.label}.json"
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"Saved {len(result['turns'])} chat turns and {len(result['work'])} work items to {path}")  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
