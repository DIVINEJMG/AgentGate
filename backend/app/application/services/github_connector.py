"""Shared GitHub installation attachment; callers enforce membership and owner consent."""

from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update

from app.application.services.integration_credentials import encode_bundle
from app.application.services.integration_foundation import IntegrationFoundation
from app.bootstrap.settings import settings
from app.domain.identity.permissions import permissions_for_role
from app.execution.providers.native.github.authentication import app_jwt, permission_scopes
from app.execution.providers.native.github.provider import ExpandedGitHubProvider
from app.infrastructure.database.models import (
    GitHubAuthFlow,
    GitHubInstallationBinding,
    HumanIdentity,
    Integration,
    IntegrationConnectionState,
    IntegrationCredential,
    OrganizationMembership,
)
from app.infrastructure.secrets.integration_crypto import encrypt_integration_secret


async def finish_onboarding(session, row, connection_id):
    row.reconnect_connection_id = connection_id
    row.status, row.credential_ciphertext = "completed", None
    if settings.github_login_enabled:
        await session.execute(
            update(GitHubAuthFlow)
            .where(
                GitHubAuthFlow.onboarding_id == row.id,
                GitHubAuthFlow.user_id == row.owner_id,
                GitHubAuthFlow.status == "connector_pending",
            )
            .values(status="connected")
        )


async def connect_installation(organization_id, installation_id, principal, session, row, user):
    # Serializes signup continuation with manual attachment for the same human.
    await session.scalar(
        select(HumanIdentity.id).where(HumanIdentity.id == principal.user_id).with_for_update()
    )
    current_role = await session.scalar(
        select(OrganizationMembership.role)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == principal.user_id,
        )
        .with_for_update()
    )
    if not current_role or "integrations.manage" not in permissions_for_role(current_role):
        raise HTTPException(
            403,
            "Workspace access changed. Connecting GitHub requires current integration permission.",
        )
    if not row.reconnect_connection_id:
        existing = await session.scalar(
            select(GitHubInstallationBinding)
            .join(
                IntegrationConnectionState,
                IntegrationConnectionState.connection_id == GitHubInstallationBinding.connection_id,
            )
            .where(
                GitHubInstallationBinding.organization_id == organization_id,
                GitHubInstallationBinding.installation_id == installation_id,
                GitHubInstallationBinding.verified_user_id == user.account_id,
                IntegrationConnectionState.owner_id == principal.user_id,
                IntegrationConnectionState.ownership_state == "owned",
            )
        )
        if existing:
            row.reconnect_connection_id = existing.connection_id
    provider = ExpandedGitHubProvider()
    installation = await provider.auth.verify_installation(
        installation_id=installation_id, user_bundle=user
    )
    # User access and installation access are intersected before app credentials
    # can expose resources. An organization installation is not access to every repo.
    repositories = []
    for page in range(1, 11):
        data = (
            await provider.api(
                "GET",
                f"/user/installations/{installation_id}/repositories?per_page=100&page={page}",
                user.access_token,
            )
        ).data
        repositories.extend(str(r["id"]) for r in data["repositories"])
        if len(data["repositories"]) < 100:
            break
    else:
        raise HTTPException(409, "Installation repository discovery exceeded its page budget.")
    if not repositories:
        raise HTTPException(409, "No accessible repositories were found in this installation.")
    bundle = await provider.auth.installation_bundle(installation_id, repositories=repositories)
    from dataclasses import replace

    bundle = replace(
        bundle, renewal_metadata={**bundle.renewal_metadata, "userBundle": encode_bundle(user)}
    )
    app = (await provider.api("GET", "/app", app_jwt())).data
    config = {
        "installationId": installation_id,
        "botLogin": app["slug"] + "[bot]",
        "ownerLogin": installation["account"]["login"],
        "accountType": installation["account"]["type"],
        "githubPermissionScopes": ",".join(permission_scopes(installation["permissions"])),
    }
    # Catalog visibility is also bounded by the verified user's access. An App
    # installation may see organization Projects that this owner cannot see.
    project_ids = []
    if config["accountType"] == "Organization" and installation["permissions"].get(
        "organization_projects"
    ):
        cursor = None
        for _ in range(10):
            projects = await provider.graphql(
                "query($login:String!,$after:String){organization(login:$login){projectsV2(first:50,after:$after){nodes{id} pageInfo{hasNextPage endCursor}}}}",
                {"login": config["ownerLogin"], "after": cursor},
                user.access_token,
            )
            page = projects["organization"]["projectsV2"]
            project_ids.extend(p["id"] for p in page["nodes"])
            if not page["pageInfo"]["hasNextPage"]:
                break
            cursor = page["pageInfo"]["endCursor"]
        else:
            raise HTTPException(409, "Project discovery exceeded its page budget.")
    import json

    config["authorizedProjectIds"] = json.dumps(project_ids)
    if row.reconnect_connection_id:
        from app.api.integration_foundation_routes import owned

        state = await owned(session, organization_id, row.reconnect_connection_id, principal)
        connection = await session.get(Integration, row.reconnect_connection_id)
        binding = await session.scalar(
            select(GitHubInstallationBinding)
            .where(GitHubInstallationBinding.connection_id == row.reconnect_connection_id)
            .with_for_update()
        )
        if (
            not connection
            or not binding
            or binding.installation_id != installation_id
            or binding.verified_user_id != user.account_id
        ):
            raise HTTPException(
                409,
                "Reconnect must verify the same GitHub user and installation. Connect other accounts separately.",
            )
        credential_row = await session.scalar(
            select(IntegrationCredential)
            .where(IntegrationCredential.integration_id == connection.id)
            .with_for_update()
        )
        if not credential_row:
            credential_row = IntegrationCredential(integration_id=connection.id)
            session.add(credential_row)
        credential_row.ciphertext = encrypt_integration_secret(encode_bundle(bundle))
        connection.config, connection.status = config, "connected"
        state.authorization_state, state.reason = (
            "connected",
            "Resume pending work to review current resource permissions.",
        )
        state.credential_metadata = {"scopes": list(bundle.scopes)}
        binding.permissions, binding.status = installation["permissions"], "active"
        await IntegrationFoundation(session).discover(connection, bundle.access_token)
        await finish_onboarding(session, row, connection.id)
        await session.commit()
        return {"connectionId": str(connection.id), "state": "connected"}
    connection = Integration(
        id=uuid4(),
        organization_id=organization_id,
        provider="github",
        status="connected",
        display_name=installation["account"]["login"],
        config=config,
    )
    session.add(connection)
    await session.flush()
    state = IntegrationConnectionState(
        organization_id=organization_id,
        connection_id=connection.id,
        owner_id=principal.user_id,
        ownership_state="owned",
        authorization_state="connected",
        reason="",
        authority_version=1,
        account_id=bundle.account_id,
        credential_metadata={"scopes": list(bundle.scopes)},
    )
    session.add_all(
        [
            state,
            IntegrationCredential(
                integration_id=connection.id,
                ciphertext=encrypt_integration_secret(encode_bundle(bundle)),
            ),
            GitHubInstallationBinding(
                organization_id=organization_id,
                connection_id=connection.id,
                installation_id=installation_id,
                account_id=str(installation["account"]["id"]),
                verified_user_id=user.account_id,
                permissions=installation["permissions"],
                status="active",
            ),
        ]
    )
    await session.flush()
    await IntegrationFoundation(session).discover(connection, bundle.access_token)
    await finish_onboarding(session, row, connection.id)
    await IntegrationFoundation(session).queue_waiting_preparation(
        organization_id, principal.user_id
    )
    await session.commit()
    return {"connectionId": str(connection.id), "state": "connected"}
