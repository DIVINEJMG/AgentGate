from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ScheduledDispatch:
    organization_id: UUID
    job_id: UUID
    job_revision_id: UUID
    idempotency_key: str
    correlation_id: str
    scheduled_at: datetime
    payload: dict[str, object]
