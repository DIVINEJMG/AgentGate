from __future__ import annotations

import base64
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from app.domain.ai.providers import (
    AIInvocationContext,
    AIMediaInput,
    AIProviderError,
)
from app.infrastructure.ai.provider import ai_gateway_from_settings
from app.infrastructure.database.session import session_factory
from app.infrastructure.qstash.verifier import QStashSignatureVerifier

router = APIRouter(prefix="/internal/v1/ai", tags=["internal-ai"])

# 1x1 transparent PNG used only for the protected vision connectivity probe.
_PROBE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


class AIProbePayload(BaseModel):
    role: Literal["planner", "vision"] = "planner"
    organization_id: UUID | None = None


def _verify_qstash(request: Request, raw: bytes, signature: str | None) -> None:
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="QStash signature required.",
        )
    try:
        QStashSignatureVerifier().verify(
            body=raw.decode("utf-8"),
            signature=signature,
            url=str(request.url),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid QStash signature.",
        ) from exc


@router.post("/probe")
async def provider_probe(
    request: Request,
    upstash_signature: str | None = Header(default=None, alias="Upstash-Signature"),
) -> dict[str, object]:
    raw = await request.body()
    _verify_qstash(request, raw, upstash_signature)
    try:
        payload = AIProbePayload.model_validate(json.loads(raw or b"{}"))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid AI probe payload.",
        ) from exc

    context = AIInvocationContext(
        organization_id=payload.organization_id,
        correlation_id="internal-ai-provider-probe",
    )
    async with session_factory() as session:
        gateway = ai_gateway_from_settings(session=session)
        try:
            if payload.role == "vision":
                response = await gateway.analyze_media(
                    role="vision",
                    system="This is a bounded provider connectivity test.",
                    prompt="Reply briefly that the image was received.",
                    media=AIMediaInput(
                        media_type="image/png",
                        data_base64=_PROBE_PNG,
                    ),
                    context=context,
                    max_output_tokens=24,
                )
            else:
                response = await gateway.generate_text(
                    role="planner",
                    system="This is a bounded provider connectivity test.",
                    prompt="Reply with OK only.",
                    context=context,
                    max_output_tokens=16,
                )
        except AIProviderError as exc:
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "status": "unavailable",
                    "category": exc.category,
                    "retryable": exc.retryable,
                },
            ) from exc
        await session.commit()

    return {
        "status": "ok",
        "role": payload.role,
        "provider": response.provider,
        "model": response.model,
        "outputReceived": bool(response.text.strip()),
        "requestIdPresent": bool(response.request_id),
    }
