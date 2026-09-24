from __future__ import annotations

import hashlib
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import organization_principal
from app.api.product_common import append_audit, not_found, require_permission, utcnow
from app.domain.identity.principals import HumanPrincipal
from app.execution.bootstrap import execution_provider_registry
from app.execution.contracts import ExecutionProviderError, ResourceDescriptor
from app.infrastructure.database.models import (
    AgentIdentity,
    CapabilityProfile,
    Integration,
    IntegrationCredential,
)
from app.infrastructure.database.session import database_session
from app.infrastructure.secrets.integration_crypto import (
    IntegrationCipherError,
    decrypt_integration_secret,
    encrypt_integration_secret,
)

v1_router = APIRouter(tags=["integrations", "capabilities"])
v2_router = APIRouter(tags=["integrations", "capabilities"])

ROUTE_PROVIDER = {
    "github": "github",
    "gmail": "gmail",
    "google-drive": "google_drive",
    "slack": "slack",
    "google-calendar": "google_calendar",
    "browser": "browser",
    "generic-mcp": "generic_mcp",
}


def _provider_id(route_provider: str) -> str:
    return ROUTE_PROVIDER.get(route_provider, route_provider.replace("-", "_"))


def _provider_or_404(provider_id: str):
    if provider_id == "generic_mcp":
        raise HTTPException(
            409,
            "Generic REST / MCP remains guarded until the F29 custom-provider boundary is enabled.",
        )
    try:
        return execution_provider_registry().get(provider_id)
    except KeyError as error:
        raise HTTPException(404, "Integration provider is not supported.") from error


def _provider_http_error(error: ExecutionProviderError) -> HTTPException:
    status_code = 503 if error.error.retryable else 400
    if error.error.code == "resource_not_found":
        status_code = 404
    return HTTPException(status_code, error.error.safe_message)


async def _discover_connection(
    provider_id: str,
    config: dict[str, str],
    credential: str | None,
) -> ResourceDescriptor:
    provider = _provider_or_404(provider_id)
    try:
        resources = await provider.discover_resources(
            configuration=config,
            credential=credential,
        )
    except ExecutionProviderError as error:
        raise _provider_http_error(error) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if len(resources) != 1:
        raise HTTPException(
            503,
            "Provider connection discovery did not resolve exactly one canonical resource.",
        )
    return resources[0]


def _config(integration: Integration) -> dict[str, Any]:
    return integration.config if isinstance(integration.config, dict) else {}


async def integration_public(session: AsyncSession, integration: Integration) -> dict[str, Any]:
    config = _config(integration)
    credential = await session.scalar(
        select(IntegrationCredential).where(IntegrationCredential.integration_id == integration.id)
    )
    return {
        "id": str(integration.id),
        "organizationId": str(integration.organization_id),
        "provider": integration.provider,
        "displayName": integration.display_name,
        "resourceKey": str(config.get("resourceKey", str(integration.id))),
        "webUrl": str(config.get("webUrl", "")),
        "metadata": config.get("metadata") if isinstance(config.get("metadata"), dict) else {},
        "supportedOperations": list(config.get("supportedOperations", [])),
        "status": integration.status,
        "credential": {
            "mode": "encrypted_secret" if credential is not None else "none",
            "configured": credential is not None,
            "fingerprint": config.get("credentialFingerprint"),
        },
        "health": {
            "message": str(config.get("healthMessage", "")),
            "lastCheckedAt": str(config.get("lastCheckedAt") or integration.updated_at.isoformat()),
        },
        "createdBy": str(config.get("createdBy", "")),
        "createdAt": integration.created_at.isoformat(),
        "updatedAt": integration.updated_at.isoformat(),
    }


def integration_v2(public: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "id": public["id"],
            "provider": public["provider"],
            "status": public["status"],
            "createdAt": public["createdAt"],
            "updatedAt": public["updatedAt"],
        },
        "resource": {
            "organizationId": public["organizationId"],
            "displayName": public["displayName"],
            "resourceKey": public["resourceKey"],
            "webUrl": public["webUrl"],
            "metadata": public["metadata"],
        },
        "boundary": {
            "supportedOperations": public["supportedOperations"],
            "credential": public["credential"],
        },
        "health": public["health"],
        "ownership": {"createdBy": public["createdBy"]},
    }


async def _connect(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    route_provider: str,
    payload: dict[str, Any],
) -> Integration:
    require_permission(principal, "integrations.manage")
    provider_id = _provider_id(route_provider)
    provider = _provider_or_404(provider_id)

    raw_config = payload.get("config")
    config: dict[str, str] = (
        {str(key): str(value) for key, value in raw_config.items()}
        if isinstance(raw_config, dict)
        else {}
    )
    credential = (
        str(payload.get("credential", "")).strip() if payload.get("credential") is not None else ""
    )
    resource = await _discover_connection(provider_id, config, credential or None)
    existing = await session.scalar(
        select(Integration).where(
            Integration.organization_id == organization_id,
            Integration.provider == provider_id,
        )
    )
    if existing is not None and existing.status != "disconnected":
        existing_key = str(_config(existing).get("resourceKey", ""))
        if existing_key == resource.external_id:
            raise HTTPException(409, "This provider resource is already connected.")

    fingerprint = (
        f"sha256:{hashlib.sha256(credential.encode()).hexdigest()[:12]}" if credential else None
    )
    now = utcnow()
    manifest = provider.manifest
    integration = Integration(
        organization_id=organization_id,
        provider=provider_id,
        display_name=resource.display_name,
        status="connected",
        config={
            **resource.configuration,
            "resourceKey": resource.external_id,
            "resourceType": resource.resource_type,
            "webUrl": resource.web_url or "",
            "metadata": resource.metadata,
            "supportedOperations": [item.operation for item in manifest.capabilities],
            "availableCapabilities": list(resource.available_capabilities),
            "providerKind": manifest.kind,
            "adapterVersion": manifest.version,
            "credentialFingerprint": fingerprint,
            "healthMessage": f"{manifest.display_name} connection validated.",
            "lastCheckedAt": now.isoformat(),
            "createdBy": str(principal.user_id),
        },
        created_at=now,
        updated_at=now,
    )
    session.add(integration)
    await session.flush()
    if credential:
        try:
            ciphertext = encrypt_integration_secret(credential)
        except IntegrationCipherError as error:
            raise HTTPException(503, str(error)) from error
        session.add(
            IntegrationCredential(
                integration_id=integration.id,
                ciphertext=ciphertext,
                key_version=1,
                created_at=now,
                updated_at=now,
            )
        )
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="integration.connected",
        category="integration",
        resource_type="integration",
        resource_id=str(integration.id),
        resource_name=integration.display_name,
        outcome="connected",
        summary=f"{manifest.display_name} integration connected.",
        metadata={
            "provider": provider_id,
            "providerKind": manifest.kind,
            "adapterVersion": manifest.version,
            "credentialConfigured": bool(credential),
        },
    )
    await session.commit()
    await session.refresh(integration)
    return integration


async def _health(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    integration_id: UUID,
) -> Integration:
    require_permission(principal, "integrations.read")
    integration = await session.scalar(
        select(Integration).where(
            Integration.organization_id == organization_id,
            Integration.id == integration_id,
        )
    )
    if integration is None:
        raise not_found("Integration")
    if integration.status == "disconnected":
        raise HTTPException(409, "Disconnected integrations cannot be checked.")

    provider = _provider_or_404(integration.provider)
    credential_row = await session.scalar(
        select(IntegrationCredential).where(IntegrationCredential.integration_id == integration.id)
    )
    credential = None
    if credential_row is not None:
        try:
            credential = decrypt_integration_secret(credential_row.ciphertext)
        except IntegrationCipherError as error:
            raise HTTPException(503, str(error)) from error
    raw_config = _config(integration)
    config = {
        str(key): str(value)
        for key, value in raw_config.items()
        if isinstance(value, (str, int, float, bool))
    }
    try:
        health = await provider.check_health(
            configuration=config,
            credential=credential,
        )
        integration.status = "connected" if health.state == "healthy" else "degraded"
        raw_config["metadata"] = health.metadata
        raw_config["healthMessage"] = health.message
        raw_config["lastCheckedAt"] = health.checked_at.isoformat()
    except ExecutionProviderError as error:
        integration.status = "degraded"
        raw_config["healthMessage"] = error.error.safe_message
        raw_config["lastCheckedAt"] = utcnow().isoformat()
    integration.config = raw_config
    await session.commit()
    await session.refresh(integration)
    return integration


async def _disconnect(
    session: AsyncSession,
    organization_id: UUID,
    principal: HumanPrincipal,
    integration_id: UUID,
) -> Integration:
    require_permission(principal, "integrations.manage")
    integration = await session.scalar(
        select(Integration).where(
            Integration.organization_id == organization_id,
            Integration.id == integration_id,
        )
    )
    if integration is None:
        raise not_found("Integration")
    await session.execute(
        delete(IntegrationCredential).where(IntegrationCredential.integration_id == integration.id)
    )
    config = _config(integration)
    config["credentialFingerprint"] = None
    config["healthMessage"] = "Integration disconnected."
    config["lastCheckedAt"] = utcnow().isoformat()
    integration.config = config
    integration.status = "disconnected"
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="integration.disconnected",
        category="integration",
        severity="warning",
        resource_type="integration",
        resource_id=str(integration.id),
        resource_name=integration.display_name,
        outcome="disconnected",
        summary=f"Integration {integration.display_name} disconnected.",
        metadata={"provider": integration.provider},
    )
    await session.commit()
    await session.refresh(integration)
    return integration


@v1_router.get("/organizations/{organization_id}/integrations")
async def list_integrations_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "integrations.read")
    rows = list(
        (
            await session.scalars(
                select(Integration)
                .where(Integration.organization_id == organization_id)
                .order_by(desc(Integration.created_at))
            )
        ).all()
    )
    public = [await integration_public(session, item) for item in rows]
    return {"integrations": public, "count": len(public)}


@v2_router.get("/organizations/{organization_id}/integrations")
async def list_integrations_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    result = await list_integrations_v1(organization_id, principal, session)
    return {
        "items": [integration_v2(item) for item in result["integrations"]],
        "total": result["count"],
    }


@v1_router.get("/organizations/{organization_id}/integrations/security")
async def integration_security_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
) -> dict[str, Any]:
    require_permission(principal, "integrations.read")
    from app.bootstrap.settings import settings

    configured = settings.integration_encryption_key is not None
    return {
        "security": {
            "credentialVaultConfigured": configured,
            "encryption": "fernet-aes128-cbc-hmac-sha256" if configured else "unconfigured",
            "secretSource": "server_environment",
        }
    }


@v2_router.get("/organizations/{organization_id}/integrations/security")
async def integration_security_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
) -> dict[str, Any]:
    return {"data": (await integration_security_v1(organization_id, principal))}


@v1_router.post(
    "/organizations/{organization_id}/integrations/connect/{provider}",
    status_code=201,
)
async def connect_v1(
    organization_id: UUID,
    provider: str,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    integration = await _connect(session, organization_id, principal, provider, payload)
    return {"integration": await integration_public(session, integration)}


@v2_router.post(
    "/organizations/{organization_id}/integrations/connect/{provider}",
    status_code=201,
)
async def connect_v2(
    organization_id: UUID,
    provider: str,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    integration = await _connect(session, organization_id, principal, provider, payload)
    return {"data": {"integration": integration_v2(await integration_public(session, integration))}}


@v1_router.post("/organizations/{organization_id}/integrations/{integration_id}/check")
async def check_v1(
    organization_id: UUID,
    integration_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    integration = await _health(session, organization_id, principal, integration_id)
    return {"integration": await integration_public(session, integration)}


@v2_router.post("/organizations/{organization_id}/integrations/{integration_id}/check")
async def check_v2(
    organization_id: UUID,
    integration_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    integration = await _health(session, organization_id, principal, integration_id)
    return {"data": {"integration": integration_v2(await integration_public(session, integration))}}


@v1_router.delete("/organizations/{organization_id}/integrations/{integration_id}")
async def disconnect_v1(
    organization_id: UUID,
    integration_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    integration = await _disconnect(session, organization_id, principal, integration_id)
    return {"integration": await integration_public(session, integration)}


@v2_router.delete("/organizations/{organization_id}/integrations/{integration_id}")
async def disconnect_v2(
    organization_id: UUID,
    integration_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    integration = await _disconnect(session, organization_id, principal, integration_id)
    return {"data": {"integration": integration_v2(await integration_public(session, integration))}}


async def _catalog(session: AsyncSession, organization_id: UUID) -> dict[str, Any]:
    integrations = list(
        (
            await session.scalars(
                select(Integration).where(
                    Integration.organization_id == organization_id,
                    Integration.status.in_(["connected", "degraded"]),
                )
            )
        ).all()
    )
    resources: list[dict[str, Any]] = []
    all_scopes: set[str] = set()
    registry = execution_provider_registry()
    for integration in integrations:
        try:
            provider = registry.get(integration.provider)
        except KeyError:
            continue
        manifest = provider.manifest
        config = _config(integration)
        actions: list[dict[str, Any]] = []
        scopes: list[str] = []
        configured_available = config.get("availableCapabilities")
        available_capabilities = (
            {str(scope) for scope in configured_available}
            if isinstance(configured_available, list)
            else {capability.scope for capability in manifest.capabilities}
        )
        credential_configured = (
            await session.scalar(
                select(IntegrationCredential.id).where(
                    IntegrationCredential.integration_id == integration.id
                )
            )
            is not None
        )
        for capability in manifest.capabilities:
            if capability.scope not in available_capabilities:
                continue
            if capability.requires_credential and not credential_configured:
                continue
            action_name = capability.operation.rsplit(".", 1)[-1]
            action = {
                "id": f"{integration.id}:{capability.operation}",
                "action": action_name,
                "target": capability.target,
                "providerOperation": capability.operation,
                "scope": capability.scope,
                "description": capability.description,
                "risk": capability.risk,
                "mode": capability.mode,
                "sideEffect": capability.side_effect,
                "requiresCredential": capability.requires_credential,
                "approvalRecommendation": capability.approval_recommendation,
                "adapterVersion": manifest.version,
            }
            actions.append(action)
            scopes.append(capability.scope)
            all_scopes.add(capability.scope)
        resource_type = (
            manifest.capabilities[0].resource_type
            if manifest.capabilities
            else str(config.get("resourceType", "resource"))
        )
        resources.append(
            {
                "id": str(integration.id),
                "organizationId": str(organization_id),
                "integrationId": str(integration.id),
                "provider": integration.provider,
                "type": resource_type,
                "key": str(config.get("resourceKey", integration.id)),
                "displayName": integration.display_name,
                "status": integration.status,
                "metadata": config.get("metadata", {}),
                "actions": actions,
                "scopes": scopes,
                "providerKind": manifest.kind,
                "adapterVersion": manifest.version,
            }
        )
    return {
        "resources": resources,
        "scopes": sorted(all_scopes),
        "summary": {
            "resources": len(resources),
            "actions": sum(len(item["actions"]) for item in resources),
            "scopes": len(all_scopes),
        },
    }


def _catalog_v2(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "resources": [
            {
                "identity": {
                    "id": item["id"],
                    "type": item["type"],
                    "provider": item["provider"],
                    "status": item["status"],
                },
                "source": {
                    "organizationId": item["organizationId"],
                    "integrationId": item["integrationId"],
                    "key": item["key"],
                    "displayName": item["displayName"],
                    "metadata": item["metadata"],
                },
                "capabilities": {
                    "actions": item["actions"],
                    "scopes": item["scopes"],
                },
            }
            for item in result["resources"]
        ],
        "summary": result["summary"],
    }


async def _profile(
    session: AsyncSession,
    organization_id: UUID,
    agent_id: UUID,
    updated_by: str | None = None,
) -> dict[str, Any]:
    agent = await session.scalar(
        select(AgentIdentity).where(
            AgentIdentity.organization_id == organization_id,
            AgentIdentity.id == agent_id,
        )
    )
    if agent is None:
        raise not_found("Agent Identity")
    rows = list(
        (
            await session.scalars(
                select(CapabilityProfile).where(
                    CapabilityProfile.organization_id == organization_id,
                    CapabilityProfile.agent_id == agent_id,
                )
            )
        ).all()
    )
    declared = sorted(row.scope for row in rows if row.active)
    catalog = await _catalog(session, organization_id)
    available = set(catalog["scopes"])
    active = [scope for scope in declared if scope in available]
    stale = [scope for scope in declared if scope not in available]
    latest = max((row.updated_at for row in rows), default=None)
    return {
        "agent": {
            "id": str(agent.id),
            "name": agent.name,
            "status": agent.status,
        },
        "declaredScopes": declared,
        "activeScopes": active,
        "staleScopes": stale,
        "updatedAt": latest.isoformat() if latest else None,
        "updatedBy": updated_by,
        "authorization": {
            "state": "contextual",
            "reason": "Capabilities declare maximum authority; policy, risk, incidents, and approvals still apply.",
        },
    }


def _profile_v2(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent": profile["agent"],
        "declaration": {
            "scopes": profile["declaredScopes"],
            "activeScopes": profile["activeScopes"],
            "staleScopes": profile["staleScopes"],
            "updatedAt": profile["updatedAt"],
            "updatedBy": profile["updatedBy"],
        },
        "enforcement": profile["authorization"],
    }


@v1_router.get("/organizations/{organization_id}/capabilities")
async def catalog_v1(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "capabilities.read")
    return await _catalog(session, organization_id)


@v2_router.get("/organizations/{organization_id}/capabilities")
async def catalog_v2(
    organization_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "capabilities.read")
    return {"data": _catalog_v2(await _catalog(session, organization_id))}


@v1_router.get("/organizations/{organization_id}/agents/{agent_id}/capabilities")
async def profile_v1(
    organization_id: UUID,
    agent_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "capabilities.read")
    return {"profile": await _profile(session, organization_id, agent_id)}


@v2_router.get("/organizations/{organization_id}/agents/{agent_id}/capabilities")
async def profile_v2(
    organization_id: UUID,
    agent_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    require_permission(principal, "capabilities.read")
    return {"data": _profile_v2(await _profile(session, organization_id, agent_id))}


async def _save_profile(
    session: AsyncSession,
    organization_id: UUID,
    agent_id: UUID,
    principal: HumanPrincipal,
    payload: dict[str, Any],
) -> dict[str, Any]:
    require_permission(principal, "capabilities.manage")
    agent = await session.scalar(
        select(AgentIdentity).where(
            AgentIdentity.organization_id == organization_id,
            AgentIdentity.id == agent_id,
        )
    )
    if agent is None:
        raise not_found("Agent Identity")
    requested = {str(scope).strip() for scope in payload.get("scopes", []) if str(scope).strip()}
    await session.execute(
        delete(CapabilityProfile).where(
            CapabilityProfile.organization_id == organization_id,
            CapabilityProfile.agent_id == agent_id,
        )
    )
    now = utcnow()
    for scope in sorted(requested):
        session.add(
            CapabilityProfile(
                organization_id=organization_id,
                agent_id=agent_id,
                scope=scope,
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
    await append_audit(
        session,
        organization_id=organization_id,
        principal=principal,
        event_type="capabilities.updated",
        category="security",
        resource_type="agent",
        resource_id=str(agent_id),
        resource_name=agent.name,
        outcome="updated",
        summary=f"Capabilities updated for {agent.name}.",
        metadata={"scopes": sorted(requested)},
    )
    await session.commit()
    return await _profile(
        session,
        organization_id,
        agent_id,
        updated_by=str(principal.user_id),
    )


@v1_router.put("/organizations/{organization_id}/agents/{agent_id}/capabilities")
async def save_profile_v1(
    organization_id: UUID,
    agent_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {"profile": await _save_profile(session, organization_id, agent_id, principal, payload)}


@v2_router.put("/organizations/{organization_id}/agents/{agent_id}/capabilities")
async def save_profile_v2(
    organization_id: UUID,
    agent_id: UUID,
    payload: dict[str, Any],
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    return {
        "data": _profile_v2(
            await _save_profile(session, organization_id, agent_id, principal, payload)
        )
    }
