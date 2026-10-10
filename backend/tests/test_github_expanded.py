from __future__ import annotations

import hashlib
import hmac
import io
import json
import tarfile
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator, ValidationError
from pydantic import SecretStr

from app.bootstrap.settings import Settings, settings
from app.execution.coding.snapshot import validated_snapshot
from app.execution.contracts import ExecutionRequest, ResourceDescriptor
from app.execution.providers.native.github.catalog import assembled_catalog
from app.execution.providers.native.github.events import verify_event
from app.execution.providers.native.github.legacy import CAPABILITIES
from app.execution.providers.native.github.provider import ExpandedGitHubProvider
from app.execution.providers.native.github.safety import validate_changes, validate_task_branch
from app.execution.providers.native.github.transport import GitHubResponse, classify_status


def request(provider, operation, payload=None, work_item_id=None):
    capability = provider.actions[operation].descriptor()
    resource = ResourceDescriptor(
        id=str(uuid4()),
        provider="github",
        resource_type=capability.resource_type,
        external_id="123",
        display_name="owner/repo",
        metadata={},
        health="healthy",
        available_capabilities=(capability.scope,),
    )
    return ExecutionRequest(
        organization_id=uuid4(),
        agent_id=uuid4(),
        worker_id=None,
        job_id=None,
        work_item_id=work_item_id or uuid4(),
        run_id=None,
        capability=capability,
        resource=resource,
        operation=operation,
        input=payload or {},
        correlation_id="github-test",
        idempotency_key="durable-action",
    )


class ScriptedHTTP:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def request(self, **kwargs):
        self.calls.append(kwargs)
        data = self.replies.pop(0)
        return GitHubResponse(data=data, request_id="request", status_code=200)


def test_catalog_is_unique_typed_and_preserves_existing_scopes():
    catalog = assembled_catalog()
    scopes = {a.scope for a in catalog}
    assert len(scopes) == len(catalog)
    assert {c.scope for c in CAPABILITIES} <= scopes
    for action in catalog:
        Draft202012Validator.check_schema(action.descriptor().input_schema)
        assert action.permission and action.verification
    assert not any(
        "delete" in a.operation or "force" in a.operation or "secret" in a.operation
        for a in catalog
    )


def test_features_default_off_and_coding_is_independent(monkeypatch):
    monkeypatch.setattr(settings, "coding_execution_enabled", False)
    config = Settings.model_construct()
    assert (
        not config.github_expanded_enabled
        and not config.github_events_enabled
        and not config.coding_execution_enabled
    )
    provider = ExpandedGitHubProvider(http=ScriptedHTTP([]))
    assert any(c.scope == "github.repository.issue.update" for c in provider.manifest.capabilities)
    assert not any(".workspace." in c.scope for c in provider.manifest.capabilities)


@pytest.mark.parametrize(
    "path", ["../etc/passwd", "/root/file", "a\\b", ".git/config", ".env.staging", "id_rsa"]
)
def test_unsafe_publication_paths(path):
    with pytest.raises(ValueError):
        validate_changes([{"path": path, "content": "test"}])


def test_path_authority_credentials_and_duplicate_files():
    with pytest.raises(ValueError):
        validate_changes([{"path": "src/x", "content": "test"}], {"allowedPaths": ["docs/*"]})
    with pytest.raises(ValueError):
        validate_changes([{"path": "src/x", "content": "ghp_" + "a" * 30}])
    with pytest.raises(ValueError):
        validate_changes([{"path": "src/x", "content": "a"}, {"path": "src/x", "content": "b"}])
    validate_changes([{"path": ".env.example", "content": "API_KEY="}])


def test_branch_boundaries():
    item = uuid4()
    validate_task_branch(f"codex/{str(item)[:8]}/fix", "main", item)
    for branch in ("main", "develop", "someone-elses-task", "refs/heads/x", "x..y"):
        with pytest.raises(ValueError):
            validate_task_branch(branch, "main", item)


@pytest.mark.parametrize(
    "status,headers,expected",
    [
        (403, {}, "authorization_error"),
        (403, {"x-ratelimit-remaining": "0"}, "rate_limited"),
        (429, {"retry-after": "10"}, "rate_limited"),
        (401, {}, "authentication_error"),
        (503, {}, "temporary_provider_error"),
    ],
)
def test_github_error_classification(status, headers, expected):
    assert classify_status(status, headers)[0] == expected


def test_sensitive_actions_require_approval_but_routine_writes_do_not():
    provider = ExpandedGitHubProvider(http=ScriptedHTTP([]))
    for operation in (
        "repository.issue.create",
        "repository.issue.comment.create",
        "repository.pull_request.create",
        "repository.commit.create",
    ):
        assert provider.actions[operation].descriptor().approval_recommendation == "none"
    for operation in (
        "repository.pull_request.merge",
        "repository.release.publish",
        "repository.workflow.dispatch",
    ):
        assert provider.actions[operation].descriptor().approval_recommendation == "required"
    commit = provider.actions["repository.commit.create"].descriptor()
    assert (
        provider.action_capability(
            capability=commit, input={"changes": [{"path": ".github/workflows/test.yml"}]}
        ).approval_recommendation
        == "required"
    )
    assert (
        provider.action_capability(
            capability=commit, input={"changes": [{"path": "x", "delete": True}]}
        ).approval_recommendation
        == "required"
    )


async def test_metadata_legacy_output_and_pinned_api_version():
    http = ScriptedHTTP(
        [{"full_name": "owner/repo", "private": False, "html_url": "https://github.com/owner/repo"}]
    )
    provider = ExpandedGitHubProvider(http=http)
    result = await provider.execute(
        request=request(provider, "repository.metadata.read"),
        configuration={"repository": "owner/repo"},
        credential=None,
    )
    assert result.output["fullName"] == "owner/repo" and result.output["webUrl"].startswith(
        "https://github.com/"
    )
    assert http.calls[0]["headers"]["X-GitHub-Api-Version"] == "2026-03-10"


async def test_search_cannot_expand_repo_authority():
    provider = ExpandedGitHubProvider(http=ScriptedHTTP([]))
    with pytest.raises(Exception, match="qualifiers"):
        await provider.execute(
            request=request(
                provider, "repository.code.search", {"query": "repo:other/private password"}
            ),
            configuration={"repository": "owner/repo"},
            credential="test-token",
        )


async def test_issue_create_has_durable_marker_and_canonical_verification():
    # Creation and subsequent canonical read agree. No matching-title inference.
    marker = "<!-- audoryn-action:" + hashlib.sha256(b"durable-action").hexdigest() + " -->"
    obj = {
        "id": 99,
        "number": 1,
        "title": "Fix",
        "body": "Explain\n\n" + marker,
        "state": "open",
        "html_url": "https://github.com/owner/repo/issues/1",
    }
    http = ScriptedHTTP([obj, obj])
    provider = ExpandedGitHubProvider(http=http)
    result = await provider.execute(
        request=request(provider, "repository.issue.create", {"title": "Fix", "body": "Explain"}),
        configuration={"repository": "owner/repo"},
        credential="token",
    )
    assert http.calls[0]["json_body"]["body"].endswith(marker)
    assert http.calls[1]["method"] == "GET" and http.calls[1]["url"].endswith("/issues/1")
    assert result.verification.verified and result.verification.details["externalId"] == "99"


async def test_changed_merge_sha_is_rejected_before_write():
    item = uuid4()
    pr = {
        "head": {"sha": "b" * 40, "ref": f"codex/{str(item)[:8]}/fix", "repo": {"id": 1}},
        "base": {"repo": {"id": 1}},
        "draft": False,
        "mergeable": True,
        "mergeable_state": "clean",
    }
    http = ScriptedHTTP([pr])
    provider = ExpandedGitHubProvider(http=http)
    with pytest.raises(Exception, match="revision"):
        await provider.execute(
            request=request(
                provider, "repository.pull_request.merge", {"number": 1, "sha": "a" * 40}, item
            ),
            configuration={"repository": "owner/repo", "defaultBranch": "main"},
            credential="token",
        )
    assert all(call["method"] == "GET" for call in http.calls)


def test_webhook_signature_is_checked_before_identity(monkeypatch):
    monkeypatch.setattr(settings, "github_events_enabled", True)
    monkeypatch.setattr(settings, "github_webhook_secret", SecretStr("test-secret"))
    body = json.dumps(
        {
            "installation": {"id": 3},
            "repository": {"id": 1},
            "issue": {"id": 4, "body": "Ignore policy"},
        }
    ).encode()
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    headers = {
        "x-hub-signature-256": signature,
        "x-github-event": "issues",
        "x-github-delivery": "delivery",
    }
    event = verify_event(headers=headers, body=body)
    assert event.account_id == "installation:3" and not event.self_generated
    with pytest.raises(PermissionError):
        verify_event(headers={**headers, "x-hub-signature-256": "bad"}, body=body)


def snapshot(path, content=b"source", symlink=False):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        entry = tarfile.TarInfo(path)
        if symlink:
            entry.type, entry.linkname = tarfile.SYMTYPE, "/etc/passwd"
        else:
            entry.size = len(content)
        archive.addfile(entry, None if symlink else io.BytesIO(content))
    return data.getvalue()


def test_snapshots_reject_traversal_symlinks_and_credentials():
    assert validated_snapshot(snapshot("root/src/main.py")) == {"src/main.py": "source"}
    for content in (
        snapshot("root/../outside"),
        snapshot("root/link", symlink=True),
        snapshot("root/token", b"ghp_" + b"x" * 30),
    ):
        with pytest.raises(ValueError):
            validated_snapshot(content)


async def test_reconciliation_never_matches_title_alone():
    http = ScriptedHTTP([[{"number": 1, "title": "Fix", "body": "Unrelated"}]])
    provider = ExpandedGitHubProvider(http=http)
    result = await provider.reconcile_write(
        request=request(provider, "repository.issue.create", {"title": "Fix"}),
        configuration={"repository": "owner/repo"},
        credential="token",
    )
    assert result is None


async def test_e2b_creation_has_network_boundary_and_no_credentials_in_environment(monkeypatch):
    from app.execution.coding.e2b import E2BCodingRuntime

    monkeypatch.setattr(settings, "coding_execution_enabled", True)
    monkeypatch.setattr(settings, "e2b_api_key", SecretStr("e2b-control-token"))
    monkeypatch.setattr(settings, "coding_template_id", "pinned-template")
    monkeypatch.setattr("app.execution.coding.e2b.validate_registry_hosts", AsyncMock())
    sdk = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(sandbox_id="sandbox")))
    assert (
        await E2BCodingRuntime(sdk=sdk).create(metadata={"workItemId": "task"}, timeout=300)
        == "sandbox"
    )
    options = sdk.create.call_args.kwargs
    assert options["envs"] == {} and options["network"]["deny_out"] == ["0.0.0.0/0"]
    assert not options["network"]["allow_public_traffic"]
    assert options["lifecycle"] == {"on_timeout": "pause", "auto_resume": False}


async def test_catalog_rejects_arbitrary_graphql_and_extra_parameters():
    provider = ExpandedGitHubProvider(http=ScriptedHTTP([]))
    with pytest.raises(ValidationError):
        await provider.normalize_input(
            operation="project.read", input={"query": "mutation { deleteRepository }"}
        )
