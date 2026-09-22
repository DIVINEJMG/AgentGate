import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.domain.jobs.dispatch import ScheduledDispatch
from app.infrastructure.database.dispatch import WorkItemDispatchRepository
from app.infrastructure.database.session import session_factory
from app.infrastructure.qstash.verifier import QStashSignatureVerifier

router = APIRouter(prefix="/internal/v1/runtime", tags=["internal-runtime"])


class DispatchPayload(BaseModel):
    organization_id: UUID
    job_id: UUID
    job_revision_id: UUID
    scheduled_at: datetime
    correlation_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=180)
    payload: dict[str, object] = Field(default_factory=dict)

    def to_dispatch(self) -> ScheduledDispatch:
        return ScheduledDispatch(
            organization_id=self.organization_id,
            job_id=self.job_id,
            job_revision_id=self.job_revision_id,
            idempotency_key=self.idempotency_key,
            correlation_id=self.correlation_id,
            scheduled_at=self.scheduled_at,
            payload=self.payload,
        )


@router.post("/dispatch")
async def dispatch(
    request: Request,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, str]:
    if not upstash_signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="QStash signature required.",
        )

    raw = await request.body()
    try:
        QStashSignatureVerifier().verify(
            body=raw.decode("utf-8"),
            signature=upstash_signature,
            url=str(request.url),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid QStash signature.",
        ) from exc

    try:
        message = DispatchPayload.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid dispatch payload.",
        ) from exc

    async with session_factory() as session:
        repo = WorkItemDispatchRepository(session)
        item = await repo.create(message.to_dispatch())
        await session.commit()

    return {"status": "accepted", "workItemId": str(item.id)}
