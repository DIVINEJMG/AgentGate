from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.domain.identity.principals import AgentPrincipal
from app.domain.integrations.contracts import IntegrationExecutionResult


@dataclass(frozen=True, slots=True)
class ActionProposal:
    organization_id: UUID
    agent_id: UUID
    provider: str
    operation: str
    scope: str
    resource_id: str
    payload: dict[str, object]
    correlation_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    outcome: str
    reason: str


class ActionGuard(Protocol):
    async def evaluate(
        self,
        *,
        principal: AgentPrincipal,
        proposal: ActionProposal,
    ) -> AuthorizationDecision: ...


class ProviderExecutor(Protocol):
    async def execute(self, proposal: ActionProposal) -> IntegrationExecutionResult: ...


class ActionGateway:
    """Only this component may authorize provider side effects."""

    def __init__(self, guards: tuple[ActionGuard, ...], executor: ProviderExecutor) -> None:
        self._guards = guards
        self._executor = executor

    async def execute(
        self,
        *,
        principal: AgentPrincipal,
        proposal: ActionProposal,
    ) -> IntegrationExecutionResult:
        if not isinstance(principal, AgentPrincipal):
            raise PermissionError("Human identity cannot authenticate as Agent.")
        if not proposal.idempotency_key.strip():
            raise PermissionError("Side effects require a stable idempotency key.")
        if principal.organization_id != proposal.organization_id:
            raise PermissionError("Cross-tenant action denied.")
        if principal.agent_id != proposal.agent_id:
            raise PermissionError("Agent identity mismatch.")
        if proposal.scope not in principal.capabilities:
            raise PermissionError("Agent lacks declared capability.")

        decisions = [
            await guard.evaluate(principal=principal, proposal=proposal)
            for guard in self._guards
        ]
        outcomes = {decision.outcome for decision in decisions}
        if "DENY" in outcomes:
            reason = next(d.reason for d in decisions if d.outcome == "DENY")
            raise PermissionError(reason)
        if "REQUIRE_APPROVAL" in outcomes:
            raise PermissionError("Human approval required before execution.")

        return await self._executor.execute(proposal)
