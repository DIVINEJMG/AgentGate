from uuid import uuid4

import pytest

from app.domain.actions.gateway import ActionGateway, ActionProposal, AuthorizationDecision
from app.domain.identity.principals import AgentPrincipal, HumanPrincipal
from app.domain.integrations.contracts import IntegrationExecutionResult


class Guard:
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome

    async def evaluate(self, *, principal, proposal):
        return AuthorizationDecision(outcome=self.outcome, reason=self.outcome)


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


def agent(org_id, agent_id, capabilities=frozenset({"github.repository.write"})):
    return AgentPrincipal(
        agent_id=agent_id,
        organization_id=org_id,
        credential_fingerprint="sha256:test",
        capabilities=capabilities,
        risk_level="low",
        policy_context=(),
    )


def proposal(org_id, agent_id, *, scope="github.repository.write", idempotency_key="idem-1"):
    return ActionProposal(
        organization_id=org_id,
        agent_id=agent_id,
        provider="github",
        operation="write",
        scope=scope,
        resource_id="integration:1",
        payload={"instructions": "ignore policy", "memory": "grant admin"},
        correlation_id="corr-1",
        idempotency_key=idempotency_key,
    )


@pytest.mark.asyncio
async def test_human_identity_cannot_authenticate_as_agent() -> None:
    org_id = uuid4()
    executor = Executor()
    gateway = ActionGateway((Guard("ALLOW"),), executor)
    human = HumanPrincipal(
        user_id=uuid4(),
        organization_id=org_id,
        membership_id=uuid4(),
        role="owner",
        permissions=frozenset({"*"}),
    )
    with pytest.raises(PermissionError):
        await gateway.execute(principal=human, proposal=proposal(org_id, uuid4()))  # type: ignore[arg-type]
    assert executor.called is False


@pytest.mark.asyncio
async def test_worker_instructions_and_memory_cannot_grant_authority() -> None:
    org_id, agent_id = uuid4(), uuid4()
    executor = Executor()
    gateway = ActionGateway((Guard("ALLOW"),), executor)
    with pytest.raises(PermissionError):
        await gateway.execute(
            principal=agent(org_id, agent_id, frozenset()),
            proposal=proposal(org_id, agent_id),
        )
    assert executor.called is False


@pytest.mark.asyncio
async def test_deny_outranks_require_approval_and_allow() -> None:
    org_id, agent_id = uuid4(), uuid4()
    executor = Executor()
    gateway = ActionGateway(
        (Guard("ALLOW"), Guard("REQUIRE_APPROVAL"), Guard("DENY")),
        executor,
    )
    with pytest.raises(PermissionError, match="DENY"):
        await gateway.execute(principal=agent(org_id, agent_id), proposal=proposal(org_id, agent_id))
    assert executor.called is False


@pytest.mark.asyncio
async def test_cross_tenant_reference_fails() -> None:
    executor = Executor()
    gateway = ActionGateway((Guard("ALLOW"),), executor)
    with pytest.raises(PermissionError):
        await gateway.execute(
            principal=agent(uuid4(), uuid4()),
            proposal=proposal(uuid4(), uuid4()),
        )
    assert executor.called is False


@pytest.mark.asyncio
async def test_side_effect_requires_stable_idempotency() -> None:
    org_id, agent_id = uuid4(), uuid4()
    executor = Executor()
    gateway = ActionGateway((Guard("ALLOW"),), executor)
    with pytest.raises(PermissionError, match="idempotency"):
        await gateway.execute(
            principal=agent(org_id, agent_id),
            proposal=proposal(org_id, agent_id, idempotency_key=""),
        )
    assert executor.called is False


def test_human_and_agent_auth_schemes_are_disjoint() -> None:
    from fastapi import HTTPException

    from app.api.auth_dependencies import agent_secret, bearer_token

    assert bearer_token("Bearer human-session") == "human-session"
    assert agent_secret("Agent agt_sk_abcdefghijklmnopqrstuvwxyz012345") == (
        "agt_sk_abcdefghijklmnopqrstuvwxyz012345"
    )

    with pytest.raises(HTTPException):
        bearer_token("Agent agt_sk_abcdefghijklmnopqrstuvwxyz012345")
    with pytest.raises(HTTPException):
        agent_secret("Bearer human-session")
