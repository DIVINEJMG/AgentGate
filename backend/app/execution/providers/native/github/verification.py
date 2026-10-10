from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import NoReturn

from app.execution.providers.native.http import ProviderTransportError

from .execution import action_marker
from .graphql import QUERIES


def mismatch() -> NoReturn:
    raise ProviderTransportError(
        code="verification_failed",
        retryable=False,
        safe_message="GitHub accepted the action, but canonical state does not establish its requested outcome.",
    )


async def verify_write(provider, action, request, configuration, credential, data, canonical):
    values = request.input
    operation = action.operation
    if canonical.startswith("graphql:"):
        if action.graphql.startswith("project_"):
            created = next(iter(data.values()), {})
            identity = (created.get("item") or created.get("projectV2Item") or {}).get(
                "id", values.get("itemId")
            )
            observed = await provider.graphql(
                QUERIES["project_item"][0], {"id": identity}, credential
            )
            item = observed.get("node")
            if (
                not item
                or item.get("project", {}).get("id") != configuration["projectNodeId"]
                or (action.graphql == "project_archive" and not item["isArchived"])
            ):
                mismatch()
            if (
                action.graphql == "project_add"
                and (item.get("content") or {}).get("id") != values["contentId"]
            ):
                mismatch()
            if action.graphql == "project_update":
                fields = item["fieldValues"]["nodes"]
                match = next(
                    (f for f in fields if f.get("field", {}).get("id") == values["fieldId"]), {}
                )
                expected = values["value"]
                if not all(
                    match.get({"singleSelectOptionId": "optionId"}.get(k, k)) == v
                    for k, v in expected.items()
                ):
                    mismatch()
            return {
                "externalId": identity,
                "projectId": configuration["projectNodeId"],
                "observedAt": datetime.now(UTC).isoformat(),
            }
        result = next(iter(data.values()), {})
        obj = (
            result.get("discussion")
            or result.get("comment")
            or result.get("pullRequest")
            or result.get("thread")
            or {}
        )
        if not obj.get("id"):
            mismatch()
        # Re-read the exact object, rather than trusting the mutation receipt.
        fragments = "... on Discussion{id title body url} ... on DiscussionComment{id body url} ... on PullRequest{id isDraft url} ... on PullRequestReviewThread{id isResolved}"
        observed = await provider.graphql(
            "query($id:ID!){node(id:$id){id " + fragments + "}}", {"id": obj["id"]}, credential
        )
        obj = observed.get("node") or {}
        for key in ("title", "body"):
            if key in values:
                expected = values[key]
                if obj.get(key) != expected and not (
                    key == "body" and obj.get(key) == expected + "\n\n" + action_marker(request)
                ):
                    mismatch()
        if action.graphql == "ready" and obj.get("isDraft") is not False:
            mismatch()
        if action.graphql == "resolve" and obj.get("isResolved") is not True:
            mismatch()
        return {
            "externalId": obj["id"],
            "webUrl": obj.get("url"),
            "observedAt": datetime.now(UTC).isoformat(),
        }
    if operation.endswith("workflow.dispatch"):
        # 204 proves acceptance only. No attempt is made to infer a run from its title.
        return {
            "accepted": True,
            "verifiedOutcome": "dispatch_accepted",
            "headSha": values["expectedSha"],
            "testsPassed": False,
        }
    if operation.endswith("workflow.rerun"):
        return {"accepted": True, "verifiedOutcome": "rerun_accepted", "testsPassed": False}
    if operation.endswith("workflow.cancel"):
        return {"accepted": True, "verifiedOutcome": "cancellation_accepted"}
    if operation.endswith("pull_request.branch.update"):
        return {
            "accepted": True,
            "verifiedOutcome": "branch_update_requested",
            "testsPassed": False,
        }
    if operation.endswith("pull_request.merge"):
        repo = provider.repo_path(configuration)
        obj = (await provider.api("GET", f"{repo}/pulls/{values['number']}", credential)).data
        if not obj.get("merged") or obj["head"]["sha"] != values["sha"]:
            mismatch()
        return {
            "externalId": str(obj["id"]),
            "headSha": values["sha"],
            "mergeSha": obj["merge_commit_sha"],
            "webUrl": obj["html_url"],
        }
    obj = (await provider.api("GET", canonical, credential)).data
    if operation == "repository.commit.create":
        if obj["object"]["sha"] != data["object"]["sha"]:
            mismatch()
        from .artifacts import associate_test_revision

        await associate_test_revision(request, obj["object"]["sha"])
        return {
            "externalId": obj["object"]["sha"],
            "baseSha": values["baseSha"],
            "canonicalPath": canonical,
            "observedAt": datetime.now(UTC).isoformat(),
        }
    elif operation in {"repository.branch.create", "repository.branch.update"}:
        if obj["object"]["sha"] != values["sha"]:
            mismatch()
    elif operation == "repository.release.publish":
        if obj.get("draft") is not False or obj.get("tag_name") != values["expectedTag"]:
            mismatch()
    elif operation == "repository.issue.labels.set":
        if sorted(i["name"] for i in obj) != sorted(values["labels"]):
            mismatch()
    elif operation == "repository.release.asset.upload":
        if (
            obj.get("name") != values["name"]
            or obj.get("state") != "uploaded"
            or obj.get("digest") != "sha256:" + values["sha256"]
        ):
            mismatch()
    else:
        for key in (
            "title",
            "name",
            "description",
            "state",
            "body",
            "tag_name",
            "head_sha",
            "status",
            "conclusion",
            "color",
            "due_on",
        ):
            if key not in values:
                continue
            actual = obj.get(key) if isinstance(obj, dict) else None
            expected = values[key]
            if operation == "repository.label.update" and key == "name":
                expected = values.get("new_name", expected)
            if (
                key in {"body", "description"}
                and isinstance(actual, str)
                and actual.startswith(expected + "\n\n<!-- audoryn-action:")
            ):
                continue
            if actual != expected:
                mismatch()
        for key in ("labels", "assignees", "reviewers", "team_reviewers"):
            if key in values:
                remote = obj.get(
                    {"reviewers": "users", "team_reviewers": "teams"}.get(key, key), []
                )
                names = {v.get("login", v.get("slug", v.get("name"))) for v in remote}
                if not set(values[key]).issubset(names):
                    mismatch()
        if operation == "repository.review.submit" and (
            obj.get("commit_id") != values["commit_id"]
            or obj.get("state")
            != {
                "COMMENT": "COMMENTED",
                "APPROVE": "APPROVED",
                "REQUEST_CHANGES": "CHANGES_REQUESTED",
            }[values["event"]]
        ):
            mismatch()
    return {
        "externalId": str(obj.get("id", obj.get("number", ""))) if isinstance(obj, dict) else "",
        "canonicalPath": canonical,
        "webUrl": obj.get("html_url") if isinstance(obj, dict) else None,
        "bodyDigest": hashlib.sha256(
            (obj.get("body") or obj.get("description") or "").encode()
        ).hexdigest()
        if isinstance(obj, dict)
        else None,
        "observedAt": datetime.now(UTC).isoformat(),
    }
