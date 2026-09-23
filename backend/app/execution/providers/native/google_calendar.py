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
from app.execution.providers.native.base import NativeProvider, mapping, text
from app.execution.providers.native.http import ProviderTransportError

CAPABILITIES = (
    CapabilityDescriptor(
        scope="google_calendar.primary.read",
        provider="google_calendar",
        resource_type="calendar",
        operation="calendar.primary.read",
        mode="read",
        risk="low",
        requires_credential=True,
        side_effect=False,
        approval_recommendation="none",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object"},
        description="Read the connected primary calendar identity and timezone.",
        target="primary_calendar",
    ),
    CapabilityDescriptor(
        scope="google_calendar.events.upcoming.read",
        provider="google_calendar",
        resource_type="calendar",
        operation="events.upcoming.read",
        mode="read",
        risk="medium",
        requires_credential=True,
        side_effect=False,
        approval_recommendation="none",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "array"},
        description="Read the next ten upcoming events from the primary calendar.",
        target="upcoming_events",
    ),
)


class GoogleCalendarProvider(NativeProvider):
    manifest = ProviderManifest(
        provider="google_calendar",
        display_name="Google Calendar",
        kind="native_api",
        version="1.0.0",
        credential_strategy="oauth_access_token",
        capabilities=CAPABILITIES,
    )

    async def _primary(
        self, credential: str | None
    ) -> tuple[dict[str, object], str | None]:
        if not credential:
            raise self._execution_error(
                operation="calendar.primary.read",
                correlation_id="provider-discovery",
                code="authentication_error",
                retryable=False,
                safe_message="Google Calendar requires an access token.",
            )
        response = await self._http.request(
            provider="Google Calendar",
            method="GET",
            url="https://www.googleapis.com/calendar/v3/calendars/primary",
            credential=credential,
        )
        payload = mapping(response.data)
        calendar_id = text(payload.get("id"))
        if not calendar_id:
            raise self._execution_error(
                operation="calendar.primary.read",
                correlation_id="provider-discovery",
                code="temporary_provider_error",
                retryable=True,
                safe_message="Google Calendar returned an invalid primary calendar.",
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
            payload, _ = await self._primary(credential)
        except ProviderTransportError as error:
            raise self._transport_error(
                error,
                operation="resource.discover",
                correlation_id="provider-discovery",
            ) from error
        calendar_id = text(payload.get("id"))
        summary = text(payload.get("summary")) or calendar_id
        return (
            ResourceDescriptor(
                id=f"google_calendar:{calendar_id.lower()}",
                provider="google_calendar",
                resource_type="calendar",
                external_id=calendar_id.lower(),
                display_name=f"Google Calendar · {summary}",
                metadata={
                    "calendarId": calendar_id,
                    "summary": summary,
                    "timeZone": text(payload.get("timeZone")) or None,
                    "accessRole": text(payload.get("accessRole")) or None,
                },
                health="healthy",
                available_capabilities=tuple(
                    item.scope for item in self.manifest.capabilities
                ),
                web_url="https://calendar.google.com/calendar/u/0/r",
                configuration={"calendarId": "primary"},
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
            if operation == "calendar.primary.read":
                payload, request_id = await self._primary(credential)
                output = {
                    "calendarId": text(payload.get("id")),
                    "summary": text(payload.get("summary")) or None,
                    "timeZone": text(payload.get("timeZone")) or None,
                    "accessRole": text(payload.get("accessRole")) or None,
                }
            elif operation == "events.upcoming.read":
                if not credential:
                    raise self._execution_error(
                        operation=operation,
                        correlation_id=request.correlation_id,
                        code="authentication_error",
                        retryable=False,
                        safe_message="Google Calendar requires an access token.",
                    )
                time_min = quote(datetime.now(UTC).isoformat())
                response = await self._http.request(
                    provider="Google Calendar",
                    method="GET",
                    url=(
                        "https://www.googleapis.com/calendar/v3/calendars/primary/events"
                        "?maxResults=10&singleEvents=true&orderBy=startTime"
                        f"&timeMin={time_min}"
                    ),
                    credential=credential,
                )
                payload = mapping(response.data)
                rows = payload.get("items")
                output = [
                    {
                        "id": text(event.get("id")),
                        "summary": text(event.get("summary")),
                        "status": text(event.get("status")),
                        "start": text(mapping(event.get("start")).get("dateTime"))
                        or text(mapping(event.get("start")).get("date")),
                        "end": text(mapping(event.get("end")).get("dateTime"))
                        or text(mapping(event.get("end")).get("date")),
                        "htmlLink": text(event.get("htmlLink")),
                        "location": text(event.get("location")),
                    }
                    for raw in rows
                    if isinstance(rows, list) and isinstance(raw, dict)
                    for event in [dict(raw)]
                ] if isinstance(rows, list) else []
                request_id = response.request_id
            else:
                raise self._execution_error(
                    operation=operation,
                    correlation_id=request.correlation_id,
                    code="unsupported_operation",
                    retryable=False,
                    safe_message="Google Calendar does not implement the requested operation.",
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
            summary="Google Calendar response was received successfully.",
            details={"operation": request.operation},
        )


google_calendar_provider = GoogleCalendarProvider()
