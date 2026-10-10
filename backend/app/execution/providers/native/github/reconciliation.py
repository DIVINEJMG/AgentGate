"""Recover uncertain writes by durable marker and exact canonical content."""

import hashlib
from datetime import UTC, datetime
from urllib.parse import quote

from app.execution.contracts import VerificationResult
from app.execution.providers.native.http import ProviderTransportError

from .execution import action_marker, assert_node, canonical_path
from .graphql import QUERIES
from .verification import verify_write


async def reconcile(provider, request, configuration, credential):
    action = provider.actions[request.operation]
    repo = provider.repo_path(configuration) if action.resource_type == "repository" else ""
    values = request.input
    operation = action.operation
    if action.graphql:
        return await reconcile_graphql(provider, action, request, configuration, credential)
    marker = action_marker(request)
    path = None
    matches = []
    if action.reconciliation in {"body_marker", "tag_marker"} and not action.graphql:
        collections = {
            "repository.issue.create": "/issues?state=all&per_page=100",
            "repository.issue.comment.create": f"/issues/{values.get('number')}/comments?per_page=100",
            "repository.pull_request.create": "/pulls?state=all&per_page=100",
            "repository.review.comment.create": f"/pulls/{values.get('number')}/comments?per_page=100",
            "repository.review.submit": f"/pulls/{values.get('number')}/reviews?per_page=100",
            "repository.release.create": "/releases?per_page=100",
            "repository.milestone.create": "/milestones?state=all&per_page=100",
        }
        suffix = collections.get(operation)
        if suffix:
            for page in range(1, 4):
                response = await provider.api("GET", repo + suffix + f"&page={page}", credential)
                field = "description" if operation == "repository.milestone.create" else "body"
                matches.extend(obj for obj in response.data if marker in (obj.get(field) or ""))
                if len(response.data) < 100:
                    break
    elif action.reconciliation == "external_id":
        response = await provider.api(
            "GET", repo + "/commits/" + values["head_sha"] + "/check-runs?per_page=100", credential
        )
        matches = [obj for obj in response.data["check_runs"] if obj.get("external_id") == marker]
    elif operation in {"repository.branch.create", "repository.branch.update"}:
        path = repo + "/git/ref/heads/" + quote(values["branch"], safe="")
    elif operation == "repository.commit.create":
        commits = (
            await provider.api(
                "GET",
                repo + "/commits?sha=" + quote(values["branch"], safe="") + "&per_page=100",
                credential,
            )
        ).data
        commit_marker = (
            "Audoryn-Action: " + hashlib.sha256(request.idempotency_key.encode()).hexdigest()
        )
        matches = [
            c
            for c in commits
            if commit_marker in c.get("commit", {}).get("message", "")
            and [p["sha"] for p in c.get("parents", [])] == [values["baseSha"]]
        ]
        if len(matches) != 1:
            return None
        commit = matches[0]
        from .safety import validate_changes

        validate_changes(values["changes"], configuration.get("constraints", {}))
        for change in values["changes"]:
            try:
                data = (
                    await provider.api(
                        "GET",
                        repo
                        + "/contents/"
                        + quote(change["path"], safe="/")
                        + "?ref="
                        + commit["sha"],
                        credential,
                    )
                ).data
            except ProviderTransportError as error:
                if error.code == "resource_not_found" and change.get("delete"):
                    continue
                raise
            if change.get("delete"):
                return None
            content = change["content"].encode()
            blob_sha = hashlib.sha1(
                b"blob " + str(len(content)).encode() + b"\x00" + content, usedforsecurity=False
            ).hexdigest()
            if data.get("sha") != blob_sha:
                return None
        return provider._result(
            request=request,
            output=provider._sanitize(commit),
            provider_request_id=None,
            started_at=datetime.now(UTC),
            verification=VerificationResult(
                verified=True,
                summary="Recovered the exact published commit and verified its parent and file contents.",
                details={
                    "externalId": commit["sha"],
                    "baseSha": values["baseSha"],
                    "webUrl": commit.get("html_url"),
                },
            ),
        )
    elif action.reconciliation == "exact_fields" and not action.graphql:
        path = repo + action.path.format(**{k: quote(str(v), safe="") for k, v in values.items()})
        if operation == "repository.label.update":
            path = repo + "/labels/" + quote(values.get("new_name", values["name"]), safe="")
    elif action.reconciliation == "release_state":
        path = f"{repo}/releases/{values['releaseId']}"
    elif action.reconciliation == "merged_sha":
        path = f"{repo}/pulls/{values['number']}"
    # Absence in a bounded page is not proof that a write was never executed.
    # Operations without an identity-based reconciliation remain paused.
    if len(matches) == 1:
        data = matches[0]
        path = canonical_path(operation, repo, "", data, values)
    elif matches:
        return None
    if not path:
        return None
    try:
        data = (await provider.api("GET", path, credential)).data
        evidence = await verify_write(
            provider, action, request, configuration, credential, data, path
        )
    except ProviderTransportError as error:
        if error.code in {"verification_failed", "resource_not_found"}:
            return None
        raise
    return provider._result(
        request=request,
        output=provider._sanitize(data),
        provider_request_id=None,
        started_at=datetime.now(UTC),
        verification=VerificationResult(
            verified=True,
            summary="Recovered the exact GitHub object and verified its canonical state.",
            details=evidence,
        ),
    )


async def reconcile_graphql(provider, action, request, configuration, credential):
    values, matches, cursor = request.input, [], None
    marker = action_marker(request)
    kind = action.graphql
    if kind in {"project_update", "project_archive", "ready", "resolve", "discussion_update"}:
        identity = (
            values.get("itemId")
            or values.get("nodeId")
            or values.get("threadId")
            or values.get("discussionId")
        )
        obj = {"id": identity}
        if not kind.startswith("project_"):
            typename = (
                "PullRequest"
                if kind == "ready"
                else "PullRequestReviewThread"
                if kind == "resolve"
                else "Discussion"
            )
            node = await assert_node(provider, identity, credential, configuration, typename)
            if kind == "discussion_update" and node.get("author", {}).get(
                "login"
            ) != configuration.get("botLogin"):
                return None
    elif kind in {"discussion_create", "discussion_comment", "project_add"}:
        for _ in range(3):
            if kind == "project_add":
                data = await provider.graphql(
                    QUERIES["project_read"][0],
                    {"projectId": configuration["projectNodeId"], "after": cursor},
                    credential,
                )
                page = data["node"]["items"]
                matches.extend(
                    i
                    for i in page["nodes"]
                    if (i.get("content") or {}).get("id") == values["contentId"]
                )
            elif kind == "discussion_create":
                owner, name = configuration["repository"].split("/", 1)
                data = await provider.graphql(
                    QUERIES["discussions_read"][0],
                    {"owner": owner, "name": name, "after": cursor},
                    credential,
                )
                page = data["repository"]["discussions"]
                matches.extend(i for i in page["nodes"] if marker in i.get("body", ""))
            else:
                query = "query($id:ID!,$after:String){node(id:$id){... on Discussion{comments(first:50,after:$after){nodes{id body} pageInfo{hasNextPage endCursor}}}}}"
                data = await provider.graphql(
                    query, {"id": values["discussionId"], "after": cursor}, credential
                )
                page = data["node"]["comments"]
                matches.extend(i for i in page["nodes"] if marker in i.get("body", ""))
            if not page["pageInfo"]["hasNextPage"]:
                break
            cursor = page["pageInfo"]["endCursor"]
        if len(matches) != 1:
            return None
        obj = matches[0]
    else:
        return None
    key = (
        "item"
        if kind.startswith("project_")
        else "pullRequest"
        if kind == "ready"
        else "thread"
        if kind == "resolve"
        else "comment"
        if kind == "discussion_comment"
        else "discussion"
    )
    try:
        proof = await verify_write(
            provider,
            action,
            request,
            configuration,
            credential,
            {"recovered": {key: obj}},
            "graphql:" + kind,
        )
    except ProviderTransportError as error:
        if error.code in {"verification_failed", "resource_not_found"}:
            return None
        raise
    return provider._result(
        request=request,
        output=provider._sanitize(obj),
        provider_request_id=None,
        started_at=datetime.now(UTC),
        verification=VerificationResult(
            verified=True,
            summary="Recovered the exact GraphQL object and re-read its requested fields.",
            details=proof,
        ),
    )
