from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import Literal

from app.execution.contracts import ExecutionError

RetryDisposition = Literal["retry", "refresh_credentials", "do_not_retry"]


@dataclass(frozen=True, slots=True)
class RetryDecision:
    disposition: RetryDisposition
    delay_seconds: float
    reason: str


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 30.0
    jitter_ratio: float = 0.25

    def decide(
        self,
        error: ExecutionError,
        *,
        attempt: int,
        random_source: Random | None = None,
    ) -> RetryDecision:
        if attempt >= self.max_attempts:
            return RetryDecision(
                disposition="do_not_retry",
                delay_seconds=0.0,
                reason="Bounded retry limit reached.",
            )

        if error.code == "authentication_error":
            return RetryDecision(
                disposition="refresh_credentials",
                delay_seconds=0.0,
                reason="Credential refresh is required before another attempt.",
            )

        retryable_codes = {
            "rate_limited",
            "temporary_provider_error",
            "timeout",
            "provider_unavailable",
            "navigation_timeout",
            "browser_crash",
        }
        if error.retryable and error.code in retryable_codes:
            delay = min(
                self.max_delay_seconds,
                self.base_delay_seconds * (2 ** max(0, attempt - 1)),
            )
            source = random_source or Random()
            jitter = delay * self.jitter_ratio * source.random()
            return RetryDecision(
                disposition="retry",
                delay_seconds=min(self.max_delay_seconds, delay + jitter),
                reason=f"{error.code} is retryable with bounded exponential backoff.",
            )

        return RetryDecision(
            disposition="do_not_retry",
            delay_seconds=0.0,
            reason=f"{error.code} is not safe to retry automatically.",
        )
