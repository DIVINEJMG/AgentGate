from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

AIModelRole = Literal[
    "conversation",
    "intent",
    "planner",
    "vision",
    "embedding",
    "reranker",
    "safety",
]
AIErrorCategory = Literal[
    "provider_unavailable",
    "rate_limited",
    "authentication_failed",
    "model_not_found",
    "invalid_provider_response",
    "context_too_large",
    "timeout",
    "cancelled",
    "content_rejected",
    "configuration_missing",
    "quota_exhausted",
]
AIResponseFormat = Literal["text", "json_object"]


@dataclass(frozen=True, slots=True)
class ModelProviderCapabilities:
    text_input: bool = True
    image_input: bool = False
    structured_json: bool = False
    tool_calls: bool = False
    streaming: bool = False
    reasoning_output: bool = False
    context_window_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class AIInvocationContext:
    organization_id: UUID | None = None
    worker_id: UUID | None = None
    job_id: UUID | None = None
    run_id: UUID | None = None
    thread_id: UUID | None = None
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class AITextRequest:
    system: str
    prompt: str
    temperature: float = 0.0
    max_output_tokens: int = 1800
    response_format: AIResponseFormat = "text"
    stream: bool = False
    correlation_id: str | None = None
    json_schema: dict[str, object] | None = None
    schema_name: str | None = None


@dataclass(frozen=True, slots=True)
class AIMediaInput:
    media_type: str
    url: str | None = None
    data_base64: str | None = None


@dataclass(frozen=True, slots=True)
class AIResponse:
    text: str
    provider: str
    model: str
    request_id: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


class AIProviderError(RuntimeError):
    def __init__(
        self,
        category: AIErrorCategory,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
        account_scoped: bool = False,
        organization_scoped: bool = False,
        rejection_reason: str | None = None,
        budget_scope: str | None = None,
        model_called: bool | None = None,
        validation_rule: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category: AIErrorCategory = category
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.account_scoped = account_scoped
        self.organization_scoped = organization_scoped
        self.budget_scope = budget_scope if budget_scope in {
            "organization_tokens", "organization_requests", "complex_tokens", "complex_requests",
        } else None
        self.model_called = model_called
        self.validation_rule = validation_rule if validation_rule in {
            "execution_assertion", "outcome_assertion", "preservation_assertion",
            "capability_assertion", "absence_assertion", "chat_action_payload",
        } else None
        self.validation_exhausted = False
        # Logs accept fixed diagnostic codes, never provider bodies or model prose.
        self.rejection_reason = rejection_reason if rejection_reason in {
            "empty_output", "truncated_output", "malformed_json", "invalid_envelope",
            "schema_failure", "unsupported_claim", "unknown_fact", "model_mismatch",
            "incomplete_output", "proposal_validation",
        } else None


class AIModelProvider(Protocol):
    name: str
    capabilities: ModelProviderCapabilities

    async def generate_text(self, *, model: str, request: AITextRequest) -> AIResponse: ...

    async def analyze_media(
        self,
        *,
        model: str,
        request: AITextRequest,
        media: AIMediaInput,
    ) -> AIResponse: ...


class AIInvocationRecorder(Protocol):
    async def start(
        self,
        *,
        context: AIInvocationContext,
        role: AIModelRole,
        provider: str,
        model: str,
        schema_name: str | None,
    ) -> UUID: ...

    async def finish(
        self,
        invocation_id: UUID,
        *,
        latency_ms: int,
        success: bool,
        request_id: str | None,
        usage: dict[str, int],
        error_category: AIErrorCategory | None,
    ) -> None: ...


class AIGateway(Protocol):
    async def generate_text(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.0,
        stream: bool = False,
    ) -> AIResponse: ...

    async def generate_structured(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        schema_name: str,
        schema: dict[str, object],
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> dict[str, object]: ...

    async def analyze_media(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        media: AIMediaInput,
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
    ) -> AIResponse: ...
