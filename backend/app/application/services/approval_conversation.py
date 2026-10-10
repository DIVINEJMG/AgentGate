"""Resolve an approval's conversation from its durable work origin."""
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
