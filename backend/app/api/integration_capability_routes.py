from __future__ import annotations

import hashlib
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import organization_principal
from app.api.product_common import append_audit, not_found, require_permission, utcnow
from app.domain.identity.principals import HumanPrincipal
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

PROVIDERS: dict[str, dict[str, Any]] = {
    "github": {
        "label": "GitHub",
        "credential": "optional",
        "operations": [
            {
                "providerOperation": "repository.metadata.read",
                "scope": "github.repository.metadata.read",
                "action": "read",
                "target": "repository metadata",
                "description": "Read repository metadata.",
                "risk": "low",
            },
            {
                "providerOperation": "repository.issues.read",
                "scope": "github.repository.issues.read",
                "action": "read",
                "target": "repository issues",
                "description": "Read open repository issues.",
                "risk": "low",
            },
            {
                "providerOperation": "repository.pull_requests.read",
                "scope": "github.repository.pull_requests.read",
                "action": "read",
                "target": "pull requests",
                "description": "Read open pull requests.",
                "risk": "low",
            },
            {
                "providerOperation": "repository.issue.create",
                "scope": "github.repository.issues.create",
                "action": "create",
                "target": "repository issue",
                "description": "Create a GitHub issue.",
                "risk": "high",
            },
        ],
    },
    "gmail": {
        "label": "Gmail",
        "credential": "required",
        "operations": [
            {
                "providerOperation": "mailbox.profile.read",
                "scope": "gmail.mailbox.profile.read",
                "action": "read",
                "target": "mailbox profile",
                "description": "Read Gmail mailbox profile metadata.",
                "risk": "low",
            },
            {
                "providerOperation": "messages.recent.read",
                "scope": "gmail.messages.recent.read",
                "action": "read",
                "target": "recent messages",
                "description": "Read bounded recent-message metadata.",
                "risk": "medium",
            },
        ],
    },
    "google_drive": {
        "label": "Google Drive",
        "credential": "required",
        "operations": [
            {
                "providerOperation": "drive.profile.read",
                "scope": "google_drive.profile.read",
                "action": "read",
                "target": "Drive profile",
                "description": "Read Drive account metadata.",
                "risk": "low",
            },
            {
                "providerOperation": "files.recent.read",
                "scope": "google_drive.files.recent.read",
                "action": "read",
                "target": "recent files",
                "description": "Read recent Drive file metadata.",
                "risk": "medium",
            },
        ],
    },
    "slack": {
        "label": "Slack",
        "credential": "required",
        "operations": [
            {
                "providerOperation": "workspace.identity.read",
                "scope": "slack.workspace.identity.read",
                "action": "read",
                "target": "workspace identity",
                "description": "Read Slack workspace identity.",
                "risk": "low",
            },
            {
                "providerOperation": "channels.read",
                "scope": "slack.channels.read",
                "action": "read",
                "target": "channels",
                "description": "Read visible Slack channels.",
                "risk": "low",
            },
            {
                "providerOperation": "chat.message.create",
                "scope": "slack.messages.create",
                "action": "create",
                "target": "Slack message",
                "description": "Post a Slack message.",
                "risk": "high",
            },
        ],
    },
    "google_calendar": {
        "label": "Google Calendar",
        "credential": "required",
        "operations": [
            {
                "providerOperation": "calendar.primary.read",
                "scope": "google_calendar.primary.read",
                "action": "read",
                "target": "primary calendar",
                "description": "Read primary calendar metadata.",
                "risk": "low",
            },
            {
                "providerOperation": "events.upcoming.read",
                "scope": "google_calendar.events.upcoming.read",
                "action": "read",
                "target": "upcoming events",
                "description": "Read upcoming calendar events.",
                "risk": "low",
            },
        ],
    },
    "generic_mcp": {
        "label": "Generic REST / MCP",
        "credential": "disabled",
        "operations": [],
    },
}

ROUTE_PROVIDER = {
    "github": "github",
    "gmail": "gmail",
    "google-drive": "google_drive",
    "slack": "slack",
    "google-calendar": "google_calendar",
    "generic-mcp": "generic_mcp",
}


async def _validate_provider(
    provider: str,
    config: dict[str, Any],
    credential: str | None,
) -> dict[str, Any]:
    descriptor = PROVIDERS[provider]
    if descriptor["credential"] == "disabled":
        raise HTTPException(
            409,
            "Generic REST / MCP is guarded until an explicit outbound-origin allowlist exists.",
        )
    if descriptor["credential"] == "required" and not credential:
        raise HTTPException(400, f"{descriptor['label']} requires an access token.")

    timeout = httpx.Timeout(10.0)
    headers = {"User-Agent": "Aduoryn/1.0"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if provider == "github":
            repository = str(config.get("repository", "")).strip()
            parts = repository.split("/", 1)
            if len(parts) != 2 or not all(parts):
                raise HTTPException(400, "GitHub repository must be owner/name.")
            if credential:
                headers["Authorization"] = f"Bearer {credential}"
            response = await client.get(
                f"https://api.github.com/repos/{parts[0]}/{parts[1]}",
                headers=headers,
            )
            if response.status_code >= 400:
                raise HTTPException(
                    400,
                    f"GitHub repository validation failed ({response.status_code}).",
                )
            data = response.json()
            full_name = str(data.get("full_name") or repository)
            return {
                "resourceKey": full_name.lower(),
                "displayName": full_name,
                "webUrl": str(data.get("html_url") or f"https://github.com/{repository}"),
                "config": {"repository": full_name},
                "metadata": {
                    "private": bool(data.get("private", False)),
                    "archived": bool(data.get("archived", False)),
                    "defaultBranch": data.get("default_branch"),
                },
            }

        headers["Authorization"] = f"Bearer {credential}"
        if provider == "gmail":
            response = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                headers=headers,
            )
            if response.status_code >= 400:
                raise HTTPException(400, "Gmail credential validation failed.")
            data = response.json()
            email = str(data.get("emailAddress") or "connected-mailbox")
            return {
                "resourceKey": email.lower(),
                "displayName": f"Gmail · {email}",
                "webUrl": "https://mail.google.com/",
                "config": {"account": email},
                "metadata": {
                    "emailAddress": email,
                    "messagesTotal": data.get("messagesTotal"),
                    "threadsTotal": data.get("threadsTotal"),
                },
            }
        if provider == "google_drive":
            response = await client.get(
                "https://www.googleapis.com/drive/v3/about?fields=user",
                headers=headers,
            )
            if response.status_code >= 400:
                raise HTTPException(400, "Google Drive credential validation failed.")
            data = response.json().get("user", {})
            email = str(data.get("emailAddress") or data.get("displayName") or "drive")
            key = str(data.get("permissionId") or email).lower()
            return {
                "resourceKey": key,
                "displayName": f"Google Drive · {email}",
                "webUrl": "https://drive.google.com/drive/my-drive",
                "config": {"account": email},
                "metadata": {
                    "emailAddress": data.get("emailAddress"),
                    "displayName": data.get("displayName"),
                    "permissionId": data.get("permissionId"),
                },
            }
        if provider == "google_calendar":
            response = await client.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary",
                headers=headers,
            )
            if response.status_code >= 400:
                raise HTTPException(400, "Google Calendar credential validation failed.")
            data = response.json()
            calendar_id = str(data.get("id") or "primary")
            summary = str(data.get("summary") or calendar_id)
            return {
                "resourceKey": calendar_id.lower(),
                "displayName": f"Google Calendar · {summary}",
                "webUrl": "https://calendar.google.com/calendar/u/0/r",
                "config": {"calendarId": "primary"},
                "metadata": {
                    "calendarId": calendar_id,
                    "summary": summary,
                    "timeZone": data.get("timeZone"),
                    "accessRole": data.get("accessRole"),
                },
            }
        if provider == "slack":
            response = await client.get(
                "https://slack.com/api/auth.test",
                headers=headers,
            )
            if response.status_code >= 400:
                raise HTTPException(400, "Slack credential validation failed.")
            data = response.json()
            if data.get("ok") is not True:
                raise HTTPException(
                    400, f"Slack credential validation failed: {data.get('error', 'unknown')}"
                )
            team_id = str(data.get("team_id") or "slack")
            team = str(data.get("team") or team_id)
            return {
                "resourceKey": team_id.lower(),
                "displayName": f"Slack · {team}",
                "webUrl": str(data.get("url") or f"https://app.slack.com/client/{team_id}"),
                "config": {"teamId": team_id},
                "metadata": {
                    "teamId": team_id,
                    "team": team,
                    "userId": data.get("user_id"),
                    "user": data.get("user"),
                    "enterpriseId": data.get("enterprise_id"),
                },
            }
    raise HTTPException(400, "Unsupported integration provider.")


def _config(integration: Integration) -> dict[str, Any]:
    return integration.config if isinstance(integration.config, dict) else {}


async def integration_public(
    session: AsyncSession, integration: Integration
) -> dict[str, Any]:
    config = _config(integration)
    credential = await session.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.integration_id == integration.id
        )
    )
    return {
        "id": str(integration.id),
        "organizationId": str(integration.organization_id),
        "provider": integration.provider,
        "displayName": integration.display_name,
        "resourceKey": str(config.get("resourceKey", str(integration.id))),
        "webUrl": str(config.get("webUrl", "")),
        "metadata": config.get("metadata")
        if isinstance(config.get("metadata"), dict)
        else {},
        "supportedOperations": list(config.get("supportedOperations", [])),
        "status": integration.status,
        "credential": {
            "mode": "encrypted_secret" if credential is not None else "none",
            "configured": credential is not None,
            "fingerprint": config.get("credentialFingerprint"),
        },
        "health": {
            "message": str(config.get("healthMessage", "")),
            "lastCheckedAt": str(
                config.get("lastCheckedAt") or integration.updated_at.isoformat()
            ),
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
    provider = ROUTE_PROVIDER.get(route_provider, route_provider.replace("-", "_"))
    if provider not in PROVIDERS:
        raise HTTPException(404, "Integration provider is not supported.")

    raw_config = payload.get("config")
    config: dict[str, Any] = (
        dict(raw_config) if isinstance(raw_config, dict) else {}
    )
    credential = (
        str(payload.get("credential", "")).strip()
        if payload.get("credential") is not None
        else ""
    )
    connection = await _validate_provider(
        provider, config, credential or None
    )
    existing = await session.scalar(
        select(Integration).where(
            Integration.organization_id == organization_id,
            Integration.provider == provider,
        )
    )
    if existing is not None and existing.status != "disconnected":
        existing_key = str(_config(existing).get("resourceKey", ""))
        if existing_key == connection["resourceKey"]:
            raise HTTPException(409, "This provider resource is already connected.")

    fingerprint = (
        f"sha256:{hashlib.sha256(credential.encode()).hexdigest()[:12]}"
        if credential
        else None
    )
    now = utcnow()
    descriptor = PROVIDERS[provider]
    integration = Integration(
        organization_id=organization_id,
        provider=provider,
        display_name=connection["displayName"],
        status="connected",
        config={
            **connection["config"],
            "resourceKey": connection["resourceKey"],
            "webUrl": connection["webUrl"],
            "metadata": connection["metadata"],
            "supportedOperations": [
                item["providerOperation"] for item in descriptor["operations"]
            ],
            "credentialFingerprint": fingerprint,
            "healthMessage": f"{descriptor['label']} connection validated.",
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
        summary=f"{descriptor['label']} integration connected.",
        metadata={"provider": provider, "credentialConfigured": bool(credential)},
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

    credential_row = await session.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.integration_id == integration.id
        )
    )
    credential = None
    if credential_row is not None:
        try:
            credential = decrypt_integration_secret(credential_row.ciphertext)
        except IntegrationCipherError as error:
            raise HTTPException(503, str(error)) from error
    config = _config(integration)
    try:
        connection = await _validate_provider(
            integration.provider, config, credential
        )
        integration.status = "connected"
        config["metadata"] = connection["metadata"]
        config["healthMessage"] = (
            f"{PROVIDERS[integration.provider]['label']} connection validated."
        )
    except HTTPException as error:
        integration.status = "degraded"
        config["healthMessage"] = str(error.detail)
    config["lastCheckedAt"] = utcnow().isoformat()
    integration.config = config
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
        delete(IntegrationCredential).where(
            IntegrationCredential.integration_id == integration.id
        )
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
    result = await list_integrations_v1(
        organization_id, principal, session
    )
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
    return {
        "data": (
            await integration_security_v1(organization_id, principal)
        )
    }


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
    integration = await _connect(
        session, organization_id, principal, provider, payload
    )
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
    integration = await _connect(
        session, organization_id, principal, provider, payload
    )
    return {
        "data": {
            "integration": integration_v2(
                await integration_public(session, integration)
            )
        }
    }


@v1_router.post("/organizations/{organization_id}/integrations/{integration_id}/check")
async def check_v1(
    organization_id: UUID,
    integration_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    integration = await _health(
        session, organization_id, principal, integration_id
    )
    return {"integration": await integration_public(session, integration)}


@v2_router.post("/organizations/{organization_id}/integrations/{integration_id}/check")
async def check_v2(
    organization_id: UUID,
    integration_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    integration = await _health(
        session, organization_id, principal, integration_id
    )
    return {
        "data": {
            "integration": integration_v2(
                await integration_public(session, integration)
            )
        }
    }


@v1_router.delete("/organizations/{organization_id}/integrations/{integration_id}")
async def disconnect_v1(
    organization_id: UUID,
    integration_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    integration = await _disconnect(
        session, organization_id, principal, integration_id
    )
    return {"integration": await integration_public(session, integration)}


@v2_router.delete("/organizations/{organization_id}/integrations/{integration_id}")
async def disconnect_v2(
    organization_id: UUID,
    integration_id: UUID,
    principal: Annotated[HumanPrincipal, Depends(organization_principal)],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, Any]:
    integration = await _disconnect(
        session, organization_id, principal, integration_id
    )
    return {
        "data": {
            "integration": integration_v2(
                await integration_public(session, integration)
            )
        }
    }


async def _catalog(
    session: AsyncSession, organization_id: UUID
) -> dict[str, Any]:
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
    for integration in integrations:
        descriptor = PROVIDERS.get(integration.provider)
        if descriptor is None:
            continue
        config = _config(integration)
        actions = []
        scopes = []
        for item in descriptor["operations"]:
            action = {
                "id": f"{integration.id}:{item['providerOperation']}",
                "action": item["action"],
                "target": item["target"],
                "providerOperation": item["providerOperation"],
                "scope": item["scope"],
                "description": item["description"],
                "risk": item["risk"],
            }
            actions.append(action)
            scopes.append(item["scope"])
            all_scopes.add(item["scope"])
        resources.append(
            {
                "id": str(integration.id),
                "organizationId": str(organization_id),
                "integrationId": str(integration.id),
                "provider": integration.provider,
                "type": "integration_resource",
                "key": str(config.get("resourceKey", integration.id)),
                "displayName": integration.display_name,
                "status": integration.status,
                "metadata": config.get("metadata", {}),
                "actions": actions,
                "scopes": scopes,
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
    return {
        "data": _profile_v2(
            await _profile(session, organization_id, agent_id)
        )
    }


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
    requested = {
        str(scope).strip()
        for scope in payload.get("scopes", [])
        if str(scope).strip()
    }
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
    return {
        "profile": await _save_profile(
            session, organization_id, agent_id, principal, payload
        )
    }


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
            await _save_profile(
                session, organization_id, agent_id, principal, payload
            )
        )
    }
