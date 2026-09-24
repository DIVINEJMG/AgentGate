from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from app.execution.capability_resolver import AuthorizedCapability


@dataclass(frozen=True, slots=True)
class PlanStep:
    index: int
    capability_scope: str
    resource_id: str
    provider: str
    operation: str
    depends_on: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class MultiProviderPlan:
    plan_id: UUID
    organization_id: UUID
    worker_id: UUID
    objective: str
    steps: tuple[PlanStep, ...]
    correlation_id: str

    @classmethod
    def build(
        cls,
        *,
        organization_id: UUID,
        worker_id: UUID,
        objective: str,
        requested_scopes: tuple[str, ...],
        available: tuple[AuthorizedCapability, ...],
        correlation_id: str,
    ) -> MultiProviderPlan:
        by_scope = {item.scope: item for item in available}
        steps: list[PlanStep] = []
        for index, scope in enumerate(requested_scopes, start=1):
            capability = by_scope.get(scope)
            if capability is None:
                raise PermissionError(
                    f"Planner requested capability that is not authorized: {scope}"
                )
            steps.append(
                PlanStep(
                    index=index,
                    capability_scope=scope,
                    resource_id=capability.resource.id,
                    provider=capability.capability.provider,
                    operation=capability.capability.operation,
                    depends_on=(index - 1,) if index > 1 else (),
                )
            )
        return cls(
            plan_id=uuid4(),
            organization_id=organization_id,
            worker_id=worker_id,
            objective=objective,
            steps=tuple(steps),
            correlation_id=correlation_id,
        )
