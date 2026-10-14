"""Resolve an approval's conversation from its durable work origin."""
from typing import Any

from sqlalchemy import select

from app.infrastructure.database.models import JobRevision, Run, WorkItem


async def approval_conversation_id(session, action):
    if not action.run_id:
        return None
    run = await session.get(Run, action.run_id)
    if not run or run.organization_id != action.organization_id:
        return None
    item = await session.get(WorkItem, run.work_item_id)
    if not item or item.organization_id != action.organization_id:
        return None
    origin = (item.payload or {}).get("integrationOrigin", {})
    if origin.get("threadId"):
        return str(origin["threadId"])
    revision = await session.get(JobRevision, item.job_revision_id)
    autonomy = (revision.definition or {}).get("autonomy", {}) if revision else {}
    return autonomy.get("resultsThreadId") or autonomy.get("sourceThreadId")


async def approval_conversation_ids(session, actions) -> dict:
    """``approval_conversation_id`` for many actions in three queries.

    Same rules, but only the two JSON parts used are read (``integrationOrigin`` from the work
    item payload, ``autonomy`` from the job revision), never the whole documents.
    """
    result: dict[Any, Any] = {action.id: None for action in actions}
    with_run = [action for action in actions if action.run_id]
    if not with_run:
        return result
    runs = {
        row.id: row
        for row in await session.execute(
            select(Run.id, Run.organization_id, Run.work_item_id)
            .where(Run.id.in_({action.run_id for action in with_run}))
        )
    }
    item_ids = {run.work_item_id for run in runs.values()}
    items = {
        row.id: row
        for row in await session.execute(
            select(
                WorkItem.id,
                WorkItem.organization_id,
                WorkItem.job_revision_id,
                WorkItem.payload["integrationOrigin"].label("origin"),
            ).where(WorkItem.id.in_(item_ids))
        )
    } if item_ids else {}
    pending = {}
    for action in with_run:
        run = runs.get(action.run_id)
        if not run or run.organization_id != action.organization_id:
            continue
        item = items.get(run.work_item_id)
        if not item or item.organization_id != action.organization_id:
            continue
        origin = item.origin if isinstance(item.origin, dict) else {}
        if origin.get("threadId"):
            result[action.id] = str(origin["threadId"])
        else:
            pending[action.id] = item.job_revision_id
    if pending:
        autonomy_by_revision = {
            row.id: row.autonomy
            for row in await session.execute(
                select(JobRevision.id, JobRevision.definition["autonomy"].label("autonomy"))
                .where(JobRevision.id.in_(set(pending.values())))
            )
        }
        for action_id, revision_id in pending.items():
            autonomy = autonomy_by_revision.get(revision_id)
            autonomy = autonomy if isinstance(autonomy, dict) else {}
            result[action_id] = autonomy.get("resultsThreadId") or autonomy.get("sourceThreadId")
    return result
