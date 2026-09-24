from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from app.execution.contracts import (
    ExecutionError,
    ExecutionProviderError,
    ExecutionRequest,
    ExecutionResult,
    VerificationResult,
)
from app.execution.recovery import RecoveryPlan, RecoveryPolicy

LifecycleStage = Literal[
    "observe",
    "locate",
    "plan",
    "policy_check",
    "act",
    "verify",
    "recover",
    "escalate",
    "completed",
]


@dataclass(frozen=True, slots=True)
class LifecycleCheckpoint:
    stage: LifecycleStage
    attempt: int
    detail: str


@dataclass(frozen=True, slots=True)
class LifecycleOutcome:
    result: ExecutionResult | None
    verification: VerificationResult | None
    recovery: RecoveryPlan | None
    error: ExecutionError | None
    checkpoints: tuple[LifecycleCheckpoint, ...]


class ObserveActVerifyLifecycle:
    """Provider-neutral F29 execution lifecycle.

    Policy/approval remains outside this class at ActionGateway. This component starts
    only after a request has crossed that boundary and never grants authority itself.
    """

    def __init__(self, recovery_policy: RecoveryPolicy | None = None) -> None:
        self._recovery = recovery_policy or RecoveryPolicy()

    async def run(
        self,
        *,
        request: ExecutionRequest,
        execute: Callable[[], Awaitable[ExecutionResult]],
        verify: Callable[[ExecutionResult], Awaitable[VerificationResult]],
        policy_allows_fallback: bool = False,
        alternate_kind=None,
    ) -> LifecycleOutcome:
        checkpoints: list[LifecycleCheckpoint] = [
            LifecycleCheckpoint("observe", 0, "Execution context observed."),
            LifecycleCheckpoint("locate", 0, "Authorized resource located."),
            LifecycleCheckpoint("plan", 0, "Provider-neutral execution request prepared."),
            LifecycleCheckpoint("policy_check", 0, "Action Gateway authorization already satisfied."),
        ]

        attempt = 0
        while True:
            attempt += 1
            checkpoints.append(LifecycleCheckpoint("act", attempt, "Provider action executed."))
            try:
                result = await execute()
            except ExecutionProviderError as error:
                recovery = self._recovery.plan(
                    request=request,
                    error=error.error,
                    attempt=attempt,
                    alternate_kind=alternate_kind,
                    policy_allows_fallback=policy_allows_fallback,
                )
                checkpoints.append(
                    LifecycleCheckpoint("recover", attempt, recovery.reason)
                )
                if recovery.action == "retry_same_provider":
                    await asyncio.sleep(recovery.delay_seconds)
                    continue
                checkpoints.append(
                    LifecycleCheckpoint("escalate", attempt, recovery.reason)
                )
                return LifecycleOutcome(
                    result=None,
                    verification=None,
                    recovery=recovery,
                    error=error.error,
                    checkpoints=tuple(checkpoints),
                )

            verification = await verify(result)
            checkpoints.append(
                LifecycleCheckpoint("verify", attempt, verification.summary)
            )
            if verification.verified:
                checkpoints.append(
                    LifecycleCheckpoint("completed", attempt, "Execution verified.")
                )
                return LifecycleOutcome(
                    result=result,
                    verification=verification,
                    recovery=None,
                    error=None,
                    checkpoints=tuple(checkpoints),
                )

            verification_error = ExecutionProviderError(
                code="verification_failed",
                retryable=False,
                provider=result.provider,
                operation=result.operation,
                correlation_id=request.correlation_id,
                safe_message=verification.summary,
            )
            recovery = self._recovery.plan(
                request=request,
                error=verification_error.error,
                attempt=attempt,
                alternate_kind=alternate_kind,
                policy_allows_fallback=policy_allows_fallback,
            )
            checkpoints.append(
                LifecycleCheckpoint("recover", attempt, recovery.reason)
            )
            checkpoints.append(
                LifecycleCheckpoint("escalate", attempt, recovery.reason)
            )
            return LifecycleOutcome(
                result=result,
                verification=verification,
                recovery=recovery,
                error=verification_error.error,
                checkpoints=tuple(checkpoints),
            )
