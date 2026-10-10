"""Read-only, conversation-scoped progress; never expose prompts or runtime logs."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import suppress
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import cast, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.orm import load_only

from app.infrastructure.database.models import (
    CodingCommand,
    CodingSession,
    ConversationCommand,
    PlannerAttempt,
    PlannerDecision,
    Run,
    RunStep,
    WorkItem,
)

TERMINAL = {"completed", "failed", "cancelled", "partial_completion", "policy_denied"}


def activity_step_query(run_ids):
    """Project presentation evidence in Postgres; never transfer file bodies to pollers."""
    data = RunStep.output["data"]
    output = cast(func.coalesce(
        func.nullif(data["output"], literal_column("'null'::jsonb")), data,
    ), JSONB)
    return select(
        RunStep.id, RunStep.run_id, RunStep.step_index, RunStep.kind,
        RunStep.status, RunStep.updated_at,
        func.jsonb_build_object(
            "scope", RunStep.input["scope"], "resourceId", RunStep.input["resourceId"],
            "actionInput", func.jsonb_build_object("path", RunStep.input["actionInput"]["path"]),
        ).label("input"),
        func.jsonb_build_object("data", func.jsonb_build_object(
            "executionCertainty", data["executionCertainty"],
            "verification", func.jsonb_build_object("verified", data["verification"]["verified"]),
            "externalReferences", data["externalReferences"],
            "output", func.jsonb_build_object(
                "path", output["path"],
                "changes", func.coalesce(
                    func.jsonb_path_query_array(output, cast('$.changes[*].path', JSONPATH)),
                    literal_column("'[]'::jsonb"),
                ),
            ),
        )).label("output"),
    ).where(RunStep.run_id.in_(run_ids)).order_by(RunStep.step_index)


async def activity_rows(session, statement):
    # Detached projections cannot accidentally alter runtime checkpoints in this session.
    return [SimpleNamespace(**row) for row in (await session.execute(statement)).mappings().all()]


def activity_item_query(organization_id, thread_id):
    runtime_keys = ("currentStep", "lastAIErrorCategory", "plannerExhaustion", "queueState",
        "plannerPhase", "plannerAttemptCount", "plannerPhaseStartedAt", "aiRetryAt", "providerRetryAt")
    runtime = func.jsonb_strip_nulls(func.jsonb_build_object(*[
        value for key in runtime_keys for value in (key, WorkItem.payload["runtime"][key])
    ]))
    return select(WorkItem.id, WorkItem.status, WorkItem.created_at, WorkItem.updated_at,
        func.jsonb_build_object("runtime", runtime,
            "integrationOrigin", WorkItem.payload["integrationOrigin"],
            "conversationOrigin", WorkItem.payload["conversationOrigin"]).label("payload"),
    ).where(WorkItem.organization_id == organization_id, or_(
        WorkItem.payload["integrationOrigin"]["threadId"].astext == str(thread_id),
        WorkItem.payload["conversationOrigin"]["threadId"].astext == str(thread_id),
    )).order_by(WorkItem.created_at.desc()).limit(100)
SECRET = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+|"
    r"nvapi-[A-Za-z0-9_-]+|Bearer\s+\S+|"
    r"(?:access_token|refresh_token|api_key|password|secret|token)[\"']?\s*[=:]\s*[\"']?[^\s,;]+)",
    re.IGNORECASE,
)


def safe_text(value: object, limit: int = 2000) -> str:
    text = str(value or "")
    text = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        "[REDACTED]",
        text,
        flags=re.DOTALL,
    )
    text = SECRET.sub("[REDACTED]", text)
    text = re.sub(
        r"(https?://|postgres(?:ql)?(?:\+asyncpg)?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text
    )
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    return text[-limit:]


def action_phase(scope: str, inputs: dict | None = None) -> tuple[str, str]:
    # Presentation only: this does not choose tools, alter authority or plan a workflow.
    if scope == "github.repository.contents.read" and inputs and inputs.get("path"):
        return "executing", "Reading repository path: " + safe_text(inputs["path"], 240)
    labels = {
        "github.repository.metadata.read": "Reading repository metadata",
        "github.repository.branch.read": "Reading branch revision",
        "github.repository.tree.read": "Reading repository tree",
        "github.repository.contents.read": "Reading repository files",
        "github.repository.workspace.open": "Opening coding workspace",
        "github.repository.workspace.read": "Reading workspace files",
        "github.repository.workspace.search": "Searching workspace files",
        "github.repository.workspace.diff": "Inspecting workspace diff",
        "github.repository.branch.create": "Creating task branch",
        "github.repository.commit.create": "Publishing task commit",
        "github.repository.pull_request.create": "Opening pull request",
    }
    if scope in labels:
        phase = "publishing" if scope.endswith(("branch.create", "commit.create", "pull_request.create")) else "executing"
        return phase, labels[scope]
    if ".command." in scope:
        return "testing", "Running or checking a command"
    if any(part in scope for part in (".commit.", ".pull_request.create", ".release.", ".push")):
        return "publishing", "Publishing authorized changes"
    if any(part in scope for part in (".file.write", ".file.edit", ".patch", ".workspace.edit")):
        return "executing", "Preparing changes"
    if any(part in scope for part in (".read", ".list", ".search", ".tree.")):
        return "executing", "Reading task information"
    return "executing", "Performing the selected action"


def activity_for(item: Any, run: Any, steps: list[Any], now: datetime) -> dict[str, Any]:
    meta = dict((item.payload or {}).get("runtime") or {})
    current = int(meta.get("currentStep", 0))
    pending = next((s for s in steps if s.step_index == current and s.status != "completed"), None)
    completed = [s for s in steps if s.kind == "action" and s.status == "completed"]
    status = item.status
    if status not in TERMINAL and run and getattr(run, "status", None) in TERMINAL:
        status = run.status
    phase, detail = "preparing", "Preparing your accepted task"
    if status in TERMINAL:
        phase = "completed" if status == "completed" else "attention"
        detail = {
            "completed": "Task finished. See the result in this conversation.",
            "failed": "Work stopped. Saved steps remain available.",
            "partial_completion": "Some work completed; the remaining work needs attention.",
            "policy_denied": "This action needs different authorization.",
            "cancelled": "Task cancelled. Saved steps remain available.",
        }[status]
    elif status.startswith("waiting") or status == "uncertain_outcome":
        phase, detail = (
            "recovery",
            {
                "waiting_approval": "Waiting for your approval",
                "waiting_reconnect": "Reconnect the account to continue",
                "waiting_ai": "Planning is paused; completed work remains saved",
                "waiting_configuration": "Setup needs attention before work can continue",
                "uncertain_outcome": "Checking an uncertain outcome before another action",
            }.get(status, "Waiting to continue; completed work remains saved"),
        )
        if (
            meta.get("lastAIErrorCategory") == "timeout"
            and (meta.get("plannerExhaustion") or {}).get("reason") == "deadline"
        ):
            detail = "The planning deadline expired. Your task and completed work are saved."
        elif meta.get("lastAIErrorCategory"):
            detail = {
                "quota_exhausted": "The planning usage allowance is exhausted. Your task is saved.",
                "rate_limited": "Waiting for planning capacity. Your task is saved.",
                "content_rejected": "The planning service declined this request. Work is paused.",
                "authentication_failed": "The planning connection needs attention. Your task is saved.",
                "invalid_provider_response": "The planning response could not be validated. No new action was executed.",
            }.get(meta["lastAIErrorCategory"], detail)
    elif meta.get("queueState") in {"retrying", "replanning"}:
        phase, detail = "recovery", "Recovering the pending step; completed work remains saved"
    elif meta.get("plannerPhase") == "awaiting_ai" and meta.get("queueState") == "planning":
        phase, detail = "awaiting_planner", "Waiting for the planner response"
        if int(meta.get("plannerAttemptCount", 0)) > 1:
            detail = "Trying another planning service; completed work remains saved"
    elif pending and pending.kind == "action":
        phase, detail = action_phase(str((pending.input or {}).get("scope", "")), (pending.input or {}).get("actionInput"))
        if meta.get("queueState") != "executing":
            detail = "Selected next action: " + detail.lower()
    timestamps: list[datetime] = [item.updated_at, *[s.updated_at for s in steps]]
    if run and getattr(run, "updated_at", None):
        timestamps.append(run.updated_at)
    updated = max(timestamps)
    started = run.created_at if run else item.created_at
    return {
        "id": str(item.id),
        "requestMessageId": (
            (item.payload or {}).get("integrationOrigin")
            or (item.payload or {}).get("conversationOrigin")
            or {}
        ).get("messageId"),
        "status": status,
        "phase": phase,
        "detail": detail,
        "startedAt": started.isoformat(),
        "updatedAt": updated.isoformat(),
        "elapsedSeconds": max(
            0, int(((updated if status in TERMINAL or status.startswith("waiting") and meta.get("queueState") != "retrying" else now) - started).total_seconds())
        ),
        "phaseStartedAt": meta.get("plannerPhaseStartedAt")
        if phase == "awaiting_planner"
        else None,
        "retryAt": meta.get("aiRetryAt") or meta.get("providerRetryAt"),
        "retryScheduled": meta.get("queueState") == "retrying",
        "responseState": "awaiting" if phase == "awaiting_planner" else None,
        "completedActions": len(completed),
        "preservedWork": bool(completed),
        "events": [
            {
                "id": str(s.id),
                "state": s.status,
                "label": action_phase(str((s.input or {}).get("scope", "")), (s.input or {}).get("actionInput"))[1],
                "verified": ((s.output or {}).get("data") or {}).get("executionCertainty")
                == "verified",
                "at": s.updated_at.isoformat(),
            }
            for s in steps
            if s.kind == "action"
        ][-20:],
        "commands": [],
        "changedFiles": [],
        "links": [],
    }


async def conversation_activity(session, organization_id: UUID, thread_id: UUID, principal) -> dict:
    from app.application.services.integration_foundation import IntegrationFoundation
    from app.bootstrap.settings import settings
    from app.infrastructure.database.models import IntegrationResource

    now = datetime.now(UTC)
    items = await activity_rows(session, activity_item_query(organization_id, thread_id))
    ids = [i.id for i in items]
    runs = (
        list(
            (
                await session.scalars(
                    select(Run)
                    .options(load_only(Run.id, Run.work_item_id, Run.status,
                        Run.created_at, Run.updated_at, raiseload=True))
                    .where(
                        Run.organization_id == organization_id,
                        Run.work_item_id.in_(ids),
                    )
                    .distinct(Run.work_item_id)
                    .order_by(Run.work_item_id, Run.created_at.desc(), Run.id.desc())
                )
            ).all()
        )
        if ids
        else []
    )
    run_ids = [r.id for r in runs]
    steps = await activity_rows(session, activity_step_query(run_ids)) if run_ids else []
    decisions = (
        list(
            (
                await session.scalars(
                    select(PlannerDecision)
                    .options(load_only(PlannerDecision.id, PlannerDecision.work_item_id,
                        PlannerDecision.run_id, PlannerDecision.deadline, raiseload=True))
                    .where(
                        PlannerDecision.organization_id == organization_id,
                        PlannerDecision.work_item_id.in_(ids),
                    )
                    .distinct(PlannerDecision.run_id)
                    .order_by(PlannerDecision.run_id, PlannerDecision.created_at.desc(), PlannerDecision.id.desc())
                )
            ).all()
        )
        if ids and settings.smart_planner_enabled
        else []
    )
    decision_ids = [d.id for d in decisions]
    attempts = (
        list(
            (
                await session.scalars(
                    select(PlannerAttempt)
                    .options(load_only(PlannerAttempt.id, PlannerAttempt.decision_id,
                        PlannerAttempt.transport_success, PlannerAttempt.call_count, raiseload=True))
                    .where(
                        PlannerAttempt.organization_id == organization_id,
                        PlannerAttempt.decision_id.in_(decision_ids),
                    )
                    .distinct(PlannerAttempt.decision_id)
                    .order_by(PlannerAttempt.decision_id, PlannerAttempt.created_at.desc(), PlannerAttempt.id.desc())
                )
            ).all()
        )
        if decision_ids
        else []
    )
    result = []
    resource_access: dict[str, bool] = {}

    async def may_read_evidence(resource_id: str) -> bool:
        if "jobs.read" not in principal.permissions:
            return False
        if resource_id not in resource_access:
            try:
                resource = await session.get(IntegrationResource, UUID(resource_id))
            except ValueError:
                resource = None
            resource_access[resource_id] = bool(
                resource
                and resource.organization_id == organization_id
                and await IntegrationFoundation(session).can_use(
                    resource.connection_id, organization_id, principal.user_id
                )
            )
        return resource_access[resource_id]

    for item in items:
        run = next((r for r in runs if r.work_item_id == item.id), None)
        task_steps = [s for s in steps if run and s.run_id == run.id]
        public = activity_for(item, run, task_steps, now)
        decision = next(
            (d for d in decisions if d.work_item_id == item.id and run and d.run_id == run.id), None
        )
        attempt = next((a for a in attempts if decision and a.decision_id == decision.id), None)
        public["deadlineAt"] = decision.deadline.isoformat() if decision else None
        if public["phase"] == "awaiting_planner":
            public["responseState"] = (
                "received" if attempt and attempt.transport_success else "awaiting"
            )
            if attempt:
                from app.infrastructure.redis.coordination import RedisCoordinator

                coordinator = None
                try:
                    coordinator = RedisCoordinator.from_settings()
                    cached = await asyncio.wait_for(
                        coordinator.cache_get(f"planner:progress:{organization_id}:{attempt.id}"),
                        timeout=2,
                    )
                    signal = json.loads(cached) if cached else {}
                    if signal.get("call") == attempt.call_count:
                        public["responseState"] = "receiving"
                        public["responseReceivedAt"] = signal.get("receivedAt")
                        public["responseBytes"] = signal.get("receivedBytes")
                except Exception as exc:  # noqa: BLE001 - live transport diagnostics are optional
                    logging.getLogger("uvicorn.error").warning(
                        "Task activity transport unavailable work_item=%s error_type=%s",
                        item.id,
                        type(exc).__name__,
                    )
                finally:
                    if coordinator:
                        with suppress(Exception):
                            await asyncio.wait_for(coordinator.close(), timeout=2)
            if decision and decision.deadline <= now:
                public["phase"], public["detail"] = (
                    "recovery",
                    "Planning deadline elapsed; waiting for recovery to record the outcome. Saved work remains available.",
                )
        # Only selected evidence fields; never action payloads, prompts, file contents or raw errors.
        for step in task_steps:
            if not await may_read_evidence(str((step.input or {}).get("resourceId", ""))):
                continue
            data = (step.output or {}).get("data") or {}
            if not isinstance(data, dict):
                continue
            output = data.get("output", data)
            output = output if isinstance(output, dict) else {}
            changes = output.get("changes")
            changes = changes if isinstance(changes, list) else []
            public["changedFiles"].extend(
                safe_text(c if isinstance(c, str) else c.get("path"), 240)
                for c in changes
                if isinstance(c, str) or isinstance(c, dict) and c.get("path")
            )
            if (
                step.status == "completed"
                and str((step.input or {}).get("scope", "")).endswith("workspace.edit")
                and output.get("path")
            ):
                public["changedFiles"].append(safe_text(output["path"], 240))
            if step.status == "completed":
                for reference in data.get("externalReferences") or []:
                    url = reference.get("url") if isinstance(reference, dict) else None
                    parsed = urlsplit(url) if isinstance(url, str) else None
                    if (
                        parsed
                        and parsed.scheme == "https"
                        and parsed.hostname
                        and not parsed.username
                        and not parsed.password
                        and not parsed.query
                    ):
                        verified = (data.get("verification") or {}).get("verified") is True
                        public["links"].append(
                            {
                                "title": "Open verified object"
                                if verified
                                else "Open object (outcome unverified)",
                                "url": url,
                            }
                        )
        public["links"] = list({link["url"]: link for link in public["links"]}.values())[:20]
        public["changedFiles"] = list(dict.fromkeys(public["changedFiles"]))[:50]
        if settings.coding_execution_enabled and "jobs.read" in principal.permissions:
            workspaces = list(
                (
                    await session.scalars(
                        select(CodingSession).where(
                            CodingSession.organization_id == organization_id,
                            CodingSession.work_item_id == item.id,
                        ).options(load_only(CodingSession.id, CodingSession.resource_id, CodingSession.status,
                                            CodingSession.evidence, raiseload=True))
                    )
                ).all()
            )
            for workspace in workspaces:
                # Reuse this request's exact resource-access result; execution
                # still performs its own fresh authorization before dispatch.
                if not await may_read_evidence(str(workspace.resource_id)):
                    continue
                initialization = (getattr(workspace, "evidence", {}) or {}).get("initialization", {})
                if getattr(workspace, "status", None) in {"creating", "creating_uncertain"} and initialization:
                    stage = initialization.get("stage")
                    labels = {"snapshot": "Downloading repository source", "validation": "Validating repository source",
                        "sandbox_creation": "Preparing workspace", "bundle_transfer": "Uploading source",
                        "verification": "Verifying workspace source"}
                    public["phase"] = "executing"
                    public["detail"] = labels.get(stage, "Preparing workspace")
                    if stage == "bundle_transfer":
                        public["detail"] += f" ({len(initialization.get('confirmedBundles', []))}/{len(initialization.get('bundles', []))} bundles)"
                    public["deadlineAt"] = initialization.get("deadline")
                commands = list(
                    (
                        await session.scalars(
                            select(CodingCommand)
                            .where(
                                CodingCommand.organization_id == organization_id,
                                CodingCommand.session_id == workspace.id,
                            )
                            .order_by(CodingCommand.created_at.desc())
                            .limit(5)
                        )
                    ).all()
                )
                public["commands"].extend(
                    {
                        "id": str(c.id),
                        "command": safe_text(c.command, 300),
                        "status": c.status,
                        "exitCode": (c.output or {}).get("exitCode"),
                        "stdout": safe_text((c.output or {}).get("stdout")),
                        "stderr": safe_text((c.output or {}).get("stderr")),
                        "observedAt": c.updated_at.isoformat(),
                    }
                    for c in commands
                )
                if (
                    any(c.status in {"starting", "running"} for c in commands)
                    and public["status"] not in TERMINAL
                ):
                    public["phase"], public["detail"] = (
                        "testing",
                        "Last observed command is running; output below reflects saved observations",
                    )
        public["_activityLease"] = f"runtime:{item.id}:{int(((item.payload or {}).get('runtime') or {}).get('currentStep', 0))}"
        result.append(public)
    # Accepted setup appears immediately, before the work item exists.
    commands = await activity_rows(session,
                select(ConversationCommand.id, ConversationCommand.source_message_id,
                    ConversationCommand.status, ConversationCommand.created_at,
                    ConversationCommand.updated_at,
                    func.jsonb_strip_nulls(func.jsonb_build_object(
                        "activePreparationDecisionId", ConversationCommand.payload["activePreparationDecisionId"],
                    )).label("payload"))
                .where(
                    ConversationCommand.organization_id == organization_id,
                    ConversationCommand.thread_id == thread_id,
                    ConversationCommand.family == "integration.execute",
                    ConversationCommand.target_type.is_(None),
                    ConversationCommand.status.in_(
                        [
                            "accepted",
                            "waiting_ai",
                            "waiting_integration",
                            "clarification_required",
                            "policy_denied",
                        ]
                    ),
                )
                .order_by(ConversationCommand.created_at.desc())
                .limit(100)
    )
    preparation_decisions = list((await session.scalars(select(PlannerDecision).options(load_only(
        PlannerDecision.id, PlannerDecision.command_id, PlannerDecision.status,
        PlannerDecision.deadline, PlannerDecision.retry_at, PlannerDecision.created_at,
        PlannerDecision.updated_at, raiseload=True,
    )).where(
        PlannerDecision.organization_id == organization_id,
        PlannerDecision.command_id.in_([c.id for c in commands]),
        PlannerDecision.purpose == "integration_preparation",
    ).distinct(PlannerDecision.command_id).order_by(PlannerDecision.command_id,
        PlannerDecision.step_index.desc()))).all()) if commands and settings.smart_planner_enabled else []
    for command in commands:
        preparation = next((d for d in preparation_decisions if d.command_id == command.id), None)
        retrying = bool(preparation and preparation.status == "pending" and preparation.retry_at
                        and preparation.deadline > now)
        result.append(
            {
                "id": str(command.id),
                "requestMessageId": str(command.source_message_id),
                "status": command.status,
                "phase": "preparing" if command.status == "accepted" else "recovery",
                "detail": "Preparing your accepted task"
                if command.status == "accepted"
                else "Task preparation needs attention; your request is saved",
                "startedAt": command.created_at.isoformat(),
                "updatedAt": command.updated_at.isoformat(),
                "elapsedSeconds": int(((now if command.status == "accepted" else command.updated_at) - command.created_at).total_seconds()),
                "completedActions": 0,
                "events": [],
                "commands": [],
                "changedFiles": [],
                "links": [],
                "preservedWork": False,
                "deadlineAt": preparation.deadline.isoformat() if preparation else None,
                "retryAt": preparation.retry_at.isoformat() if preparation and preparation.retry_at else None,
                "retryScheduled": retrying,
                "_activityLease": f"preparation:{organization_id}:{command.id}:"
                    f"{command.payload.get('activePreparationDecisionId', 'initial')}",
            }
        )
        if preparation:
            started = preparation.created_at or command.created_at
            result[-1]["startedAt"] = started.isoformat()
            result[-1]["updatedAt"] = max(command.updated_at, preparation.updated_at).isoformat()
            result[-1]["elapsedSeconds"] = int((now - started).total_seconds())
            if preparation.status == "exhausted":
                result[-1]["_activityStopped"] = True
                result[-1]["phase"], result[-1]["detail"] = "recovery", "Task preparation stopped; your request is saved."
            elif preparation.deadline <= now:
                result[-1]["phase"], result[-1]["detail"] = "recovery", "Task preparation reached its time limit; your request is saved."
            elif preparation.status == "pending":
                # This accepted task has durable preparation/recovery pending; a
                # prior waiting_ai receipt must not hide its current active process.
                result[-1]["status"] = "accepted"
                result[-1]["phase"] = "recovery" if retrying else "awaiting_planner"
                result[-1]["detail"] = "Waiting to retry task preparation" if retrying else "Choosing the next steps for your task"
    await observe_execution_ownership(result, now)
    return {"observedAt": now.isoformat(), "tasks": result}


async def observe_execution_ownership(tasks: list[dict], now: datetime) -> None:
    """Presentation only: stale database labels cannot prove a worker is executing."""
    from app.infrastructure.redis.coordination import RedisCoordinator

    keys = list(dict.fromkeys(task["_activityLease"] for task in tasks
        if task["status"] not in TERMINAL and task["_activityLease"]))
    ownership = None
    coordinator = None
    if keys:
        try:
            coordinator = RedisCoordinator.from_settings()
            ownership = await asyncio.wait_for(coordinator.active_locks(keys), timeout=2)
        except Exception as error:  # noqa: BLE001 - reporting must not alter execution
            logging.getLogger("uvicorn.error").warning(
                "Task activity ownership unavailable error_type=%s", type(error).__name__)
        finally:
            if coordinator:
                with suppress(Exception):
                    await asyncio.wait_for(coordinator.close(), timeout=2)
    for task in tasks:
        key = task.pop("_activityLease", None)
        deadline = activity_timestamp(task.get("deadlineAt"))
        retry_at = activity_timestamp(task.get("retryAt"))
        expired = bool(deadline and deadline <= now
                       and task["phase"] in {"preparing", "awaiting_planner", "recovery"})
        retry = bool(task.get("retryScheduled") and retry_at and retry_at > now and not expired)
        blocked = task.pop("_activityStopped", False) or task["status"] in TERMINAL or task["status"] in {
            "paused", "uncertain_outcome", "waiting_approval", "waiting_reconnect",
            "waiting_configuration", "waiting_integration", "clarification_required"}
        running = bool(not blocked and not expired and ownership and ownership.get(key))
        task["isActive"] = not blocked and (running or retry)
        task["retryScheduled"] = not blocked and retry
        task["executionState"] = ("running" if running else "retry_scheduled" if task["retryScheduled"]
                                  else "unknown" if ownership is None and not blocked and not expired
                                  else "inactive")
        if not task["isActive"]:
            task["elapsedSeconds"] = max(0, int((datetime.fromisoformat(task["updatedAt"])
                                                 - datetime.fromisoformat(task["startedAt"])).total_seconds()))
            if not blocked:
                task["phase"] = "attention"
                task["detail"] = ("The planning time limit was reached. Your task and saved work remain available."
                    if expired else "Current execution could not be confirmed. Last saved activity is shown."
                    if ownership is None else "No worker is currently executing this task. Your request and saved work remain available.")


def activity_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value)
        return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
    except ValueError:
        return None
