"""GitHub adapter, with the original contract preserved during adoption."""

from .legacy import CAPABILITIES, GitHubProvider, github_provider


def configured_provider():
    from app.bootstrap.settings import settings

    if settings.github_expanded_enabled and settings.integration_foundation_enabled:
        from .provider import ExpandedGitHubProvider

        return ExpandedGitHubProvider()
    return github_provider


__all__ = ["CAPABILITIES", "GitHubProvider", "github_provider"]
