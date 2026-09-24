from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.api.internal.outbox import _verify_qstash as verify_outbox_qstash
from app.api.realtime_routes import _ensure_organization_access
from app.domain.identity.errors import AuthorizationError
from app.domain.identity.principals import HumanPrincipal
from app.environment.model import (
    EnvironmentSnapshot,
    EnvironmentSystem,
    ResourceRelationship,
)
from app.execution.authorization import CredentialReference, ProviderPermissionSnapshot
from app.execution.bootstrap import execution_provider_registry
from app.execution.capability_resolver import CapabilityResolver
from app.execution.contracts import (
    CapabilityDescriptor,
    ExecutionRequest,
    ExecutionResult,
    ResourceDescriptor,
)
from app.execution.provenance import audit_provenance_payload, provenance_from_action_payload
from app.execution.providers.base import ExecutionProvider
from app.execution.providers.native.http import ProviderTransportError
from app.observability.metrics import (
    CAPABILITY_SUCCESS,
    EVENT_DELIVERY_LATENCY,
    EXECUTION_RETRY,
    PROVIDER_EXECUTION_LATENCY,
    ExecutionMetrics,
    MetricPoint,
)
from app.realtime.bus import RedisRealtimeBus
from app.realtime.contracts import RealtimeEvent
from app.runtime.planner.contracts import MultiProviderPlan
from app.runtime.planner.delegation import DelegationRequest, WorkerDelegation


def _resource(provider: str, capability: CapabilityDescriptor) -> ResourceDescriptor:
    return ResourceDescriptor(
        id=f"integration:{provider}",
        provider=provider,
        resource_type=capability.resource_type,
        external_id=f"{provider}-resource",
        display_name=f"{provider} resource",
        metadata={},
        health="healthy",
        available_capabilities=(capability.scope,),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider",
    execution_provider_registry().providers(),
    ids=lambda provider: provider.manifest.provider,
)
async def test_f29_provider_conformance_suite(provider: ExecutionProvider) -> None:
    manifest = provider.manifest
    assert manifest.provider
    assert manifest.version
    assert manifest.kind == "native_api"
    assert manifest.capabilities

    capabilities = await provider.discover_capabilities(resource=None)
    assert capabilities == manifest.capabilities
    assert all(item.risk in {"low", "medium", "high", "critical"} for item in capabilities)
    assert all(item.mode in {"read", "write", "action"} for item in capabilities)
    assert all(item.provider == manifest.provider for item in capabilities)

    capability = capabilities[0]
    resource = _resource(manifest.provider, capability)
    credential_reference = (
        None
        if manifest.credential_strategy == "none"
        else "secret://conformance/provider-credential"
    )
    permissions = await provider.discover_permissions(
        resource=resource,
        configuration={},
        credential="conformance-secret" if capability.requires_credential else None,
        credential_reference=credential_reference,
    )
    assert permissions.provider == manifest.provider
    assert permissions.resource_id == resource.id
    if not capability.requires_credential:
        assert capability.scope in permissions.capability_scopes

    result = ExecutionResult.successful(
        provider=manifest.provider,
        adapter=manifest.kind,
        adapter_version=manifest.version,
        operation=capability.operation,
        output={"ok": True},
        provider_request_id="conformance-request",
        started_at=datetime.now(UTC),
    )
    request = ExecutionRequest(
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
        correlation_id="f29-provider-conformance",
        idempotency_key=f"f29-provider-{manifest.provider}",
    )
    verification = await provider.verify(
        request=request,
        result=result,
        configuration={},
        credential=None,
    )
    assert verification.verified is True

    native_error = provider._transport_error(  # type: ignore[attr-defined]
        ProviderTransportError(
            code="rate_limited",
            retryable=True,
            safe_message="rate limited",
        ),
        operation=capability.operation,
        correlation_id=request.correlation_id,
    )
    assert native_error.error.code == "rate_limited"
    assert native_error.error.retryable is True
    assert native_error.error.provider == manifest.provider


class FakeRedis:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.values: dict[str, str] = {}
        self.publishes: list[tuple[str, str]] = []
        self.counter = 0

    async def set(self, key, value, nx=False, ex=None):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = str(value)
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)
        return 1

    async def xadd(self, key, fields, **kwargs):
        del kwargs
        self.counter += 1
        stream_id = f"{self.counter}-0"
        self.streams.setdefault(key, []).append((stream_id, dict(fields)))
        return stream_id

    async def xread(self, streams, count, block):
        del block
        key, after = next(iter(streams.items()))
        rows = self.streams.get(key, [])
        if after == "$":
            return []
        after_num = int(str(after).split("-", 1)[0])
        messages = [
            (stream_id, fields)
            for stream_id, fields in rows
            if int(stream_id.split("-", 1)[0]) > after_num
        ][:count]
        return [(key, messages)] if messages else []

    async def publish(self, channel, payload):
        self.publishes.append((channel, payload))
        return 1

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_f29_realtime_conformance_dedup_order_replay_and_org_isolation() -> None:
    redis = FakeRedis()
    bus = RedisRealtimeBus(redis)  # type: ignore[arg-type]
    org_a = uuid4()
    org_b = uuid4()

    first = RealtimeEvent(
        event_id="stable-outbox-id",
        event_type="run.started",
        organization_id=org_a,
        correlation_id="corr-a",
        payload={"sequence": 1},
    )
    second = RealtimeEvent(
        event_id="second-event",
        event_type="run.progress",
        organization_id=org_a,
        correlation_id="corr-a",
        payload={"sequence": 2},
    )

    first_id = await bus.emit(first)
    duplicate_id = await bus.emit(first)
    second_id = await bus.emit(second)

    assert first_id == duplicate_id
    assert first_id == "1-0"
    assert second_id == "2-0"
    assert len(redis.streams[bus.organization_stream(org_a)]) == 2
    assert bus.organization_stream(org_b) not in redis.streams

    replay = await bus.read(
        organization_id=org_a,
        after=first_id,
        count=10,
    )
    assert [item["event_id"] for item in replay] == ["second-event"]
    assert [item["stream_id"] for item in replay] == ["2-0"]


class RecordingSink:
    def __init__(self) -> None:
        self.points: list[MetricPoint] = []

    async def record(self, point: MetricPoint) -> None:
        self.points.append(point)


@pytest.mark.asyncio
async def test_f29_observability_preserves_correlation_id() -> None:
    sink = RecordingSink()
    metrics = ExecutionMetrics(sink)
    await metrics.execution(
        provider="github",
        capability="github.repository.issues.create",
        adapter="native_api",
        correlation_id="corr-metrics",
        latency_ms=42,
        verified=True,
        retries=2,
        fallback_used=False,
    )
    await metrics.realtime_delivery(
        event_type="result.created",
        correlation_id="corr-metrics",
        latency_ms=3.5,
    )

    names = {point.name for point in sink.points}
    assert PROVIDER_EXECUTION_LATENCY in names
    assert CAPABILITY_SUCCESS in names
    assert EXECUTION_RETRY in names
    assert EVENT_DELIVERY_LATENCY in names
    assert all(point.labels["correlation_id"] == "corr-metrics" for point in sink.points)


def _authorization_fixture():
    registry = execution_provider_registry()
    github = registry.get("github")
    slack = registry.get("slack")
    github_cap = github.manifest.capabilities[0]
    slack_cap = slack.manifest.capabilities[0]
    github_resource = _resource("github", github_cap)
    slack_resource = _resource("slack", slack_cap)
    permissions = (
        ProviderPermissionSnapshot(
            provider="github",
            resource_id=github_resource.id,
            capability_scopes=(github_cap.scope,),
            credential=CredentialReference(
                strategy=github.manifest.credential_strategy,
                reference="secret://conformance/github",
            ),
            checked_at=datetime.now(UTC),
            adapter=github.manifest.kind,
            adapter_version=github.manifest.version,
        ),
        ProviderPermissionSnapshot(
            provider="slack",
            resource_id=slack_resource.id,
            capability_scopes=(slack_cap.scope,),
            credential=CredentialReference(
                strategy=slack.manifest.credential_strategy,
                reference="secret://conformance/slack",
            ),
            checked_at=datetime.now(UTC),
            adapter=slack.manifest.kind,
            adapter_version=slack.manifest.version,
        ),
    )
    return github_cap, slack_cap, github_resource, slack_resource, permissions


def test_f29_planner_capability_query_filters_authority_and_controls() -> None:
    github_cap, slack_cap, github_resource, slack_resource, permissions = (
        _authorization_fixture()
    )
    resolver = CapabilityResolver(execution_provider_registry())
    authorized = resolver.resolve(
        worker_scopes=frozenset({github_cap.scope, slack_cap.scope}),
        resources=(github_resource, slack_resource),
        permission_snapshots=permissions,
        policy_outcomes={
            (github_resource.id, github_cap.scope): "ALLOW",
            (slack_resource.id, slack_cap.scope): "ALLOW",
        },
        suspended_resources=frozenset({slack_resource.id}),
    )

    assert [item.scope for item in authorized] == [github_cap.scope]
    assert authorized[0].resource.id == github_resource.id


def test_f29_multi_provider_plan_uses_authorized_capabilities_only() -> None:
    github_cap, slack_cap, github_resource, slack_resource, permissions = (
        _authorization_fixture()
    )
    resolver = CapabilityResolver(execution_provider_registry())
    authorized = resolver.resolve(
        worker_scopes=frozenset({github_cap.scope, slack_cap.scope}),
        resources=(github_resource, slack_resource),
        permission_snapshots=permissions,
        policy_outcomes={
            (github_resource.id, github_cap.scope): "ALLOW",
            (slack_resource.id, slack_cap.scope): "ALLOW",
        },
    )

    plan = MultiProviderPlan.build(
        organization_id=uuid4(),
        worker_id=uuid4(),
        objective="Use two providers",
        requested_scopes=(github_cap.scope, slack_cap.scope),
        available=authorized,
        correlation_id="corr-plan",
    )

    assert len(plan.steps) == 2
    assert {step.provider for step in plan.steps} == {"github", "slack"}
    assert plan.steps[1].depends_on == (1,)


def test_f29_worker_delegation_never_inherits_source_authority() -> None:
    github_cap, slack_cap, github_resource, slack_resource, permissions = (
        _authorization_fixture()
    )
    resolver = CapabilityResolver(execution_provider_registry())
    target_authority = resolver.resolve(
        worker_scopes=frozenset({slack_cap.scope}),
        resources=(github_resource, slack_resource),
        permission_snapshots=permissions,
        policy_outcomes={
            (github_resource.id, github_cap.scope): "ALLOW",
            (slack_resource.id, slack_cap.scope): "ALLOW",
        },
    )
    request = DelegationRequest(
        organization_id=uuid4(),
        source_worker_id=uuid4(),
        target_worker_id=uuid4(),
        objective="Ask the target Worker to act.",
        requested_scopes=(github_cap.scope,),
        correlation_id="corr-delegation",
        idempotency_key="delegation-1",
    )

    with pytest.raises(PermissionError, match="does not independently hold"):
        WorkerDelegation().create(
            request,
            target_authorized_capabilities=target_authority,
        )


def test_f29_environment_model_foundation_contains_canonical_entities() -> None:
    github_cap, _, github_resource, _, permissions = _authorization_fixture()
    snapshot = EnvironmentSnapshot(
        systems=(
            EnvironmentSystem(
                id="github-system",
                provider="github",
                display_name="GitHub",
                resources=(github_resource,),
            ),
        ),
        relationships=(
            ResourceRelationship(
                source_resource_id=github_resource.id,
                target_resource_id="issues",
                relationship="contains",
            ),
        ),
        capabilities=(github_cap,),
        credentials=(permissions[0].credential,),
        owners=("worker:1",),
        metadata={"organization": "test"},
    )

    assert snapshot.resource_index()[github_resource.id] == github_resource
    assert snapshot.capability_index()[github_cap.scope] == github_cap


def test_f29_final_cutover_keeps_vercel_auto_deploy_disabled() -> None:
    root = Path(__file__).resolve().parents[2]
    vercel = (root / "vercel.json").read_text(encoding="utf-8")
    assert '"deploymentEnabled": false' in vercel


def test_f29_realtime_cross_organization_access_is_explicitly_rejected() -> None:
    principal = HumanPrincipal(
        user_id=uuid4(),
        organization_id=uuid4(),
        membership_id=uuid4(),
        role="owner",
        permissions=frozenset({"jobs.read"}),
    )
    other_organization = uuid4()

    with pytest.raises(AuthorizationError, match="Cross-organization"):
        _ensure_organization_access(principal, other_organization)

    assert _ensure_organization_access(principal, principal.organization_id) is principal


def test_f29_audit_and_result_provenance_retain_execution_identity() -> None:
    payload = {
        "request": {
            "provider": "github",
            "scope": "github.repository.issues.create",
            "resourceId": "integration:repo-a",
            "adapterVersion": "1.0.0",
        },
        "authorization": {
            "provider": "github",
            "capability_scope": "github.repository.issues.create",
            "resource_id": "integration:repo-a",
            "adapter": "native_api",
            "adapter_version": "1.0.0",
        },
    }
    provenance = provenance_from_action_payload(
        scope="github.repository.issues.create",
        resource_id="integration:repo-a",
        payload=payload,
        action_id="action-1",
    )

    assert provenance.provider == "github"
    assert provenance.capability == "github.repository.issues.create"
    assert provenance.resource_id == "integration:repo-a"
    assert provenance.adapter == "native_api"
    assert provenance.adapter_version == "1.0.0"
    assert provenance.action_id == "action-1"

    audit = audit_provenance_payload(
        scope=provenance.capability,
        resource_id=provenance.resource_id,
        payload=payload,
    )
    assert audit == {
        "provider": "github",
        "capability": "github.repository.issues.create",
        "resource_id": "integration:repo-a",
        "adapter": "native_api",
        "adapter_version": "1.0.0",
    }


def test_f29_qstash_outbox_drain_requires_signed_delivery() -> None:
    from fastapi import HTTPException
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/v1/outbox/drain",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )

    with pytest.raises(HTTPException) as error:
        verify_outbox_qstash(request, b"{}", None)

    assert error.value.status_code == 401


def test_f29_outbox_after_commit_requests_immediate_qstash_drain(monkeypatch) -> None:
    from app.infrastructure.database import session as session_module

    requested: list[str] = []

    def fake_request(*, reason: str) -> None:
        requested.append(reason)

    monkeypatch.setattr(
        session_module,
        "request_outbox_drain_after_commit",
        fake_request,
    )

    class FakeSession:
        def __init__(self) -> None:
            self.info = {"outbox_pending": True}

    fake_session = FakeSession()
    session_module._request_outbox_drain_after_commit(fake_session)  # type: ignore[arg-type]

    assert requested == ["transaction_committed"]
    assert fake_session.info == {}
