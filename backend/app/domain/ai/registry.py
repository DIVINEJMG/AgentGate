from __future__ import annotations

from dataclasses import dataclass

from app.domain.ai.providers import AIModelRole


@dataclass(frozen=True, slots=True)
class ModelRoute:
    role: AIModelRole
    provider: str
    model: str


class ModelRegistry:
    def __init__(self, routes: list[ModelRoute]) -> None:
        self._routes = {route.role: route for route in routes}

    def route(self, role: AIModelRole) -> ModelRoute:
        try:
            return self._routes[role]
        except KeyError as exc:
            raise KeyError(f"No AI model is configured for role {role}.") from exc

    def as_dict(self) -> dict[str, dict[str, str]]:
        return {
            role: {"provider": route.provider, "model": route.model}
            for role, route in self._routes.items()
        }
