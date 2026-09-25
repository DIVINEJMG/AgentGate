from __future__ import annotations

import json
from typing import Any

from app.domain.ai.providers import AIGateway, AIInvocationContext
from app.domain.workforce.drafts import WorkerDraft


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
                "canonical schedule plus a human_readable description. Use approval boundaries "
                "for instructions such as never submit without asking, external sends requiring "
                "approval, or allowed-origin restrictions. Never grant authority or connections. "
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
        return WorkerDraft.model_validate(parsed)
