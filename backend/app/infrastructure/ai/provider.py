from app.domain.ai.providers import ModelProvider, ModelRequest, ModelResponse


class UnconfiguredModelProvider(ModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        raise RuntimeError("No model provider is configured.")
