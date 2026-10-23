"""GitHub adapter, with the original contract preserved during adoption."""

from audoryn_shared.execution.providers.native.github.legacy import (
    CAPABILITIES,
    GitHubProvider,
    github_provider,
)


def configured_provider():
    from audoryn_shared.bootstrap.settings import settings

    if settings.github_expanded_enabled and settings.integration_foundation_enabled:
        from audoryn_shared.execution.providers.native.github.provider import ExpandedGitHubProvider

        return ExpandedGitHubProvider()
    return github_provider


__all__ = ["CAPABILITIES", "GitHubProvider", "github_provider"]
