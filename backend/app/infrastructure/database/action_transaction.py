from collections.abc import Mapping
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import Action, Approval, AuditEvent
from app.infrastructure.database.outbox import TransactionalOutbox


class ActionApprovalTransaction:
    """Atomic Action + optional Approval + Audit + Outbox write."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._outbox = TransactionalOutbox(session)

    async def record(
        self,
        *,
        organization_id: UUID,
        agent_id: UUID,
        resource_id: str,
        scope: str,
        idempotency_key: str,
        payload: Mapping[str, object],
        correlation_id: str,
        require_approval: bool,
    ) -> tuple[Action, Approval | None]:
        action = Action(
            organization_id=organization_id,
            agent_id=agent_id,
            run_id=None,
            status="pending_approval" if require_approval else "authorized",
            resource_id=resource_id,
            scope=scope,
            idempotency_key=idempotency_key,
            payload=dict(payload),
        )
        self._session.add(action)
        await self._session.flush()

        approval: Approval | None = None
        if require_approval:
            approval = Approval(
                organization_id=organization_id,
                action_id=action.id,
                status="pending",
                decided_by=None,
                decision_reason=None,
            )
            self._session.add(approval)
            await self._session.flush()

        audit = AuditEvent(
            id=uuid4(),
            organization_id=organization_id,
            event_type="action.proposed",
            category="authorization",
            severity="info",
            correlation_id=correlation_id,
            actor={"type": "agent", "id": str(agent_id)},
            resource={"type": "action", "id": str(action.id)},
            payload={
                "scope": scope,
                "resource_id": resource_id,
                "approval_required": require_approval,
            },
        )
        self._session.add(audit)
        await self._outbox.enqueue(
            topic="action.proposed",
            aggregate_type="action",
            aggregate_id=str(action.id),
            payload={
                "organization_id": str(organization_id),
                "action_id": str(action.id),
                "correlation_id": correlation_id,
                "approval_required": require_approval,
            },
        )
        if approval is not None:
            await self._outbox.enqueue(
                topic="approval.created",
                aggregate_type="approval",
                aggregate_id=str(approval.id),
                payload={
                    "organization_id": str(organization_id),
                    "approval_id": str(approval.id),
                    "action_id": str(action.id),
                    "correlation_id": correlation_id,
                },
            )
        await self._session.flush()
        return action, approval
