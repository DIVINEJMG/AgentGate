"""First-run setup: new workspaces answer a few optional questions and get a starter worker,
approval rules, working hours and a welcome conversation, with nothing scheduled or run; a
getting-started checklist is worked out from what exists, in one query."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import organization_routes
from app.application.services import onboarding
from app.domain.identity.permissions import permissions_for_role
from app.domain.identity.principals import HumanPrincipal
from app.execution.providers.registry import ProviderRegistry
from app.infrastructure.database import models as m
from scripts.runtime_benchmark import BenchProvider, BenchSession, _aware_sqlite_datetimes
from tests.test_approvals_phase4 import TABLES
from tests.test_efficiency_queries import ORG, Database


class Workspace:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, onboarding_row: bool = True) -> None:
        registry = ProviderRegistry((BenchProvider(),))
        monkeypatch.setattr(onboarding, "execution_provider_registry", lambda: registry)
        self.db = Database(TABLES)
        self.human = self.db.add(m.HumanIdentity, display_name="Ada Lovelace")
        self.db.add(m.Organization, id=ORG, name="Acme", created_by=self.human.id)
        if onboarding_row:
            self.db.add(m.OrganizationOnboarding, organization_id=ORG, user_id=self.human.id, state="welcome",
                        answers={}, setup={}, checklist_dismissed=False)
        self.principal = HumanPrincipal(user_id=self.human.id, organization_id=ORG, membership_id=uuid4(),
                                        role="owner", permissions=permissions_for_role("owner"))

    def session(self) -> Any:
        return BenchSession(Session(self.db.engine, expire_on_commit=False))

    async def set_up(self, answers: dict[str, Any]) -> m.OrganizationOnboarding:
        with _aware_sqlite_datetimes():
            return await onboarding.set_up_workspace(self.session(), ORG, self.principal, answers, person="Ada")

    def rows(self, model: Any) -> list[Any]:
        with Session(self.db.engine) as session:
            return list(session.scalars(select(model)).all())


@pytest.mark.asyncio
async def test_setup_builds_a_starter_worker_and_welcome_conversation_without_running_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Workspace(monkeypatch)
    row = await workspace.set_up({"goal": "engineering", "tools": ["github", "slack"], "care": "visible",
                                  "timezone": "Africa/Lagos"})
    assert row.state == "ready"
    [worker] = workspace.rows(m.Worker)
    assert (worker.name, worker.status, worker.department) == ("Engineering Assistant", "active", "Engineering")
    assert worker.profile["workingHours"]["timezone"] == "Africa/Lagos"
    [thread] = workspace.rows(m.ConversationThread)
    [message] = workspace.rows(m.ConversationMessage)
    assert thread.worker_id == worker.id and message.role == "worker"
    assert message.content.startswith("Hi Ada! I'm Engineering Assistant, your first worker.")
    assert "Connect GitHub and Slack from Connections" in message.content
    assert "• Summarise the open issues in my repository" in message.content
    assert "Nothing runs until you ask." in message.content
    assert row.setup == {"workerId": str(worker.id), "workerName": worker.name, "policyId": None, "threadId": str(thread.id)}
    # Nothing is scheduled or queued, and the default needs no extra approval rule.
    assert workspace.rows(m.Job) == [] and workspace.rows(m.WorkItem) == [] and workspace.rows(m.Policy) == []


@pytest.mark.asyncio
async def test_setup_is_safe_to_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Workspace(monkeypatch)
    first = await workspace.set_up({"goal": "research"})
    again = await workspace.set_up({"goal": "support"})
    assert again.setup == first.setup and len(workspace.rows(m.Worker)) == 1
    assert len(workspace.rows(m.ConversationThread)) == 1


@pytest.mark.asyncio
async def test_a_retry_after_a_failure_continues_where_it_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Workspace(monkeypatch)
    original = onboarding._care_policy

    async def broken(*args: Any) -> str | None:
        raise RuntimeError("database went away")

    monkeypatch.setattr(onboarding, "_care_policy", broken)
    with pytest.raises(RuntimeError):
        await workspace.set_up({"goal": "operations", "care": "changes"})
    monkeypatch.setattr(onboarding, "_care_policy", original)
    row = await workspace.set_up({"goal": "operations", "care": "changes"})
    assert row.state == "ready" and len(workspace.rows(m.Worker)) == 1 and row.setup["policyId"]


@pytest.mark.asyncio
async def test_skipped_or_unknown_answers_use_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    assert onboarding.clean_answers({"goal": "piracy", "tools": ["github", "fax", "github"], "care": "never",
                                     "timezone": "Mars/Base"}) == {
        "goal": "general", "tools": ["github"], "care": "visible", "timezone": "UTC"}
    workspace = Workspace(monkeypatch)
    row = await workspace.set_up({})
    assert row.answers == {"goal": "general", "tools": [], "care": "visible", "timezone": "UTC"}
    assert workspace.rows(m.Worker)[0].name == "Workspace Assistant"


@pytest.mark.asyncio
@pytest.mark.parametrize(("care", "effect", "risks"), [("changes", "require_approval", []), ("risky", "allow", ["low", "medium"])])
async def test_how_careful_becomes_one_visible_approval_rule(monkeypatch: pytest.MonkeyPatch, care: str, effect: str,
                                                             risks: list[str]) -> None:
    workspace = Workspace(monkeypatch)
    row = await workspace.set_up({"care": care})
    [policy] = workspace.rows(m.Policy)
    [revision] = workspace.rows(m.PolicyRevision)
    assert row.setup["policyId"] == str(policy.id) and policy.name.endswith("(set up when the workspace was created)")
    assert (revision.effect, revision.priority) == (effect, 150)
    assert "bench.record.write" in revision.selectors["scopes"] and "bench.record.read" not in revision.selectors["scopes"]
    assert revision.selectors["risks"] == risks
    assert ("ask you first" in workspace.rows(m.ConversationMessage)[0].content) is (care == "changes")


@pytest.mark.asyncio
async def test_the_checklist_follows_what_exists_in_one_query(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Workspace(monkeypatch)
    await workspace.set_up({"goal": "engineering", "tools": ["web_research", "github"]})
    db = workspace.db
    with _aware_sqlite_datetimes():
        db.statements.clear()
        steps = await onboarding.checklist(workspace.session(), ORG, {"tools": ["web_research", "github"]})
    assert len(db.statements) == 1
    assert [step["complete"] for step in steps] == [False] * 5
    assert steps[0]["destination"] == "connections/directory/github"

    worker = workspace.rows(m.Worker)[0]
    thread = workspace.rows(m.ConversationThread)[0]
    db.add(m.Integration, organization_id=ORG, provider="github", display_name="acme", status="connected", config={})
    db.add(m.ConversationMessage, organization_id=ORG, thread_id=thread.id, role="human", content="hi",
           artifact_references=[], command_references=[], result_references=[])
    job = db.add(m.Job, organization_id=ORG, worker_id=worker.id, name="Daily", status="active", current_revision=1)
    db.add(m.JobRevision, job_id=job.id, revision=1, created_by=workspace.human.id, created_at=datetime.now(UTC),
           definition={"triggerConfig": {"schedule": {"enabled": True}}})
    item = db.add(m.WorkItem, organization_id=ORG, job_id=job.id, status="completed", correlation_id="c", payload={})
    agent = workspace.rows(m.AgentIdentity)[0]
    action = db.add(m.Action, organization_id=ORG, agent_id=agent.id, status="executed", resource_id="r", scope="s",
                    idempotency_key="k", payload={})
    db.add(m.Approval, organization_id=ORG, action_id=action.id, status="approved")
    with _aware_sqlite_datetimes():
        steps = await onboarding.checklist(workspace.session(), ORG, {})
    assert [step["complete"] for step in steps] == [True] * 5
    assert item is not None and steps[0]["destination"] == "connections/directory"


@pytest.mark.asyncio
async def test_only_new_workspaces_get_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = Workspace(monkeypatch, onboarding_row=False)
    with _aware_sqlite_datetimes():
        assert await onboarding.onboarding_public(existing.session(), ORG) == {"state": "none"}
        created = await organization_routes._create("New Co", existing.human.id, cast(Any, existing.session()))
        state = await onboarding.onboarding_public(existing.session(), created.id)
    assert state["state"] == "welcome" and "checklist" not in state


@pytest.mark.asyncio
async def test_a_ready_workspace_shows_its_checklist_and_examples(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = Workspace(monkeypatch)
    await workspace.set_up({"goal": "support", "tools": ["gmail"]})
    with _aware_sqlite_datetimes():
        state = await onboarding.onboarding_public(workspace.session(), ORG)
    assert state["state"] == "ready" and state["checklistDismissed"] is False
    assert state["checklist"]["total"] == 5 and state["checklist"]["complete"] == 0
    assert state["examples"][0] == "Summarise the unread support emails from today"
