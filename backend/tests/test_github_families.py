"""Deterministic API evidence tests; these do not establish live App readiness."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap.settings import settings
from app.execution.coding.service import admit_capacity, inspect_command
from app.execution.contracts import ExecutionProviderError
from app.execution.providers.integration_hooks import CredentialBundle
from app.execution.providers.native.github.authentication import GitHubAuthentication
from app.execution.providers.native.github.provider import ExpandedGitHubProvider
from app.execution.providers.native.github.transport import GitHubHTTPClient
from app.infrastructure.database.models import OutboxEvent
from app.infrastructure.database.outbox import TransactionalOutbox
from tests.test_github_expanded import ScriptedHTTP, request, snapshot


@pytest.mark.parametrize(
    "operation,payload,suffix",
    [
        (
            "repository.issues.list",
            {"page": 2, "per_page": 10, "state": "closed"},
            "/issues?page=2&per_page=10&state=closed",
        ),
        ("repository.pull_request.files.read", {"number": 3, "page": 2}, "/pulls/3/files?page=2"),
        ("repository.reviews.read", {"number": 3}, "/pulls/3/reviews"),
        ("repository.workflow.jobs.read", {"runId": 42}, "/actions/runs/42/jobs"),
        ("repository.releases.read", {}, "/releases"),
        ("repository.dependabot.read", {}, "/dependabot/alerts"),
        ("repository.code_scanning.read", {}, "/code-scanning/alerts"),
        ("repository.deployment.statuses.read", {"deploymentId": 7}, "/deployments/7/statuses"),
    ],
)
async def test_read_families_use_bound_repo_and_pagination(operation, payload, suffix):
    http = ScriptedHTTP([{"items": []}])
    provider = ExpandedGitHubProvider(http=http)
    result = await provider.execute(
        request=request(provider, operation, payload),
        configuration={"repository": "owner/repo"},
        credential="test",
    )
    assert http.calls[0]["url"] == "https://api.github.com/repos/owner/repo" + suffix
    assert result.verification.verified


async def test_repository_rename_uses_stable_id_and_current_coordinates():
    http = ScriptedHTTP(
        [
            {"id": 123, "node_id": "R_123", "full_name": "new/name", "default_branch": "main"},
            {"items": []},
        ]
    )
    provider = ExpandedGitHubProvider(http=http)
    await provider.execute(
        request=request(provider, "repository.issues.list"),
        configuration={"repositoryId": "123", "repository": "old/name"},
        credential="test",
    )
    assert http.calls[0]["url"].endswith("/repositories/123")
    assert http.calls[1]["url"].endswith("/repos/new/name/issues")


async def test_branch_race_stops_before_publishing():
    item = uuid4()
    http = ScriptedHTTP(
        [{"default_branch": "main"}, {"protected": False, "commit": {"sha": "b" * 40}}]
    )
    provider = ExpandedGitHubProvider(http=http)
    with pytest.raises(ExecutionProviderError, match="branch moved"):
        await provider.execute(
            request=request(
                provider,
                "repository.commit.create",
                {
                    "branch": f"codex/{str(item)[:8]}/fix",
                    "baseSha": "a" * 40,
                    "message": "Fix",
                    "changes": [{"path": "src/a", "content": "fixed"}],
                },
                item,
            ),
            configuration={"repository": "owner/repo"},
            credential="test",
        )
    assert all(call["method"] == "GET" for call in http.calls)


async def test_atomic_commit_preserves_parent_modes_and_never_force_pushes(monkeypatch):
    monkeypatch.setattr(
        "app.execution.providers.native.github.artifacts.associate_test_revision", AsyncMock()
    )
    item, sha, published = uuid4(), "a" * 40, "b" * 40
    http = ScriptedHTTP(
        [
            {"default_branch": "main"},
            {"protected": False, "commit": {"sha": sha}},
            {"tree": {"sha": "tree"}},
            {"tree": [{"path": "script", "mode": "100755"}]},
            {"sha": "new-tree"},
            {"sha": published},
            {"object": {"sha": published}},
            {"object": {"sha": published}},
        ]
    )
    provider = ExpandedGitHubProvider(http=http)
    result = await provider.execute(
        request=request(
            provider,
            "repository.commit.create",
            {
                "branch": f"codex/{str(item)[:8]}/fix",
                "baseSha": sha,
                "message": "Fix",
                "changes": [{"path": "script", "content": "fixed"}],
            },
            item,
        ),
        configuration={"repository": "owner/repo"},
        credential="test",
    )
    assert http.calls[4]["json_body"]["tree"][0]["mode"] == "100755"
    assert http.calls[5]["json_body"]["parents"] == [sha]
    assert http.calls[6]["json_body"] == {"sha": published, "force": False}
    assert result.verification.details["externalId"] == published


@pytest.mark.parametrize("operation", ["repository.workflow.rerun", "repository.workflow.cancel"])
async def test_workflow_approval_rechecks_attempt_and_sha(operation):
    http = ScriptedHTTP([{"head_sha": "a" * 40, "run_attempt": 2}])
    provider = ExpandedGitHubProvider(http=http)
    with pytest.raises(ExecutionProviderError, match="attempt changed"):
        await provider.execute(
            request=request(
                provider, operation, {"runId": 7, "expectedSha": "a" * 40, "expectedAttempt": 1}
            ),
            configuration={"repository": "owner/repo"},
            credential="test",
        )
    assert len(http.calls) == 1 and http.calls[0]["method"] == "GET"


async def test_workflow_dispatch_receipt_never_claims_tests_passed():
    http = ScriptedHTTP([{"sha": "a" * 40}, {}])
    provider = ExpandedGitHubProvider(http=http)
    result = await provider.execute(
        request=request(
            provider,
            "repository.workflow.dispatch",
            {"workflowId": "ci.yml", "ref": "task", "expectedSha": "a" * 40},
        ),
        configuration={"repository": "owner/repo"},
        credential="test",
    )
    assert result.output == {"accepted": True, "state": "started", "testsPassed": False}
    assert "expectedSha" not in http.calls[1]["json_body"]


async def test_release_publication_rechecks_exact_draft_content():
    http = ScriptedHTTP([{"draft": True, "tag_name": "v1", "body": "changed"}])
    provider = ExpandedGitHubProvider(http=http)
    with pytest.raises(ExecutionProviderError, match="content changed"):
        await provider.execute(
            request=request(
                provider,
                "repository.release.publish",
                {
                    "releaseId": 5,
                    "expectedTag": "v1",
                    "expectedBodyHash": hashlib.sha256(b"approved").hexdigest(),
                },
            ),
            configuration={"repository": "owner/repo"},
            credential="test",
        )
    assert len(http.calls) == 1


async def test_project_item_cannot_target_another_project():
    http = ScriptedHTTP(
        [
            {"data": {"node": {"fields": {"nodes": []}}}},
            {"data": {"node": {"id": "item", "project": {"id": "other"}}}},
        ]
    )
    provider = ExpandedGitHubProvider(http=http)
    with pytest.raises(ExecutionProviderError, match="bound Project"):
        await provider.execute(
            request=request(provider, "project.item.archive", {"itemId": "item"}),
            configuration={"projectNodeId": "selected"},
            credential="test",
        )
    assert all("mutation" not in call["json_body"]["query"] for call in http.calls)


async def test_project_item_publication_is_verified_by_exact_node():
    http = ScriptedHTTP(
        [
            {"data": {"node": {"repository": {"databaseId": 123}}}},
            {"data": {"addProjectV2ItemById": {"item": {"id": "item"}}}},
            {
                "data": {
                    "node": {
                        "id": "item",
                        "project": {"id": "selected"},
                        "content": {"id": "issue"},
                        "fieldValues": {"nodes": []},
                    }
                }
            },
        ]
    )
    provider = ExpandedGitHubProvider(http=http)
    result = await provider.execute(
        request=request(provider, "project.item.add", {"contentId": "issue"}),
        configuration={"projectNodeId": "selected", "authorizedRepositoryIds": '["123"]'},
        credential="test",
    )
    assert result.verification.details["projectId"] == "selected"
    assert http.calls[-1]["json_body"]["variables"] == {"id": "item"}


async def test_discussion_creation_checks_category_and_re_reads_content():
    marker = "<!-- audoryn-action:" + hashlib.sha256(b"durable-action").hexdigest() + " -->"
    obj = {
        "id": "discussion",
        "title": "Plan",
        "body": "Evidence\n\n" + marker,
        "url": "https://github.com/owner/repo/discussions/1",
    }
    http = ScriptedHTTP(
        [
            {"data": {"repository": {"discussionCategories": {"nodes": [{"id": "category"}]}}}},
            {"data": {"createDiscussion": {"discussion": obj}}},
            {"data": {"node": obj}},
        ]
    )
    provider = ExpandedGitHubProvider(http=http)
    result = await provider.execute(
        request=request(
            provider,
            "repository.discussion.create",
            {"categoryId": "category", "title": "Plan", "body": "Evidence"},
        ),
        configuration={"repository": "owner/repo", "repositoryNodeId": "repo"},
        credential="test",
    )
    assert result.verification.details["externalId"] == "discussion"
    assert marker in http.calls[1]["json_body"]["variables"]["body"]


async def test_static_token_remains_static_and_no_app_token_is_minted():
    provider = ExpandedGitHubProvider(http=ScriptedHTTP([]))
    bundle = CredentialBundle(access_token="static")
    token, original = await provider.scope_execution_credential(
        configuration={}, scope="github.repository.metadata.read", bundle=bundle
    )
    assert (
        token == "static"
        and original == bundle
        and not provider.can_renew_credentials(bundle=bundle)
    )


async def test_installation_renewal_without_refresh_preserves_user_authority():
    auth = GitHubAuthentication(ScriptedHTTP([]))
    bundle = CredentialBundle(
        access_token="expired",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        renewal_metadata={
            "strategy": "github_installation",
            "installationId": "1",
            "repositoryIds": ["123"],
            "permissions": {"contents": "read"},
            "userBundle": "encrypted-parent-value",
        },
    )
    auth.installation_bundle = AsyncMock(
        return_value=replace(
            bundle, access_token="rotated", renewal_metadata={"strategy": "github_installation"}
        )
    )
    result = await auth.renew_credentials(bundle=bundle)
    assert (
        result.access_token == "rotated"
        and result.renewal_metadata["userBundle"] == "encrypted-parent-value"
    )
    assert auth.installation_bundle.call_args.kwargs["repositories"] == ["123"]


async def test_reconnect_cannot_execute_after_user_repository_access_is_revoked(monkeypatch):
    from app.application.services.integration_credentials import encode_bundle

    user = CredentialBundle(access_token="user", account_id="user")
    parent = CredentialBundle(
        access_token="app",
        scopes=("contents:write",),
        renewal_metadata={"userBundle": encode_bundle(user)},
    )
    http = ScriptedHTTP([{"permissions": {"pull": False, "push": False}}])
    provider = ExpandedGitHubProvider(http=http)
    provider.auth.installation_bundle = AsyncMock()
    with pytest.raises(PermissionError):
        await provider.scope_execution_credential(
            configuration={"installationId": "1", "repositoryId": "123"},
            scope="github.repository.commit.create",
            bundle=parent,
        )
    provider.auth.installation_bundle.assert_not_awaited()


async def test_command_exit_file_cannot_fake_a_successful_test():
    command = SimpleNamespace(
        id=uuid4(),
        session_id=uuid4(),
        organization_id=uuid4(),
        status="running",
        process_id=12,
        command="tests",
        output={},
    )
    row = SimpleNamespace(
        id=command.session_id, sandbox_id="sandbox", base_sha="a" * 40, evidence={}
    )
    req = SimpleNamespace(
        organization_id=command.organization_id, input={"commandId": str(command.id)}
    )
    runtime = SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(stdout="0", exit_code=0)),
        command_result=AsyncMock(side_effect=RuntimeError("lost process")),
    )
    result = await inspect_command(
        SimpleNamespace(get=AsyncMock(return_value=command), commit=AsyncMock()), runtime, row, req, False, {}
    )
    assert result["status"] == "uncertain_outcome" and "exitCode" not in result
    assert all(".exit" not in call.args[1] for call in runtime.run.call_args_list)


async def test_coding_capacity_is_durable_and_does_not_create_an_extra_sandbox(monkeypatch):
    monkeypatch.setattr(settings, "coding_global_concurrency", 2)
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[2, 0]))
    with pytest.raises(ExecutionProviderError, match="capacity is full"):
        await admit_capacity(
            session,
            SimpleNamespace(
                organization_id=uuid4(),
                operation="repository.workspace.open",
                correlation_id="test",
            ),
        )


async def test_delayed_outbox_retry_retains_event_and_allows_other_deliveries():
    event = SimpleNamespace(delivery_attempts=0, next_delivery_at=None, published_at=None)
    outbox = TransactionalOutbox(cast(AsyncSession, SimpleNamespace(flush=AsyncMock())))
    before = datetime.now(UTC)
    await outbox.mark_failed(cast(OutboxEvent, event))
    assert event.delivery_attempts == 1 and event.next_delivery_at >= before + timedelta(seconds=10)
    assert event.published_at is None


def test_secret_redaction_removes_entire_private_key():
    value = (
        "before\n-----BEGIN PRIVATE KEY-----\nVERY_SENSITIVE_BODY\n-----END PRIVATE KEY-----\nafter"  # fake key fixture
    )
    safe = ExpandedGitHubProvider._sanitize(value)
    assert "VERY_SENSITIVE_BODY" not in safe and "after" in safe


def test_binary_assets_survive_snapshot_transfer_without_binary_publication():
    from app.execution.coding.snapshot import validated_snapshot
    from app.execution.providers.native.github.safety import validate_changes

    files = validated_snapshot(snapshot("root/logo.png", b"\x89PNG\xff"), include_binary=True)
    assert files["logo.png"]["binaryBase64"]
    with pytest.raises(ValueError, match="Binary changes"):
        validate_changes([{"path": "logo.png", "content": files["logo.png"]}])


async def test_installation_circuit_breaker_is_account_scoped():
    cache = {}

    async def get(key):
        return cache.get(key)

    async def put(key, value, **kwargs):
        cache[key] = value

    coordinator = SimpleNamespace(cache_get=get, cache_set=put)
    client = GitHubHTTPClient()
    for _ in range(5):
        await client._failed(coordinator, "github:installation-a")
    assert "github:installation-a:cooldown" in cache
    assert "github:installation-b:cooldown" not in cache
    assert not any("slack" in key or "gmail" in key for key in cache)


async def test_registry_hostname_cannot_resolve_to_private_network(monkeypatch):
    from app.execution.coding.e2b import validate_registry_hosts
    from app.execution.providers.integration_hooks import HookUnavailable

    loop = SimpleNamespace(getaddrinfo=AsyncMock(return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]))
    monkeypatch.setattr("app.execution.coding.e2b.asyncio.get_running_loop", lambda: loop)
    with pytest.raises(HookUnavailable, match="public addresses"):
        await validate_registry_hosts(["registry.example.com"])


async def test_next_day_resume_cannot_bypass_daily_budget(monkeypatch):
    from app.execution.coding.service import rollover_budget

    monkeypatch.setattr(settings, "coding_task_active_seconds", 1800)
    monkeypatch.setattr(settings, "coding_organization_daily_seconds", 7200)
    row = SimpleNamespace(
        organization_id=uuid4(),
        active_seconds=300,
        evidence={"budgetDay": "2026-10-05", "reservedSeconds": 1800},
    )
    session = SimpleNamespace(
        execute=AsyncMock(), scalar=AsyncMock(return_value=SimpleNamespace(reserved_seconds=7200))
    )
    req = SimpleNamespace(operation="repository.workspace.read", correlation_id="test")
    with pytest.raises(ExecutionProviderError, match="Today's organization"):
        await rollover_budget(session, row, req, datetime(2026, 10, 6, tzinfo=UTC))
    assert row.evidence["budgetDay"] == "2026-10-05"


async def test_failed_export_does_not_block_other_session_retention(monkeypatch):
    from app.execution.coding import service

    monkeypatch.setattr(settings, "coding_execution_enabled", True)
    now = datetime.now(UTC)

    def row(status, expired):
        return SimpleNamespace(
            id=uuid4(),
            organization_id=uuid4(),
            work_item_id=uuid4(),
            resource_id=uuid4(),
            sandbox_id=str(uuid4()),
            status=status,
            last_activity_at=now - timedelta(hours=1),
            active_since=now - timedelta(minutes=2),
            active_seconds=0,
            expires_at=now + timedelta(hours=-1 if expired else 1),
            evidence={"baselineArtifactId": str(uuid4())},
        )

    first, second = row("running", False), row("paused", True)
    session = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [first, second])),
        refresh=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    context = AsyncMock()
    context.__aenter__.return_value = session
    coordinator = SimpleNamespace(
        acquire_lock=AsyncMock(return_value="lease"), release_lock=AsyncMock(), close=AsyncMock()
    )
    monkeypatch.setattr(service, "session_factory", lambda: context)
    monkeypatch.setattr(service.RedisCoordinator, "from_settings", lambda: coordinator)
    monkeypatch.setattr(service, "export", AsyncMock(side_effect=ValueError("unsafe export")))
    monkeypatch.setattr(service, "release_unused_budget", AsyncMock())
    runtime = SimpleNamespace(connect=AsyncMock(), pause=AsyncMock(), delete=AsyncMock())
    await service.maintain_coding_sessions(runtime)
    assert first.status == "paused" and first.evidence["cleanupWarning"]
    assert second.status == "expired"
    runtime.delete.assert_awaited_once_with(second.sandbox_id)
    assert session.commit.await_count == 2


async def test_project_catalog_does_not_expose_installation_only_projects():
    http = ScriptedHTTP(
        [
            {
                "data": {
                    "organization": {
                        "projectsV2": {
                            "nodes": [
                                {
                                    "id": "visible",
                                    "title": "Visible",
                                    "number": 1,
                                    "url": "https://github.com/orgs/owner/projects/1",
                                },
                                {
                                    "id": "hidden",
                                    "title": "Private",
                                    "number": 2,
                                    "url": "https://github.com/orgs/owner/projects/2",
                                },
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        ]
    )
    provider = ExpandedGitHubProvider(http=http)
    page = await provider.resources.projects(
        {
            "ownerLogin": "owner",
            "githubPermissionScopes": "organization_projects:read",
            "authorizedProjectIds": '["visible"]',
        },
        "installation-token",
        None,
    )
    assert [r.external_id for r in page.resources] == ["visible"]
