from audoryn_shared.domain.actions.gateway import ActionGateway, ActionProposal
from audoryn_shared.domain.identity.principals import AgentPrincipal
from audoryn_shared.domain.integrations.contracts import IntegrationExecutionResult


class ActionExecutionService:
    def __init__(self, gateway: ActionGateway) -> None:
        self._gateway = gateway

    async def execute(
        self,
        *,
        principal: AgentPrincipal,
        proposal: ActionProposal,
    ) -> IntegrationExecutionResult:
        return await self._gateway.execute(principal=principal, proposal=proposal)
