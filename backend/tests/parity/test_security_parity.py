import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.domain.actions.gateway import ActionGateway, ActionProposal, AuthorizationDecision
from app.domain.identity.principals import AgentPrincipal
from app.domain.integrations.contracts import IntegrationExecutionResult
from app.domain.policies.precedence import PolicyCandidate, resolve_policy_precedence

_CASES = json.loads((Path(__file__).with_name("cases.json")).read_text(encoding="utf-8"))


class AllowGuard:
    async def evaluate(self, *, principal, proposal):
        return AuthorizationDecision(outcome="ALLOW", reason="allowed")


class Executor:
    def __init__(self) -> None:
        self.called = False

    async def execute(self, proposal):
        self.called = True
        return IntegrationExecutionResult(
            provider_operation=proposal.operation,
            summary="executed",
            data={},
        )


def _case(case_id: str) -> dict[str, str]:
    return next(case for case in _CASES if case["id"] == case_id)


@pytest.mark.asyncio
async def test_missing_capability_matches_typescript_default_deny() -> None:
    contract = _case("missing-capability-default-deny")
    org_id, agent_id = uuid4(), uuid4()
    executor = Executor()
    gateway = ActionGateway((AllowGuard(),), executor)
    principal = AgentPrincipal(
        agent_id=agent_id,
        organization_id=org_id,
        credential_fingerprint="sha256:test",
        capabilities=frozenset(),
        risk_level="low",
        policy_context=(),
    )
    proposal = ActionProposal(
        organization_id=org_id,
        agent_id=agent_id,
        provider="github",
        operation="write",
        scope="github.repository.write",
        resource_id="integration:1",
        payload={},
        correlation_id="parity-1",
        idempotency_key="parity-1",
    )
    with pytest.raises(PermissionError):
        await gateway.execute(principal=principal, proposal=proposal)
    assert contract["expected"] == "DENY"
    assert executor.called is False


def test_same_priority_deny_precedence_matches_typescript() -> None:
    contract = _case("same-priority-allow-vs-deny")
    outcome = resolve_policy_precedence(
        (
            PolicyCandidate(outcome="ALLOW", priority=100),
            PolicyCandidate(outcome="DENY", priority=100),
        )
    )
    assert outcome == contract["expected"] == "DENY"


def test_same_priority_approval_precedence_matches_typescript() -> None:
    contract = _case("same-priority-approval-vs-allow")
    outcome = resolve_policy_precedence(
        (
            PolicyCandidate(outcome="ALLOW", priority=100),
            PolicyCandidate(outcome="REQUIRE_APPROVAL", priority=100),
        )
    )
    assert outcome == contract["expected"] == "REQUIRE_APPROVAL"


def test_no_policy_defaults_to_deny_like_typescript() -> None:
    contract = _case("no-matching-policy-default-deny")
    assert resolve_policy_precedence(()) == contract["expected"] == "DENY"
