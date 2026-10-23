from __future__ import annotations

from typing import TYPE_CHECKING


class BrowserElementNotFound(LookupError):
    pass


class BrowserStaleObservation(LookupError):
    pass


class BrowserDetachedFrame(LookupError):
    pass


class BrowserDownloadFailure(RuntimeError):
    pass


class BrowserCrash(RuntimeError):
    pass


class BrowserRuntimeLimitExceeded(RuntimeError):
    pass


class BrowserCapacityUnavailable(RuntimeError):
    """Chromium capacity is temporarily unavailable and the action should be retried."""


class BrowserOwnershipLost(RuntimeError):
    """The local context must stop because another browser owner superseded it."""


if TYPE_CHECKING:
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
else:
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    except ImportError:  # the core image: no Playwright, and no browser runs there to raise these

        class PlaywrightError(Exception):
            pass

        class PlaywrightTimeoutError(PlaywrightError):
            pass
