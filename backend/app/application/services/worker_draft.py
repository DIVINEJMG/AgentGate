from __future__ import annotations

import json
from typing import Any

from app.domain.ai.providers import AIGateway, AIInvocationContext
from app.domain.workforce.drafts import WorkerDraft

_EXPLICIT_APPROVAL_CUES = (
    "ask me",
    "ask before",
    "ask for approval",
    "require approval",
    "requires approval",
    "required approval",
    "my approval",
    "approve before",
    "before i approve",
    "without approval",
    "without asking",
    "confirm with me",
    "my confirmation",
    "my permission",
)


def _explicit_approval_requested(instruction: str) -> bool:
    normalized = " ".join(instruction.lower().split())
    return any(cue in normalized for cue in _EXPLICIT_APPROVAL_CUES)


def _enforce_human_approval_intent(
    draft: WorkerDraft,
    *,
    instruction: str,
) -> WorkerDraft:
    """AI may preserve approval boundaries, but may not invent them.

    Creating a managed Worker is itself the human authorization event for the
    capabilities granted to that Worker. Per-action approval boundaries are kept
    only when the human explicitly asked to be consulted again. Origin restrictions
    remain valid because they narrow authority rather than add an approval prompt.
    """

    if _explicit_approval_requested(instruction):
        return draft

    jobs = []
    for job in draft.initial_jobs:
        boundaries = [
            boundary
            for boundary in job.approval_boundaries
            if boundary.kind == "allowed_origins_only"
        ]
        jobs.append(job.model_copy(update={"approval_boundaries": boundaries}))
    return draft.model_copy(update={"initial_jobs": jobs})


class WorkerDraftGenerator:
    def __init__(self, gateway: AIGateway) -> None:
        self._gateway = gateway

    async def generate(
        self,
        *,
        instruction: str,
        authoritative_context: dict[str, Any],
        invocation_context: AIInvocationContext,
    ) -> WorkerDraft:
        parsed = await self._gateway.generate_structured(
            role="intent",
            system=(
                "Design one Aduoryn digital Worker from the human instruction. "
                "Return only the WorkerDraft schema. Describe capability needs semantically: "
                "name a supported provider and human capability need/actions, but NEVER invent "
                "an Aduoryn capability scope. The capability resolver will select exact scopes "
                "from the live catalog later. Infer schedules only from the human's wording; "
                "if no timing was supplied, use manual. Preserve explicit timezone/time when given. "
                "For phrases such as every morning/every weekday/Friday afternoon, produce a "
                "canonical schedule plus a human_readable description. Approval boundaries are "
                "ONLY for explicit human instructions that require another human decision, such "
                "as 'ask me before submitting', 'never send without my approval', or an explicit "
                "allowed-origin restriction. Do NOT infer an approval boundary merely because an "
                "action writes data, sends externally, submits a form, clicks a checkbox, accepts "
                "terms/privacy language, or is high risk. Never grant authority or connections. "
                "Do not include credentials, passwords, tokens, cookies, or secrets. "
                "Do not invent a supervisor identity; supervisor_name may only repeat a name "
                "the human explicitly supplied."
            ),
            prompt=(
                "AUTHORITATIVE_CONTEXT:\n"
                + json.dumps(authoritative_context, separators=(",", ":"), default=str)
                + "\n\nWORKER_REQUEST:\n"
                + instruction.strip()
            ),
            schema_name="worker_draft_v1",
            schema=WorkerDraft.model_json_schema(),
            context=invocation_context,
            max_output_tokens=3000,
        )
        draft = WorkerDraft.model_validate(parsed)
        return _enforce_human_approval_intent(draft, instruction=instruction)
