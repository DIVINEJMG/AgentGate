from __future__ import annotations

from functools import lru_cache

from audoryn_shared.execution.providers.browser import browser_provider
from audoryn_shared.execution.providers.native.github import configured_provider
from audoryn_shared.execution.providers.native.gmail import gmail_provider
from audoryn_shared.execution.providers.native.google_calendar import google_calendar_provider
from audoryn_shared.execution.providers.native.google_drive import google_drive_provider
from audoryn_shared.execution.providers.native.slack import slack_provider
from audoryn_shared.execution.providers.native.web_research import web_research_provider
from audoryn_shared.execution.providers.registry import ProviderRegistry


@lru_cache(maxsize=1)
def execution_provider_registry() -> ProviderRegistry:
    return ProviderRegistry(
        (
            configured_provider(),
            browser_provider,
            web_research_provider,
            gmail_provider,
            slack_provider,
            google_drive_provider,
            google_calendar_provider,
        )
    )
