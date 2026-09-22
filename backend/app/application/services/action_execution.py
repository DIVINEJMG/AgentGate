from app.domain.actions.gateway import ActionGateway, ActionProposal
from app.domain.identity.principals import AgentPrincipal
from app.domain.integrations.contracts import IntegrationExecutionResult


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
