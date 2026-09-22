from uuid import uuid4

import pytest

from app.domain.actions.gateway import (
    ActionGateway,
    ActionProposal,
    AuthorizationDecision,
)
from app.domain.identity.principals import AgentPrincipal, HumanPrincipal
from app.domain.integrations.contracts import IntegrationExecutionResult


class AllowGuard:
    async def evaluate(self, *, principal, proposal):
        return AuthorizationDecision(outcome="ALLOW", reason="allowed")


class DenyGuard:
    async def evaluate(self, *, principal, proposal):
        return AuthorizationDecision(outcome="DENY", reason="blocked")


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


def agent(org_id, agent_id):
    return AgentPrincipal(
        agent_id=agent_id,
        organization_id=org_id,
        credential_fingerprint="sha256:test",
        capabilities=frozenset({"github.repository.write"}),
        risk_level="low",
        policy_context=frozendict(),
    )


def test_human_and_agent_principals_are_distinct_types() -> None:
    assert HumanPrincipal is not AgentPrincipal


@pytest.mark.asyncio
async def test_action_gateway_blocks_missing_capability() -> None:
    org_id, agent_id = uuid4(), uuid4()
    executor = Executor()
    gateway = ActionGateway((AllowGuard(),), executor)
    principal = agent(org_id, agent_id)
    proposal = ActionProposal(
        organization_id=org_id,
        agent_id=agent_id,
        provider="github",
        operation="write",
        scope="gmail.message.send",
        resource_id="integration:1",
        payload={},
        correlation_id="corr-1",
        idempotency_key="idem-1",
    )
    with pytest.raises(PermissionError):
        await gateway.execute(principal=principal, proposal=proposal)
    assert executor.called is False


@pytest.mark.asyncio
async def test_action_gateway_denies_before_provider_execution() -> None:
    org_id, agent_id = uuid4(), uuid4()
    executor = Executor()
    gateway = ActionGateway((DenyGuard(),), executor)
    proposal = ActionProposal(
        organization_id=org_id,
        agent_id=agent_id,
        provider="github",
        operation="write",
        scope="github.repository.write",
        resource_id="integration:1",
        payload={},
        correlation_id="corr-1",
        idempotency_key="idem-1",
    )
    with pytest.raises(PermissionError):
        await gateway.execute(principal=agent(org_id, agent_id), proposal=proposal)
    assert executor.called is False
