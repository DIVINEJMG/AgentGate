"""A new chat request that the intent model sent to "run an existing job now" against an earlier chat
task's one-shot job becomes a new task instead of a hidden 500; and the conversation route keeps a
service's own refusal and logs where an unexpected failure happened."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import conversation_routes
from app.application.services import conversation_commands as commands
from app.application.services.conversation_commands import ConversationCommandCompiler
from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.domain.conversation.intent import WorkerCommandIntent

ORG = uuid4()


def _compiler(monkeypatch: pytest.MonkeyPatch, *, one_shot: bool) -> tuple[ConversationCommandCompiler, list[str]]:
    monkeypatch.setattr(settings, "integration_foundation_enabled", True)
    compiler = ConversationCommandCompiler.__new__(ConversationCommandCompiler)
    job = SimpleNamespace(id=uuid4(), name="Earlier chat task")

    async def worker(*_: Any) -> Any:
        return SimpleNamespace(id=uuid4(), name="Ada")

    async def resolve_job(*_: Any, **__: Any) -> Any:
        return job

    async def revision(_session: Any, _job: Any) -> Any:
        return SimpleNamespace(definition={"autonomy": {"oneShot": one_shot}})

    queued: list[str] = []

    async def queue(*_: Any) -> Any:
        queued.append("queued")
        return SimpleNamespace(id=uuid4(), payload={})

    compiler._resolve_worker_for_job = worker  # type: ignore[method-assign]
    compiler._resolve_job = resolve_job  # type: ignore[method-assign]
    compiler._session = cast(Any, None)
    monkeypatch.setattr(commands, "current_revision", revision)
    monkeypatch.setattr(commands, "queue_job", queue)
    original = compiler._dispatch

    async def dispatch(**kwargs: Any) -> Any:
        if kwargs["intent"].family == "integration.execute":
            queued.append("new task")
            return "new task receipt"
        return await original(**kwargs)

    compiler._dispatch = dispatch  # type: ignore[method-assign]
    return compiler, queued


async def _run(compiler: ConversationCommandCompiler, command: Any) -> Any:
    principal = SimpleNamespace(organization_id=ORG, user_id=uuid4(), permissions=frozenset({"jobs.run", "jobs.manage"}))
    return await compiler._dispatch(
        organization_id=ORG, principal=cast(Any, principal), thread=cast(Any, SimpleNamespace(id=uuid4())),
        source_message=cast(Any, SimpleNamespace(id=uuid4(), content="Add a README section")),
        intent=WorkerCommandIntent(family="work.execute_now"), context={}, command=command, confirmed=False)


@pytest.mark.asyncio
async def test_a_one_shot_job_named_by_a_new_message_becomes_a_new_task(monkeypatch: pytest.MonkeyPatch) -> None:
    compiler, calls = _compiler(monkeypatch, one_shot=True)
    command = SimpleNamespace(id=uuid4(), family="work.execute_now", payload={"intent": {}}, target_type=None, target_id=None)
    assert await _run(compiler, command) == "new task receipt"
    assert calls == ["new task"]
    assert command.family == "integration.execute"
    assert command.payload["intent"]["family"] == "integration.execute"


@pytest.mark.asyncio
async def test_an_ordinary_job_still_runs_now(monkeypatch: pytest.MonkeyPatch) -> None:
    compiler, calls = _compiler(monkeypatch, one_shot=False)
    command = SimpleNamespace(id=uuid4(), family="work.execute_now", payload={}, target_type=None, target_id=None)
    receipt = await _run(compiler, command)
    assert calls == ["queued"] and receipt.status == "accepted"


def test_a_service_refusal_keeps_its_status_and_message() -> None:
    refusal = HTTPException(409, "This job belongs to one chat request.")
    assert conversation_routes._http_error(refusal) is refusal


def test_an_unexpected_failure_is_logged_with_its_place_not_its_message(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        result = conversation_routes._http_error(TypeError("secret task text"))
    assert result.status_code == 500
    assert "Conversation operation failed error_type=TypeError" in caplog.text
    assert "secret task text" not in caplog.text
