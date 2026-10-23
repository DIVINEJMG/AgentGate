"""Fixes from the Oracle live check and the browser setup review (docs/plans/oracle-findings-fix-plan.md)."""
from __future__ import annotations

from typing import Any, cast

import pytest


@pytest.mark.parametrize("recorded", [
    {"outcome": "REQUIRE_APPROVAL", "reason": "Approval required."},  # what the runtime records
    None,                                                              # nothing recorded
    {"outcome": "ALLOW", "reason": "ok", "winningPolicy": {"id": "p"}, "matchedPolicies": [{"id": "p"}]},
])
def test_every_action_record_carries_the_full_policy_shape(recorded) -> None:
    """F30: the Activity page reads matchedPolicies on every action; a runtime action has to carry
    it too (empty), or the page crashes."""
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from audoryn_shared.api.governance_routes import _action_record

    action = SimpleNamespace(id=uuid4(), organization_id=uuid4(), agent_id=uuid4(), resource_id="r", scope="s",
                             status="held", idempotency_key="k", created_at=datetime.now(UTC),
                             payload={"policy": recorded} if recorded is not None else {})
    policy = _action_record(action, "Ada", None)["policy"]  # type: ignore[arg-type]
    assert isinstance(policy["matchedPolicies"], list) and "winningPolicy" in policy and policy["outcome"]
    if recorded and recorded.get("matchedPolicies"):
        assert policy["matchedPolicies"] == recorded["matchedPolicies"] and policy["winningPolicy"] == {"id": "p"}


def _pull_request(item, **changes):
    pr = {"id": 7, "number": 8, "html_url": "https://github.com/o/r/pull/8", "draft": False, "mergeable": True,
          "mergeable_state": "clean", "merged": False, "merge_commit_sha": None,
          "head": {"sha": "a" * 40, "ref": f"codex/{str(item)[:8]}/fix", "repo": {"id": 1}}, "base": {"repo": {"id": 1}}}
    return {**pr, **changes}


@pytest.mark.asyncio
@pytest.mark.parametrize("after_write", [True, False])
async def test_an_unreadable_result_after_a_write_is_uncertain_not_failed(after_write: bool, caplog) -> None:
    """F31: GitHub merged the pull request, then its result could not be read (an unexpected
    shape). The write happened, so the outcome is uncertain (and checked again), not failed; before
    the write is sent, the same kind of error is still a plain failure. The cause is logged without
    values."""
    from uuid import uuid4

    from audoryn_shared.execution.contracts import ExecutionProviderError
    from audoryn_shared.execution.providers.native.github.provider import ExpandedGitHubProvider
    from tests.test_github_expanded import ScriptedHTTP, request

    item = uuid4()
    unreadable = ["not a pull request"]
    responses = ([_pull_request(item), {"merged": True, "sha": "b" * 40}, unreadable] if after_write
                 else [{k: v for k, v in _pull_request(item).items() if k != "head"}])
    provider = ExpandedGitHubProvider(http=ScriptedHTTP(responses))
    with pytest.raises(ExecutionProviderError) as raised:
        await provider.execute(request=request(provider, "repository.pull_request.merge",
                                               {"number": 8, "sha": "a" * 40, "merge_method": "merge"}, item),
                               configuration={"repository": "o/r", "defaultBranch": "main"}, credential="token")
    assert raised.value.error.code == ("verification_failed" if after_write else "validation_error")
    error_type = "AttributeError" if after_write else "KeyError"
    assert f"after_write={after_write}" in caplog.text and f"error_type={error_type}" in caplog.text
    assert "not a pull request" not in caplog.text and "a" * 40 not in caplog.text  # no values


@pytest.mark.asyncio
async def test_a_merge_is_confirmed_by_github_saying_merged() -> None:
    """F47: on Oracle a merged pull request was read without `merge_commit_sha`; the merge happened
    but could never be confirmed. Merged at the approved head is the proof; missing extras are not."""
    from uuid import uuid4

    from audoryn_shared.execution.providers.native.github.provider import ExpandedGitHubProvider
    from tests.test_github_expanded import ScriptedHTTP, request

    item = uuid4()
    merged = {k: v for k, v in _pull_request(item, merged=True).items() if k not in {"merge_commit_sha", "html_url"}}
    provider = ExpandedGitHubProvider(http=ScriptedHTTP([_pull_request(item), {"merged": True, "sha": "b" * 40}, merged]))
    result = await provider.execute(request=request(provider, "repository.pull_request.merge",
                                                    {"number": 8, "sha": "a" * 40, "merge_method": "merge"}, item),
                                    configuration={"repository": "o/r", "defaultBranch": "main"}, credential="token")
    assert result.status == "success"


@pytest.mark.asyncio
async def test_a_merge_at_another_head_is_still_not_confirmed() -> None:
    """F47: the head check stays: a pull request merged at a different commit than approved is a
    mismatch, never a success."""
    from uuid import uuid4

    from audoryn_shared.execution.contracts import ExecutionProviderError
    from audoryn_shared.execution.providers.native.github.provider import ExpandedGitHubProvider
    from tests.test_github_expanded import ScriptedHTTP, request

    item = uuid4()
    other = _pull_request(item, merged=True, head={"sha": "c" * 40, "ref": "x", "repo": {"id": 1}})
    provider = ExpandedGitHubProvider(http=ScriptedHTTP([_pull_request(item), {"merged": True, "sha": "b" * 40}, other]))
    with pytest.raises(ExecutionProviderError):
        await provider.execute(request=request(provider, "repository.pull_request.merge",
                                               {"number": 8, "sha": "a" * 40, "merge_method": "merge"}, item),
                               configuration={"repository": "o/r", "defaultBranch": "main"}, credential="token")


def test_a_write_counts_as_sent_only_when_it_changes_github() -> None:
    from audoryn_shared.execution.providers.native.github.provider import _changes_github

    assert not _changes_github("GET", "/repos/o/r", None)
    assert not _changes_github("POST", "/graphql", {"query": "query { viewer { login } }"})
    assert _changes_github("POST", "/graphql", {"query": " mutation { x }"})
    assert _changes_github("PUT", "/repos/o/r/pulls/8/merge", {"sha": "a"})


def _stream(finish: str | None):
    import json

    import httpx

    chunk = {"id": "1", "model": "m", "choices": [{"delta": {"content": "{}"}, "finish_reason": finish}]}
    return httpx.Response(200, content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())


@pytest.mark.asyncio
@pytest.mark.parametrize(("finish", "code", "reason"), [
    (None, "none", "incomplete_output"),            # the stream was cut: no finish reason
    ("tool_calls", "tool_calls", "incomplete_output"),
    ("weird_value", "other", "incomplete_output"),  # provider text never passes through
    ("length", "length", "truncated_output"),
])
async def test_a_cut_off_answer_records_how_it_ended(finish, code, reason) -> None:
    """F33: the finish reason is recorded as a fixed code."""
    from audoryn_shared.domain.ai.providers import AIProviderError
    from audoryn_shared.infrastructure.ai.streaming import collect_response

    with pytest.raises(AIProviderError) as raised:
        await collect_response(_stream(finish), model="m")
    assert (raised.value.finish_reason, raised.value.rejection_reason) == (code, reason)


@pytest.mark.asyncio
@pytest.mark.parametrize(("finish", "calls", "answered"), [(None, 2, True), ("length", 1, False), ("tool_calls", 1, False)])
async def test_only_a_dropped_answer_is_asked_again_of_the_same_model(finish, calls, answered) -> None:
    """F33: an answer that ended with no finish reason is asked once more of the same model; any
    other ending moves on as before."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from audoryn_shared.domain.ai.providers import (
        AIProviderError,
        AIResponse,
        AITextRequest,
        ModelProviderCapabilities,
    )
    from audoryn_shared.runtime.smart_planner import CountedProvider

    cut = AIProviderError("invalid_provider_response", "cut", retryable=False,
                          rejection_reason="truncated_output" if finish == "length" else "incomplete_output",
                          finish_reason=finish)
    provider = SimpleNamespace(name="fake", credential="c", capabilities=ModelProviderCapabilities(),
                               generate_text=AsyncMock(side_effect=[cut, AIResponse(text="{}", provider="fake", model="m")]))
    coordinator = SimpleNamespace(acquire_slot=AsyncMock(return_value="slot"), release_slot=AsyncMock(),
                                  reserve_limit=AsyncMock(return_value=True), reserve_units=AsyncMock(return_value=True),
                                  reserve_ai_budget=AsyncMock(return_value=-1), settle_ai_budget=AsyncMock(return_value=True))
    attempt = SimpleNamespace(id=uuid4(), call_count=0, transport_success=False)
    counted = CountedProvider(provider, SimpleNamespace(commit=AsyncMock()), attempt, coordinator, uuid4())
    if answered:
        assert (await counted.generate_text(model="m", request=AITextRequest("", ""))).text == "{}"
    else:
        with pytest.raises(AIProviderError):
            await counted.generate_text(model="m", request=AITextRequest("", ""))
    assert provider.generate_text.await_count == calls == attempt.call_count


@pytest.mark.parametrize(("scope", "output", "said"), [
    ("github.repository.reviews.read", [], "No reviews."),
    ("github.repository.checks.read", {"check_runs": [], "total_count": 0}, "No check runs."),
    ("github.repository.status.read", {"sha": "a" * 40, "state": "pending", "statuses": []}, "State: pending; no statuses."),
])
def test_an_empty_read_says_what_was_empty(scope, output, said) -> None:
    """F32: the three empty reads of the merge task no longer all read "Found nothing"."""
    from audoryn_execution.application.context.narrator import narrate

    assert narrate(scope, output) == said


def _page(url: str, *, login: bool):
    from datetime import UTC, datetime
    from uuid import uuid4

    from audoryn_shared.execution.browser.contracts import (
        BrowserElement,
        BrowserForm,
        BrowserObservation,
    )

    def field(ref, kind):
        return BrowserElement(ref=ref, tag="input", role="textbox", name=ref, text="", element_type=kind, value=None,
                              checked=None, selected=None, disabled=False, href=None)

    elements = (field("e1", "email"), field("e2", "password"), field("e3", "submit")) if login else ()
    forms = (BrowserForm(ref="f1", action="/login", method="post", field_refs=("e1", "e2"), submit_refs=("e3",)),) if login else ()
    return BrowserObservation(id=f"obs_{uuid4().hex}", session_id=uuid4(), url=url, title="Portal" if not login else "Sign in",
                              visible_text="", aria_snapshot="", dom_snapshot="", elements=elements, form_details=forms,
                              observed_at=datetime.now(UTC))


class _ProbeRuntime:
    """Serves a start page (a login page or not) and records what the probe did."""

    def __init__(self, *, login_page: bool, accepted: bool = True, blocked: bool = False) -> None:
        self.login_page, self.accepted, self.blocked = login_page, accepted, blocked
        self.bindings: list[tuple[str, str]] = []
        self.closed = False

    async def health(self):
        return True

    async def create_session(self, **kwargs):
        from types import SimpleNamespace
        from uuid import uuid4

        return SimpleNamespace(id=uuid4())

    async def navigate(self, session_id, **kwargs):
        if self.blocked:
            from audoryn_shared.execution.browser.policy import (
                BrowserNavigationBlocked,
                BrowserNavigationDecision,
            )

            raise BrowserNavigationBlocked(BrowserNavigationDecision(
                allowed=False, source_url=None, target_url=kwargs["url"], target_origin=None,
                reason="Browser destination origin is explicitly denied.", cross_origin=False))
        return _page(kwargs["url"], login=self.login_page)

    async def authenticate(self, session_id, *, bindings, **kwargs):
        self.bindings = [(locator.value, key) for locator, key in bindings]
        return _page("https://portal.example.com/home", login=not self.accepted)

    async def close(self, session_id):
        self.closed = True


async def _probe(runtime, *, credential=None, sign_in=False, start="https://portal.example.com/login"):
    from uuid import uuid4

    from audoryn_shared.execution.browser.policy import BrowserDomainPolicy
    from audoryn_shared.execution.browser.probe import probe_connection

    policy = BrowserDomainPolicy.from_configuration({"allowedOrigins": "https://portal.example.com"})
    return await probe_connection(runtime, organization_id=uuid4(), policy=policy, start_url=start,
                                  credential=credential, sign_in=sign_in)


LOGIN = '{"username": "ada@example.com", "password": "secret-value"}'


@pytest.mark.asyncio
async def test_the_live_check_opens_the_start_page_through_the_rules() -> None:
    """F34: the check really opens the start page and reports what it reached; a page the rules
    block fails with the rule's reason. The session is always closed."""
    runtime = _ProbeRuntime(login_page=False)
    health = await _probe(runtime)
    assert health.state == "healthy" and health.message == "Opened the start page: Portal."
    assert health.metadata["reached"] == {"url": "https://portal.example.com/login", "title": "Portal",
                                          "withinAllowedSites": True} and runtime.closed
    blocked = _ProbeRuntime(login_page=False, blocked=True)
    health = await _probe(blocked)
    assert health.state == "unavailable" and "explicitly denied" in health.message and blocked.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(("login_page", "accepted", "credential", "outcome", "state"), [
    (True, True, LOGIN, "signed_in", "healthy"),
    (True, False, LOGIN, "still_on_login", "degraded"),
    (True, True, None, "no_login_stored", "degraded"),
    (True, True, '{"company": "acme", "pin": "1"}', "needs_fields", "degraded"),
    (False, True, LOGIN, "no_login_form", "degraded"),
])
async def test_test_sign_in_fills_the_login_form_from_the_stored_fields(login_page, accepted, credential, outcome, state) -> None:
    """F34 (Test sign-in): the login form is found from the page's structure, the stored username
    and password fill it, and the result says whether the page moved past the login. No value
    appears in what is reported."""
    runtime = _ProbeRuntime(login_page=login_page, accepted=accepted)
    health = await _probe(runtime, credential=credential, sign_in=True)
    assert (health.metadata["signIn"], health.state) == (outcome, state)
    if outcome in {"signed_in", "still_on_login"}:
        assert runtime.bindings == [("e1", "username"), ("e2", "password")]
    assert "secret-value" not in str(health) and "ada@example.com" not in str(health)


@pytest.mark.asyncio
async def test_a_connection_check_saves_what_it_found(monkeypatch) -> None:
    """F34: the check's message and details are saved. They used to be written into the stored
    dict in place, which a JSON column never saves, so the setup kept showing its first message."""
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from audoryn_shared.api import integration_capability_routes as routes
    from audoryn_shared.execution.contracts import ProviderHealth

    stored = {"displayName": "Portal", "startUrl": "https://portal.example.com", "healthMessage": "validated"}
    integration = SimpleNamespace(id=uuid4(), organization_id=uuid4(), provider="browser", status="connected",
                                  config=stored)

    class Prober:
        async def probe(self, *, organization_id, configuration, credential, sign_in=False):
            return ProviderHealth(state="degraded", message="After signing in, the login form is still showing.",
                                  checked_at=datetime.now(UTC), metadata={"signIn": "still_on_login"})

        async def check_health(self, **kwargs):
            raise AssertionError("a provider that can probe is probed")

    monkeypatch.setattr(routes, "_provider_or_404", lambda name: Prober())
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[integration, None]), commit=AsyncMock(), refresh=AsyncMock())
    principal = SimpleNamespace(permissions=frozenset({"integrations.read"}), user_id=uuid4())
    checked = await routes._health(session, integration.organization_id, principal, integration.id, sign_in=True)  # type: ignore[arg-type]
    assert checked.config is not stored  # a new value, so the column saves it
    assert checked.config["healthMessage"].startswith("After signing in") and checked.status == "degraded"
    assert checked.config["metadata"] == {"signIn": "still_on_login"} and stored["healthMessage"] == "validated"


def test_a_login_is_described_by_its_field_names_only() -> None:
    """F35: planning and drafting see which fields a login stores, never the values."""
    from audoryn_shared.execution.browser.credentials import (
        BrowserAuthenticationFailure,
        BrowserCredentialBundle,
        login_field_names,
    )

    assert login_field_names('{"username": "ada", "password": "s3cret", "company": "acme"}') == [
        "username", "password", "company"]
    assert login_field_names(None) == [] and login_field_names("not json") == []
    bundle = BrowserCredentialBundle.from_secret('{"username": "ada", "password": "s3cret"}')
    with pytest.raises(BrowserAuthenticationFailure) as refused:
        bundle.require(("username", "pin"))  # checked before anything is typed
    assert "no field 'pin'" in str(refused.value) and "username, password" in str(refused.value)
    assert "s3cret" not in str(refused.value)


@pytest.mark.parametrize(("keys", "corrected"), [(["username", "password"], False), (["email", "password"], True)])
def test_the_planner_binds_only_stored_login_fields(keys, corrected) -> None:
    """F35: a sign-in naming a field the login does not store is corrected at planning time."""
    from audoryn_shared.runtime.planner.adaptive import Proposal, RuleContext, _login_fields

    tool = {"scope": "browser.auth.login", "loginFields": ["username", "password"]}
    proposal = Proposal.of({"decision": "act", "scope": "browser.auth.login", "resourceId": "r",
                            "input": {"bindings": [{"locator": {}, "credentialKey": key} for key in keys]}})
    context = RuleContext(tool=tool, tools=[tool], observations=[], browser=None)
    if corrected:
        with pytest.raises(RuntimeError, match="no field 'email'; bind only these fields: username, password"):
            _login_fields(proposal, context)
    else:
        _login_fields(proposal, context)


@pytest.mark.asyncio
async def test_a_browser_connection_records_its_login_fields() -> None:
    """F35: connecting records the field names in the connection's settings."""
    from audoryn_shared.execution.bootstrap import browser_provider

    with_login = await browser_provider.discover_resources(
        configuration={"startUrl": "https://portal.example.com/login"},
        credential='{"username": "ada", "password": "s3cret"}')
    without = await browser_provider.discover_resources(
        configuration={"startUrl": "https://portal.example.com/login"}, credential=None)
    assert with_login[0].configuration["loginFields"] == "username,password"
    assert without[0].configuration["loginFields"] == "" and "s3cret" not in str(with_login[0])


@pytest.mark.asyncio
@pytest.mark.parametrize(("started", "committed", "waits"), [(False, None, False), (True, True, True),
                                                            (True, False, False)])
async def test_a_submit_that_opens_a_page_is_observed_once_it_loads(started, committed, waits) -> None:
    """F38: a submit that starts opening a page is read once that page loads, however slowly the
    site answers; a same-page form, or a page that failed, is read as it stands."""
    import asyncio
    from types import SimpleNamespace

    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    from audoryn_execution.execution.browser.runtime import _SubmitWatch

    loop = asyncio.get_running_loop()

    def settled(ok: bool | None):
        future = loop.create_future()
        if ok is None:
            return future  # never happens; the watch must not wait on it
        future.set_result(object()) if ok else future.set_exception(PlaywrightTimeoutError("none"))
        return future

    watch = cast(Any, _SubmitWatch.__new__(_SubmitWatch))
    watch.started = settled(started)
    watch.committed = settled(committed) if committed else settled(None)
    watch.failed = settled(False) if committed is False else settled(None)
    waited: list[str] = []

    async def wait_for_load_state(state, timeout):
        waited.append(state)

    await asyncio.wait_for(watch.settle(cast(Any, SimpleNamespace(wait_for_load_state=wait_for_load_state)),
                                        5_000), timeout=2)
    watch.close()
    assert waited == (["domcontentloaded"] if waits else [])


@pytest.mark.parametrize(
    ("algorithm", "secret", "at", "code"),
    [  # RFC 6238, appendix B
        ("SHA1", b"12345678901234567890", 59, "94287082"),
        ("SHA1", b"12345678901234567890", 1111111109, "07081804"),
        ("SHA1", b"12345678901234567890", 20000000000, "65353130"),
        ("SHA256", b"12345678901234567890123456789012", 59, "46119246"),
        ("SHA256", b"12345678901234567890123456789012", 1111111109, "68084774"),
        ("SHA512", b"1234567890" * 6 + b"1234", 59, "90693936"),
        ("SHA512", b"1234567890" * 6 + b"1234", 1111111109, "25091201"),
    ],
)
def test_one_time_codes_match_the_rfc(algorithm, secret, at, code) -> None:
    """F36: codes follow RFC 6238, from an otpauth:// link (and a plain base32 key for SHA1)."""
    import base64

    from audoryn_shared.execution.browser.credentials import one_time_code

    key = base64.b32encode(secret).decode().rstrip("=")
    link = f"otpauth://totp/Site:ada?secret={key}&algorithm={algorithm}&digits=8&period=30"
    assert one_time_code(link, at=at) == code
    assert one_time_code(key.lower(), at=at) == code[-6:] or algorithm != "SHA1"


def test_a_stored_otp_secret_is_offered_as_the_current_code_never_typed() -> None:
    """F36: the secret is bound as oneTimeCode, which types the current code."""
    from audoryn_shared.execution.browser.credentials import (
        BrowserAuthenticationFailure,
        BrowserCredentialBundle,
        login_field_names,
        one_time_code,
    )

    secret = '{"username": "ada", "password": "pw", "otpSecret": "JBSWY3DPEHPK3PXP"}'
    bundle = BrowserCredentialBundle.from_secret(secret)
    assert login_field_names(secret) == ["username", "password", "oneTimeCode"]
    assert bundle.resolve("oneTimeCode") == one_time_code("JBSWY3DPEHPK3PXP")
    with pytest.raises(BrowserAuthenticationFailure, match="no field 'otpSecret'"):
        bundle.resolve("otpSecret")
    with pytest.raises(BrowserAuthenticationFailure, match="not a one-time-code secret"):
        BrowserCredentialBundle.from_secret('{"password": "pw", "otpSecret": "not base32!"}')


def _box(ref, element_type, name, field_name=None):
    from audoryn_shared.execution.browser.contracts import BrowserElement

    return BrowserElement(ref=ref, tag="input", role="textbox", name=name, text="", element_type=element_type,
                          value=None, checked=None, selected=None, disabled=False, href=None,
                          field_name=field_name)


def _form_page(*elements):
    from datetime import UTC, datetime
    from uuid import uuid4

    from audoryn_shared.execution.browser.contracts import BrowserForm, BrowserObservation

    form = BrowserForm(ref="f1", action="/login", method="post",
                       field_refs=tuple(element.ref for element in elements), submit_refs=())
    return BrowserObservation(id=f"obs_{uuid4().hex}", session_id=uuid4(), url="https://site.example/login",
                              title="Sign in", visible_text="", aria_snapshot="", dom_snapshot="",
                              elements=tuple(elements), form_details=(form,), observed_at=datetime.now(UTC))


def test_the_sign_in_finds_a_code_box_on_the_form_or_the_next_form_page() -> None:
    """F36: a code box is known by its name or label, on the login form or a page of its own."""
    from audoryn_shared.execution.browser.probe import code_field, login_form

    together = login_form(_form_page(_box("u", "email", "Email"), _box("p", "password", "Password"),
                                _box("c", "text", "Authentication code", "otp")))
    assert together is not None and (together.username_ref, together.code_ref) == ("u", "c")
    plain = login_form(_form_page(_box("u", "email", "Email"), _box("p", "password", "Password"),
                             _box("n", "text", "Company")))
    assert plain is not None and plain.code_ref is None
    found = code_field(_form_page(_box("c", "tel", "Verification code")))
    assert found is not None and found[1] == "c"
    assert code_field(_form_page(_box("s", "text", "Search"))) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("with_login", [True, False])
async def test_drafting_is_told_which_browser_connections_hold_a_login(with_login) -> None:
    """F37: choosing a job's browser actions sees each connection's stored login (field names
    only), so work behind a login can be given browser.auth.login."""

    from uuid import uuid4

    from sqlalchemy.ext.asyncio import AsyncSession

    from audoryn_shared.application.services.capability_autoresolver import (
        SemanticCapabilityResolver,
    )
    from audoryn_shared.domain.ai.providers import AIInvocationContext
    from audoryn_shared.domain.workforce.drafts import CapabilityNeed
    from audoryn_shared.infrastructure.database.models import Integration

    origin = "https://portal.example.com"
    integration = Integration(id=uuid4(), organization_id=uuid4(), provider="browser", display_name="Portal",
                              status="connected",
                              config={"startUrl": f"{origin}/login", "allowedOrigins": origin,
                                      "loginFields": "username,password"})
    seen: dict[str, str] = {}

    class Gateway:
        async def generate_structured(self, *, system, prompt, schema, **_):
            seen.update(system=system, prompt=prompt)
            offered = schema["properties"]["selectedScopes"]["items"]["enum"]
            return {"selectedScopes": [s for s in ("browser.auth.login", "browser.navigation.open") if s in offered]}

    class Session:
        async def scalars(self, statement):
            rows = [integration.id] if with_login else []
            items = rows if "integration_credentials" in str(statement) else [integration]
            return type("Rows", (), {"all": lambda self: items})()

    resolver = SemanticCapabilityResolver(cast(AsyncSession, Session()), cast(object, Gateway()))  # type: ignore[arg-type]
    result = await resolver.resolve(
        organization_id=integration.organization_id,
        needs=[CapabilityNeed(provider="browser", need="Read the orders on the dashboard", actions=["read"])],
        required_browser_origins=(origin,), require_browser_origin_authority=True,
        invocation_context=AIInvocationContext(organization_id=integration.organization_id))

    assert "storedLogins" in seen["system"] and "browser.auth.login" in seen["system"]
    if with_login:
        assert '"storedLogins":[{"connection":"Portal","origins":["https://portal.example.com"],' \
               '"loginFields":["username","password"]}]' in seen["prompt"]
        assert "browser.auth.login" in result.scopes
    else:
        assert '"storedLogins":[]' in seen["prompt"] and '"browser.auth.login"' not in seen["prompt"]


@pytest.mark.asyncio
async def test_a_pages_own_requests_follow_the_blocked_sites_and_private_network_rules(monkeypatch) -> None:
    """F39: scripts, styles, fetch and WebSockets are refused for blocked sites and private
    addresses; other sites a page loads from stay allowed; each host is looked up once."""
    from audoryn_shared.execution.browser import egress

    looked_up: list[str] = []
    real = egress.evaluate_browser_egress

    async def counted(url, *, allow_private_network=False):
        looked_up.append(url)
        return await real(url, allow_private_network=allow_private_network)

    monkeypatch.setattr(egress, "evaluate_browser_egress", counted)
    remembered: dict = {}

    async def refusal(url, *, denied=(), private=False):
        return await egress.resource_refusal(url, denied_origins=denied, allow_private_network=private,
                                             remembered=remembered) or ""

    assert await refusal("data:image/png;base64,AAAA") == ""
    assert "explicitly denied" in await refusal("https://ads.example.com/x.js", denied=("https://ads.example.com",))
    assert "private/internal" in await refusal("http://10.0.0.5/admin")
    assert "cloud metadata" in await refusal("http://169.254.169.254/latest/meta-data/")
    assert "localhost" in await refusal("http://localhost:8080/")
    assert "private/internal" in await refusal("http://10.0.0.5/other")  # remembered
    assert looked_up.count("http://10.0.0.5/admin") == 1 and "http://10.0.0.5/other" not in looked_up
    remembered.clear()
    assert await refusal("http://10.0.0.5/admin", private=True) == ""


@pytest.mark.asyncio
async def test_a_result_lists_the_files_its_run_downloaded() -> None:
    """F41: a run's downloads (not its screenshots) are listed on the result, to open there."""
    from uuid import uuid4

    from audoryn_execution.runtime.managed import ManagedRuntimeExecutor

    download, screenshot = uuid4(), uuid4()
    rows = [(screenshot, {"kind": "browser_screenshot", "name": "page.png"}),
            (download, {"kind": "browser_download", "name": "report.csv"})]

    class Session:
        async def execute(self, statement):
            assert "artifacts.run_id" in str(statement)
            return type("Rows", (), {"all": lambda self: rows})()

    executor = ManagedRuntimeExecutor.__new__(ManagedRuntimeExecutor)
    executor._session = cast(Any, Session())
    assert await executor._run_files(uuid4()) == [(str(download), "report.csv")]


BESTWORTH = ("Using the browser integration Navigate to the bestworthproductsltd.ng. "
             "Read out all products in the product section")


@pytest.mark.asyncio
async def test_a_browser_task_from_chat_runs_as_a_one_time_job(monkeypatch) -> None:
    """F29: the Bestworth request becomes a one-time browser job of the worker that starts now,
    reports to this conversation, carries the attached files (F40), and never goes to native task
    preparation (which asked for a GitHub account)."""
    from types import SimpleNamespace
    from uuid import uuid4

    from app.application.services import conversation_commands as commands
    from app.application.services.conversation_commands import ConversationCommandCompiler
    from audoryn_shared.bootstrap.settings import settings
    from audoryn_shared.domain.conversation.intent import WorkerCommandIntent

    monkeypatch.setattr(settings, "integration_foundation_enabled", True)
    compiler = ConversationCommandCompiler.__new__(ConversationCommandCompiler)
    compiler._session = cast(Any, None)
    worker = SimpleNamespace(id=uuid4(), name="Bestworth Admin")
    seen: dict[str, Any] = {}
    files = [{"artifactId": str(uuid4()), "name": "catalog.pdf", "mediaType": "application/pdf", "sizeBytes": 10}]

    async def resolve_worker(*_):
        return worker

    async def prepare(**kwargs):
        seen["prepare"] = kwargs
        return {**kwargs["arguments"], "workerId": str(worker.id), "name": kwargs["name"]}, ("browser.navigation.open",)

    async def task_files(*_):
        return files

    async def create(_session, _org, _principal, payload):
        seen["job"] = payload
        return SimpleNamespace(id=uuid4(), name=payload["name"])

    async def provision(*args):
        seen["scopes"] = args[-1]

    async def activate(*_):
        return None

    async def queue(_session, _org, _job, _principal, *, trigger):
        seen["trigger"] = trigger
        return SimpleNamespace(id=uuid4(), payload={})

    class NoOutbox:
        def __init__(self, *_):
            raise AssertionError("a browser task must not go to native task preparation")

    compiler._resolve_worker_for_job = resolve_worker  # type: ignore[method-assign]
    compiler._prepare_existing_worker_job_payload = prepare  # type: ignore[method-assign]
    compiler._task_files = task_files  # type: ignore[method-assign]
    for name, value in {"create_job": create, "_provision_managed_job_authority": provision,
                        "job_status_v1": activate, "queue_job": queue, "TransactionalOutbox": NoOutbox}.items():
        monkeypatch.setattr(commands, name, value)
    command = SimpleNamespace(id=uuid4(), payload={}, target_type=None, target_id=None)
    thread = SimpleNamespace(id=uuid4())
    principal = SimpleNamespace(user_id=uuid4(), permissions=frozenset({"jobs.run", "jobs.manage"}))
    receipt = await compiler._on_integration_execute(
        organization_id=uuid4(), principal=cast(Any, principal), thread=cast(Any, thread),
        source_message=cast(Any, SimpleNamespace(id=uuid4(), content=BESTWORTH, artifact_references=[])),
        intent=WorkerCommandIntent(family="integration.execute", arguments={"integrationProvider": "browser"}),
        context={}, command=cast(Any, command))

    assert receipt is not None and receipt.status == "accepted"
    assert "one-time task for Bestworth Admin on bestworthproductsltd.ng" in receipt.message
    assert "GitHub" not in receipt.message
    assert seen["job"]["autonomy"] == {"oneShot": True} and seen["job"]["objective"] == BESTWORTH
    assert seen["scopes"] == ["browser.navigation.open"]
    assert seen["trigger"]["payload"] == {"files": files}
    assert command.target_type == "job"


def test_only_a_browser_provider_routes_to_the_browser() -> None:
    from app.application.services.conversation_commands import _is_browser_task
    from audoryn_shared.domain.conversation.intent import WorkerCommandIntent

    def intent(arguments):
        return WorkerCommandIntent(family="integration.execute", arguments=arguments)

    assert _is_browser_task(intent({"integrationProvider": "browser"}))
    assert _is_browser_task(intent({"provider": "Website"}))
    assert not _is_browser_task(intent({"integrationProvider": "github"}))
    assert not _is_browser_task(intent({}))


@pytest.mark.asyncio
@pytest.mark.parametrize(("instruction", "asks_to_connect"), [
    (BESTWORTH, False), ("Summarise the open issues in my GitHub repository", True)])
async def test_preparation_never_asks_for_an_account_the_person_did_not_name(instruction, asks_to_connect) -> None:
    """F29: with no account of the provider, a request that named it is asked to connect one; a
    request that never named it is asked what to use instead."""
    from types import SimpleNamespace

    from audoryn_shared.application.services.integration_foundation import (
        IntegrationFoundation,
        IntegrationRequirementError,
    )

    need = SimpleNamespace(provider="github", scopes=["github.repository.metadata.read"], resource_hint=None,
                           account_hint=None)
    with pytest.raises(IntegrationRequirementError) as raised:
        await IntegrationFoundation.__new__(IntegrationFoundation)._explain_unresolved(need, [], [], instruction)
    message = str(raised.value)
    assert message.startswith("Connect or share the GitHub account") == asks_to_connect
    assert raised.value.missing == asks_to_connect
    if not asks_to_connect:
        assert "which you didn't mention" in message


@pytest.mark.asyncio
async def test_a_one_time_job_is_queued_only_once(monkeypatch) -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    from fastapi import HTTPException

    from audoryn_shared.api import jobs_routes
    from audoryn_shared.bootstrap.settings import settings

    monkeypatch.setattr(settings, "integration_foundation_enabled", True)

    async def revision(*_):
        return SimpleNamespace(definition={"autonomy": {"oneShot": True}})

    class Session:
        async def scalar(self, _statement):
            return uuid4()  # its one run already exists

    monkeypatch.setattr(jobs_routes, "current_revision", revision)
    principal = SimpleNamespace(permissions=frozenset({"jobs.run"}))
    with pytest.raises(HTTPException) as refused:
        await jobs_routes.queue_job(cast(Any, Session()), uuid4(), cast(Any, SimpleNamespace(id=uuid4())),
                                    cast(Any, principal))
    assert refused.value.status_code == 409


@pytest.mark.asyncio
async def test_files_attached_to_a_request_are_bound_to_its_worker() -> None:
    """F40: an attached file becomes the worker's to upload; one another worker holds is left out."""
    from types import SimpleNamespace
    from uuid import uuid4

    from app.application.services.conversation_commands import ConversationCommandCompiler

    worker, other = uuid4(), uuid4()
    free = SimpleNamespace(id=uuid4(), media_type="text/csv", size_bytes=29, metadata_json={"name": "stock.csv"})
    held = SimpleNamespace(id=uuid4(), media_type="text/csv", size_bytes=5,
                           metadata_json={"name": "other.csv", "workerId": str(other)})

    class Session:
        async def scalars(self, _statement):
            return type("Rows", (), {"all": lambda self: [free, held]})()

    compiler = ConversationCommandCompiler.__new__(ConversationCommandCompiler)
    compiler._session = cast(Any, Session())
    message = SimpleNamespace(artifact_references=[{"id": str(free.id)}, {"id": str(held.id)}, {"id": "bad"}])
    files = await compiler._task_files(uuid4(), cast(Any, SimpleNamespace(id=worker)), cast(Any, message))
    assert files == [{"artifactId": str(free.id), "name": "stock.csv", "mediaType": "text/csv", "sizeBytes": 29}]
    assert free.metadata_json["workerId"] == str(worker) and held.metadata_json["workerId"] == str(other)


def test_a_connection_the_person_set_up_replaces_agentgates_own_for_the_same_site() -> None:
    """F43: AgentGate's managed connection is skipped when the person's own connection covers its
    sites (and the run may use it); otherwise it stays."""
    from types import SimpleNamespace

    from audoryn_execution.runtime.managed import _superseded_managed_browsers

    def connection(config):
        return SimpleNamespace(provider="browser", config=config)

    site = "http://127.0.0.1:8788"
    integrations = {
        "managed": connection({"managedBy": "worker_autonomy", "allowedOrigins": site}),
        "personal": connection({"allowedOrigins": site, "allowPrivateNetwork": "true"}),
        "managed-other": connection({"managedBy": "worker_autonomy", "allowedOrigins": "https://other.example"}),
        "heavy": connection({"allowedOrigins": "https://other.example", "loadVisualResources": "true"}),
    }
    resources = [{"id": key, "provider": "browser", "status": "connected"} for key in integrations]
    assert _superseded_managed_browsers(resources, cast(Any, integrations)) == {"managed"}


@pytest.mark.parametrize(("scope", "output", "said"), [
    ("browser.navigation.open", {"observation": {"title": "Files - Test site", "url": "http://site/files"},
                                 "realtimeEvents": [{}, {}, {}]}, "Opened Files - Test site (http://site/files)."),
    ("browser.file.download", {"downloadArtifact": {"id": "a1", "name": "report.csv", "sizeBytes": 29},
                               "realtimeEvents": [{}]}, "Downloaded report.csv (29 bytes; file id a1)."),
    ("browser.file.upload", {"uploadArtifact": {"name": "stock.csv", "sizeBytes": 45}},
     "Chose stock.csv (45 bytes) in the upload field."),
    ("browser.element.click", {"observation": {"title": "Home", "url": "http://site/"}}, "Now on Home (http://site/)."),
    ("browser.page.read", {"realtimeEvents": [{}, {}]}, None),
])
def test_a_browser_step_says_what_it_did_not_how_many_results(scope, output, said) -> None:
    """F44: a browser step was narrated as "Found 6 results" (its event list counted); a download
    now names its file and id so a later step can use it."""
    from audoryn_execution.application.context.narrator import narrate

    assert narrate(scope, output) == said


def test_a_login_secret_is_never_sent_in_a_web_address() -> None:
    """F45: any request carrying a typed secret in its address, plain or encoded, is refused; a
    form's missing method no longer refuses the login. Names (username, email) are not secrets."""
    from audoryn_execution.execution.browser.runtime import _carries_secret
    from audoryn_shared.execution.browser.credentials import BrowserCredentialBundle

    assert BrowserCredentialBundle.is_secret("password") and BrowserCredentialBundle.is_secret("oneTimeCode")
    assert BrowserCredentialBundle.is_secret("pin") and BrowserCredentialBundle.is_secret("apiToken")
    assert not BrowserCredentialBundle.is_secret("username") and not BrowserCredentialBundle.is_secret("email")
    secrets = {"s3cret pass!", "123456", "ab"}
    assert _carries_secret("https://site/login?user=ada&password=s3cret+pass%21", secrets)
    assert _carries_secret("https://site/verify?code=123456", secrets)
    assert not _carries_secret("https://site/products?page=ab", secrets)  # too short to tell apart
    assert not _carries_secret("https://site/account/ada", secrets)
