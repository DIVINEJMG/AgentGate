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
from app.execution.providers.native.base import NativeProvider, mapping, number, text
from app.execution.providers.native.http import ProviderTransportError

CAPABILITIES = (
    CapabilityDescriptor(
        scope="github.repository.metadata.read",
        provider="github",
        resource_type="repository",
        operation="repository.metadata.read",
        mode="read",
        risk="low",
        requires_credential=False,
        side_effect=False,
        approval_recommendation="none",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object"},
        description="Read connected repository metadata and visibility information.",
        target="metadata",
    ),
    CapabilityDescriptor(
        scope="github.repository.issues.read",
        provider="github",
        resource_type="repository",
        operation="repository.issues.read",
        mode="read",
        risk="low",
        requires_credential=False,
        side_effect=False,
        approval_recommendation="none",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "array"},
        description="Read open issues exposed by the connected repository.",
        target="issues",
    ),
    CapabilityDescriptor(
        scope="github.repository.pull_requests.read",
        provider="github",
        resource_type="repository",
        operation="repository.pull_requests.read",
        mode="read",
        risk="low",
        requires_credential=False,
        side_effect=False,
        approval_recommendation="none",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "array"},
        description="Read open pull requests exposed by the connected repository.",
        target="pull_requests",
    ),
    CapabilityDescriptor(
        scope="github.repository.issues.create",
        provider="github",
        resource_type="repository",
        operation="repository.issue.create",
        mode="write",
        risk="high",
        requires_credential=True,
        side_effect=True,
        approval_recommendation="required",
        input_schema={
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string", "maxLength": 256},
                "body": {"type": "string", "maxLength": 10000},
                "labels": {
                    "type": "array",
                    "maxItems": 10,
                    "items": {"type": "string", "maxLength": 50},
                },
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        description="Create an issue in the connected repository after human approval.",
        target="issue",
    ),
)


class GitHubProvider(NativeProvider):
    manifest = ProviderManifest(
        provider="github",
        display_name="GitHub",
        kind="native_api",
        version="1.0.0",
        credential_strategy="api_token",
        capabilities=CAPABILITIES,
    )

    def _coordinates(
        self,
        configuration: dict[str, str],
        *,
        operation: str,
        correlation_id: str,
    ) -> tuple[str, str]:
        repository = configuration.get("repository", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise self._execution_error(
                operation=operation,
                correlation_id=correlation_id,
                code="validation_error",
                retryable=False,
                safe_message="Repository must use the owner/repository format.",
            )
        owner, name = repository.split("/", 1)
        return owner, name

    async def _repository(
        self,
        configuration: dict[str, str],
        credential: str | None,
    ) -> tuple[dict[str, object], str | None]:
        owner, name = self._coordinates(
            configuration,
            operation="resource.discover",
            correlation_id="provider-discovery",
        )
        response = await self._http.request(
            provider="GitHub",
            method="GET",
            url=f"https://api.github.com/repos/{quote(owner)}/{quote(name)}",
            credential=credential,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        return mapping(response.data), response.request_id

    async def discover_resources(
        self,
        *,
        configuration: dict[str, str],
        credential: str | None,
    ) -> tuple[ResourceDescriptor, ...]:
        try:
            payload, _ = await self._repository(configuration, credential)
        except ProviderTransportError as error:
            raise self._transport_error(
                error,
                operation="resource.discover",
                correlation_id="provider-discovery",
            ) from error
        full_name = text(payload.get("full_name"))
        owner = text(mapping(payload.get("owner")).get("login"))
        name = text(payload.get("name"))
        if not full_name or not owner or not name:
            raise self._execution_error(
                operation="resource.discover",
                correlation_id="provider-discovery",
                code="temporary_provider_error",
                retryable=True,
                safe_message="GitHub returned an invalid repository response.",
            )
        web_url = text(payload.get("html_url")) or f"https://github.com/{full_name}"
        return (
            ResourceDescriptor(
                id=f"github:{full_name.lower()}",
                provider="github",
                resource_type="repository",
                external_id=full_name.lower(),
                display_name=full_name,
                metadata={
                    "owner": owner,
                    "repository": name,
                    "private": payload.get("private") is True,
                    "defaultBranch": text(payload.get("default_branch")) or "main",
                    "archived": payload.get("archived") is True,
                },
                health="healthy",
                available_capabilities=tuple(
                    capability.scope for capability in self.manifest.capabilities
                ),
                web_url=web_url,
                configuration={"repository": full_name},
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
            message=f"Repository {resource.display_name} is reachable.",
            checked_at=datetime.now(UTC),
            metadata=resource.metadata,
        )

    async def normalize_input(
        self,
        *,
        operation: str,
        input: dict[str, object],
    ) -> dict[str, object]:
        if operation != "repository.issue.create":
            return await super().normalize_input(operation=operation, input=input)

        supported = {"title", "body", "labels"}
        unknown = [key for key in input if key not in supported]
        if unknown:
            raise self._execution_error(
                operation=operation,
                correlation_id="input-validation",
                code="validation_error",
                retryable=False,
                safe_message=f"Unsupported GitHub issue field: {unknown[0]}.",
            )
        title = text(input.get("title")).strip()
        body = text(input.get("body")).strip()
        labels_raw = input.get("labels", [])
        if not title or len(title) > 256:
            raise self._execution_error(
                operation=operation,
                correlation_id="input-validation",
                code="validation_error",
                retryable=False,
                safe_message="GitHub issue title must be between 1 and 256 characters.",
            )
        if len(body) > 10_000:
            raise self._execution_error(
                operation=operation,
                correlation_id="input-validation",
                code="validation_error",
                retryable=False,
                safe_message="GitHub issue body must be 10,000 characters or fewer.",
            )
        if not isinstance(labels_raw, list):
            raise self._execution_error(
                operation=operation,
                correlation_id="input-validation",
                code="validation_error",
                retryable=False,
                safe_message="GitHub issue labels must be an array.",
            )
        labels = list(
            dict.fromkeys(label.strip() for raw in labels_raw if (label := text(raw).strip()))
        )
        if len(labels) > 10 or any(len(label) > 50 for label in labels):
            raise self._execution_error(
                operation=operation,
                correlation_id="input-validation",
                code="validation_error",
                retryable=False,
                safe_message="GitHub issue supports at most 10 labels of 50 characters or fewer.",
            )
        return {"title": title, "body": body, "labels": labels}

    async def execute(
        self,
        *,
        request: ExecutionRequest,
        configuration: dict[str, str],
        credential: str | None,
    ) -> ExecutionResult:
        started_at = datetime.now(UTC)
        operation = request.operation
        owner, name = self._coordinates(
            configuration,
            operation=operation,
            correlation_id=request.correlation_id,
        )
        repo_path = f"/repos/{quote(owner)}/{quote(name)}"
        try:
            if operation == "repository.metadata.read":
                response = await self._http.request(
                    provider="GitHub",
                    method="GET",
                    url=f"https://api.github.com{repo_path}",
                    credential=credential,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                payload = mapping(response.data)
                output = {
                    "fullName": text(payload.get("full_name")),
                    "private": payload.get("private") is True,
                    "archived": payload.get("archived") is True,
                    "defaultBranch": text(payload.get("default_branch")),
                    "openIssuesCount": number(payload.get("open_issues_count")),
                    "watchersCount": number(payload.get("watchers_count")),
                    "forksCount": number(payload.get("forks_count")),
                    "webUrl": text(payload.get("html_url")),
                }
            elif operation == "repository.issues.read":
                response = await self._http.request(
                    provider="GitHub",
                    method="GET",
                    url=f"https://api.github.com{repo_path}/issues?state=open&per_page=20",
                    credential=credential,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                rows = response.data if isinstance(response.data, list) else []
                output = [
                    {
                        "number": number(item.get("number")),
                        "title": text(item.get("title")),
                        "state": text(item.get("state")),
                        "author": text(mapping(item.get("user")).get("login")),
                        "webUrl": text(item.get("html_url")),
                        "createdAt": text(item.get("created_at")),
                        "updatedAt": text(item.get("updated_at")),
                    }
                    for raw in rows
                    if isinstance(raw, dict)
                    for item in [dict(raw)]
                    if "pull_request" not in item
                ]
            elif operation == "repository.pull_requests.read":
                response = await self._http.request(
                    provider="GitHub",
                    method="GET",
                    url=f"https://api.github.com{repo_path}/pulls?state=open&per_page=20",
                    credential=credential,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                rows = response.data if isinstance(response.data, list) else []
                output = [
                    {
                        "number": number(item.get("number")),
                        "title": text(item.get("title")),
                        "state": text(item.get("state")),
                        "draft": item.get("draft") is True,
                        "author": text(mapping(item.get("user")).get("login")),
                        "webUrl": text(item.get("html_url")),
                        "createdAt": text(item.get("created_at")),
                        "updatedAt": text(item.get("updated_at")),
                    }
                    for raw in rows
                    if isinstance(raw, dict)
                    for item in [dict(raw)]
                ]
            elif operation == "repository.issue.create":
                if not credential:
                    raise self._execution_error(
                        operation=operation,
                        correlation_id=request.correlation_id,
                        code="authentication_error",
                        retryable=False,
                        safe_message="GitHub issue creation requires a connected access token.",
                    )
                normalized = await self.normalize_input(
                    operation=operation,
                    input=request.input,
                )
                response = await self._http.request(
                    provider="GitHub",
                    method="POST",
                    url=f"https://api.github.com{repo_path}/issues",
                    credential=credential,
                    json_body=normalized,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                item = mapping(response.data)
                issue_number = number(item.get("number"))
                web_url = text(item.get("html_url"))
                if issue_number is None or not web_url:
                    raise self._execution_error(
                        operation=operation,
                        correlation_id=request.correlation_id,
                        code="verification_failed",
                        retryable=False,
                        safe_message="GitHub returned an invalid issue creation response.",
                    )
                output = {
                    "number": issue_number,
                    "title": text(item.get("title")),
                    "state": text(item.get("state")),
                    "author": text(mapping(item.get("user")).get("login")),
                    "webUrl": web_url,
                    "createdAt": text(item.get("created_at")),
                }
            else:
                raise self._execution_error(
                    operation=operation,
                    correlation_id=request.correlation_id,
                    code="unsupported_operation",
                    retryable=False,
                    safe_message="GitHub does not implement the requested operation.",
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
            provider_request_id=response.request_id,
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
            provider_request_id=response.request_id,
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
        if request.operation == "repository.issue.create":
            output = mapping(result.output)
            verified = number(output.get("number")) is not None and bool(text(output.get("webUrl")))
            return VerificationResult(
                verified=verified,
                summary=(
                    "GitHub returned a canonical issue identifier and URL."
                    if verified
                    else "GitHub issue creation could not be verified."
                ),
                details={"number": output.get("number"), "webUrl": output.get("webUrl")},
            )
        return VerificationResult(
            verified=result.status == "success",
            summary="GitHub read response was received successfully.",
            details={"operation": request.operation},
        )


github_provider = GitHubProvider()
