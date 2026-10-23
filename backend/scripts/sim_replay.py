"""Replay a simulated scenario without a person answering the model.

Sends the scenario's first message through the conversation service, answers each model request
from the recorded transcript (tests/fixtures/sim/transcripts/<scenario>.jsonl: the Nth request
of a schema gets the Nth recorded answer of that schema), arms the scenario's faults, and waits
for the task to settle. A request with no recorded answer is reported and left for a person (or
`python scripts/ai_simulator.py`). Steps only a person takes (approving, pressing an outcome
button, opening a GitHub issue) are announced; `--approve` approves the task's approvals.
`--resume WORK_ITEM` attaches to a task already under way (after the replay itself was stopped)
and continues with the recorded answers its earlier requests did not use. `--faults JSON` replaces
the scenario's GitHub faults (see run_simulation.install_fault_injection), and `--cut-planning N`
first answers the Nth planning request as a dropped stream (no finish reason), giving the
recorded answer to the request that follows.

Needs the simulation stack running (ai-simulator, agentgate-sim, the local QStash). Scenarios
that write to GitHub need `--allow-writes`.

    python scripts/sim_replay.py s1_open_issues
    python scripts/sim_replay.py s2_create_issue --allow-writes --approve
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = BACKEND / "tests" / "fixtures" / "sim"
SIM = BACKEND / ".sim"
WRITES = ("create", "update", "merge", "ready", "commit", "edit", "comment")
SETTLED = {"completed", "failed", "cancelled", "partial_completion", "uncertain_outcome", "waiting_ai",
           "policy_denied", "waiting_reconnect"}


def recorded_answers(scenario: str) -> dict[str, list[dict[str, Any]]]:
    queues: dict[str, list[dict[str, Any]]] = {}
    path = FIXTURES / "transcripts" / f"{scenario}.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            row = json.loads(line)
            queues.setdefault(row["schema_name"], []).append(row["answer"])
    return queues


def writes_github(scenario: dict[str, Any]) -> bool:
    queues = recorded_answers(scenario["id"])
    return any(isinstance(answer.get("content"), dict) and answer["content"].get("decision") == "act"
               and any(word in str(answer["content"].get("scope", "")) for word in WRITES)
               for answer in queues.get("audoryn_next_action", []))


# The planner prompt gives the task's own branch prefix (Phase 2, F7). A live model uses it; a
# recorded answer still carries the original task's, so it is swapped for the one the prompt gives.
TASK_BRANCH = re.compile(r"branches start with ((?:audoryn|codex)/[0-9a-f]{8}/)")
RECORDED_BRANCH = re.compile(r"(?:audoryn|codex)/[0-9a-f]{8}/")


# Planning requests carry their task's work item; one from another task (a scheduled job that wakes
# during the replay) is left for a person instead of getting this scenario's answers.
WORK_ITEM = re.compile(r'"workItemId": "([0-9a-f-]{36})"')


def other_task(record: dict[str, Any], focus: dict[str, str | None]) -> bool | None:
    """True for another task's planning request, None while this scenario's task is not known yet."""
    if record["schema_name"] != "audoryn_next_action":
        return False
    found = WORK_ITEM.search(" ".join(str(message.get("content", "")) for message in record.get("messages", [])))
    if found is None:
        return False
    return None if focus["item"] is None else found.group(1) != focus["item"]


def adapted(answer: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """A recorded answer as a live model reading this request's prompt would give it."""
    prefix = TASK_BRANCH.search(json.dumps(record.get("messages", ""), ensure_ascii=False))
    return json.loads(RECORDED_BRANCH.sub(prefix.group(1), json.dumps(answer))) if prefix else answer


async def answer_from_recording(queues: dict[str, list[dict[str, Any]]], stop: asyncio.Event,
                                focus: dict[str, str | None], cut_planning: int = 0) -> None:
    """Answer each new pending request of this scenario with the next recorded answer for its schema
    (the `cut_planning`th planning request first gets that answer as a dropped stream)."""
    seen: set[str] = set()
    planning = 0
    while not stop.is_set():
        for path in sorted((SIM / "pending").glob("*.json")):
            if path.name in seen:
                continue
            seen.add(path.name)
            record = json.loads(path.read_text(encoding="utf-8"))
            elsewhere = other_task(record, focus)
            if elsewhere is None:
                seen.discard(path.name)  # ask again once the scenario's task is known
                continue
            if elsewhere:
                print(f"[replay] request {path.stem} belongs to another task; left for a person", flush=True)
                continue
            queue = queues.get(record["schema_name"]) or []
            if not queue:
                print(f"[replay] request {path.stem} ({record['schema_name']}) has no recorded answer; "
                      "answer it with scripts/ai_simulator.py", flush=True)
                continue
            target = SIM / "answers" / path.name
            planning += record["schema_name"] == "audoryn_next_action"
            if record["schema_name"] == "audoryn_next_action" and planning == cut_planning:
                target.write_text(json.dumps({**adapted(queue[0], record), "finish_reason": "none"}), encoding="utf-8")
                print(f"[replay] answered {path.stem} as a dropped stream (no finish reason)", flush=True)
                continue
            target.write_text(json.dumps(adapted(queue.pop(0), record)), encoding="utf-8")
            print(f"[replay] answered {path.stem} ({record['schema_name']})", flush=True)
        await asyncio.sleep(1)


async def member_principal(session: Any, organization_id: Any, user_id: Any) -> Any:
    """The person's identity in the organisation, as the API would build it after sign-in."""
    from sqlalchemy import select

    from audoryn_shared.domain.identity.permissions import permissions_for_role
    from audoryn_shared.domain.identity.principals import HumanPrincipal
    from audoryn_shared.infrastructure.database.models import OrganizationMembership

    membership = await session.scalar(select(OrganizationMembership).where(
        OrganizationMembership.organization_id == organization_id, OrganizationMembership.user_id == user_id))
    if membership is None:
        raise SystemExit("That person is not a member of the organisation.")
    return HumanPrincipal(user_id=membership.user_id, organization_id=membership.organization_id,
                          membership_id=membership.id, role=membership.role,
                          permissions=permissions_for_role(membership.role))


async def send_message(thread_id: str, content: str) -> None:
    from uuid import UUID

    from app.application.services.conversations import ConversationService
    from audoryn_shared.infrastructure.database.models import ConversationThread
    from audoryn_shared.infrastructure.database.session import session_factory

    async with session_factory() as session:
        thread = await session.get(ConversationThread, UUID(thread_id))
        if thread is None:
            raise SystemExit(f"Thread {thread_id} is not in the simulation database.")
        principal = await member_principal(session, thread.organization_id, thread.created_by)
        _, reply, receipt = await ConversationService(session).send_message(
            organization_id=thread.organization_id, principal=principal, thread_id=thread.id, content=content)
        print(f"[replay] sent; reply: {reply.content[:200]!r} ({receipt.status})", flush=True)


async def newest_item(session: Any, since: Any) -> Any:
    from sqlalchemy import select

    from audoryn_shared.infrastructure.database.models import WorkItem

    return await session.scalar(select(WorkItem).where(WorkItem.created_at >= since)
                                .order_by(WorkItem.created_at.desc()).limit(1))


async def report_settled(session: Any, item: Any) -> None:
    from sqlalchemy import select

    from audoryn_shared.infrastructure.database.models import Run

    summary = await session.scalar(select(Run.result_summary).where(Run.work_item_id == item.id))
    print(f"[replay] {str(item.id)[:8]} {item.status}: {summary or ''}"[:400], flush=True)


async def handle_approvals(session: Any, item: Any, approve: bool, announced: set[str]) -> None:
    """Approve the task's new pending approvals (with --approve), or say they are waiting."""
    from sqlalchemy import select

    from audoryn_shared.api.governance_routes import _decide_approval
    from audoryn_shared.infrastructure.database.models import Action, Approval, Run

    pending = [approval for approval in (await session.scalars(
        select(Approval).join(Action, Action.id == Approval.action_id).join(Run, Run.id == Action.run_id)
        .where(Run.work_item_id == item.id, Approval.status == "pending"))).all()
        if str(approval.id) not in announced]
    if not pending:
        return
    announced.update(str(approval.id) for approval in pending)
    if not approve:
        print(f"[replay] {len(pending)} approval(s) waiting: approve in the workspace, or rerun with --approve",
              flush=True)
        return
    principal = await member_principal(session, item.organization_id, (item.payload or {}).get("requestedBy"))
    for approval in pending:
        await _decide_approval(session, item.organization_id, principal, approval.id,
                               {"decision": "approve", "note": "Approved by sim_replay."})
        print(f"[replay] approved {str(approval.id)[:8]}", flush=True)


async def watch(started: float, approve: bool, timeout: float, focus: dict[str, str | None]) -> str | None:
    """Wait for the task (the newest work item created after `started`, or the one being resumed) to
    settle, polling every 5 s. A dropped database connection only skips a poll."""
    from datetime import UTC, datetime
    from uuid import UUID

    from audoryn_shared.infrastructure.database.models import WorkItem
    from audoryn_shared.infrastructure.database.session import session_factory

    since = datetime.fromtimestamp(started, UTC)
    resumed = focus["item"]
    deadline = time.time() + timeout
    announced: set[str] = set()
    while time.time() < deadline:
        try:
            async with session_factory() as session:
                item = await (session.get(WorkItem, UUID(resumed)) if resumed else newest_item(session, since))
                if item is not None:
                    focus["item"] = str(item.id)
                # A planning retry already scheduled is still under way.
                retrying = item is not None and item.status == "waiting_ai" and item.scheduled_at > datetime.now(UTC)
                if item is not None and item.status in SETTLED and not retrying:
                    await report_settled(session, item)
                    return item.status
                if item is not None and item.status == "waiting_approval":
                    await handle_approvals(session, item, approve, announced)
        except (OSError, SQLAlchemyError) as error:
            print(f"[replay] database unavailable for this poll ({type(error).__name__}); trying again", flush=True)
        await asyncio.sleep(5)
    print("[replay] timed out waiting for the task to settle")
    return None


def answered_for(work_item: str) -> int:
    """Planning requests of this task already answered (the simulator's log)."""
    records = (json.loads(path.read_text(encoding="utf-8"))["request"] for path in (SIM / "log").glob("*.json"))
    return sum(1 for record in records if record["schema_name"] == "audoryn_next_action"
               and other_task(record, {"item": work_item}) is False
               and WORK_ITEM.search(" ".join(str(message.get("content", "")) for message in record["messages"])))


async def replay(scenario: dict[str, Any], thread_id: str, approve: bool, timeout: float,
                 resume: str | None = None, faults_override: list | None = None, cut_planning: int = 0) -> str | None:
    faults = SIM / "faults.json"
    rules = scenario.get("faults", []) if faults_override is None else faults_override
    faults.write_text(json.dumps({"rules": rules}), encoding="utf-8")
    if scenario.get("humanSteps"):
        print("[replay] this scenario needs a person for: " + "; ".join(scenario["humanSteps"]), flush=True)
    stop = asyncio.Event()
    focus: dict[str, str | None] = {"item": resume}
    queues = recorded_answers(scenario["id"])
    if resume:  # the task exists: only its remaining planning answers are needed
        queues = {"audoryn_next_action": queues.get("audoryn_next_action", [])[answered_for(resume):]}
    answering = asyncio.create_task(answer_from_recording(queues, stop, focus, cut_planning))
    started = time.time()
    try:
        if not resume:
            await send_message(thread_id, scenario["messages"][0])
        return await watch(started, approve, timeout, focus)
    finally:
        stop.set()
        await answering


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario")
    parser.add_argument("--thread", default="73e3eed3-b1d3-4290-8eb3-a28b2a0bb138",
                        help="conversation thread in the simulation database")
    parser.add_argument("--allow-writes", action="store_true")
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--resume", metavar="WORK_ITEM", help="attach to this task instead of sending the message")
    parser.add_argument("--faults", type=json.loads, help="GitHub fault rules (JSON list) instead of the scenario's")
    parser.add_argument("--cut-planning", type=int, default=0, metavar="N",
                        help="answer the Nth planning request as a dropped stream first")
    args = parser.parse_args()
    catalogue = {item["id"]: item for item in json.loads((FIXTURES / "scenarios.json").read_text(encoding="utf-8"))}
    scenario = catalogue.get(args.scenario)
    if scenario is None:
        raise SystemExit("Unknown scenario. Known: " + ", ".join(catalogue))
    if writes_github(scenario) and not args.allow_writes:
        raise SystemExit(f"{args.scenario} writes to GitHub; rerun with --allow-writes.")
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))
    from scripts.run_simulation import _read_env_file, simulation_environment

    os.environ.update(simulation_environment(_read_env_file(BACKEND / ".env.sim"), 8788))
    status = asyncio.run(replay(scenario, args.thread, args.approve, args.timeout, args.resume,
                                args.faults, args.cut_planning))
    raise SystemExit(0 if status is not None else 1)


if __name__ == "__main__":
    main()
