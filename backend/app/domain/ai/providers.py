from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class ModelRequest:
    system: str
    prompt: str
    temperature: float = 0.0
    max_output_tokens: int = 1800
    response_format: Literal["text", "json_object", "json_schema"] = "text"
    schema_name: str = "response"
    json_schema: dict[str, object] | None = None
    reasoning_effort: Literal["none", "low", "medium", "high"] = "low"


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    provider: str
    model: str


class ModelProvider(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse: ...
