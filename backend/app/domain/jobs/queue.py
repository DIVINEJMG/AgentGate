from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ClaimedWorkItem:
    id: UUID
    organization_id: UUID
    job_id: UUID
    job_revision_id: UUID
    correlation_id: str
    payload: dict[str, object]
    scheduled_at: datetime


class WorkItemQueue(Protocol):
    async def list_due_ids(self, *, limit: int = 100) -> list[UUID]: ...
    async def claim_next(self) -> ClaimedWorkItem | None: ...
    async def checkpoint_succeeded(self, work_item_id: UUID) -> None: ...
    async def checkpoint_failed(self, work_item_id: UUID, *, reason: str) -> None: ...
