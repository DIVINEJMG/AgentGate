from __future__ import annotations

from datetime import UTC, datetime
from random import Random
from uuid import uuid4

import pytest

from app.execution.contracts import (
    CapabilityDescriptor,
    ExecutionError,
    ExecutionProviderError,
    ExecutionRequest,
    ExecutionResult,
    ResourceDescriptor,
    VerificationResult,
)
from app.execution.lifecycle import ObserveActVerifyLifecycle
from app.execution.providers.browser import BrowserExecutionProvider
from app.execution.providers.extensible import (
    CustomExecutionProvider,
    MCPExecutionProvider,
)
from app.execution.recovery import RecoveryPolicy
from app.execution.retry import RetryPolicy


def _request(*, risk: str = "low") -> ExecutionRequest:
    capability = CapabilityDescriptor(
        scope="test.resource.write",
        provider="test",
        resource_type="resource",
        operation="resource.write",
        mode="write",
        risk=risk,  # type: ignore[arg-type]
        requires_credential=False,
        side_effect=True,
        approval_recommendation="required",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        description="Write a test resource.",
        target="resource",
    )
    resource = ResourceDescriptor(
        id="resource:test",
        provider="test",
        resource_type="resource",
        external_id="test",
        display_name="Test resource",
        metadata={},
        health="healthy",
        available_capabilities=(capability.scope,),
    )
    return ExecutionRequest(
        organization_id=uuid4(),
        worker_id=uuid4(),
        agent_id=uuid4(),
        job_id=uuid4(),
        work_item_id=uuid4(),
        run_id=uuid4(),
        capability=capability,
        resource=resource,
        operation=capability.operation,
        input={},
        correlation_id="corr-f29-21-30",
        idempotency_key="idem-f29-21-30",
    )


def _error(code: str, *, retryable: bool) -> ExecutionError:
    return ExecutionError(
        code=code,  # type: ignore[arg-type]
        retryable=retryable,
        provider="test",
        operation="resource.write",
        correlation_id="corr-f29-21-30",
        safe_message=code,
    )


def _success(request: ExecutionRequest) -> ExecutionResult:
    return ExecutionResult.successful(
        provider=request.resource.provider,
        adapter="native_api",
        adapter_version="1.0.0",
        operation=request.operation,
        output={"id": "ok"},
        provider_request_id="req-1",
        started_at=datetime.now(UTC),
    )


def test_f29_retry_policy_uses_bounded_jittered_backoff() -> None:
    policy = RetryPolicy(max_attempts=3, base_delay_seconds=2.0, jitter_ratio=0.25)
    decision = policy.decide(
        _error("rate_limited", retryable=True),
        attempt=1,
        random_source=Random(7),
    )

    assert decision.disposition == "retry"
    assert 2.0 <= decision.delay_seconds <= 2.5

    exhausted = policy.decide(
        _error("timeout", retryable=True),
        attempt=3,
        random_source=Random(7),
    )
    assert exhausted.disposition == "do_not_retry"


def test_f29_retry_policy_never_retries_authorization_or_validation() -> None:
    policy = RetryPolicy()

    assert policy.decide(
        _error("authorization_error", retryable=False),
        attempt=1,
    ).disposition == "do_not_retry"
    assert policy.decide(
        _error("validation_error", retryable=False),
        attempt=1,
    ).disposition == "do_not_retry"
    assert policy.decide(
        _error("authentication_error", retryable=False),
        attempt=1,
    ).disposition == "refresh_credentials"


def test_f29_risky_browser_fallback_requires_new_governance_decision() -> None:
    request = _request(risk="high")
    plan = RecoveryPolicy().plan(
        request=request,
        error=_error("authorization_error", retryable=False),
        attempt=1,
        alternate_kind="browser",
        policy_allows_fallback=True,
    )

    assert plan.action == "escalate"
    assert plan.target_kind == "browser"
    assert plan.requires_human_approval is True


def test_f29_safe_policy_approved_fallback_can_be_selected() -> None:
    request = _request(risk="low")
    plan = RecoveryPolicy().plan(
        request=request,
        error=_error("authorization_error", retryable=False),
        attempt=1,
        alternate_kind="browser",
        policy_allows_fallback=True,
    )

    assert plan.action == "fallback_provider"
    assert plan.target_kind == "browser"
    assert plan.requires_human_approval is False


@pytest.mark.asyncio
async def test_f29_observe_act_verify_retries_then_completes() -> None:
    request = _request()
    attempts = 0

    async def execute() -> ExecutionResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ExecutionProviderError(
                code="temporary_provider_error",
                retryable=True,
                provider="test",
                operation=request.operation,
                correlation_id=request.correlation_id,
                safe_message="temporary",
            )
        return _success(request)

    async def verify(result: ExecutionResult) -> VerificationResult:
        return VerificationResult(
            verified=result.status == "success",
            summary="Verified expected provider state.",
        )

    lifecycle = ObserveActVerifyLifecycle(
        RecoveryPolicy(RetryPolicy(base_delay_seconds=0.0, jitter_ratio=0.0))
    )
    outcome = await lifecycle.run(
        request=request,
        execute=execute,
        verify=verify,
    )

    assert attempts == 2
    assert outcome.result is not None
    assert outcome.verification is not None
    assert outcome.verification.verified is True
    assert outcome.error is None
    assert [item.stage for item in outcome.checkpoints].count("act") == 2
    assert outcome.checkpoints[-1].stage == "completed"


@pytest.mark.asyncio
async def test_f29_verification_failure_escalates_instead_of_claiming_success() -> None:
    request = _request()

    async def execute() -> ExecutionResult:
        return _success(request)

    async def verify(_: ExecutionResult) -> VerificationResult:
        return VerificationResult(
            verified=False,
            summary="Expected state was not observed.",
        )

    outcome = await ObserveActVerifyLifecycle().run(
        request=request,
        execute=execute,
        verify=verify,
    )

    assert outcome.verification is not None
    assert outcome.verification.verified is False
    assert outcome.error is not None
    assert outcome.error.code == "verification_failed"
    assert outcome.recovery is not None
    assert outcome.recovery.action == "escalate"


def test_f29_browser_provider_contract_keeps_governed_provider_surface() -> None:
    required = {
        "discover_resources",
        "discover_capabilities",
        "check_health",
        "discover_permissions",
        "normalize_input",
        "open_session",
        "discover_page_capabilities",
        "observe",
        "execute",
        "verify",
        "recover",
    }
    assert required.issubset(set(dir(BrowserExecutionProvider)))


def test_f29_custom_and_mcp_contracts_keep_universal_provider_surface() -> None:
    universal = {
        "discover_resources",
        "discover_capabilities",
        "check_health",
        "discover_permissions",
        "normalize_input",
        "execute",
        "verify",
    }
    assert universal.issubset(set(dir(CustomExecutionProvider)))
    assert {"discover_tools", "normalize_tool"}.issubset(set(dir(CustomExecutionProvider)))
    assert {"connect", "close"}.issubset(set(dir(MCPExecutionProvider)))
