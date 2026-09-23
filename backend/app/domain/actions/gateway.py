from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.domain.identity.principals import AgentPrincipal
from app.domain.integrations.contracts import IntegrationExecutionResult
from app.execution.authorization import (
    ExecutionAuthorizationSnapshot,
    UniversalActionRequest,
    build_authorization_snapshot,
)


@dataclass(frozen=True, slots=True)
class ActionProposal:
    """F28/F29 compatibility proposal.

    New execution should be prepared into UniversalActionRequest before authorization.
    """

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


@runtime_checkable
class ProviderExecutor(Protocol):
    async def execute(self, proposal: ActionProposal) -> IntegrationExecutionResult: ...


@runtime_checkable
class UniversalProviderExecutorProtocol(ProviderExecutor, Protocol):
    async def prepare(self, proposal: ActionProposal) -> UniversalActionRequest: ...

    async def execute_request(
        self,
        request: UniversalActionRequest,
        snapshot: ExecutionAuthorizationSnapshot,
    ) -> IntegrationExecutionResult: ...


class ActionGateway:
    """The mandatory authorization boundary for every provider execution.

    F29.11 makes UniversalActionRequest the canonical path. The execute method
    remains as a compatibility shim for older F28 callers and is upgraded through
    ProviderExecutor.prepare when the executor supports the universal contract.
    """

    def __init__(self, guards: tuple[ActionGuard, ...], executor: ProviderExecutor) -> None:
        self._guards = guards
        self._executor = executor

    @staticmethod
    def _validate_principal(
        *,
        principal: AgentPrincipal,
        organization_id: UUID,
        agent_id: UUID,
        capability_scope: str,
        idempotency_key: str,
    ) -> None:
        if not isinstance(principal, AgentPrincipal):
            raise PermissionError("Human identity cannot authenticate as Agent.")
        if not idempotency_key.strip():
            raise PermissionError("Side effects require a stable idempotency key.")
        if principal.organization_id != organization_id:
            raise PermissionError("Cross-tenant action denied.")
        if principal.agent_id != agent_id:
            raise PermissionError("Agent identity mismatch.")
        if capability_scope not in principal.capabilities:
            raise PermissionError("Agent lacks declared capability.")

    async def _evaluate_guards(
        self,
        *,
        principal: AgentPrincipal,
        proposal: ActionProposal,
    ) -> tuple[str, str]:
        decisions = [
            await guard.evaluate(principal=principal, proposal=proposal)
            for guard in self._guards
        ]
        if not decisions:
            return ("ALLOW", "No additional gateway guards configured.")
        outcomes = {decision.outcome for decision in decisions}
        if "DENY" in outcomes:
            decision = next(item for item in decisions if item.outcome == "DENY")
            return ("DENY", decision.reason)
        if "REQUIRE_APPROVAL" in outcomes:
            decision = next(item for item in decisions if item.outcome == "REQUIRE_APPROVAL")
            return ("REQUIRE_APPROVAL", decision.reason)
        return ("ALLOW", "; ".join(item.reason for item in decisions if item.reason) or "Allowed.")

    async def execute_request(
        self,
        *,
        principal: AgentPrincipal,
        request: UniversalActionRequest,
    ) -> IntegrationExecutionResult:
        execution = request.execution
        self._validate_principal(
            principal=principal,
            organization_id=execution.organization_id,
            agent_id=execution.agent_id,
            capability_scope=execution.capability.scope,
            idempotency_key=execution.idempotency_key,
        )

        permissions = request.provider_permissions
        if permissions.resource_id != execution.resource.id:
            raise PermissionError("Provider permission snapshot is bound to another resource.")
        if permissions.provider != execution.resource.provider:
            raise PermissionError("Provider permission snapshot is bound to another provider.")
        if not permissions.allows(execution.capability.scope):
            raise PermissionError("Provider credential does not permit this capability on the resource.")
        if execution.capability.requires_credential and permissions.credential.reference is None:
            raise PermissionError("Capability requires an external credential.")

        proposal = ActionProposal(
            organization_id=execution.organization_id,
            agent_id=execution.agent_id,
            provider=execution.resource.provider,
            operation=execution.operation,
            scope=execution.capability.scope,
            resource_id=execution.resource.id,
            payload=execution.input,
            correlation_id=execution.correlation_id,
            idempotency_key=execution.idempotency_key,
        )
        outcome, reason = await self._evaluate_guards(
            principal=principal,
            proposal=proposal,
        )
        if outcome == "DENY":
            raise PermissionError(reason)
        if outcome == "REQUIRE_APPROVAL" and request.approval_status != "approved":
            raise PermissionError("Human approval required before execution.")

        snapshot = build_authorization_snapshot(
            request=request,
            risk=principal.risk_level,
            policy_outcome=outcome,
            policy_reason=reason,
            adapter=permissions.adapter,
            adapter_version=permissions.adapter_version,
        )
        if not isinstance(self._executor, UniversalProviderExecutorProtocol):
            raise TypeError("Universal Action Gateway requires a universal provider executor.")
        return await self._executor.execute_request(request, snapshot)

    async def execute(
        self,
        *,
        principal: AgentPrincipal,
        proposal: ActionProposal,
    ) -> IntegrationExecutionResult:
        self._validate_principal(
            principal=principal,
            organization_id=proposal.organization_id,
            agent_id=proposal.agent_id,
            capability_scope=proposal.scope,
            idempotency_key=proposal.idempotency_key,
        )

        if isinstance(self._executor, UniversalProviderExecutorProtocol):
            universal_request = await self._executor.prepare(proposal)
            return await self.execute_request(
                principal=principal,
                request=universal_request,
            )

        outcome, reason = await self._evaluate_guards(
            principal=principal,
            proposal=proposal,
        )
        if outcome == "DENY":
            raise PermissionError(reason)
        if outcome == "REQUIRE_APPROVAL":
            raise PermissionError("Human approval required before execution.")
        return await self._executor.execute(proposal)
