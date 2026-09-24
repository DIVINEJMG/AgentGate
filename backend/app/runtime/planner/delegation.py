from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from app.execution.capability_resolver import AuthorizedCapability


@dataclass(frozen=True, slots=True)
class DelegationRequest:
    organization_id: UUID
    source_worker_id: UUID
    target_worker_id: UUID
    objective: str
    requested_scopes: tuple[str, ...]
    correlation_id: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if self.source_worker_id == self.target_worker_id:
            raise ValueError("A Worker cannot delegate to itself.")
        if not self.objective.strip():
            raise ValueError("Delegation objective is required.")
        if not self.idempotency_key.strip():
            raise ValueError("Delegation requires an idempotency key.")


@dataclass(frozen=True, slots=True)
class GovernedWorkRequest:
    request_id: UUID
    organization_id: UUID
    source_worker_id: UUID
    target_worker_id: UUID
    objective: str
    granted_scopes: tuple[str, ...]
    correlation_id: str
    idempotency_key: str


class WorkerDelegation:
    """Create a fresh governed request; never inherit source authority."""

    def create(
        self,
        request: DelegationRequest,
        *,
        target_authorized_capabilities: tuple[AuthorizedCapability, ...],
    ) -> GovernedWorkRequest:
        target_scopes = {item.scope for item in target_authorized_capabilities}
        missing = [scope for scope in request.requested_scopes if scope not in target_scopes]
        if missing:
            raise PermissionError(
                "Delegation requested authority the target Worker does not independently hold: "
                + ", ".join(sorted(missing))
            )

        return GovernedWorkRequest(
            request_id=uuid4(),
            organization_id=request.organization_id,
            source_worker_id=request.source_worker_id,
            target_worker_id=request.target_worker_id,
            objective=request.objective,
            granted_scopes=tuple(sorted(set(request.requested_scopes))),
            correlation_id=request.correlation_id,
            idempotency_key=request.idempotency_key,
        )
