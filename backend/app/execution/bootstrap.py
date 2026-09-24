from __future__ import annotations

from functools import lru_cache

from app.execution.providers.browser import browser_provider
from app.execution.providers.native.github import github_provider
from app.execution.providers.native.gmail import gmail_provider
from app.execution.providers.native.google_calendar import google_calendar_provider
from app.execution.providers.native.google_drive import google_drive_provider
from app.execution.providers.native.slack import slack_provider
from app.execution.providers.registry import ProviderRegistry


@lru_cache(maxsize=1)
def execution_provider_registry() -> ProviderRegistry:
    return ProviderRegistry(
        (
            github_provider,
            browser_provider,
            gmail_provider,
            slack_provider,
            google_drive_provider,
            google_calendar_provider,
        )
    )
