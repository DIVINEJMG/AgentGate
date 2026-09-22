from app.domain.integrations.contracts import IntegrationAdapter


class IntegrationRegistry:
    def __init__(self, adapters: list[IntegrationAdapter]) -> None:
        self._adapters = {adapter.provider: adapter for adapter in adapters}

    def get(self, provider: str) -> IntegrationAdapter:
        try:
            return self._adapters[provider]
        except KeyError as exc:
            raise KeyError(f"Unknown integration provider: {provider}") from exc

    def providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))
