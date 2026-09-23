from __future__ import annotations

import re
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
from app.execution.providers.native.base import NativeProvider, mapping, text
from app.execution.providers.native.http import ProviderTransportError

CAPABILITIES = (
    CapabilityDescriptor(
        scope="slack.workspace.identity.read",
        provider="slack",
        resource_type="workspace",
        operation="workspace.identity.read",
        mode="read",
        risk="low",
        requires_credential=True,
        side_effect=False,
        approval_recommendation="none",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object"},
        description="Read the connected Slack workspace and authenticated actor identity.",
        target="identity",
    ),
    CapabilityDescriptor(
        scope="slack.channels.read",
        provider="slack",
        resource_type="workspace",
        operation="channels.read",
        mode="read",
        risk="medium",
        requires_credential=True,
        side_effect=False,
        approval_recommendation="none",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "array"},
        description="Read a bounded list of channels visible to the supplied Slack token.",
        target="channels",
    ),
    CapabilityDescriptor(
        scope="slack.messages.create",
        provider="slack",
        resource_type="workspace",
        operation="chat.message.create",
        mode="write",
        risk="high",
        requires_credential=True,
        side_effect=True,
        approval_recommendation="required",
        input_schema={
            "type": "object",
            "required": ["channel", "text"],
            "properties": {
                "channel": {"type": "string"},
                "text": {"type": "string", "maxLength": 4000},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        description="Post a bounded message to a connected Slack channel after human approval.",
        target="message",
    ),
)


class SlackProvider(NativeProvider):
    manifest = ProviderManifest(
        provider="slack",
        display_name="Slack",
        kind="native_api",
        version="1.0.0",
        credential_strategy="api_token",
        capabilities=CAPABILITIES,
    )

    async def _auth(
        self, credential: str | None
    ) -> tuple[dict[str, object], str | None]:
        if not credential:
            raise self._execution_error(
                operation="workspace.identity.read",
                correlation_id="provider-discovery",
                code="authentication_error",
                retryable=False,
                safe_message="Slack requires an access token.",
            )
        response = await self._http.request(
            provider="Slack",
            method="POST",
            url="https://slack.com/api/auth.test",
            credential=credential,
        )
        payload = mapping(response.data)
        if payload.get("ok") is not True:
            raise self._execution_error(
                operation="workspace.identity.read",
                correlation_id="provider-discovery",
                code="authentication_error",
                retryable=False,
                safe_message=(
                    f"Slack rejected the token: {text(payload.get('error')) or 'unknown'}."
                ),
            )
        team_id = text(payload.get("team_id"))
        if not team_id:
            raise self._execution_error(
                operation="workspace.identity.read",
                correlation_id="provider-discovery",
                code="temporary_provider_error",
                retryable=True,
                safe_message="Slack returned an invalid workspace identity.",
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
            payload, _ = await self._auth(credential)
        except ProviderTransportError as error:
            raise self._transport_error(
                error,
                operation="resource.discover",
                correlation_id="provider-discovery",
            ) from error
        team_id = text(payload.get("team_id"))
        team = text(payload.get("team")) or team_id
        web_url = text(payload.get("url"))
        if not web_url.startswith("https://"):
            web_url = f"https://app.slack.com/client/{team_id}"
        return (
            ResourceDescriptor(
                id=f"slack:{team_id.lower()}",
                provider="slack",
                resource_type="workspace",
                external_id=team_id.lower(),
                display_name=f"Slack · {team}",
                metadata={
                    "teamId": team_id,
                    "team": team,
                    "userId": text(payload.get("user_id")) or None,
                    "user": text(payload.get("user")) or None,
                    "enterpriseId": text(payload.get("enterprise_id")) or None,
                },
                health="healthy",
                available_capabilities=tuple(
                    item.scope for item in self.manifest.capabilities
                ),
                web_url=web_url,
                configuration={"teamId": team_id},
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

    async def normalize_input(
        self,
        *,
        operation: str,
        input: dict[str, object],
    ) -> dict[str, object]:
        if operation != "chat.message.create":
            return await super().normalize_input(operation=operation, input=input)
        unknown = [key for key in input if key not in {"channel", "text"}]
        if unknown:
            raise self._execution_error(
                operation=operation,
                correlation_id="input-validation",
                code="validation_error",
                retryable=False,
                safe_message=f"Unsupported Slack message field: {unknown[0]}.",
            )
        channel = text(input.get("channel")).strip()
        message = text(input.get("text")).strip()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{2,79}", channel, re.IGNORECASE):
            raise self._execution_error(
                operation=operation,
                correlation_id="input-validation",
                code="validation_error",
                retryable=False,
                safe_message="Slack message channel must be a valid channel identifier.",
            )
        if not message or len(message) > 4000:
            raise self._execution_error(
                operation=operation,
                correlation_id="input-validation",
                code="validation_error",
                retryable=False,
                safe_message="Slack message text must be between 1 and 4,000 characters.",
            )
        return {"channel": channel, "text": message}

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
            if operation == "workspace.identity.read":
                payload, request_id = await self._auth(credential)
                team_id = text(payload.get("team_id"))
                output = {
                    "teamId": team_id,
                    "team": text(payload.get("team")) or None,
                    "userId": text(payload.get("user_id")) or None,
                    "user": text(payload.get("user")) or None,
                    "enterpriseId": text(payload.get("enterprise_id")) or None,
                }
            elif operation == "channels.read":
                if not credential:
                    raise self._execution_error(
                        operation=operation,
                        correlation_id=request.correlation_id,
                        code="authentication_error",
                        retryable=False,
                        safe_message="Slack requires an access token.",
                    )
                team_id = configuration.get("teamId", "")
                suffix = f"&team_id={quote(team_id)}" if team_id else ""
                response = await self._http.request(
                    provider="Slack",
                    method="GET",
                    url=(
                        "https://slack.com/api/conversations.list"
                        "?limit=100&exclude_archived=true"
                        "&types=public_channel,private_channel"
                        f"{suffix}"
                    ),
                    credential=credential,
                )
                payload = mapping(response.data)
                if payload.get("ok") is not True:
                    raise self._execution_error(
                        operation=operation,
                        correlation_id=request.correlation_id,
                        code="authorization_error",
                        retryable=False,
                        safe_message=(
                            f"Slack conversations.list failed: "
                            f"{text(payload.get('error')) or 'unknown'}."
                        ),
                    )
                rows = payload.get("channels")
                output = [
                    {
                        "id": text(channel.get("id")),
                        "name": text(channel.get("name")),
                        "isPrivate": channel.get("is_private") is True,
                        "isMember": channel.get("is_member") is True,
                        "topic": text(mapping(channel.get("topic")).get("value")),
                        "purpose": text(mapping(channel.get("purpose")).get("value")),
                    }
                    for raw in rows
                    if isinstance(rows, list) and isinstance(raw, dict)
                    for channel in [dict(raw)]
                ] if isinstance(rows, list) else []
                request_id = response.request_id
            elif operation == "chat.message.create":
                if not credential:
                    raise self._execution_error(
                        operation=operation,
                        correlation_id=request.correlation_id,
                        code="authentication_error",
                        retryable=False,
                        safe_message="Slack message creation requires a connected access token.",
                    )
                normalized = await self.normalize_input(
                    operation=operation,
                    input=request.input,
                )
                response = await self._http.request(
                    provider="Slack",
                    method="POST",
                    url="https://slack.com/api/chat.postMessage",
                    credential=credential,
                    json_body=normalized,
                )
                payload = mapping(response.data)
                if payload.get("ok") is not True:
                    raise self._execution_error(
                        operation=operation,
                        correlation_id=request.correlation_id,
                        code="validation_error",
                        retryable=False,
                        safe_message=(
                            f"Slack chat.postMessage failed: "
                            f"{text(payload.get('error')) or 'unknown'}."
                        ),
                    )
                output = {
                    "channel": text(normalized.get("channel")),
                    "ts": text(payload.get("ts")),
                    "message": text(mapping(payload.get("message")).get("text")),
                }
                request_id = response.request_id
            else:
                raise self._execution_error(
                    operation=operation,
                    correlation_id=request.correlation_id,
                    code="unsupported_operation",
                    retryable=False,
                    safe_message="Slack does not implement the requested operation.",
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
        if request.operation == "chat.message.create":
            output = mapping(result.output)
            verified = bool(text(output.get("channel")) and text(output.get("ts")))
            return VerificationResult(
                verified=verified,
                summary=(
                    "Slack returned a channel and message timestamp."
                    if verified
                    else "Slack message creation could not be verified."
                ),
                details={
                    "channel": output.get("channel"),
                    "ts": output.get("ts"),
                },
            )
        return VerificationResult(
            verified=result.status == "success",
            summary="Slack read response was received successfully.",
            details={"operation": request.operation},
        )


slack_provider = SlackProvider()
