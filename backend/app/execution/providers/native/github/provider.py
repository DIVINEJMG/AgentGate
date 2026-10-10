from __future__ import annotations

import json
import re
import time
from collections import OrderedDict
from contextlib import AsyncExitStack
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from jsonschema import validate

from app.bootstrap.settings import settings
from app.execution.contracts import ProviderHealth, ProviderManifest, VerificationResult
from app.execution.providers.native.base import NativeProvider
from app.execution.providers.native.http import ProviderTransportError

from .authentication import GitHubAuthentication
from .catalog import assembled_catalog
from .events import verify_event
from .resources import GitHubResources, allowed_actions
from .safety import SECRET, TASK_CONSTRAINT_SCHEMA
from .transport import GitHubHTTPClient, GitHubResponse, account_context


def operation_missing_content(operation, output):
    return (
        operation == "repository.contents.read"
        and isinstance(output, dict)
        and output.get("exists") is False
    )


class ExpandedGitHubProvider(NativeProvider):
    task_constraint_schema = TASK_CONSTRAINT_SCHEMA
    manifest = ProviderManifest(
        provider="github",
        display_name="GitHub",
        kind="native_api",
        version="2.0.0",
        credential_strategy="api_token",
        capabilities=tuple(a.descriptor() for a in assembled_catalog()),
    )

    def __init__(self, http: Any = None):
        super().__init__()
        self._github_http: Any = http or GitHubHTTPClient()
        self.actions = {a.operation: a for a in assembled_catalog()}
        from importlib.util import find_spec

        coding_ready = (
            settings.coding_execution_enabled
            and settings.e2b_api_key
            and settings.coding_template_id
            and find_spec("e2b")
        )
        self.manifest = replace(
            self.manifest,
            capabilities=tuple(
                c
                for c in self.manifest.capabilities
                if ".workspace." not in c.scope or coding_ready
            ),
        )
        self.auth = GitHubAuthentication(self._github_http)
        self.resources = GitHubResources(self)
        self._immutable_reads: OrderedDict = OrderedDict()

    def runtime_catalog(self):
        scopes = [c.scope for c in self.manifest.capabilities if ".workspace." in c.scope]
        return {
            "e2b": {
                "provider": "github",
                "scopes": scopes,
                "reason": ""
                if scopes
                else "Coding execution needs its feature flag, E2B dependency, API key and template configured.",
            }
        }

    def resource_capabilities(self, *, resource, connection, state):
        """Refresh runtime availability without expanding saved business capabilities or grants."""
        available = {c.scope for c in self.manifest.capabilities}
        scopes = tuple((state.credential_metadata or {}).get("scopes", []))
        if not scopes:
            scopes = tuple(
                str((connection.config or {}).get("githubPermissionScopes", "")).split(",")
            )
        consent = set(allowed_actions(self.actions.values(), scopes)) if any(scopes) else available
        caps = set(resource.capabilities) & available & consent
        if resource.resource_type == "repository" and resource.health == "healthy":
            caps.update(s for s in available & consent if ".workspace." in s)
        return sorted(caps)

    def explicit_task_resources(self, instruction):
        # Recognize repository coordinates, never infer them from worker bindings.
        return list(
            dict.fromkeys(
                re.findall(
                    r"(?<![\w/])(?:https://github\.com/)?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?![\w/])",
                    instruction,
                )
            )
        )

    async def api(self, method, path, credential, payload=None, **kwargs) -> GitHubResponse:
        return await self._github_http.request(
            provider="GitHub",
            method=method,
            url="https://api.github.com" + path,
            credential=credential,
            json_body=payload,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": settings.github_api_version,
            },
            **kwargs,
        )

    async def graphql(self, query, variables, credential) -> dict[str, Any]:
        response = await self.api(
            "POST", "/graphql", credential, {"query": query, "variables": variables}
        )
        if not isinstance(response.data, dict) or response.data.get("errors"):
            # GraphQL may partially succeed. Do not expose messages containing
            # provider text or misclassify a mutation as safely retryable.
            raise ProviderTransportError(
                code="verification_failed",
                retryable=False,
                safe_message="GitHub GraphQL did not return a complete verifiable result.",
            )
        return response.data["data"]

    def repo_path(self, configuration):
        repo = configuration.get("repository", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("Repository must use owner/repository coordinates.")
        # A stable numeric identity survives renames. Canonical endpoints return
        # the current coordinates for subsequent GitHub-provided links.
        return "/repos/" + quote(repo, safe="/")

    def project_capabilities(self):
        return tuple(a.scope for a in self.actions.values() if a.resource_type == "project")

    def capabilities_for_granted_scopes(self, *, scopes):
        return allowed_actions(tuple(self.actions.values()), scopes)

    async def discover_resource_page(self, **kwargs):
        return await self.resources.page(**kwargs)

    async def lookup_resource(self, **kwargs):
        return await self.resources.lookup(**kwargs)

    async def discover_resources(self, *, configuration, credential):
        return (
            await self.resources.page(
                configuration=configuration, credential=credential, cursor=None
            )
        ).resources

    async def check_health(self, *, configuration, credential):
        if configuration.get("installationId"):
            await self.api("GET", "/installation/repositories?per_page=1", credential)
        elif configuration.get("projectNodeId"):
            await self.resources.lookup(
                external_id=configuration["projectNodeId"],
                configuration=configuration,
                credential=credential,
            )
        else:
            await self.api("GET", self.repo_path(configuration), credential)
        return ProviderHealth(
            state="healthy", message="GitHub resource is reachable.", checked_at=datetime.now(UTC)
        )

    def task_prerequisites(self, scopes):
        """Catalog dependencies expose read tools, never a business workflow."""
        selected = set(scopes)
        actions = {a.scope: a for a in self.actions.values()}
        pending = list(selected)
        while pending:
            scope = pending.pop()
            action = actions.get(scope)
            if action:
                for dependency in action.prerequisites:
                    prerequisite = actions[dependency]
                    if prerequisite.write or prerequisite.resource_type != action.resource_type:
                        raise ValueError(
                            "Task prerequisites must be reads on the same resource type."
                        )
                    if dependency not in selected:
                        selected.add(dependency)
                        pending.append(dependency)
        return sorted(selected)

    async def normalize_input(self, *, operation, input):
        action = self.actions.get(operation)
        if action is None:
            raise self._execution_error(
                operation=operation,
                correlation_id="input",
                code="unsupported_operation",
                retryable=False,
                safe_message="GitHub action is unavailable.",
            )
        validate(input, action.descriptor().input_schema)
        return dict(input)

    def action_capability(self, *, capability, input):
        if capability.operation == "repository.review.submit" and input.get("event") == "COMMENT":
            return replace(capability, risk="medium", approval_recommendation="none")
        sensitive = capability.operation == "repository.commit.create" and any(
            c.get("delete") or c.get("path", "").startswith(".github/workflows/")
            for c in input.get("changes", [])
        )
        if sensitive:
            return replace(capability, risk="high", approval_recommendation="required")
        return capability

    async def record_dispatch(self, *, session, request):
        from .evidence import record_dispatch

        await record_dispatch(session, request)

    async def record_outcome(self, *, session, request, verification):
        from .evidence import record_outcome

        await record_outcome(session, request, verification)

    async def scope_execution_credential(self, *, configuration, scope, bundle, input=None):
        if not configuration.get("installationId"):
            return bundle.access_token, bundle
        token = account_context.set(configuration["installationId"])
        try:
            return await self._scope_installation_credential(
                configuration=configuration, scope=scope, bundle=bundle, input=input
            )
        finally:
            account_context.reset(token)

    async def _scope_installation_credential(self, *, configuration, scope, bundle, input=None):
        action = next(a for a in self.actions.values() if a.scope == scope)
        name, level = action.permission.split(":")
        permissions = {name: level, "metadata": "read"}
        if action.operation == "repository.pull_request.merge":
            permissions.update({"pull_requests": "read", "checks": "read", "statuses": "read"})
        if any(
            change.get("path", "").startswith(".github/workflows/")
            for change in (input or {}).get("changes", [])
        ):
            if "workflows:write" not in bundle.scopes:
                raise PermissionError(
                    "Provider consent does not cover workflow-file modifications."
                )
            permissions["workflows"] = "write"
        repositories = (
            [configuration["repositoryId"]]
            if configuration.get("repositoryId")
            else json.loads(configuration.get("authorizedRepositoryIds", "[]"))
        )
        if not repositories and action.resource_type == "repository":
            raise PermissionError("Installation execution needs exact repository task bindings.")
        from app.application.services.integration_credentials import decode_bundle, encode_bundle

        user = decode_bundle(bundle.renewal_metadata["userBundle"])
        if user.expires_at and user.expires_at <= datetime.now(UTC):
            user = await self.auth.renew_credentials(bundle=user)
            bundle = replace(
                bundle,
                renewal_metadata={**bundle.renewal_metadata, "userBundle": encode_bundle(user)},
            )
        if action.resource_type == "project":
            project = await self.graphql(
                "query($id:ID!){node(id:$id){... on ProjectV2{id viewerCanUpdate}}}",
                {"id": configuration["projectNodeId"]},
                user.access_token,
            )
            if not project.get("node") or (action.write and not project["node"]["viewerCanUpdate"]):
                raise PermissionError(
                    "The connection owner's GitHub access no longer covers this Project action."
                )
        for repository_id in repositories:
            observed = await self.api("GET", "/repositories/" + repository_id, user.access_token)
            permission = "push" if action.write and action.resource_type == "repository" else "pull"
            if not observed.data.get("permissions", {}).get(permission):
                raise PermissionError(
                    "The connection owner's GitHub access no longer covers this action."
                )
        scoped = await self.auth.installation_bundle(
            configuration["installationId"], repositories=repositories, permissions=permissions
        )
        return scoped.access_token, bundle

    async def initiate_authorization(self, **kwargs):
        return await self.auth.initiate_authorization(**kwargs)

    async def complete_authorization(self, **kwargs):
        return await self.auth.complete_authorization(**kwargs)

    async def renew_credentials(self, **kwargs):
        try:
            return await self.auth.renew_credentials(**kwargs)
        except ProviderTransportError as error:
            raise self._transport_error(
                error, operation="credential.renew", correlation_id="renew"
            ) from error

    def can_renew_credentials(self, *, bundle):
        return bundle.renewal_metadata.get("strategy") == "github_installation"

    async def revoke_credentials(self, **kwargs):
        return await self.auth.revoke_credentials(**kwargs)

    async def accept_event(self, *, session, event):
        from .event_routing import accept_github_event

        return await accept_github_event(session, event)

    async def event_allows_matching(self, *, session, event):
        from .event_routing import github_event_state

        return await github_event_state(session, event)

    async def verify_and_normalize_event(self, **kwargs):
        return verify_event(**kwargs)

    async def execute(self, *, request, configuration, credential):
        async with AsyncExitStack() as stack:
            if isinstance(self._github_http, GitHubHTTPClient):
                await stack.enter_async_context(self._github_http.execution_session())
            return await self._execute(
                request=request, configuration=configuration, credential=credential
            )

    async def _execute(self, *, request, configuration, credential):
        configuration = self.execution_configuration(configuration)
        action = self.actions[request.operation]
        started = datetime.now(UTC)
        token = account_context.set(configuration.get("installationId", request.resource.id))
        try:
            authority = request.resource.metadata.get("authorityVersion")
            revision = request.input.get("sha", request.input.get("ref"))
            cacheable = (
                request.operation
                in {"repository.contents.read", "repository.tree.read", "repository.commit.read"}
                and isinstance(revision, str)
                and re.fullmatch(r"[a-fA-F0-9]{40}", revision)
                and authority is not None
                and request.work_item_id is not None
            )
            cache_key = (
                str(request.organization_id),
                str(request.work_item_id),
                request.resource.id,
                authority,
                request.operation,
                json.dumps(request.input, sort_keys=True),
            )
            cached = self._immutable_reads.get(cache_key) if cacheable else None
            if cached and time.monotonic() - cached[0] <= 30:
                return self._result(
                    request=request,
                    output=deepcopy(cached[1]),
                    provider_request_id=None,
                    started_at=started,
                    verification=VerificationResult(
                        verified=True,
                        summary="Previously observed content at the exact commit was reused within this task.",
                        details={**cached[2], "reusedImmutableEvidence": True},
                    ),
                )
            # API publication always refreshes identity/default branch. Workspace
            # operations use the pinned session; only opening needs the remote identity.
            workspace_local = (
                ".workspace." in request.operation
                and request.operation != "repository.workspace.open"
            )
            if (
                action.resource_type == "repository"
                and configuration.get("repositoryId")
                and not workspace_local
            ):
                metadata = (
                    await self.api(
                        "GET", "/repositories/" + configuration["repositoryId"], credential
                    )
                ).data
                if str(metadata["id"]) != configuration["repositoryId"]:
                    raise ValueError("GitHub repository identity changed.")
                configuration.update(
                    repository=metadata["full_name"],
                    defaultBranch=metadata["default_branch"],
                    repositoryNodeId=metadata["node_id"],
                )
                if action.write and metadata.get("archived"):
                    raise ValueError("Archived repositories cannot be mutated by the worker.")
            if ".workspace." in request.operation:
                from app.execution.coding.service import execute_coding_action

                output = await execute_coding_action(self, request, configuration, credential)
                return self._result(
                    request=request,
                    output=self._sanitize(output),
                    provider_request_id=None,
                    started_at=started,
                    verification=VerificationResult(
                        verified=True,
                        summary="Isolated workspace state was inspected; GitHub publication is a separate action.",
                        details={"observedAt": datetime.now(UTC).isoformat()},
                    ),
                )
            response, canonical, output = await self._perform(
                action, request, configuration, credential
            )
            if action.write:
                evidence = await self._verify_write(
                    action, request, configuration, credential, response.data, canonical
                )
            else:
                evidence = {
                    "observedAt": datetime.now(UTC).isoformat(),
                    "requestId": response.request_id,
                }
            output = self._sanitize(output)
            verification = VerificationResult(
                verified=True,
                summary="The requested path was not found at the verified GitHub revision; no content was read or created."
                if operation_missing_content(request.operation, output)
                else "GitHub canonical state was inspected."
                if action.write
                else "GitHub response was inspected within the declared evidence limits.",
                details=evidence,
            )
            if cacheable and len(json.dumps(output)) <= 30000:
                self._immutable_reads[cache_key] = (time.monotonic(), deepcopy(output), evidence)
                self._immutable_reads.move_to_end(cache_key)
                while len(self._immutable_reads) > 32:
                    self._immutable_reads.popitem(last=False)
            return self._result(
                request=request,
                output=output,
                provider_request_id=response.request_id,
                started_at=started,
                verification=verification,
            )
        except ProviderTransportError as error:
            raise self._transport_error(
                error, operation=request.operation, correlation_id=request.correlation_id
            ) from error
        except (ValueError, KeyError) as error:
            raise self._execution_error(
                operation=request.operation,
                correlation_id=request.correlation_id,
                code="validation_error",
                retryable=False,
                safe_message=str(error)
                if isinstance(error, ValueError)
                else "GitHub returned incomplete action evidence.",
            ) from error
        finally:
            account_context.reset(token)

    @staticmethod
    def _sanitize(value):
        if isinstance(value, str):
            return SECRET.sub("[REDACTED CREDENTIAL]", value)
        if isinstance(value, list):
            return [ExpandedGitHubProvider._sanitize(v) for v in value]
        if isinstance(value, dict):
            return {
                k: ExpandedGitHubProvider._sanitize(v)
                for k, v in value.items()
                if k not in {"token", "access_token", "refresh_token", "private_key"}
            }
        return value

    async def verify(self, *, request, result, configuration, credential):
        del request, configuration, credential
        return result.verification or VerificationResult(
            verified=False, summary="No canonical GitHub evidence was recorded.", details={}
        )

    async def _perform(self, action, request, configuration, credential):
        from .execution import perform

        return await perform(self, action, request, configuration, credential)

    async def _verify_write(self, action, request, configuration, credential, data, canonical):
        from .verification import verify_write

        return await verify_write(self, action, request, configuration, credential, data, canonical)

    async def reconcile_write(self, *, request, configuration, credential):
        from .reconciliation import reconcile

        return await reconcile(
            self, request, self.execution_configuration(configuration), credential
        )

    @staticmethod
    def execution_configuration(configuration):
        configuration = dict(configuration)
        for key, default in (("constraints", {}), ("authorizedRepositoryIds", [])):
            raw = configuration.get(key, default)
            configuration[key] = json.loads(raw) if isinstance(raw, str) else raw
        return configuration
