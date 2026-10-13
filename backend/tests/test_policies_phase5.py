"""Improvement plan, phase 5: policies in people's terms, every decision explained, a simulator,
and starting policies where irreversible actions ask unless the request asked for them."""
from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import governance_routes
from app.api.governance_routes import _matches, explain_decision
from app.api.jobs_routes import _provision_managed_job_authority
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.database import models as m
from tests.test_approvals_phase4 import Approvals
from tests.test_efficiency_queries import ORG

POLICY = {"selectors": {"providers": ["github"], "resourceNames": ["acme/*"]}}


@pytest.mark.parametrize(("provider", "name", "expected"), [
    ("github", "acme/api", True),
    ("GitHub", "ACME/Web", True),          # case does not matter
    ("github", "other/api", False),
    ("slack", "acme/api", False),
])
def test_policies_can_target_providers_and_resource_name_patterns(provider: str, name: str, expected: bool) -> None:
    assert _matches(POLICY, agent_id="a", resource_id="r", action="x", scope="s", risk="low",
                    provider=provider, resource_name=name) is expected


def test_policies_without_the_new_selectors_match_as_before() -> None:
    assert _matches({"selectors": {"scopes": ["s"]}}, agent_id="a", resource_id="r", action="x", scope="s", risk="low")


REQUEST = {"agentName": "Ada", "resourceName": "acme/api", "action": "merge"}


@pytest.mark.parametrize(("decision", "expected"), [
    ({"outcome": "REQUIRE_APPROVAL", "request": REQUEST, "winningPolicy": {"name": "Review merges", "priority": 5},
      "matchedPolicies": [{"priority": 5}]}, "Needs a person's approval because of the policy “Review merges”."),
    ({"outcome": "DENY", "request": REQUEST, "winningPolicy": {"name": "No merges", "priority": 5},
      "matchedPolicies": [{"priority": 5}, {"priority": 5}]}, "Blocked by the policy “No merges”. Several policies"),
    ({"outcome": "DENY", "request": REQUEST, "winningPolicy": None, "preconditions": {
        "agentActive": True, "resourceLive": True, "scopeAvailable": True, "scopeDeclared": True}},
     "No policy allows Ada to merge on acme/api, so it is blocked by default."),
    ({"outcome": "DENY", "request": REQUEST, "winningPolicy": None, "preconditions": {
        "agentActive": False, "resourceLive": True, "scopeAvailable": True, "scopeDeclared": True}},
     "Ada is paused or not active"),
    ({"outcome": "DENY", "request": {**REQUEST, "resourceName": None}, "winningPolicy": None, "preconditions": {}},
     "The resource isn't connected or visible to Ada"),
])
def test_every_decision_is_explained_in_plain_words(decision: dict[str, Any], expected: str) -> None:
    assert explain_decision(decision).startswith(expected)


async def _simulate(approvals: Approvals, payload: dict[str, Any]) -> dict[str, Any]:
    from scripts.runtime_benchmark import BenchSession, _aware_sqlite_datetimes

    with _aware_sqlite_datetimes():
        session = BenchSession(Session(approvals.db.engine, expire_on_commit=False))
        return await governance_routes._simulate(cast(Any, session), ORG, approvals.principal("policies.read"), payload)


@pytest.mark.asyncio
async def test_the_simulator_answers_what_would_happen_with_and_without_a_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    approvals = Approvals(monkeypatch)
    ask = {"agentId": str(approvals.agent.id), "resourceId": str(approvals.integration.id), "scope": "bench.record.write"}
    now = await _simulate(approvals, ask)
    assert now["decision"]["outcome"] == "REQUIRE_APPROVAL" and now["withDraft"] is False
    allow = await _simulate(approvals, {**ask, "draftPolicy": {"name": "Writes are fine", "effect": "allow", "priority": 9,
                                                               "selectors": {"providers": ["bench"], "resourceNames": ["bench*"]}}})
    assert allow["decision"]["outcome"] == "ALLOW" and allow["explanation"] == "Allowed by the policy “Writes are fine”."
    with Session(approvals.db.engine) as session:  # nothing was saved
        assert session.scalar(select(m.Policy).where(m.Policy.name == "Writes are fine")) is None
    with pytest.raises(HTTPException, match="Choose a worker"):
        await _simulate(approvals, {"scope": "bench.record.write"})


@pytest.mark.asyncio
@pytest.mark.parametrize(("explicit", "submit_effect"), [(set(), "require_approval"), ({"browser.form.submit"}, "allow")])
async def test_irreversible_actions_ask_unless_the_request_asked_for_them(
    monkeypatch: pytest.MonkeyPatch, explicit: set[str], submit_effect: str,
) -> None:
    from scripts.runtime_benchmark import BenchSession, _aware_sqlite_datetimes

    approvals = Approvals(monkeypatch)
    worker = approvals.db.add(m.Worker, organization_id=ORG, agent_identity_id=approvals.agent.id, name="Ada",
                              status="active", profile={"agentIdentityProvisioning": "automatic"})
    principal = HumanPrincipal(user_id=approvals.human.id, organization_id=ORG, membership_id=uuid4(), role="owner",
                               permissions=frozenset())
    with _aware_sqlite_datetimes():
        session = BenchSession(Session(approvals.db.engine, expire_on_commit=False))
        await _provision_managed_job_authority(cast(Any, session), ORG, cast(Any, session.sync_session.get(m.Worker, worker.id)),
            principal, ["browser.page.read", "browser.element.type", "browser.form.submit"],
            standing_approval=True, explicit_irreversible=explicit)
    with Session(approvals.db.engine) as session:
        effects: dict[str, str] = {}
        for policy in session.scalars(select(m.Policy).where(m.Policy.name.startswith("Managed Runtime"), m.Policy.status == "enabled")):
            revision = session.scalar(select(m.PolicyRevision).where(m.PolicyRevision.policy_id == policy.id))
            assert revision is not None
            effects.update({scope: revision.effect for scope in revision.selectors["scopes"]})
    assert effects["browser.page.read"] == "allow" and effects["browser.element.type"] == "allow"
    assert effects["browser.form.submit"] == submit_effect
