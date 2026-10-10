"""Named GitHub operations. No arbitrary HTTP or GraphQL reaches the planner."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import quote, urlencode

from app.execution.providers.native.http import ProviderTransportError

from .actions.git import execute_git
from .graphql import QUERIES
from .safety import safe_path, validate_task_branch
from .transport import GitHubResponse


def action_marker(request):
    return (
        "<!-- audoryn-action:"
        + hashlib.sha256(request.idempotency_key.encode()).hexdigest()
        + " -->"
    )


async def inspect_pr(provider, repo, number, credential, configuration, request):
    pr = (await provider.api("GET", f"{repo}/pulls/{number}", credential)).data
    if str(pr["head"]["repo"]["id"]) != configuration.get(
        "repositoryId", str(pr["base"]["repo"]["id"])
    ):
        raise ValueError("Fork mutation needs its own exact repository grant.")
    validate_task_branch(
        pr["head"]["ref"],
        configuration.get("defaultBranch", "main"),
        request.work_item_id,
        configuration.get("constraints", {}),
    )
    return pr


async def assert_node(provider, node_id, credential, configuration, typename):
    # Identity lookup is fixed code, not a planner-supplied query.
    selection = "... on PullRequest { number repository { databaseId } } ... on PullRequestReviewThread { pullRequest { repository { databaseId } } } ... on Discussion { repository { databaseId } author { login } }"
    data = await provider.graphql(
        "query($id:ID!){node(id:$id){id __typename " + selection + "}}", {"id": node_id}, credential
    )
    node = data.get("node")
    if not node or node["__typename"] != typename:
        raise ValueError("GitHub node does not identify the requested object type.")
    repo = node.get("repository", node.get("pullRequest", {}).get("repository", {}))
    if str(repo.get("databaseId")) != configuration.get("repositoryId"):
        raise ValueError("GitHub node is outside the bound repository.")
    return node


async def perform(provider, action, request, configuration, credential):
    values = dict(request.input)
    repo = provider.repo_path(configuration) if action.resource_type == "repository" else ""
    constraints = configuration.get("constraints", {})
    operation = action.operation
    if operation in {
        "repository.branch.create",
        "repository.branch.update",
        "repository.commit.create",
    }:
        response, canonical = await execute_git(
            provider, request, configuration, credential, constraints
        )
        return response, canonical, response.data
    if action.graphql:
        return await perform_graphql(provider, action, request, configuration, credential)
    if operation in {"repository.code.search", "repository.issues.search"}:
        # Search is always narrowed to the exact bound repository, even when the
        # user query tries to include another repo or organization qualifier.
        query = values.pop("query")
        if any(term.startswith(("repo:", "org:", "user:")) for term in query.split()):
            raise ValueError("Search qualifiers cannot expand the bound repository.")
        path = "/search/" + ("code" if operation.endswith("code.search") else "issues")
        values["q"] = query + " repo:" + configuration["repository"]
        response = await provider.api("GET", path + "?" + urlencode(values), credential)
        return response, "", response.data
    if operation == "repository.release.asset.upload":
        # Exported artifacts must come from the authenticated task artifact store.
        from .artifacts import upload_release_asset

        return await upload_release_asset(provider, request, configuration, credential)
    if operation in {"repository.workflow.logs.read", "repository.workflow.artifact.read"}:
        path = repo + action.path.format(**{k: quote(str(v), safe="") for k, v in values.items()})
        response = await provider.api("GET", path, credential, binary=True, max_bytes=2_000_000)
        if operation.endswith("logs.read"):
            output = {"log": response.raw.decode("utf-8", errors="replace"), "maxBytes": 2_000_000}
        else:
            from .artifacts import store_task_artifact

            output = await store_task_artifact(request, response.raw, "application/zip")
        return response, "", output
    path_keys = [key for key in values if "{" + key + "}" in action.path]
    path = repo + action.path.format(**{key: quote(str(values[key]), safe="") for key in path_keys})
    payload: dict[str, Any] = {
        key: value
        for key, value in values.items()
        if key not in path_keys and key not in action.query
    }
    if operation == "repository.contents.read":
        safe_path(values["path"])
        path = repo + "/contents/" + quote(values["path"], safe="/")
    if operation == "repository.pull_request.create":
        metadata = (await provider.api("GET", repo, credential)).data
        validate_task_branch(
            values["head"], metadata["default_branch"], request.work_item_id, constraints
        )
        if ":" in values["head"]:
            raise ValueError("Cross-repository pull requests require separate source authority.")
        await provider.api("GET", repo + "/branches/" + quote(values["head"], safe=""), credential)
        payload["draft"] = True
    if operation.startswith("repository.pull_request.") and "number" in values and action.write:
        pr = await inspect_pr(provider, repo, values["number"], credential, configuration, request)
        if operation.endswith("merge") and (
            pr["head"]["sha"] != values["sha"]
            or pr.get("draft")
            or pr.get("mergeable") is not True
            or pr.get("mergeable_state") != "clean"
        ):
            raise ValueError(
                "Pull request revision, protection or checks changed; merge needs fresh validation."
            )
        if operation.endswith("branch.update") and pr["head"]["sha"] != values["expected_head_sha"]:
            raise ValueError("Pull request head changed after approval.")
    if operation.startswith("repository.review.") and "number" in values and action.write:
        pr = (await provider.api("GET", f"{repo}/pulls/{values['number']}", credential)).data
        if values.get("commit_id") != pr["head"]["sha"]:
            raise ValueError("Review must target the currently observed pull request revision.")
        if "path" in values:
            safe_path(values["path"])
            files = (
                await provider.api(
                    "GET", f"{repo}/pulls/{values['number']}/files?per_page=100", credential
                )
            ).data
            if not any(f["filename"] == values["path"] and f.get("patch") for f in files):
                raise ValueError("Review path has no observed patch in the bounded file page.")
    if operation == "repository.workflow.dispatch":
        branch = (
            await provider.api(
                "GET", repo + "/commits/" + quote(values["ref"], safe=""), credential
            )
        ).data
        if branch["sha"] != values["expectedSha"]:
            raise ValueError("Workflow revision changed after approval.")
        payload.pop("expectedSha")
    if operation in {"repository.workflow.rerun", "repository.workflow.cancel"}:
        run = (await provider.api("GET", f"{repo}/actions/runs/{values['runId']}", credential)).data
        if (
            run["head_sha"] != values["expectedSha"]
            or run["run_attempt"] != values["expectedAttempt"]
        ):
            raise ValueError("Workflow revision or attempt changed after approval.")
        payload.pop("expectedSha")
        payload.pop("expectedAttempt")
    if operation in {"repository.release.update", "repository.release.publish"}:
        release = (await provider.api("GET", path, credential)).data
        if not release["draft"]:
            raise ValueError("This action can only modify an unpublished draft release.")
        if operation.endswith("publish"):
            if (
                release["tag_name"] != values["expectedTag"]
                or hashlib.sha256((release.get("body") or "").encode()).hexdigest()
                != values["expectedBodyHash"]
            ):
                raise ValueError("Release content changed after approval.")
            payload = {"draft": False}
    if operation == "repository.release.create":
        await provider.api("GET", repo + "/commits/" + values["target_commitish"], credential)
        payload["draft"] = True
    if operation == "repository.check.create":
        if not values["name"].startswith("Aduoryn/"):
            raise ValueError("Workers can only publish Aduoryn-owned checks.")
        from .artifacts import verify_test_evidence

        await verify_test_evidence(request, values["head_sha"], values.get("conclusion"))
        payload["external_id"] = action_marker(request)
    if action.reconciliation in {"body_marker", "tag_marker"}:
        field = "description" if operation == "repository.milestone.create" else "body"
        payload[field] = (payload.get(field) or "") + "\n\n" + action_marker(request)
    if action.query:
        query = {key: values[key] for key in action.query if key in values}
        if query:
            path += "?" + urlencode(query)
    if operation in {"repository.issues.read", "repository.pull_requests.read"}:
        path += "?state=open&per_page=20"
    try:
        response = await provider.api(
            action.method, path, credential, payload if action.write else None
        )
    except ProviderTransportError as error:
        if operation != "repository.contents.read" or error.code != "resource_not_found":
            raise
        # GitHub hides inaccessible objects behind 404. Establish repository and
        # revision access before treating this particular path as absent.
        metadata = (await provider.api("GET", repo, credential)).data
        ref = values.get("ref") or metadata["default_branch"]
        try:
            commit = await provider.api("GET", repo + "/commits/" + quote(ref, safe=""), credential)
        except ProviderTransportError as revision_error:
            if revision_error.code != "resource_not_found":
                raise
            raise ProviderTransportError(
                code="resource_not_found",
                retryable=False,
                safe_message="The requested GitHub revision could not be accessed; content absence is not established.",
            ) from revision_error
        sha = commit.data["sha"]
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            raise ValueError("GitHub did not return a valid observed revision.")
        # Pin the second lookup: the branch may have moved since the first read.
        pinned_path = (
            repo + "/contents/" + quote(values["path"], safe="/") + "?" + urlencode({"ref": sha})
        )
        try:
            response = await provider.api("GET", pinned_path, credential)
            path = pinned_path
        except ProviderTransportError as content_error:
            if content_error.code != "resource_not_found":
                raise
            missing = {
                "exists": False,
                "status": "not_found",
                "path": values["path"],
                "requestedRef": ref,
                "revision": sha,
                "repositoryAccessible": True,
                "revisionAccessible": True,
                "evidence": "The path lookup returned 404 at this accessible repository revision.",
                "coverage": "Only this path was checked; no other requested files were inspected.",
            }
            return GitHubResponse(data=missing, status_code=404, request_id=None), "", missing
    canonical = canonical_path(operation, repo, path, response.data, values)
    output = response.data
    if operation == "repository.metadata.read":
        data = response.data
        output = {
            "fullName": data.get("full_name", ""),
            "private": data.get("private") is True,
            "archived": data.get("archived") is True,
            "defaultBranch": data.get("default_branch", ""),
            "openIssuesCount": data.get("open_issues_count", 0),
            "watchersCount": data.get("watchers_count", 0),
            "forksCount": data.get("forks_count", 0),
            "webUrl": data.get("html_url", ""),
        }
    elif operation in {
        "repository.issues.read",
        "repository.pull_requests.read",
        "repository.issue.create",
    }:

        def legacy_object(item):
            value = {
                "number": item["number"],
                "title": item["title"],
                "state": item["state"],
                "author": item.get("user", {}).get("login", ""),
                "webUrl": item.get("html_url", ""),
                "createdAt": item.get("created_at", ""),
            }
            if operation != "repository.issue.create":
                value["updatedAt"] = item.get("updated_at", "")
            if operation == "repository.pull_requests.read":
                value["draft"] = item.get("draft") is True
            return value

        output = (
            legacy_object(response.data)
            if operation == "repository.issue.create"
            else [
                legacy_object(item)
                for item in response.data
                if operation != "repository.issues.read" or "pull_request" not in item
            ]
        )
    elif isinstance(output, dict) and output.get("html_url"):
        output = {**output, "webUrl": output["html_url"]}
    if action.write and operation.startswith("repository.workflow."):
        output = {
            "accepted": True,
            "state": "started" if operation.endswith("dispatch") else "requested",
            "testsPassed": False,
        }
    return response, canonical, output


def canonical_path(operation, repo, path, data, values):
    if not isinstance(data, dict):
        return path
    if operation == "repository.issue.create":
        return f"{repo}/issues/{data['number']}"
    if operation == "repository.issue.comment.create":
        return f"{repo}/issues/comments/{data['id']}"
    if operation == "repository.pull_request.create":
        return f"{repo}/pulls/{data['number']}"
    if operation == "repository.review.comment.create":
        return f"{repo}/pulls/comments/{data['id']}"
    if operation == "repository.review.submit":
        return f"{repo}/pulls/{values['number']}/reviews/{data['id']}"
    if operation == "repository.check.create":
        return f"{repo}/check-runs/{data['id']}"
    if operation == "repository.release.create":
        return f"{repo}/releases/{data['id']}"
    if operation == "repository.milestone.create":
        return f"{repo}/milestones/{data['number']}"
    if operation == "repository.label.create":
        return repo + "/labels/" + quote(data["name"], safe="")
    if operation == "repository.label.update":
        return repo + "/labels/" + quote(values.get("new_name", values["name"]), safe="")
    return path


async def perform_graphql(provider, action, request, configuration, credential):
    query, names = QUERIES[action.graphql]
    values = dict(request.input)
    project = configuration.get("projectNodeId")
    if action.resource_type == "project":
        if not project:
            raise ValueError(
                "Project actions require an exact separately granted Project resource."
            )
        values["projectId"] = project
        values["id"] = project
        if values.get("itemId") or values.get("fieldId"):
            observed = await provider.graphql(
                QUERIES["project_read"][0], {"projectId": project, "after": None}, credential
            )
            node = observed["node"]
            if values.get("itemId"):
                item = (
                    await provider.graphql(
                        QUERIES["project_item"][0], {"id": values["itemId"]}, credential
                    )
                ).get("node")
                if not item or item["project"]["id"] != project:
                    raise ValueError("Project item does not belong to the bound Project.")
            if values.get("fieldId") and not any(
                field["id"] == values["fieldId"] for field in node["fields"]["nodes"]
            ):
                raise ValueError("Project field does not belong to the bound Project.")
        if values.get("contentId"):
            # Linked content requires an exact repository binding too.
            data = await provider.graphql(
                "query($id:ID!){node(id:$id){... on Issue{repository{databaseId}} ... on PullRequest{repository{databaseId}}}}",
                {"id": values["contentId"]},
                credential,
            )
            if str(
                data.get("node", {}).get("repository", {}).get("databaseId")
            ) not in configuration.get("authorizedRepositoryIds", []):
                raise ValueError("Project content needs an exact repository task grant.")
    else:
        if action.graphql == "discussions_search":
            query_text = values["query"]
            if any(term.startswith(("repo:", "org:", "user:")) for term in query_text.split()):
                raise ValueError("Search qualifiers cannot expand the bound repository.")
            values["query"] = query_text + " repo:" + configuration["repository"]
        values["repositoryId"] = configuration["repositoryNodeId"]
        values["owner"], values["name"] = configuration["repository"].split("/", 1)
        if action.graphql == "ready":
            node = await assert_node(
                provider, values["nodeId"], credential, configuration, "PullRequest"
            )
            await inspect_pr(
                provider,
                provider.repo_path(configuration),
                node["number"],
                credential,
                configuration,
                request,
            )
            values["id"] = values["nodeId"]
        elif action.graphql == "resolve":
            await assert_node(
                provider, values["threadId"], credential, configuration, "PullRequestReviewThread"
            )
            values["id"] = values["threadId"]
        elif values.get("discussionId"):
            node = await assert_node(
                provider, values["discussionId"], credential, configuration, "Discussion"
            )
            if action.graphql == "discussion_update" and node.get("author", {}).get(
                "login"
            ) != configuration.get("botLogin"):
                raise ValueError("Only worker-authored discussion content can be updated.")
        if action.graphql == "discussion_create":
            categories = await provider.graphql(
                QUERIES["discussion_categories"][0],
                {"owner": values["owner"], "name": values["name"]},
                credential,
            )
            if values["categoryId"] not in [
                c["id"] for c in categories["repository"]["discussionCategories"]["nodes"]
            ]:
                raise ValueError(
                    "Discussion category belongs to another repository or was not observed."
                )
    if action.reconciliation == "body_marker":
        values["body"] = (values.get("body") or "") + "\n\n" + action_marker(request)
    variables = {name: values.get(name) for name in names}
    data = await provider.graphql(query, variables, credential)
    if action.graphql == "project_read" and data.get("node"):
        for item in data["node"]["items"]["nodes"]:
            content = item.get("content") or {}
            repository = content.get("repository")
            if repository and str(repository["databaseId"]) not in configuration.get(
                "authorizedRepositoryIds", []
            ):
                item["content"] = {
                    "unavailable": "Linked repository content is outside this task's bindings."
                }
    response = GitHubResponse(data=data, request_id=None, status_code=200, headers={}, raw=b"")
    return response, "graphql:" + action.graphql, data
