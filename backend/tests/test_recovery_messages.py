"""Phase 4 recovery and messages: one catalogue of plain, specific wording (F10, F14, F20, F21)."""
from __future__ import annotations

import pytest

from audoryn_execution.application.messages import (
    approval_wait,
    planning_paused,
    refused_write,
    uncertain_write,
)

REPO = "DIVINEJMG/school"


@pytest.mark.parametrize(("scope", "request_input", "expected"), [
    ("github.repository.branch.create", {"branch": "codex/55ea0379/readme-setup", "sha": "a" * 40},
     "Waiting for your approval to create branch codex/55ea0379/readme-setup in DIVINEJMG/school."),
    ("github.repository.issues.create", {"title": "Simulation test", "body": "x"},
     "Waiting for your approval to create the issue “Simulation test” in DIVINEJMG/school."),
    ("github.repository.pull_request.create", {"title": "Add setup", "head": "codex/1/x", "base": "main"},
     "Waiting for your approval to open the pull request “Add setup” as a draft in DIVINEJMG/school."),
    ("github.repository.pull_request.merge", {"number": 8, "sha": "a" * 40},
     "Waiting for your approval to merge pull request #8 in DIVINEJMG/school."),
    ("github.repository.issue.comment.create", {"number": 7, "body": "Looking into it"},
     "Waiting for your approval to create a comment on #7 in DIVINEJMG/school."),
])
def test_an_approval_wait_says_what_waits(scope: str, request_input: dict[str, object], expected: str) -> None:  # F10
    assert approval_wait(scope, request_input, REPO) == expected
    assert "polic" not in approval_wait(scope, request_input, REPO)  # policy detail stays in the audit record


def test_an_unconfirmed_write_is_named_with_where_to_look_and_the_answers() -> None:  # F21
    message = uncertain_write("github.repository.issues.create", {"title": "Uncertain write test"}, REPO)
    assert message.startswith("I couldn't confirm whether the issue “Uncertain write test” was created in DIVINEJMG/school.")
    assert "Please check DIVINEJMG/school, then tell me: It happened, It didn't happen, or Check again." in message


def test_a_paused_task_says_why_and_how_to_continue() -> None:  # F14
    message = planning_paused("content_rejected")
    assert message.startswith("The AI planner declined to plan the next step, so I've paused this task.")
    assert message.endswith("Press Retry planning to ask again.")
    assert "Retry planning" in planning_paused("something_new")


def test_a_refused_write_is_explained_with_the_way_forward() -> None:  # F20
    message = refused_write("Use this task's branch prefix codex/1e81a73a/, or a branch or pull request the person named "
                            "(granted in task preparation).")
    assert "may only change its own new branches" in message and "update pull request #8" in message
    assert "codex/1e81a73a" not in message  # rule text stays in the record
    assert refused_write("Something else broke.").startswith("I stopped: Something else broke.")


def test_waiting_for_a_planning_turn_spends_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """F27: another task holding the planning slots is a wait, not a failure; F26: once the
    automatic retries are spent, the message says how to continue."""
    from datetime import UTC, datetime, timedelta

    from audoryn_execution.api.internal import runtime
    from audoryn_execution.api.internal.runtime import PLANNING_TURN, _planning_wait
    from audoryn_shared.domain.ai.providers import AIProviderError

    # The smart planner's wait messages (F26); set here rather than taken from the local .env.
    monkeypatch.setattr(runtime.settings, "smart_planner_enabled", True)

    now = datetime.now(UTC)
    busy = AIProviderError("rate_limited", "Organization planning capacity is full.", retryable=True,
                           budget_scope="concurrency", retry_after_seconds=15)
    for spent in (0, 9):
        state, summary, count, retry_at = _planning_wait(busy, spent, preparing=False, now=now)
        assert (state, summary, count, retry_at) == ("waiting_ai", PLANNING_TURN, spent, now + timedelta(seconds=15))
    limited = AIProviderError("rate_limited", "Provider limit.", retryable=True)
    _, summary, _, retry_at = _planning_wait(limited, 99, preparing=False, now=now)
    assert retry_at is None and summary.endswith("Press Retry planning to try again.")
