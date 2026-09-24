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
