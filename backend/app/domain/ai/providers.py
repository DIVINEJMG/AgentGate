from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ModelRequest:
    system: str
    prompt: str
    temperature: float = 0.0


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    provider: str
    model: str


class ModelProvider(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse: ...
