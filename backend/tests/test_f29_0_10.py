from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.execution.bootstrap import execution_provider_registry
from app.execution.contracts import (
    CapabilityDescriptor,
    ExecutionRequest,
    ExecutionResult,
    ProviderHealth,
    ProviderManifest,
    ProviderRuntimeContext,
    ResourceDescriptor,
    VerificationResult,
)
from app.execution.providers.base import ExecutionProvider
from app.execution.providers.resolver import ExecutionResolver
from app.integrations.builtin import (
    calendar_adapter,
    drive_adapter,
    github_adapter,
    gmail_adapter,
    slack_adapter,
)


def capability(
    *,
    provider: str = "test",
    requires_credential: bool = False,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        scope=f"{provider}.thing.read",
        provider=provider,
        resource_type="thing",
        operation="thing.read",
        mode="read",
        risk="low",
        requires_credential=requires_credential,
        side_effect=False,
        approval_recommendation="none",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        description="Read a thing.",
        target="thing",
    )


def resource(
    *,
    provider: str = "test",
    scope: str | None = None,
    health: str = "healthy",
) -> ResourceDescriptor:
    return ResourceDescriptor(
        id=f"integration:{uuid4()}",
        provider=provider,
        resource_type="thing",
        external_id="thing-1",
        display_name="Thing",
        metadata={},
        health=health,  # type: ignore[arg-type]
        available_capabilities=(scope or f"{provider}.thing.read",),
        configuration={},
    )


class FakeProvider:
    def __init__(
        self,
        *,
        provider: str = "test",
        kind: str = "native_api",
        requires_credential: bool = False,
        health: str = "healthy",
    ) -> None:
        self.capability = capability(
            provider=provider,
            requires_credential=requires_credential,
        )
        self.manifest = ProviderManifest(
            provider=provider,
            display_name=provider.title(),
            kind=kind,  # type: ignore[arg-type]
            version="1.0.0",
            credential_strategy="api_token",
            capabilities=(self.capability,),
        )
        self.health = health

    async def discover_resources(self, *, configuration, credential):
        return (resource(provider=self.manifest.provider),)

    async def discover_capabilities(self, *, resource=None):
        return self.manifest.capabilities

    async def check_health(self, *, configuration, credential):
        return ProviderHealth(
            state=self.health,  # type: ignore[arg-type]
            message="test",
            checked_at=datetime.now(UTC),
        )

    async def normalize_input(self, *, operation, input):
        return input

    async def execute(self, *, request, configuration, credential):
        return ExecutionResult.successful(
            provider=self.manifest.provider,
            adapter=self.manifest.kind,
            adapter_version=self.manifest.version,
            operation=request.operation,
            output={"ok": True},
            provider_request_id="req-1",
            started_at=datetime.now(UTC),
        )

    async def verify(
        self,
        *,
        request,
        result,
        configuration,
        credential,
    ):
        return VerificationResult(
            verified=True,
            summary="verified",
        )


def test_f29_execution_contract_is_provider_neutral() -> None:
    cap = capability(provider="github")
    res = ResourceDescriptor(
        id="integration:1",
        provider="github",
        resource_type="thing",
        external_id="repo",
        display_name="repo",
        metadata={},
        health="healthy",
        available_capabilities=(cap.scope,),
    )
    request = ExecutionRequest(
        organization_id=uuid4(),
        worker_id=uuid4(),
        agent_id=uuid4(),
        job_id=uuid4(),
        work_item_id=uuid4(),
        run_id=uuid4(),
        capability=cap,
        resource=res,
        operation=cap.operation,
        input={},
        correlation_id="corr-1",
        idempotency_key="idem-1",
    )
    assert request.capability.provider == "github"
    assert request.execution_preferences.preferred_provider_kinds == (
        "native_api",
        "browser",
        "mcp",
    )


def test_capability_rejects_read_side_effects() -> None:
    with pytest.raises(ValueError, match="Read capabilities"):
        CapabilityDescriptor(
            scope="bad.read",
            provider="bad",
            resource_type="bad",
            operation="bad.read",
            mode="read",
            risk="low",
            requires_credential=False,
            side_effect=True,
            approval_recommendation="none",
            input_schema={},
            output_schema={},
            description="bad",
            target="bad",
        )


def test_default_registry_contains_only_f29_native_providers() -> None:
    registry = execution_provider_registry()
    assert tuple(manifest.provider for manifest in registry.manifests()) == (
        "github",
        "gmail",
        "google_calendar",
        "google_drive",
        "slack",
    )
    assert all(manifest.kind == "native_api" for manifest in registry.manifests())
    assert all(manifest.version == "1.0.0" for manifest in registry.manifests())


def test_all_native_providers_conform_to_execution_protocol() -> None:
    registry = execution_provider_registry()
    assert all(isinstance(provider, ExecutionProvider) for provider in registry.providers())
    for provider in registry.providers():
        scopes = [item.scope for item in provider.manifest.capabilities]
        operations = [item.operation for item in provider.manifest.capabilities]
        assert scopes
        assert len(scopes) == len(set(scopes))
        assert len(operations) == len(set(operations))


@pytest.mark.asyncio
async def test_resolver_prefers_native_provider_and_checks_health() -> None:
    provider = FakeProvider()
    from app.execution.providers.registry import ProviderRegistry

    registry = ProviderRegistry((provider,))
    resolver = ExecutionResolver(registry)
    res = resource(provider="test")
    request = ExecutionRequest(
        organization_id=uuid4(),
        worker_id=None,
        agent_id=uuid4(),
        job_id=None,
        work_item_id=None,
        run_id=None,
        capability=provider.capability,
        resource=res,
        operation=provider.capability.operation,
        input={},
        correlation_id="corr-1",
        idempotency_key="idem-1",
    )
    resolved = await resolver.resolve(
        request,
        contexts={
            "test": ProviderRuntimeContext(
                configuration={},
                credential=None,
                resource=res,
            )
        },
    )
    assert resolved.provider.manifest.kind == "native_api"


@pytest.mark.asyncio
async def test_resolver_fails_closed_without_required_credential() -> None:
    provider = FakeProvider(requires_credential=True)
    from app.execution.providers.registry import ProviderRegistry

    resolver = ExecutionResolver(ProviderRegistry((provider,)))
    res = resource(provider="test")
    request = ExecutionRequest(
        organization_id=uuid4(),
        worker_id=None,
        agent_id=uuid4(),
        job_id=None,
        work_item_id=None,
        run_id=None,
        capability=provider.capability,
        resource=res,
        operation=provider.capability.operation,
        input={},
        correlation_id="corr-1",
        idempotency_key="idem-1",
    )
    with pytest.raises(LookupError, match="No healthy execution provider"):
        await resolver.resolve(
            request,
            contexts={
                "test": ProviderRuntimeContext(
                    configuration={},
                    credential=None,
                    resource=res,
                )
            },
        )


def test_legacy_builtin_adapters_are_universal_compatibility_facades() -> None:
    for adapter in (
        github_adapter,
        gmail_adapter,
        slack_adapter,
        drive_adapter,
        calendar_adapter,
    ):
        universal = execution_provider_registry().get(adapter.provider)
        assert adapter._provider is universal
        assert adapter.manifests


def test_core_runtime_and_gateway_have_no_provider_name_branches() -> None:
    root = Path(__file__).resolve().parents[1] / "app"
    paths = (
        root / "runtime",
        root / "domain" / "actions",
        root / "execution" / "provider_executor.py",
    )
    provider_literals = (
        '"github"',
        '"gmail"',
        '"slack"',
        '"google_drive"',
        '"google_calendar"',
    )
    offenders: list[str] = []
    for path in paths:
        files = path.rglob("*.py") if path.is_dir() else (path,)
        for file in files:
            text = file.read_text(encoding="utf-8")
            if any(
                token in text and ("provider ==" in text or "provider in" in text)
                for token in provider_literals
            ):
                offenders.append(str(file.relative_to(root)))
    assert not offenders, f"Provider-specific runtime branches found: {offenders}"


def test_public_api_still_mounts_v1_and_v2() -> None:
    from app.bootstrap.application import create_application

    paths = set(create_application().openapi()["paths"])
    assert "/api/v1/organizations/{organization_id}/integrations" in paths
    assert "/api/v2/organizations/{organization_id}/integrations" in paths
    assert "/api/v1/organizations/{organization_id}/capabilities" in paths
    assert "/api/v2/organizations/{organization_id}/capabilities" in paths
