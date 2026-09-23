from __future__ import annotations

from datetime import UTC, datetime

from app.execution.contracts import (
    CapabilityDescriptor,
    ExecutionRequest,
    ExecutionResult,
    ProviderHealth,
    ProviderManifest,
    ResourceDescriptor,
    VerificationResult,
)
from app.execution.providers.native.base import NativeProvider, mapping, text
from app.execution.providers.native.http import ProviderTransportError

CAPABILITIES = (
    CapabilityDescriptor(
        scope="google_drive.profile.read",
        provider="google_drive",
        resource_type="drive",
        operation="drive.profile.read",
        mode="read",
        risk="low",
        requires_credential=True,
        side_effect=False,
        approval_recommendation="none",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object"},
        description="Read the connected Google Drive account identity.",
        target="profile",
    ),
    CapabilityDescriptor(
        scope="google_drive.files.recent.read",
        provider="google_drive",
        resource_type="drive",
        operation="files.recent.read",
        mode="read",
        risk="medium",
        requires_credential=True,
        side_effect=False,
        approval_recommendation="none",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "array"},
        description="Read bounded metadata for recently modified Drive files.",
        target="recent_files",
    ),
)


class GoogleDriveProvider(NativeProvider):
    manifest = ProviderManifest(
        provider="google_drive",
        display_name="Google Drive",
        kind="native_api",
        version="1.0.0",
        credential_strategy="oauth_access_token",
        capabilities=CAPABILITIES,
    )

    async def _about(
        self, credential: str | None
    ) -> tuple[dict[str, object], str | None]:
        if not credential:
            raise self._execution_error(
                operation="drive.profile.read",
                correlation_id="provider-discovery",
                code="authentication_error",
                retryable=False,
                safe_message="Google Drive requires an access token.",
            )
        response = await self._http.request(
            provider="Google Drive",
            method="GET",
            url=(
                "https://www.googleapis.com/drive/v3/about"
                "?fields=user(displayName,emailAddress,permissionId),storageQuota(limit,usage)"
            ),
            credential=credential,
        )
        payload = mapping(response.data)
        user = mapping(payload.get("user"))
        email = text(user.get("emailAddress"))
        permission_id = text(user.get("permissionId"))
        if not email and not permission_id:
            raise self._execution_error(
                operation="drive.profile.read",
                correlation_id="provider-discovery",
                code="temporary_provider_error",
                retryable=True,
                safe_message="Google Drive returned an invalid account profile.",
            )
        return payload, response.request_id

    async def discover_resources(
        self,
        *,
        configuration: dict[str, str],
        credential: str | None,
    ) -> tuple[ResourceDescriptor, ...]:
        del configuration
        try:
            payload, _ = await self._about(credential)
        except ProviderTransportError as error:
            raise self._transport_error(
                error,
                operation="resource.discover",
                correlation_id="provider-discovery",
            ) from error
        user = mapping(payload.get("user"))
        email = text(user.get("emailAddress"))
        permission_id = text(user.get("permissionId"))
        display_name = email or text(user.get("displayName")) or permission_id
        external_id = permission_id or email.lower()
        return (
            ResourceDescriptor(
                id=f"google_drive:{external_id}",
                provider="google_drive",
                resource_type="drive",
                external_id=external_id,
                display_name=f"Google Drive · {display_name}",
                metadata={
                    "emailAddress": email or None,
                    "displayName": text(user.get("displayName")) or None,
                    "permissionId": permission_id or None,
                },
                health="healthy",
                available_capabilities=tuple(
                    item.scope for item in self.manifest.capabilities
                ),
                web_url="https://drive.google.com/drive/my-drive",
                configuration={"account": email or external_id},
            ),
        )

    async def check_health(
        self,
        *,
        configuration: dict[str, str],
        credential: str | None,
    ) -> ProviderHealth:
        resources = await self.discover_resources(
            configuration=configuration,
            credential=credential,
        )
        resource = resources[0]
        return ProviderHealth(
            state="healthy",
            message=f"{resource.display_name} is reachable.",
            checked_at=datetime.now(UTC),
            metadata=resource.metadata,
        )

    async def execute(
        self,
        *,
        request: ExecutionRequest,
        configuration: dict[str, str],
        credential: str | None,
    ) -> ExecutionResult:
        started_at = datetime.now(UTC)
        operation = request.operation
        try:
            if operation == "drive.profile.read":
                payload, request_id = await self._about(credential)
                user = mapping(payload.get("user"))
                output = {
                    "emailAddress": text(user.get("emailAddress")) or None,
                    "displayName": text(user.get("displayName")) or None,
                    "permissionId": text(user.get("permissionId")) or None,
                }
            elif operation == "files.recent.read":
                if not credential:
                    raise self._execution_error(
                        operation=operation,
                        correlation_id=request.correlation_id,
                        code="authentication_error",
                        retryable=False,
                        safe_message="Google Drive requires an access token.",
                    )
                response = await self._http.request(
                    provider="Google Drive",
                    method="GET",
                    url=(
                        "https://www.googleapis.com/drive/v3/files"
                        "?pageSize=20&orderBy=modifiedTime%20desc"
                        "&fields=files(id,name,mimeType,modifiedTime,webViewLink,ownedByMe,starred),nextPageToken"
                    ),
                    credential=credential,
                )
                payload = mapping(response.data)
                rows = payload.get("files")
                output = [
                    {
                        "id": text(file.get("id")),
                        "name": text(file.get("name")),
                        "mimeType": text(file.get("mimeType")),
                        "modifiedTime": text(file.get("modifiedTime")),
                        "webViewLink": text(file.get("webViewLink")),
                        "ownedByMe": file.get("ownedByMe") is True,
                        "starred": file.get("starred") is True,
                    }
                    for raw in rows
                    if isinstance(rows, list) and isinstance(raw, dict)
                    for file in [dict(raw)]
                ] if isinstance(rows, list) else []
                request_id = response.request_id
            else:
                raise self._execution_error(
                    operation=operation,
                    correlation_id=request.correlation_id,
                    code="unsupported_operation",
                    retryable=False,
                    safe_message="Google Drive does not implement the requested operation.",
                )
        except ProviderTransportError as error:
            raise self._transport_error(
                error,
                operation=operation,
                correlation_id=request.correlation_id,
            ) from error

        result = self._result(
            request=request,
            output=output,
            provider_request_id=request_id,
            started_at=started_at,
        )
        verification = await self.verify(
            request=request,
            result=result,
            configuration=configuration,
            credential=credential,
        )
        return self._result(
            request=request,
            output=output,
            provider_request_id=request_id,
            started_at=started_at,
            verification=verification,
        )

    async def verify(
        self,
        *,
        request: ExecutionRequest,
        result: ExecutionResult,
        configuration: dict[str, str],
        credential: str | None,
    ) -> VerificationResult:
        del configuration, credential
        return VerificationResult(
            verified=result.status == "success",
            summary="Google Drive response was received successfully.",
            details={"operation": request.operation},
        )


google_drive_provider = GoogleDriveProvider()
