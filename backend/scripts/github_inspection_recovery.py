"""Explicit recovery of one scripted read; never replay an uncertain mutation."""
from datetime import UTC, datetime

from sqlalchemy import select

from app.application.services.integration_foundation import IntegrationFoundation
from app.infrastructure.database.models import Action, Run
from scripts.github_scripted_planner import FixtureBlocked

INSPECTIONS = {
    "repository.workspace.diff", "repository.workspace.read",
    "repository.workspace.search", "repository.workspace.command.status",
}


def validate_recovery(item, run, steps, resource_id):
    if item.status != run.status or item.status not in {"uncertain_outcome", "partial_completion"}:
        raise FixtureBlocked("Inspection recovery requires the existing uncertain or partially completed task and run.")
    if any(step.kind == "action" and not str((step.input or {}).get("title", "")).startswith("[script:") for step in steps):
        raise FixtureBlocked("Recovery refuses unscripted action history.")
    current = int(((item.payload or {}).get("runtime") or {}).get("currentStep", 0))
    if item.status == "partial_completion":
        failed = [index for index, step in enumerate(steps) if step.status == "failed"]
        if len(failed) != 1:
            raise FixtureBlocked("Partial recovery requires exactly one failed inspection.")
        target = failed[0]
        if current <= target or any(step.status != "completed" for step in steps[:target]) or any(
                step.kind != "finish" or step.status != "completed" for step in steps[target + 1:]):
            raise FixtureBlocked("Later actions or incomplete earlier work prevent inspection recovery.")
        failure = ((steps[target].output or {}).get("data") or {}).get("integrationFailure") or {}
        if failure.get("executed") is not False:
            raise FixtureBlocked("Failed inspection execution certainty was not recorded.")
        current = target
    if current < 0 or current >= len(steps):
        raise FixtureBlocked("No pending inspection exists at the saved step.")
    step = steps[current]
    spec = step.input or {}
    operation = spec.get("operation")
    allowed_status = {"failed"} if item.status == "partial_completion" else {"pending", "processing"}
    if (step.kind != "action" or step.status not in allowed_status
            or spec.get("provider") != "github" or operation not in INSPECTIONS
            or spec.get("scope") != "github." + operation
            or spec.get("resourceId") != resource_id):
        raise FixtureBlocked("The pending action is not an eligible inspection; uncertain mutations cannot resume here.")
    return current, spec


async def resume_inspection(session, item, runtime, resource_id, coordinator):
    run = await session.scalar(select(Run).where(Run.organization_id == item.organization_id,
        Run.work_item_id == item.id).order_by(Run.created_at.desc()).limit(1))
    if not run:
        raise FixtureBlocked("The saved run was not found; recovery will not create another run.")
    steps = await runtime._load_steps(run)
    current, _ = validate_recovery(item, run, steps, resource_id)
    lease = await coordinator.acquire_lock(f"runtime:{item.id}:{current}", ttl_seconds=120)
    if not lease:
        raise FixtureBlocked("Another runtime owns the saved inspection; recovery did not start.")
    try:
        # Lock and refresh after obtaining ownership; refuse a changed checkpoint.
        await session.refresh(item, with_for_update=True)
        await session.refresh(run, with_for_update=True)
        steps = await runtime._load_steps(run)
        checked, spec = validate_recovery(item, run, steps, resource_id)
        if checked != current:
            raise FixtureBlocked("The saved step changed during recovery.")
        key = f"runtime:{run.id}:{current}"
        unresolved = (await session.scalars(select(Action).where(Action.organization_id == item.organization_id,
            Action.run_id == run.id, Action.status == "processing"))).all()
        if any(action.idempotency_key != key or action.scope != spec["scope"]
               or action.resource_id != resource_id for action in unresolved):
            raise FixtureBlocked("Another action has an unresolved outcome; inspection recovery cannot bypass it.")
        _job, _revision, _worker, agent = await runtime._load_context(item)
        from uuid import UUID
        await IntegrationFoundation(session).authorize(organization_id=item.organization_id,
            agent_id=agent.id, work_item_id=item.id, resource_id=UUID(resource_id),
            scope=spec["scope"], payload=spec.get("actionInput", {}))
        if not await coordinator.renew_lock(lease, ttl_seconds=120):
            raise FixtureBlocked("Recovery ownership expired; the task remains paused.")
        meta = dict((item.payload or {}).get("runtime") or {})
        history = list(meta.get("inspectionRecoveries", []))
        if len(history) >= 5:
            raise FixtureBlocked("Controlled inspection recovery budget is exhausted; inspect its recorded failures.")
        history.append({"at": datetime.now(UTC).isoformat(), "step": current,
            "scope": spec["scope"], "previousReason": meta.get("waitingReason"),
            "previousCertainty": meta.get("executionCertainty"),
            "previousStatus": item.status, "previousInspectionOutput": steps[current].output if item.status == "partial_completion" else None,
            "previousResultId": meta.get("resultId"), "previousResultSummary": meta.get("resultSummary"),
            "previousRunSummary": getattr(run, "result_summary", None)})
        if item.status == "partial_completion":
            steps[current].status = "pending"
            steps[current].output = {}
            # Preserve the old result and finish record; a resumed outcome gets its own result.
            for name in ("resultId", "resultSummary", "resultStatus", "completedAt", "executionOutcome"):
                meta.pop(name, None)
            meta.update(currentStep=current, providerRetryCount=0, providerRetryAt=None,
                        verificationRecoveryStep=current)
            run.result_summary = None
        meta.update(queueState="executing", inspectionRecoveries=history,
            waitingReason="Explicit scripted inspection recovery requested.", executionCertainty="not_executed")
        item.payload = {**item.payload, "runtime": meta}
        item.payload.pop("completedAt", None)
        item.status, run.status = "running", "running"
        await session.commit()
        return current
    finally:
        await coordinator.release_lock(lease)
