"""Planner compatibility data; API differences never determine task authority."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.ai.providers import AIProviderError


class PlannerModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_tokens: int = Field(ge=1024)
    generation_tokens: int = Field(default=1800, ge=16)
    output_format: Literal["prompt_json", "json_object", "json_schema"] = "prompt_json"
    streaming: bool = True
    request_parameters: dict[str, object] = Field(default_factory=dict)

    @field_validator("request_parameters")
    @classmethod
    def safe_parameters(cls, value: dict[str, object]) -> dict[str, object]:
        if set(value) - {"reasoning_effort", "chat_template_kwargs", "top_p"}:
            raise ValueError("Only documented reasoning/template/top_p parameters are allowed.")
        effort = value.get("reasoning_effort")
        if effort is not None and (
            not isinstance(effort, str)
            or effort not in {"low", "medium", "high", "max", "minimal", "none"}
        ):
            raise ValueError("Invalid reasoning effort.")
        template = value.get("chat_template_kwargs")
        if template is not None and (
            not isinstance(template, dict)
            or set(template) - {"enable_thinking", "clear_thinking"}
            or any(not isinstance(item, bool) for item in template.values())
        ):
            raise ValueError("Invalid thinking template parameters.")
        if "top_p" in value and (
            not isinstance(value["top_p"], (float, int)) or not 0 < value["top_p"] <= 1
        ):
            raise ValueError("Invalid top_p.")
        return value


# Generation allowance can include private reasoning; final planner JSON stays bounded.
DEFAULT_PROFILES: dict[str, PlannerModelProfile] = {
    "groq:qwen/qwen3.8-27b": PlannerModelProfile(context_tokens=131072, generation_tokens=8192,
        output_format="json_schema", request_parameters={"reasoning_effort": "none"}),
    "hf_featherless:Qwen/Qwen2.5-7B-Instruct": PlannerModelProfile(context_tokens=32768, streaming=False),
    "hf_novita:meta-llama/Llama-3.1-8B-Instruct": PlannerModelProfile(context_tokens=32768, streaming=False),
    "nvidia_nim:moonshotai/kimi-k3": PlannerModelProfile(
        context_tokens=1048576,
        generation_tokens=16384,
        request_parameters={"reasoning_effort": "high"},
    ),
    "nvidia_nim:z-ai/glm-5.3": PlannerModelProfile(
        context_tokens=1048576,
        generation_tokens=16384,
        request_parameters={
            "reasoning_effort": "high",
            "chat_template_kwargs": {"clear_thinking": True},
        },
    ),
    "nvidia_nim:z-ai/glm-5.3-flash": PlannerModelProfile(
        context_tokens=1048576,
        generation_tokens=8192,
        request_parameters={
            "reasoning_effort": "high",
            "chat_template_kwargs": {"clear_thinking": True},
        },
    ),
    "nvidia_nim:nvidia/nemotron-3-ultra-550b-a55b": PlannerModelProfile(
        context_tokens=256000,
        request_parameters={"chat_template_kwargs": {"enable_thinking": False}},
    ),
    "nvidia_nim:poolside/laguna-xs-2.1": PlannerModelProfile(
        context_tokens=262144, generation_tokens=8192
    ),
    "nvidia_nim:openai/gpt-oss-20b": PlannerModelProfile(
        context_tokens=131072, generation_tokens=8192
    ),
    "openrouter:google/gemma-4-26b-a4b-it:free": PlannerModelProfile(
        context_tokens=256000, output_format="json_object"
    ),
    "openrouter:google/gemma-4-31b-it:free": PlannerModelProfile(
        context_tokens=256000, output_format="json_object"
    ),
    "openrouter:nvidia/nemotron-3.5-lightning:free": PlannerModelProfile(context_tokens=1000000),
    "openrouter:nvidia/nemotron-3-super-120b-a12b:free": PlannerModelProfile(
        context_tokens=256000, output_format="json_schema"
    ),
    "openrouter:cohere/north-mini-code:free": PlannerModelProfile(context_tokens=256000),
    "openrouter:thinkingmachines/inkling:free": PlannerModelProfile(context_tokens=1000000),
}


def model_profile(provider: str, model: str) -> PlannerModelProfile:
    from app.bootstrap.settings import settings

    key = f"{provider}:{model}"
    override = settings.smart_planner_model_profiles.get(key)
    if override is not None:
        return override
    profile = DEFAULT_PROFILES.get(key)
    if profile is None:
        raise AIProviderError(
            "configuration_missing",
            "Planner model needs a compatibility profile before execution.",
            retryable=False,
        )
    return profile


def generation_allowance(profile: PlannerModelProfile, final_tokens: int) -> int:
    return max(profile.generation_tokens, final_tokens)


def validate_context(
    profile: PlannerModelProfile, system: str, prompt: str, final_tokens: int
) -> None:
    # UTF-8 bytes are a deliberately conservative token bound, never silent clipping.
    if (
        len((system + prompt).encode()) + generation_allowance(profile, final_tokens)
        > profile.context_tokens
    ):
        raise AIProviderError(
            "context_too_large",
            "Required evidence exceeds this model's safe context bound.",
            retryable=False,
        )


def apply_profile(body: dict, profile: PlannerModelProfile, request) -> None:
    forbidden = {
        "model",
        "models",
        "messages",
        "provider",
        "stream",
        "stream_options",
        "temperature",
        "max_tokens",
        "response_format",
    }
    if forbidden.intersection(profile.request_parameters):
        raise AIProviderError(
            "configuration_missing",
            "Model parameters cannot override planner identity, evidence or routing boundaries.",
            retryable=False,
        )
    body.update(profile.request_parameters)
    body["max_tokens"] = generation_allowance(profile, request.max_output_tokens)
    if request.response_format == "json_object" and profile.output_format != "prompt_json":
        body["response_format"] = {"type": "json_object"}
        if profile.output_format == "json_schema" and request.json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name or "planner_decision",
                    "schema": request.json_schema,
                    "strict": True,
                },
            }
