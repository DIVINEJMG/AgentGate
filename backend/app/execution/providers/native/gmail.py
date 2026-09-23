from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote

from app.execution.contracts import (
    CapabilityDescriptor,
    ExecutionRequest,
    ExecutionResult,
    ProviderHealth,
    ProviderManifest,
    ResourceDescriptor,
    VerificationResult,
)
from app.execution.providers.native.base import NativeProvider, mapping, number, text
from app.execution.providers.native.http import ProviderTransportError

CAPABILITIES = (
    CapabilityDescriptor(
        scope="gmail.mailbox.profile.read",
        provider="gmail",
        resource_type="mailbox",
        operation="mailbox.profile.read",
        mode="read",
        risk="low",
        requires_credential=True,
        side_effect=False,
        approval_recommendation="none",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object"},
        description="Read the connected Gmail mailbox identity and message counts.",
        target="profile",
    ),
    CapabilityDescriptor(
        scope="gmail.messages.recent.read",
        provider="gmail",
        resource_type="mailbox",
        operation="messages.recent.read",
        mode="read",
        risk="medium",
        requires_credential=True,
        side_effect=False,
        approval_recommendation="none",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "array"},
        description="Read bounded metadata and snippets for the five most recent Gmail messages.",
        target="recent_messages",
    ),
)


class GmailProvider(NativeProvider):
    manifest = ProviderManifest(
        provider="gmail",
        display_name="Gmail",
        kind="native_api",
        version="1.0.0",
        credential_strategy="oauth_access_token",
        capabilities=CAPABILITIES,
    )

    async def _profile(self, credential: str | None) -> tuple[dict[str, object], str | None]:
        if not credential:
            raise self._execution_error(
                operation="mailbox.profile.read",
                correlation_id="provider-discovery",
                code="authentication_error",
                retryable=False,
                safe_message="Gmail requires an access token.",
            )
        response = await self._http.request(
            provider="Gmail",
            method="GET",
            url="https://gmail.googleapis.com/gmail/v1/users/me/profile",
            credential=credential,
        )
        payload = mapping(response.data)
        email = text(payload.get("emailAddress"))
        if not email or "@" not in email:
            raise self._execution_error(
                operation="mailbox.profile.read",
                correlation_id="provider-discovery",
                code="temporary_provider_error",
                retryable=True,
                safe_message="Gmail returned an invalid mailbox profile.",
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
            payload, _ = await self._profile(credential)
        except ProviderTransportError as error:
            raise self._transport_error(
                error,
                operation="resource.discover",
                correlation_id="provider-discovery",
            ) from error
        email = text(payload.get("emailAddress"))
        return (
            ResourceDescriptor(
                id=f"gmail:{email.lower()}",
                provider="gmail",
                resource_type="mailbox",
                external_id=email.lower(),
                display_name=f"Gmail · {email}",
                metadata={
                    "emailAddress": email,
                    "messagesTotal": number(payload.get("messagesTotal")),
                    "threadsTotal": number(payload.get("threadsTotal")),
                },
                health="healthy",
                available_capabilities=tuple(item.scope for item in self.manifest.capabilities),
                web_url="https://mail.google.com/",
                configuration={"account": email},
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
            message=f"Gmail mailbox {resource.external_id} is reachable.",
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
            if operation == "mailbox.profile.read":
                payload, request_id = await self._profile(credential)
                output = {
                    "emailAddress": text(payload.get("emailAddress")),
                    "messagesTotal": number(payload.get("messagesTotal")),
                    "threadsTotal": number(payload.get("threadsTotal")),
                }
            elif operation == "messages.recent.read":
                if not credential:
                    raise self._execution_error(
                        operation=operation,
                        correlation_id=request.correlation_id,
                        code="authentication_error",
                        retryable=False,
                        safe_message="Gmail requires an access token.",
                    )
                listed = await self._http.request(
                    provider="Gmail",
                    method="GET",
                    url="https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=5",
                    credential=credential,
                )
                root = mapping(listed.data)
                rows = root.get("messages")
                ids = (
                    [
                        text(mapping(item).get("id"))
                        for item in rows
                        if isinstance(rows, list)
                        and isinstance(item, dict)
                        and text(mapping(item).get("id"))
                    ]
                    if isinstance(rows, list)
                    else []
                )
                messages: list[dict[str, object]] = []
                for message_id in ids[:5]:
                    detail = await self._http.request(
                        provider="Gmail",
                        method="GET",
                        url=(
                            "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
                            f"{quote(message_id)}?format=metadata"
                            "&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date"
                        ),
                        credential=credential,
                    )
                    payload = mapping(detail.data)
                    payload_root = mapping(payload.get("payload"))
                    headers = payload_root.get("headers")
                    header_map: dict[str, str] = {}
                    if isinstance(headers, list):
                        for raw in headers:
                            header = mapping(raw)
                            name = text(header.get("name")).lower()
                            if name:
                                header_map[name] = text(header.get("value"))
                    messages.append(
                        {
                            "id": text(payload.get("id")),
                            "threadId": text(payload.get("threadId")),
                            "from": header_map.get("from", ""),
                            "subject": header_map.get("subject", ""),
                            "date": header_map.get("date", ""),
                            "snippet": text(payload.get("snippet")),
                        }
                    )
                output = messages
                request_id = listed.request_id
            else:
                raise self._execution_error(
                    operation=operation,
                    correlation_id=request.correlation_id,
                    code="unsupported_operation",
                    retryable=False,
                    safe_message="Gmail does not implement the requested operation.",
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
            summary="Gmail response was received successfully.",
            details={"operation": request.operation},
        )


gmail_provider = GmailProvider()
