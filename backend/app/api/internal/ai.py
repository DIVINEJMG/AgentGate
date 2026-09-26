from __future__ import annotations

import json
import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from app.application.services.worker_draft import WorkerDraftGenerator
from app.domain.ai.providers import (
    AIInvocationContext,
    AIMediaInput,
    AIProviderError,
)
from app.runtime.planner.adaptive import AdaptiveRuntimePlanner
from app.infrastructure.ai.provider import ai_gateway_from_settings
from app.infrastructure.database.session import session_factory
from app.infrastructure.qstash.verifier import QStashSignatureVerifier

router = APIRouter(prefix="/internal/v1/ai", tags=["internal-ai"])
logger = logging.getLogger(__name__)

# 1x1 transparent PNG used only for the protected vision connectivity probe.
_PROBE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


class AIProbePayload(BaseModel):
    role: Literal["planner", "vision"] = "planner"
    mode: Literal["text", "structured", "worker_draft", "planner_decision"] = "text"
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
    structured: dict[str, object] | None = None
    probe_result: dict[str, object] | None = None
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
            elif payload.mode == "structured":
                structured = await gateway.generate_structured(
                    role="planner",
                    system=(
                        "This is a bounded provider connectivity test. "
                        "Return only the requested JSON object."
                    ),
                    prompt='Return {"status":"ok"} exactly.',
                    schema_name="provider_structured_probe_v1",
                    schema={
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "status": {"type": "string", "const": "ok"}
                        },
                        "required": ["status"],
                    },
                    context=context,
                    max_output_tokens=64,
                )
                response = None
            elif payload.mode == "worker_draft":
                draft = await WorkerDraftGenerator(gateway).generate(
                    instruction=(
                        "Create a worker named Scout. Scout's job is to visit "
                        "https://www.nvidia.com/en-us/ and identify the main headline "
                        "or featured announcement on the page. Summarize what it finds "
                        "in 3 short bullet points and save the result for me. Run this "
                        "job every day at 9:00 AM in Africa/Lagos time. This worker is "
                        "read-only: it must not log in, submit forms, make purchases, "
                        "send messages, or change anything without asking me first."
                    ),
                    authoritative_context={
                        "workers": [],
                        "supportedProviders": ["browser"],
                        "supportedCapabilityNeeds": [
                            "open a public webpage",
                            "read page content",
                            "scroll a page",
                        ],
                    },
                    invocation_context=context,
                )
                probe_result = {
                    "draftValid": True,
                    "suggestedName": draft.suggested_name,
                    "jobCount": len(draft.initial_jobs),
                    "scheduleKind": draft.initial_jobs[0].schedule.kind,
                }
                response = None
            elif payload.mode == "planner_decision":
                decision = await AdaptiveRuntimePlanner(gateway).choose_next(
                    job={
                        "name": "NVIDIA Daily Headline Monitor",
                        "objective": (
                            "Visit NVIDIA homepage, identify the main headline or "
                            "featured announcement, summarize it in three bullets."
                        ),
                        "completionCriteria": [
                            "Main headline identified",
                            "Three bullet summary produced",
                        ],
                    },
                    worker={
                        "name": "Scout",
                        "charter": "Read-only web research worker.",
                    },
                    trigger={"type": "manual"},
                    tools=[
                        {
                            "resourceId": "browser-probe",
                            "scope": "browser.navigation.open",
                            "provider": "browser",
                            "operation": "open",
                            "defaultStartUrl": "https://www.nvidia.com/en-us/",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"url": {"type": "string"}},
                                "required": ["url"],
                            },
                        }
                    ],
                    observations=[],
                    action_count=0,
                    max_actions=4,
                    invocation_context=context,
                )
                probe_result = {
                    "decisionValid": True,
                    "decision": decision.decision,
                    "scope": decision.scope,
                    "resourceId": decision.resource_id,
                }
                response = None
            else:
                response = await gateway.generate_text(
                    role="planner",
                    system="This is a bounded provider connectivity test.",
                    prompt="Reply with OK only.",
                    context=context,
                    max_output_tokens=16,
                )
        except AIProviderError as exc:
            logger.warning(
                "AI provider probe failed role=%s mode=%s category=%s retryable=%s status_code=%s",
                payload.role,
                payload.mode,
                exc.category,
                exc.retryable,
                exc.status_code,
            )
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

    if payload.mode == "structured":
        return {
            "status": "ok",
            "role": payload.role,
            "mode": payload.mode,
            "structuredOutputValid": structured is not None and structured.get("status") == "ok",
        }
    if payload.mode in {"worker_draft", "planner_decision"}:
        return {
            "status": "ok",
            "role": payload.role,
            "mode": payload.mode,
            **(probe_result or {}),
        }
    assert response is not None
    return {
        "status": "ok",
        "role": payload.role,
        "mode": payload.mode,
        "provider": response.provider,
        "model": response.model,
        "outputReceived": bool(response.text.strip()),
        "requestIdPresent": bool(response.request_id),
    }
