from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.actions.gateway import ActionGateway, AuthorizationDecision
from app.domain.identity.principals import AgentPrincipal
from app.domain.integrations.contracts import IntegrationExecutionResult
from app.execution.authorization import (
    CredentialReference,
    ExecutionAuthorizationSnapshot,
    ProviderPermissionSnapshot,
    UniversalActionRequest,
)
from app.execution.contracts import CapabilityDescriptor, ExecutionRequest, ResourceDescriptor
from app.realtime.bus import RedisRealtimeBus
from app.realtime.contracts import REALTIME_EVENT_TYPES, RealtimeEvent


def _capability() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        scope="github.repository.issues.create",
        provider="github",
        resource_type="repository",
        operation="issues.create",
        mode="write",
        risk="high",
        requires_credential=True,
        side_effect=True,
        approval_recommendation="required",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        description="Create an issue.",
        target="repository",
    )


def _resource(scope: str) -> ResourceDescriptor:
    return ResourceDescriptor(
        id=f"integration:{uuid4()}",
        provider="github",
        resource_type="repository",
        external_id="repo-a",
        display_name="Repository A",
        metadata={},
        health="healthy",
        available_capabilities=(scope,),
    )


def _universal(*, allowed: bool = True) -> UniversalActionRequest:
    capability = _capability()
    resource = _resource(capability.scope)
    execution = ExecutionRequest(
        organization_id=uuid4(),
        worker_id=uuid4(),
        agent_id=uuid4(),
        job_id=uuid4(),
        work_item_id=uuid4(),
        run_id=uuid4(),
        capability=capability,
        resource=resource,
        operation=capability.operation,
        input={"title": "Test"},
        correlation_id="corr-f29",
        idempotency_key="idem-f29",
    )
    return UniversalActionRequest(
        execution=execution,
        provider_permissions=ProviderPermissionSnapshot(
            provider="github",
            resource_id=resource.id,
            capability_scopes=(capability.scope,) if allowed else (),
            credential=CredentialReference(
                strategy="api_token",
                reference="secret://integration-credential/credential-1",
            ),
            checked_at=datetime.now(UTC),
            adapter="native_api",
            adapter_version="1.0.0",
        ),
    )


class AllowGuard:
    async def evaluate(self, *, principal, proposal):
        return AuthorizationDecision("ALLOW", "matched allow policy")


class ApprovalGuard:
    async def evaluate(self, *, principal, proposal):
        return AuthorizationDecision("REQUIRE_APPROVAL", "write requires approval")


class RecordingExecutor:
    def __init__(self) -> None:
        self.snapshot: ExecutionAuthorizationSnapshot | None = None

    async def execute_request(self, request, snapshot):
        self.snapshot = snapshot
        return IntegrationExecutionResult(
            provider_operation=request.execution.operation,
            summary="ok",
            data={"authorization": snapshot.as_dict()},
        )

    async def prepare(self, proposal):
        raise AssertionError("not used")

    async def execute(self, proposal):
        raise AssertionError("legacy execution must not be used")


def _principal(request: UniversalActionRequest) -> AgentPrincipal:
    execution = request.execution
    return AgentPrincipal(
        agent_id=execution.agent_id,
        organization_id=execution.organization_id,
        credential_fingerprint="fp",
        capabilities=frozenset({execution.capability.scope}),
        risk_level="high",
        policy_context=(),
    )


@pytest.mark.asyncio
async def test_f29_action_gateway_authorizes_capability_and_resource_intersection() -> None:
    request = _universal()
    executor = RecordingExecutor()
    gateway = ActionGateway((AllowGuard(),), executor)

    result = await gateway.execute_request(
        principal=_principal(request),
        request=request,
    )

    assert result.summary == "ok"
    assert executor.snapshot is not None
    assert executor.snapshot.capability_scope == request.execution.capability.scope
    assert executor.snapshot.resource_id == request.execution.resource.id
    assert executor.snapshot.provider_permissions == (
        request.execution.capability.scope,
    )
    assert executor.snapshot.credential_reference is not None
    assert executor.snapshot.credential_reference.startswith("secret://")


@pytest.mark.asyncio
async def test_f29_resource_bound_authorization_fails_closed() -> None:
    request = _universal(allowed=False)
    gateway = ActionGateway((AllowGuard(),), RecordingExecutor())

    with pytest.raises(PermissionError, match="does not permit"):
        await gateway.execute_request(
            principal=_principal(request),
            request=request,
        )


@pytest.mark.asyncio
async def test_f29_approval_is_required_before_execution() -> None:
    request = _universal()
    gateway = ActionGateway((ApprovalGuard(),), RecordingExecutor())

    with pytest.raises(PermissionError, match="Human approval required"):
        await gateway.execute_request(
            principal=_principal(request),
            request=request,
        )


def test_f29_credential_reference_never_accepts_raw_secret() -> None:
    with pytest.raises(ValueError, match="secret://"):
        CredentialReference(strategy="api_token", reference="ghp_raw_secret")


class FakeRedis:
    def __init__(self) -> None:
        self.xadds: list[tuple[str, dict[str, str]]] = []
        self.publishes: list[tuple[str, str]] = []

    async def xadd(self, key, fields, **kwargs):
        self.xadds.append((key, fields))
        return "1-0"

    async def publish(self, channel, payload):
        self.publishes.append((channel, payload))
        return 1

    async def xread(self, streams, count, block):
        return []

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_f29_realtime_bus_uses_streams_and_pubsub_fast_lane() -> None:
    redis = FakeRedis()
    bus = RedisRealtimeBus(redis)  # type: ignore[arg-type]
    organization_id = uuid4()
    worker_id = uuid4()
    run_id = uuid4()

    stream_id = await bus.emit(
        RealtimeEvent(
            event_type="run.progress",
            organization_id=organization_id,
            worker_id=worker_id,
            run_id=run_id,
            correlation_id="corr-live",
            payload={"progress": 45},
        )
    )

    assert stream_id == "1-0"
    keys = [item[0] for item in redis.xadds]
    assert f"audoryn:events:{organization_id}" in keys
    assert f"audoryn:runs:{run_id}" in keys
    assert f"audoryn:worker:{worker_id}" in keys
    assert redis.publishes[0][0] == f"audoryn:realtime:{organization_id}"


def test_f29_realtime_event_catalog_matches_contract() -> None:
    expected = {
        "run.created",
        "run.started",
        "run.progress",
        "run.step.started",
        "run.step.completed",
        "run.waiting_approval",
        "run.failed",
        "run.completed",
        "result.created",
        "result.updated",
        "worker.status.changed",
        "action.proposed",
        "action.approved",
        "action.blocked",
        "action.executed",
        "approval.created",
        "approval.decided",
        "incident.created",
        "incident.updated",
        "integration.health.changed",
    }
    assert expected <= REALTIME_EVENT_TYPES


def test_f29_websocket_and_sse_routes_are_defined() -> None:
    from app.api.realtime_routes import v2_router, ws_router

    websocket_paths = {
        str(route.path)  # type: ignore[attr-defined]
        for route in ws_router.routes
        if hasattr(route, "path")
    }
    sse_paths = {
        str(route.path)  # type: ignore[attr-defined]
        for route in v2_router.routes
        if hasattr(route, "path")
    }

    assert "/ws/v1/organizations/{organization_id}" in websocket_paths
    assert "/organizations/{organization_id}/events/stream" in sse_paths
