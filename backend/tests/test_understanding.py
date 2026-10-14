"""The AI understands requests by meaning, not by required wording, and asks when it cannot tell:
the answer continues the original request."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.services import conversation_commands
from app.application.services.conversation_commands import ConversationCommandCompiler
from app.application.services.grounding import grounded_in
from app.application.services.worker_draft import _enforce_human_approval_intent
from app.domain.conversation.intent import CommandReceipt, WorkerCommandIntent
from app.domain.workforce.drafts import WorkerDraft
from app.infrastructure.database import models as m
from app.infrastructure.database.base import Base
from scripts.runtime_benchmark import BenchSession, _aware_sqlite_datetimes
from tests.test_efficiency_queries import ORG, Database

TABLES = [mapper.class_ for mapper in Base.registry.mappers]


# ---------- the human's words, however they are written ----------


@pytest.mark.parametrize(("quote", "text", "expected"), [
    ("search the web", "Please   SEARCH the web for news", True),
    ("“BBC News”", 'Watch "bbc news" every morning', True),
    ("check with me before posting", "Check with me before posting, thanks!", True),
    ("bbc.com", "use bbc.com please", True),
    ("search the web", "never search beyond these sites", False),   # not the human's words
    ("web search", "search the web", False),                         # same words, different order
    ("", "anything", False),
    (None, "anything", False),
])
def test_quotes_match_the_humans_words_not_their_formatting(quote: Any, text: str, expected: bool) -> None:
    assert grounded_in(quote, text) is expected


def _draft(boundary: dict[str, Any]) -> WorkerDraft:
    return WorkerDraft.model_validate({
        "suggested_name": "Poster", "role": "Publisher", "charter": "Posts news", "initial_jobs": [{
            "name": "Post", "objective": "Post the news", "approval_boundaries": [boundary]}]})


@pytest.mark.parametrize(("request_text", "excerpt", "kept"), [
    ("Post our news, but let me review it first", "let me review it first", True),     # any wording
    ("Post our news, check with me before posting", "check with me before posting", True),
    ("Post our news to the site", "ask before posting", False),                          # invented
    ("Post our news but ask me before posting", None, True),                             # old phrases still work
])
def test_an_approval_step_needs_the_humans_words_not_set_phrases(request_text: str, excerpt: str | None, kept: bool) -> None:
    draft = _draft({"kind": "submit_requires_approval", "reason": "Human review", "request_excerpt": excerpt})
    result = _enforce_human_approval_intent(draft, instruction=request_text)
    assert bool(result.initial_jobs[0].approval_boundaries) is kept


# ---------- asking, then continuing ----------


class Chat:
    """A thread in SQLite and a compiler whose operations are recorded instead of performed."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.db = Database(TABLES)
        self.human = self.db.add(m.HumanIdentity, display_name="Owner")
        self.thread = self.db.add(m.ConversationThread, organization_id=ORG, title="T", status="active",
                                  created_by=self.human.id)
        self.dispatched: list[dict[str, Any]] = []

        async def dispatch(compiler: Any, **kwargs: Any) -> CommandReceipt:
            self.dispatched.append({"content": kwargs["source_message"].content, "intent": kwargs["intent"]})
            return CommandReceipt(status="completed", message="done")

        monkeypatch.setattr(ConversationCommandCompiler, "_dispatch", dispatch)
        monkeypatch.setattr(ConversationCommandCompiler, "_event", AsyncMock())

    async def send(self, text: str, intent: dict[str, Any]) -> tuple[CommandReceipt, Any]:
        message = self.db.add(m.ConversationMessage, organization_id=ORG, thread_id=self.thread.id, role="human",
                              content=text)
        with _aware_sqlite_datetimes():
            session = BenchSession(Session(self.db.engine, expire_on_commit=False))
            compiler = ConversationCommandCompiler(cast(Any, session), cast(Any, None))
            outcome = await compiler.compile_and_execute(
                organization_id=ORG, principal=cast(Any, SimpleNamespace(organization_id=ORG, user_id=self.human.id)),
                thread=cast(Any, session.sync_session.get(m.ConversationThread, self.thread.id)),
                source_message=cast(Any, session.sync_session.get(m.ConversationMessage, message.id)),
                intent=WorkerCommandIntent.model_validate(intent), authoritative_context={})
            return outcome.receipt, outcome.command

    def status(self, command_id: Any) -> str:
        with Session(self.db.engine) as session:
            return session.scalar(select(m.ConversationCommand.status).where(m.ConversationCommand.id == command_id)) or ""


@pytest.mark.asyncio
async def test_an_unclear_request_gets_one_question_and_nothing_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = Chat(monkeypatch)
    receipt, command = await chat.send("Do the usual report", {
        "family": "work.execute_now", "clarification_question": "Which report: the daily sales or the weekly summary?"})
    assert receipt.status == "clarification_required"
    assert receipt.message == "Which report: the daily sales or the weekly summary?"
    assert chat.dispatched == [] and chat.status(command.id) == "clarification_required"


@pytest.mark.asyncio
async def test_the_answer_continues_the_original_request(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = Chat(monkeypatch)
    _, asked = await chat.send("Create a worker that posts our news every morning", {
        "family": "worker.create", "clarification_question": "Which website should it post to?"})
    receipt, command = await chat.send("our blog at example.com", {
        "family": "worker.create", "arguments": {"resumeCommandId": str(asked.id)}})
    assert receipt.status == "completed"
    [run] = chat.dispatched
    assert run["content"] == ("Create a worker that posts our news every morning\n\n"
                              "You asked: Which website should it post to?\nMy answer: our blog at example.com")
    assert "resumeCommandId" not in run["intent"].arguments
    assert chat.status(asked.id) == "resumed"
    with Session(chat.db.engine) as session:
        saved = session.get(m.ConversationCommand, command.id)
        assert saved is not None and saved.payload["resumedFrom"] == str(asked.id)
        assert saved.payload["request"] == run["content"]  # a further question continues all of it


@pytest.mark.asyncio
async def test_a_stale_or_foreign_answer_is_treated_as_a_new_request(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = Chat(monkeypatch)
    await chat.send("hello", {"family": "conversation.answer", "arguments": {"resumeCommandId": str(uuid4())}})
    assert chat.dispatched[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_a_worker_request_the_designer_cannot_settle_becomes_a_question(monkeypatch: pytest.MonkeyPatch) -> None:
    class Designer:
        def __init__(self, *args: Any) -> None:
            pass

        async def create_from_instruction(self, **kwargs: Any) -> Any:
            raise ValueError("This worker request names more than ten websites. Please split it into smaller jobs.")

    monkeypatch.setattr(conversation_commands, "WorkerAutonomyService", Designer)
    compiler = ConversationCommandCompiler(cast(Any, SimpleNamespace()), cast(Any, None))
    with pytest.raises(conversation_commands.CommandResolutionError, match="more than ten websites"):
        await compiler._dispatch(
            organization_id=ORG, principal=cast(Any, SimpleNamespace(permissions=frozenset({"workforce.manage", "jobs.manage"}))),
            thread=cast(Any, SimpleNamespace(id=uuid4(), worker_id=None)),
            source_message=cast(Any, SimpleNamespace(id=uuid4(), content="Create a worker")),
            intent=WorkerCommandIntent(family="worker.create"), context={}, command=cast(Any, SimpleNamespace(id=uuid4())),
            confirmed=False)


@pytest.mark.asyncio
async def test_the_create_worker_form_does_not_guess_the_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application.services import conversations
    from app.application.services.conversations import ConversationService

    interpreted: list[str] = []

    class Interpreter:
        def __init__(self, gateway: Any) -> None:
            pass

        async def interpret(self, **kwargs: Any) -> WorkerCommandIntent:
            interpreted.append(kwargs["message"])
            return WorkerCommandIntent(family="web.research")

    compiled: list[WorkerCommandIntent] = []

    class Compiler:
        def __init__(self, *args: Any) -> None:
            pass

        async def compile_and_execute(self, **kwargs: Any) -> Any:
            compiled.append(kwargs["intent"])
            receipt = CommandReceipt(status="completed", message="ok")
            return SimpleNamespace(receipt=receipt, command=SimpleNamespace(id=uuid4(), family="worker.create", status="completed"))

    monkeypatch.setattr(conversations, "IntentInterpreter", Interpreter)
    monkeypatch.setattr(conversations, "ConversationCommandCompiler", Compiler)
    service = object.__new__(ConversationService)
    thread = SimpleNamespace(id=uuid4(), worker_id=None, title="Create worker", organization_id=ORG)
    session = SimpleNamespace(add=lambda row: None, flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
    service._session = cast(Any, session)
    service._require_org = lambda *a: None  # type: ignore[method-assign]
    service.get_thread = AsyncMock(return_value=(thread, None))  # type: ignore[method-assign]
    service._validated_artifact_refs = AsyncMock(return_value=[])  # type: ignore[method-assign]
    service._message_event = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(conversations, "ConversationContextAssembler",
                        lambda session: SimpleNamespace(assemble=AsyncMock(return_value={})))
    monkeypatch.setattr(conversations, "ai_gateway_from_settings", lambda **kwargs: None)
    resume = uuid4()
    await service.send_message(organization_id=ORG, principal=cast(Any, SimpleNamespace(user_id=uuid4())),
                               thread_id=thread.id, content="Research competitors every week",
                               operation="worker.create", resume_command_id=resume)
    assert interpreted == []
    assert compiled[0].family == "worker.create" and compiled[0].arguments == {"resumeCommandId": str(resume)}


@pytest.mark.asyncio
async def test_a_message_on_another_subject_after_a_question_is_a_new_request(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = Chat(monkeypatch)
    _, asked = await chat.send("Create a worker that posts our news", {
        "family": "worker.create", "clarification_question": "Which website should it post to?"})
    await chat.send("never mind, how did yesterday's report go?", {
        "family": "result.query", "arguments": {"resumeCommandId": str(asked.id)}})
    assert chat.dispatched[0]["content"] == "never mind, how did yesterday's report go?"
    assert chat.status(asked.id) == "clarification_required"  # still answerable later


@pytest.mark.parametrize(("written", "days"), [
    (["Mon", "thurs"], ["monday", "thursday"]),
    (["weekdays"], ["monday", "tuesday", "wednesday", "thursday", "friday"]),
    (["Weekend", "SUNDAY"], ["saturday", "sunday"]),
    (["Tuesdays"], ["tuesday"]),
])
def test_schedule_days_can_be_written_any_common_way(written: list[str], days: list[str]) -> None:
    from app.application.services.schedule_inference import compile_schedule
    from app.domain.workforce.drafts import ScheduleDraft

    compiled = compile_schedule(ScheduleDraft(kind="weekly", weekdays=written, local_time="09:00", timezone="UTC"))
    assert compiled.trigger_config is not None
    schedule: Any = compiled.trigger_config["schedule"]
    assert schedule["weekdays"] == days


def test_an_unknown_day_is_still_refused() -> None:
    from app.application.services.schedule_inference import compile_schedule
    from app.domain.workforce.drafts import ScheduleDraft

    with pytest.raises(ValueError, match="Invalid schedule weekdays"):
        compile_schedule(ScheduleDraft(kind="weekly", weekdays=["someday"], timezone="UTC"))


@pytest.mark.parametrize(("written", "clock"), [
    ("9:00", "09:00"), ("9am", "09:00"), ("9:30 pm", "21:30"), ("21.15", "21:15"), ("12am", "00:00"), ("12 PM", "12:00"),
])
def test_schedule_times_can_be_written_any_common_way(written: str, clock: str) -> None:
    from app.domain.workforce.drafts import ScheduleDraft

    assert ScheduleDraft(kind="daily", local_time=written).local_time == clock


@pytest.mark.parametrize("unclear", ["9", "noon", "soon"])
def test_an_unclear_time_is_still_refused(unclear: str) -> None:
    from pydantic import ValidationError

    from app.domain.workforce.drafts import ScheduleDraft

    with pytest.raises(ValidationError):
        ScheduleDraft(kind="daily", local_time=unclear)
