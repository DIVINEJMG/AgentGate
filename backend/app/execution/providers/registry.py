from __future__ import annotations

from collections.abc import Iterable

from app.execution.contracts import ProviderKind, ProviderManifest
from app.execution.providers.base import ExecutionProvider


class ProviderRegistry:
    def __init__(self, providers: Iterable[ExecutionProvider] = ()) -> None:
        self._providers: dict[str, ExecutionProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: ExecutionProvider) -> None:
        provider_id = provider.manifest.provider
        if provider_id in self._providers:
            raise ValueError(f"Provider already registered: {provider_id}")
        self._providers[provider_id] = provider

    def get(self, provider: str) -> ExecutionProvider:
        try:
            return self._providers[provider]
        except KeyError as error:
            raise KeyError(f"Unknown execution provider: {provider}") from error

    def providers(
        self,
        *,
        kind: ProviderKind | None = None,
        enabled_only: bool = True,
    ) -> tuple[ExecutionProvider, ...]:
        items = []
        for provider in self._providers.values():
            manifest = provider.manifest
            if enabled_only and not manifest.enabled:
                continue
            if kind is not None and manifest.kind != kind:
                continue
            items.append(provider)
        return tuple(sorted(items, key=lambda item: item.manifest.provider))

    def manifests(
        self,
        *,
        kind: ProviderKind | None = None,
        enabled_only: bool = True,
    ) -> tuple[ProviderManifest, ...]:
        return tuple(
            provider.manifest
            for provider in self.providers(kind=kind, enabled_only=enabled_only)
        )

    def capability_providers(self, scope: str) -> tuple[ExecutionProvider, ...]:
        return tuple(
            provider
            for provider in self.providers()
            if any(
                capability.scope == scope
                for capability in provider.manifest.capabilities
            )
        )
