"""Explicit pre-expansion provider fixtures, independent of an operator's .env."""
from audoryn_shared.execution.bootstrap import execution_provider_registry
from audoryn_shared.execution.providers.native.github import github_provider
from audoryn_shared.execution.providers.registry import ProviderRegistry


def legacy_registry() -> ProviderRegistry:
    return ProviderRegistry(
        github_provider if provider.manifest.provider == "github" else provider
        for provider in execution_provider_registry().providers()
    )
