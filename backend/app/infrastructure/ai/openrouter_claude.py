"""Claude through OpenRouter's chat completions API (used when an Anthropic account can't be
opened). The same choices as the native Claude transport, in OpenRouter's terms:
- routed only to Anthropic itself, with no data collection and no fallback to other hosts;
- structured outputs whenever the schema fits Claude's subset (checked afterwards anyway);
- the system prompt is cached; effort comes from the model profile and thinking is not returned;
- no temperature (Claude doesn't take one below 1, and OpenRouter lists none for these models).
"""

from __future__ import annotations

from typing import Any

from app.infrastructure.ai.anthropic_messages import EFFORTS, claude_schema
from app.infrastructure.ai.connections import wire_model
from app.infrastructure.ai.model_profiles import generation_allowance
from app.infrastructure.ai.openai_chat import ConfiguredChatProvider


class _SameModel(set):
    """OpenRouter may report a dated variant of the requested model (for example
    "anthropic/claude-haiku-5.5-20261007"); that is still the requested model."""

    def __contains__(self, item: object) -> bool:
        return isinstance(item, str) and any(item == name or item.startswith(name + "-") for name in self)


class OpenRouterClaudeProvider(ConfiguredChatProvider):
    def accepted_models(self, model, requested):
        return _SameModel({model, requested})

    def body(self, model, request):
        system = [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}] if request.system else None
        body: dict[str, Any] = {
            "model": wire_model(self.connection, model),
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": request.prompt}],
            "max_tokens": generation_allowance(self.planner_profile, request.max_output_tokens),
            "stream": bool(request.stream),
            "provider": {"only": ["anthropic"], "allow_fallbacks": False, "data_collection": "deny"},
        }
        effort = self.planner_profile.request_parameters.get("reasoning_effort")
        if effort in EFFORTS:
            body["reasoning"] = {"effort": effort, "exclude": True}
        if request.response_format == "json_object":
            schema = claude_schema(request.json_schema)
            if schema is not None:
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": request.schema_name or "decision", "strict": True, "schema": schema}}
        if body["stream"]:
            body["stream_options"] = {"include_usage": True}
        return body
