from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.execution.contracts import ExecutionError, ExecutionRequest, ProviderKind
from app.execution.retry import RetryDecision, RetryPolicy

RecoveryAction = Literal[
    "retry_same_provider",
    "refresh_credentials",
    "fallback_provider",
    "escalate",
]


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    action: RecoveryAction
    reason: str
    delay_seconds: float = 0.0
    target_kind: ProviderKind | None = None
    requires_human_approval: bool = False


class RecoveryPolicy:
    """Fail closed when recovery could expand authority or execution surface."""

    def __init__(self, retry_policy: RetryPolicy | None = None) -> None:
        self._retry_policy = retry_policy or RetryPolicy()

    def plan(
        self,
        *,
        request: ExecutionRequest,
        error: ExecutionError,
        attempt: int,
        alternate_kind: ProviderKind | None = None,
        policy_allows_fallback: bool = False,
    ) -> RecoveryPlan:
        retry: RetryDecision = self._retry_policy.decide(error, attempt=attempt)
        if retry.disposition == "retry":
            return RecoveryPlan(
                action="retry_same_provider",
                reason=retry.reason,
                delay_seconds=retry.delay_seconds,
            )
        if retry.disposition == "refresh_credentials":
            return RecoveryPlan(
                action="refresh_credentials",
                reason=retry.reason,
            )

        if (
            alternate_kind is not None
            and request.execution_preferences.allow_fallback
            and policy_allows_fallback
        ):
            risky_browser_fallback = (
                alternate_kind == "browser"
                and request.capability.risk in {"high", "critical"}
            )
            if risky_browser_fallback:
                return RecoveryPlan(
                    action="escalate",
                    reason=(
                        "Risky native-to-browser fallback requires an explicit new "
                        "governance decision and may not happen silently."
                    ),
                    target_kind=alternate_kind,
                    requires_human_approval=True,
                )
            return RecoveryPlan(
                action="fallback_provider",
                reason="A policy-approved alternate execution provider is available.",
                target_kind=alternate_kind,
            )

        return RecoveryPlan(
            action="escalate",
            reason="No safe automatic recovery path remains.",
        )
