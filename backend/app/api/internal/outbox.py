from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.infrastructure.qstash.verifier import QStashSignatureVerifier
from app.runtime.outbox_publisher import drain_outbox_batch
from app.runtime.outbox_trigger import request_outbox_drain

router = APIRouter(prefix="/internal/v1/outbox", tags=["internal-outbox"])


def _verify_qstash(request: Request, body: bytes, signature: str | None) -> None:
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="QStash signature required.",
        )
    try:
        QStashSignatureVerifier().verify(
            body=body.decode("utf-8"),
            signature=signature,
            url=str(request.url),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid QStash signature.",
        ) from exc


@router.post("/drain")
async def drain(
    request: Request,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, object]:
    body = await request.body()
    _verify_qstash(request, body, upstash_signature)

    limit = 100
    published = await drain_outbox_batch(limit=limit)

    continuation_queued = False
    if published >= limit:
        continuation_queued = (
            await request_outbox_drain(reason="continuation") is not None
        )

    return {
        "status": "ok",
        "published": published,
        "continuationQueued": continuation_queued,
    }
