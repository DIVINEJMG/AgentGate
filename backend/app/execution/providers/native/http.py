from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.execution.contracts import ExecutionErrorCode


@dataclass(frozen=True, slots=True)
class ProviderHTTPResponse:
    data: object
    request_id: str | None
    status_code: int


class ProviderTransportError(RuntimeError):
    def __init__(
        self,
        *,
        code: ExecutionErrorCode,
        retryable: bool,
        safe_message: str,
        internal_details: str | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.retryable = retryable
        self.safe_message = safe_message
        self.internal_details = internal_details


class ProviderHTTPClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        user_agent: str = "Aduoryn/1.0",
    ) -> None:
        self._timeout = httpx.Timeout(timeout_seconds)
        self._user_agent = user_agent

    async def request(
        self,
        *,
        provider: str,
        method: str,
        url: str,
        credential: str | None,
        json_body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        request_id_headers: tuple[str, ...] = (
            "x-request-id",
            "x-github-request-id",
        ),
    ) -> ProviderHTTPResponse:
        request_headers = {
            "Accept": "application/json",
            "User-Agent": self._user_agent,
            **(headers or {}),
        }
        if credential:
            request_headers["Authorization"] = f"Bearer {credential}"
        if json_body is not None:
            request_headers["Content-Type"] = "application/json"

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
            ) as client:
                response = await client.request(
                    method,
                    url,
                    headers=request_headers,
                    json=json_body,
                )
        except httpx.TimeoutException as error:
            raise ProviderTransportError(
                code="timeout",
                retryable=True,
                safe_message=f"{provider} request timed out.",
                internal_details=str(error),
            ) from error
        except httpx.RequestError as error:
            raise ProviderTransportError(
                code="provider_unavailable",
                retryable=True,
                safe_message=f"{provider} is temporarily unavailable.",
                internal_details=str(error),
            ) from error

        request_id = next(
            (
                response.headers.get(header)
                for header in request_id_headers
                if response.headers.get(header)
            ),
            None,
        )
        if response.status_code == 401:
            raise ProviderTransportError(
                code="authentication_error",
                retryable=False,
                safe_message=f"{provider} rejected the supplied credential.",
                internal_details=response.text[:500],
            )
        if response.status_code == 403:
            raise ProviderTransportError(
                code="authorization_error",
                retryable=False,
                safe_message=f"{provider} denied this request.",
                internal_details=response.text[:500],
            )
        if response.status_code == 404:
            raise ProviderTransportError(
                code="resource_not_found",
                retryable=False,
                safe_message=f"{provider} resource was not found.",
                internal_details=response.text[:500],
            )
        if response.status_code == 429:
            raise ProviderTransportError(
                code="rate_limited",
                retryable=True,
                safe_message=f"{provider} rate limit was reached.",
                internal_details=response.text[:500],
            )
        if response.status_code in {408, 425, 500, 502, 503, 504}:
            raise ProviderTransportError(
                code="temporary_provider_error",
                retryable=True,
                safe_message=f"{provider} returned a temporary error.",
                internal_details=f"HTTP {response.status_code}: {response.text[:500]}",
            )
        if response.status_code >= 400:
            raise ProviderTransportError(
                code="validation_error",
                retryable=False,
                safe_message=f"{provider} rejected the request.",
                internal_details=f"HTTP {response.status_code}: {response.text[:500]}",
            )

        if not response.content:
            data: object = {}
        else:
            try:
                data = response.json()
            except ValueError as error:
                raise ProviderTransportError(
                    code="temporary_provider_error",
                    retryable=True,
                    safe_message=f"{provider} returned an invalid response.",
                    internal_details=response.text[:500],
                ) from error
        return ProviderHTTPResponse(
            data=data,
            request_id=request_id,
            status_code=response.status_code,
        )
