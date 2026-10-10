from __future__ import annotations


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
