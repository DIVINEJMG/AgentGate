"""Fixes from the first live test (13 October 2026): one-off reads are not mistaken for ongoing
work, reads say what they found, chat never claims a missing permission, internal status lines
read like sentences, and "react when a new issue is opened" sets up a real event trigger."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.conversation_semantics import validate_route_proposal
from app.application.services.reply_claims import unsupported_assertion
from audoryn_execution.application.context.narrator import narrate
from audoryn_execution.application.services import integration_tasks
from audoryn_shared.application.services import integration_foundation
from audoryn_shared.application.services.integration_foundation import (
    IntegrationFoundation,
    IntegrationRequirementError,
)
from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.domain.ai.providers import AIGateway, AIProviderError
from audoryn_shared.domain.integrations.foundation import IntegrationTaskDraft
from audoryn_shared.execution.providers.native.github.provider import ExpandedGitHubProvider
from audoryn_shared.execution.providers.registry import ProviderRegistry
from audoryn_shared.infrastructure.database.models import (
    ConversationMessage,
    IntegrationEventSubscription,
    WorkItem,
)
from tests.test_integration_preparation_gaps import preparation_session


@pytest.mark.parametrize(("scope", "output", "expected"), [
    ("github.repository.issues.list", [], "No issues."),
    ("github.repository.issues.list", [{"number": 3, "title": "Aud"}, {"number": 2, "title": "TE"}],
     "Found 2 issues: #3 Aud; #2 TE."),
    ("github.repository.code.search", {"items": [{"name": "a"}, {"name": "b"}, {"name": "c"}, {"name": "d"}]},
     "Found 4 results: a; b; c; …."),
    ("github.repository.commit.read", {"sha": "abc", "message": "x"}, None),  # nothing to name: the verification note is kept
    ("github.repository.issue.read", {"number": 5, "title": "GE", "body": None}, "Read #5 GE."),  # one object is named
])
def test_a_read_says_what_it_found(scope: str, output: object, expected: str | None) -> None:
    assert narrate(scope, output) == expected


@pytest.mark.parametrize("answer", [
    "I’m sorry, but I don’t have permission to comment on issues.",  # the live test's reply
    "I don't have access to GitHub.",
    "I’m not allowed to post comments.",
])
def test_chat_never_claims_a_missing_permission(answer: str) -> None:
    proposal = {"family": "conversation.answer", "reply": {"answer": answer, "factIds": []}}
    with pytest.raises(AIProviderError, match="lacks a tool or permission"):  # one correction, then the right family
        validate_route_proposal(proposal)


@pytest.mark.parametrize("answer", ["I can monitor the repository.", "Do you want me to comment on issue #2?",
                                    "I don't have any changes yet."])
def test_ordinary_replies_are_left_alone(answer: str) -> None:
    assert unsupported_assertion(answer) is None
    validate_route_proposal({"family": "conversation.answer", "reply": {"answer": answer, "factIds": []}})


DRAFT = {"objective": "Summarise each new issue", "completion_criteria": ["Summary posted"],
         "needs": [{"provider": "github", "resource_hint": "DIVINEJMG/school", "scopes": ["github.repository.issues.read"]}],
         "runtime_requirements": []}


def _foundation(monkeypatch: pytest.MonkeyPatch, answer: dict[str, Any]) -> tuple[IntegrationFoundation, Any]:
    registry = ProviderRegistry((ExpandedGitHubProvider(),))
    monkeypatch.setattr(integration_foundation, "execution_provider_registry", lambda: registry)
    foundation = IntegrationFoundation(cast(AsyncSession, SimpleNamespace()))
    monkeypatch.setattr(foundation, "catalog", AsyncMock(return_value=[]))
    return foundation, SimpleNamespace(generate_structured=AsyncMock(return_value=answer))


@pytest.mark.asyncio
async def test_an_event_request_in_the_persons_words_becomes_a_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    instruction = "react when a new issue is opened in DIVINEJMG/school github repo"
    foundation, gateway = _foundation(monkeypatch, {**DRAFT, "event_trigger": {
        "provider": "github", "event": "issues.opened", "request_excerpt": "react when a new issue is opened"}})
    draft = await foundation.interpret(cast(AIGateway, gateway), organization_id=uuid4(), user_id=uuid4(), instruction=instruction)
    assert draft.event_trigger is not None and draft.event_trigger.event == "issues.opened"
    schema = gateway.generate_structured.call_args.kwargs["schema"]
    assert "issues.opened" in schema["properties"]["event_trigger"]["anyOf"][0]["properties"]["event"]["enum"]


@pytest.mark.asyncio
async def test_an_event_trigger_the_person_never_asked_for_is_a_question(monkeypatch: pytest.MonkeyPatch) -> None:
    foundation, gateway = _foundation(monkeypatch, {**DRAFT, "event_trigger": {
        "provider": "github", "event": "issues.opened", "request_excerpt": "whenever an issue opens"}})
    with pytest.raises(IntegrationRequirementError, match="every time a new issue is opened"):
        await foundation.interpret(cast(AIGateway, gateway), organization_id=uuid4(), user_id=uuid4(),
                                   instruction="Summarise the issues in DIVINEJMG/school")


def _trigger_session(monkeypatch: pytest.MonkeyPatch, *, events_on: bool,
                     subscribed: set[str] | None = frozenset({"issues"})) -> tuple[Any, Any, list[Any]]:  # type: ignore[assignment]
    monkeypatch.setattr(settings, "smart_planner_enabled", False)
    monkeypatch.setattr(settings, "github_events_enabled", events_on)
    monkeypatch.setattr(settings, "github_webhook_secret", SecretStr("s"))
    registry = ProviderRegistry((ExpandedGitHubProvider(),))
    monkeypatch.setattr(integration_tasks, "execution_provider_registry", lambda: registry)
    monkeypatch.setattr(ExpandedGitHubProvider, "subscribed_events", staticmethod(AsyncMock(return_value=subscribed)))
    instruction = "react when a new issue is opened in DIVINEJMG/school github repo"
    draft = IntegrationTaskDraft.model_validate({**DRAFT, "event_trigger": {
        "provider": "github", "event": "issues.opened", "request_excerpt": "react when a new issue is opened"}})
    session, command, records = preparation_session(instruction=instruction, draft=draft.model_dump())
    resource = str(uuid4())
    monkeypatch.setattr(integration_tasks.IntegrationFoundation, "resolve", AsyncMock(return_value=[{
        "id": resource, "connectionId": str(uuid4()), "provider": "github", "name": "DIVINEJMG/school",
        "scopes": ["github.repository.issues.read"], "destinations": {}, "constraints": {}}]))
    monkeypatch.setattr(integration_tasks.IntegrationFoundation, "bind", AsyncMock())
    monkeypatch.setattr(integration_tasks.TransactionalOutbox, "enqueue", AsyncMock())
    return session, command, records


@pytest.mark.asyncio
async def test_react_when_a_new_issue_is_opened_sets_up_a_trigger_and_runs_nothing_now(monkeypatch: pytest.MonkeyPatch) -> None:
    session, command, records = _trigger_session(monkeypatch, events_on=True)
    await integration_tasks.prepare_integration_task(session, command.id)
    [subscription] = [row for row in records if isinstance(row, IntegrationEventSubscription)]
    assert subscription.event_type == "issues.opened" and subscription.results_thread_id == command.thread_id
    assert not [row for row in records if isinstance(row, WorkItem)]  # nothing runs until an issue is opened
    assert integration_tasks.IntegrationFoundation.bind.await_args.kwargs["standing"] is True  # type: ignore[attr-defined]
    assert command.status == "completed" and command.target_type == "job"
    [message] = [row for row in records if isinstance(row, ConversationMessage)]
    assert message.content == ("Set up: every time a new issue is opened in DIVINEJMG/school, I'll run this task: "
                               "“Summarise each new issue” Results will be posted here; you can pause or stop it from the job.")


@pytest.mark.asyncio
async def test_without_event_delivery_the_worker_says_so_instead_of_promising(monkeypatch: pytest.MonkeyPatch) -> None:
    session, command, records = _trigger_session(monkeypatch, events_on=False)
    await integration_tasks.prepare_integration_task(session, command.id)
    assert command.status == "waiting_integration"
    assert not [row for row in records if isinstance(row, IntegrationEventSubscription)]
    [message] = [row for row in records if isinstance(row, ConversationMessage)]
    assert "event delivery isn't turned on" in message.content


@pytest.mark.asyncio
async def test_a_trigger_github_will_not_send_is_reported_instead_of_promised(monkeypatch: pytest.MonkeyPatch) -> None:
    # The live test: the GitHub App subscribed to no events, so "Set up" was a promise nothing could keep.
    session, command, records = _trigger_session(monkeypatch, events_on=True, subscribed=set())
    await integration_tasks.prepare_integration_task(session, command.id)
    assert command.status == "waiting_integration"
    assert not [row for row in records if isinstance(row, IntegrationEventSubscription)]
    [message] = [row for row in records if isinstance(row, ConversationMessage)]
    assert "tick “Issues” in Subscribe to events" in message.content


@pytest.mark.asyncio
async def test_when_github_cannot_be_asked_the_trigger_is_still_set_up(monkeypatch: pytest.MonkeyPatch) -> None:
    session, command, records = _trigger_session(monkeypatch, events_on=True, subscribed=None)
    await integration_tasks.prepare_integration_task(session, command.id)
    assert [row for row in records if isinstance(row, IntegrationEventSubscription)]



# --- Writes other people can see ask first, unless the request asked for them -----------------

COMMENT, READ_ISSUES, EDIT = ("github.repository.issue.comment.create", "github.repository.issues.read",
                              "github.repository.workspace.edit")


def _github_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = ProviderRegistry((ExpandedGitHubProvider(),))
    monkeypatch.setattr(integration_foundation, "execution_provider_registry", lambda: registry)


def test_only_writes_other_people_can_see_count_as_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    _github_registry(monkeypatch)
    assert integration_foundation.visible_write_scopes("github", [COMMENT, READ_ISSUES, EDIT]) == [COMMENT]


@pytest.mark.asyncio
async def test_every_visible_write_asks_first(monkeypatch: pytest.MonkeyPatch) -> None:  # Q1
    from audoryn_shared.infrastructure.database.models import Policy, PolicyRevision
    _github_registry(monkeypatch)
    added: list[Any] = []
    session = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=list)), add=added.append, flush=AsyncMock())
    worker = SimpleNamespace(organization_id=uuid4(), agent_identity_id=uuid4())
    # Even a write the person asked for in so many words asks first; "Always allow" is the way to stop asking.
    binding = {"id": str(uuid4()), "provider": "github", "scopes": [READ_ISSUES, COMMENT]}
    await IntegrationFoundation(cast(AsyncSession, session)).provision_bound_authority(
        SimpleNamespace(id=uuid4()), worker, [binding])
    revisions = [row for row in added if isinstance(row, PolicyRevision)]
    assert [(row.effect, row.selectors["scopes"]) for row in revisions] == [
        ("allow", [READ_ISSUES, COMMENT]), ("require_approval", [COMMENT])]
    assert revisions[1].priority > revisions[0].priority  # the approval rule outranks the task's allow rule
    assert any(isinstance(row, Policy) and row.name.startswith("Integration binding approval") for row in added)


def test_worker_jobs_treat_visible_writes_like_irreversible_actions() -> None:
    from audoryn_shared.api.jobs_routes import needs_explicit_request
    actions = {action.scope: action.descriptor() for action in ExpandedGitHubProvider().actions.values()}
    assert needs_explicit_request(actions[COMMENT])
    assert not needs_explicit_request(actions[READ_ISSUES]) and not needs_explicit_request(actions[EDIT])


# --- Judgement checks advise; the AI decides -------------------------------------------------

from app.application.services.reply_claims import account_state_claim
from audoryn_shared.application.services.ai_gateway import ProviderAIGateway


@pytest.mark.parametrize(("answer", "claims"), [
    ("There are currently no open issues in the DIVINEJMG/school repository.", True),  # the live test's reply
    ("The repository has 3 open issues.", True),
    ("There were no open issues when I checked at 17:06.", False),   # the past, said as the past
    ("If there are no open issues, the job does nothing.", False),
    ("Are there any open issues?", False),
])
def test_only_claims_about_an_accounts_contents_now_are_noticed(answer: str, claims: bool) -> None:
    assert account_state_claim(answer) is claims
    assert unsupported_assertion(answer) is None  # replies are never blocked by it


def test_a_stale_account_answer_is_sent_back_once_with_the_reason() -> None:
    proposal = {"family": "conversation.answer", "reply": {
        "answer": "There are currently no open issues in the DIVINEJMG/school repository.", "factIds": []}}
    with pytest.raises(AIProviderError, match="only a fresh read can tell") as caught:
        validate_route_proposal(proposal)
    assert caught.value.validation_rule in ProviderAIGateway.ADVISORY_RULES


def test_a_job_that_needs_a_connected_account_is_advised_not_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application.services import conversation_semantics
    registry = ProviderRegistry((ExpandedGitHubProvider(),))
    monkeypatch.setattr("audoryn_shared.execution.bootstrap.execution_provider_registry", lambda: registry)
    monkeypatch.setattr(settings, "integration_foundation_enabled", True)
    job = {"family": "job.create", "arguments": {"name": "Summarize new issues",
                                                 "requiredCapabilities": ["github.repository.issues.list"]}}
    with pytest.raises(AIProviderError, match="job.create cannot bind an account") as caught:
        validate_route_proposal(job)
    assert caught.value.validation_rule in ProviderAIGateway.ADVISORY_RULES
    # Time-scheduled jobs and jobs without connected-account tools are left alone.
    validate_route_proposal({**job, "arguments": {"name": "x", "requiredCapabilities": ["browser.page.read"]}})
    assert conversation_semantics.needs_connected_account(SimpleNamespace(
        family="job.create", schedule={"cadence": "daily"}, arguments=job["arguments"])) is False


class _Script:
    """A gateway whose model answers from a script; the checks under test run for real."""

    def __init__(self, answers: list[dict[str, Any]]) -> None:
        self.answers, self.prompts = list(answers), []

    def _provider_for(self, role: str) -> tuple[None, str]:
        return None, "scripted"

    async def generate_structured(self, **kwargs: Any) -> dict[str, Any]:
        self.prompts.append(kwargs["prompt"])
        return self.answers.pop(0)


async def _validated(gateway: _Script, validator: Any) -> dict[str, Any]:
    script = cast(Any, gateway)
    script.ADVISORY_RULES = ProviderAIGateway.ADVISORY_RULES
    return await ProviderAIGateway.generate_validated_structured(
        script, proposal_validator=validator, role="intent", schema_name="s", schema={}, prompt="P")


STALE = {"family": "conversation.answer", "reply": {"answer": "There are currently no open issues.", "factIds": []}}


@pytest.mark.asyncio
async def test_the_correction_explains_why_and_a_corrected_choice_is_taken() -> None:
    gateway = _Script([STALE, {"family": "integration.execute"}])
    assert (await _validated(gateway, validate_route_proposal))["family"] == "integration.execute"
    assert "Why: The reply states what a connected account contains now" in gateway.prompts[1]


@pytest.mark.asyncio
async def test_if_the_ai_still_disagrees_after_one_correction_its_answer_stands() -> None:
    gateway = _Script([STALE, STALE])  # no dead end: the request is answered, not failed
    assert (await _validated(gateway, validate_route_proposal))["family"] == "conversation.answer"


@pytest.mark.asyncio
async def test_contract_failures_are_still_strict() -> None:
    bad = {"family": "conversation.answer", "arguments": {"actionInput": {"x": 1}}}  # chat carrying an action
    with pytest.raises(AIProviderError):
        await _validated(_Script([bad, bad]), validate_route_proposal)


@pytest.mark.parametrize(("definition", "runs"), [
    # The live test: an AI-described "continuous monitoring" with nothing set up.
    ({"autonomy": {"humanSchedule": "Continuous monitoring; react when a new issue is opened"}},
     "only when someone asks; no schedule or event trigger is set up"),
    ({"autonomy": {"eventTrigger": {"provider": "github", "event": "issues.opened"}}},
     "each time the github event issues.opened happens"),
    ({"triggerConfig": {"schedule": {"enabled": True}}}, "on its schedule"),
])
def test_chat_sees_how_a_job_actually_runs_not_how_it_was_described(definition: dict[str, Any], runs: str) -> None:
    from app.application.services.conversation_context import _job_runs
    assert _job_runs(definition) == runs


@pytest.mark.asyncio
async def test_a_connection_needing_reconnection_says_so_instead_of_missing_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    from audoryn_shared.application.services.integration_foundation import (
        IntegrationRequirementError,
    )
    from audoryn_shared.domain.integrations.foundation import IntegrationNeed, IntegrationTaskDraft
    _github_registry(monkeypatch)
    state = SimpleNamespace(authorization_state="reconnect_required",
                            reason="GitHub installation access changed; rediscover and review affected task bindings.")
    foundation = IntegrationFoundation(cast(AsyncSession, SimpleNamespace(scalar=AsyncMock(return_value=state))))
    monkeypatch.setattr(foundation, "catalog", AsyncMock(return_value=[{
        "id": "1", "connectionId": str(uuid4()), "provider": "github", "externalId": "980890773", "name": "DIVINEJMG/school",
        "account": "DIVINEJMG", "capabilities": [], "health": "healthy", "aliases": []}]))
    draft = IntegrationTaskDraft.model_construct(runtime_requirements=[], needs=[IntegrationNeed.model_construct(
        provider="github", scopes=[READ_ISSUES], resource_hint="DIVINEJMG/school", account_hint="", destinations={},
        constraints={}, request_excerpt="")])
    with pytest.raises(IntegrationRequirementError, match=r"connection \(DIVINEJMG\) needs reconnecting before I can use DIVINEJMG/school"):
        await foundation.resolve(organization_id=uuid4(), user_id=uuid4(), draft=draft)


SCHEDULED_JOB_ANSWER = {"family": "conversation.answer", "requested_start": "on_event", "reply": {
    "answer": "The Summarize New Issues job is already active and scheduled to run every weekday at 9:00 AM UTC.",
    "factIds": []}}


def test_an_event_request_answered_with_a_scheduled_job_is_sent_back_once() -> None:
    # The live test: "When a new issue is opened, summarise it here" answered with a weekday-9:00 job.
    with pytest.raises(AIProviderError, match="does not start on an event") as caught:
        validate_route_proposal(SCHEDULED_JOB_ANSWER)
    assert caught.value.validation_rule in ProviderAIGateway.ADVISORY_RULES


@pytest.mark.parametrize("proposal", [
    {"family": "integration.execute", "requested_start": "on_event"},       # sets the trigger up
    {"family": "conversation.answer", "requested_start": "unspecified",     # an ordinary question
     "reply": {"answer": "That job runs every weekday at 9:00.", "factIds": []}},
])
def test_consistent_choices_are_left_alone(proposal: dict[str, Any]) -> None:
    validate_route_proposal(proposal)


@pytest.mark.asyncio
async def test_the_ai_corrects_itself_or_keeps_its_answer() -> None:
    fixed = {"family": "integration.execute", "requested_start": "on_event"}
    assert (await _validated(_Script([SCHEDULED_JOB_ANSWER, fixed]), validate_route_proposal))["family"] == "integration.execute"
    kept = await _validated(_Script([SCHEDULED_JOB_ANSWER, SCHEDULED_JOB_ANSWER]), validate_route_proposal)
    assert kept["family"] == "conversation.answer"  # advisory: never a dead end


def test_a_repeated_request_is_seen_without_the_replies_it_already_got() -> None:
    from app.application.services.conversation_semantics import without_repeated_replies
    ask, stock = "When a new issue is opened, summarise it here", "The Summarize New Issues job is already active."
    context = {"thread": {"messages": [
        {"role": "human", "content": "Hello"}, {"role": "worker", "content": "Hi!"},
        *[entry for _ in range(3) for entry in ({"role": "human", "content": ask}, {"role": "worker", "content": stock})],
        {"role": "human", "content": "Other question"}, {"role": "worker", "content": "Hi!"}]}}
    view = without_repeated_replies(context, ask)["thread"]
    assert [entry["content"] for entry in view["messages"]] == ["Hello", "Other question", "Hi!"]  # one copy of a repeat
    assert "sent this same request 3 time(s) before" in view["repeatedRequest"]
    assert len(context["thread"]["messages"]) == 10  # the stored history is unchanged


def test_changing_an_existing_job_to_start_on_an_event_is_sent_back_once() -> None:
    with pytest.raises(AIProviderError, match="cannot make it start on a connected-account event"):
        validate_route_proposal({"family": "job.update", "requested_start": "on_event"})
    validate_route_proposal({"family": "job.update", "requested_start": "on_schedule"})  # ordinary edits pass


@pytest.mark.asyncio
async def test_continuing_after_a_first_step_question_prepares_the_task(monkeypatch: pytest.MonkeyPatch) -> None:
    # The live test: the question was asked while the request was first read, so no task details
    # existed yet and "Continue task setup" failed with KeyError 'integrationTask'.
    from audoryn_shared.api import integration_foundation_routes as routes
    monkeypatch.setattr(settings, "integration_foundation_enabled", True)
    monkeypatch.setattr("audoryn_shared.application.services.preparation_ai.restart_exhausted_preparation", AsyncMock(return_value="ready"))
    monkeypatch.setattr(routes.TransactionalOutbox, "enqueue", AsyncMock())
    from tests.test_efficiency_queries import ORG
    user, worker = uuid4(), uuid4()
    command = SimpleNamespace(id=uuid4(), organization_id=ORG, created_by=user, family="integration.execute", target_id=None,
                              thread_id=uuid4(), source_message_id=uuid4(), status="clarification_required",
                              payload={"request": "In DIVINEJMG/school, add a README section and a helper script, then open a draft PR."})
    session = SimpleNamespace(scalar=AsyncMock(return_value=command), get=AsyncMock(return_value=SimpleNamespace(worker_id=worker)),
                              commit=AsyncMock())
    answer = await routes.resume_preparation(ORG, command.id, routes.ResumeInput(clarification="Python, Test PR"),
                                             cast(Any, SimpleNamespace(user_id=user)), cast(Any, session))
    task = command.payload["integrationTask"]
    assert answer["status"] == "accepted" and command.status == "accepted"
    assert task["workerId"] == str(worker) and task["threadId"] == str(command.thread_id)
    assert task["instruction"].endswith("open a draft PR.\nHuman clarification: Python, Test PR")
