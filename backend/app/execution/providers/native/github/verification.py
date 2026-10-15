from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, NoReturn

from app.execution.providers.native.http import ProviderTransportError

from .execution import action_marker
from .graphql import QUERIES

# Requested values a re-read must match exactly, and lists the re-read must contain.
CONFIRMED_VALUES = (
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
)
CONFIRMED_MEMBERS = ("labels", "assignees", "reviewers", "team_reviewers")
MEMBER_SOURCES = {"reviewers": "users", "team_reviewers": "teams"}
REVIEW_STATES = {
    "COMMENT": "COMMENTED",
    "APPROVE": "APPROVED",
    "REQUEST_CHANGES": "CHANGES_REQUESTED",
}
NODE_FRAGMENTS = (
    "... on Discussion{id title body url} ... on DiscussionComment{id body url} "
    "... on PullRequest{id isDraft url} ... on PullRequestReviewThread{id isResolved}"
)
# Writes whose 2xx proves acceptance only: nothing is inferred about the run or update they start.
ACCEPTED = {
    "workflow.rerun": {"accepted": True, "verifiedOutcome": "rerun_accepted", "testsPassed": False},
    "workflow.cancel": {"accepted": True, "verifiedOutcome": "cancellation_accepted"},
    "pull_request.branch.update": {
        "accepted": True,
        "verifiedOutcome": "branch_update_requested",
        "testsPassed": False,
    },
}

Check = Callable[[str, Any, dict[str, Any]], list[str]]


def mismatch() -> NoReturn:
    raise ProviderTransportError(
        code="verification_failed",
        retryable=False,
        safe_message="GitHub accepted the action, but canonical state does not establish its requested outcome.",
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def verify_write(provider, action, request, configuration, credential, data, canonical):
    """Re-read what a write changed and prove it holds the requested values. The evidence lists
    the fields it confirmed (`confirmedFields`), so the planner can show what was written."""
    values = request.input
    operation = action.operation
    if canonical.startswith("graphql:"):
        if action.graphql.startswith("project_"):
            return await _verify_project_item(
                provider, action.graphql, values, configuration, credential, data
            )
        return await _verify_node(provider, action.graphql, request, credential, data)
    if operation.endswith("workflow.dispatch"):
        # 204 proves acceptance only. No attempt is made to infer a run from its title.
        return {
            "accepted": True,
            "verifiedOutcome": "dispatch_accepted",
            "headSha": values["expectedSha"],
            "testsPassed": False,
        }
    accepted = next(
        (evidence for suffix, evidence in ACCEPTED.items() if operation.endswith(suffix)), None
    )
    if accepted is not None:
        return dict(accepted)
    if operation.endswith("pull_request.merge"):
        return await _verify_merge(provider, values, configuration, credential)
    obj = (await provider.api("GET", canonical, credential)).data
    if operation == "repository.commit.create":
        return await _verify_commit(request, obj, data, canonical)
    confirmed = CHECKS.get(operation, _matched_fields)(operation, obj, values)
    record = obj if isinstance(obj, dict) else {}
    return {
        "externalId": str(record.get("id", record.get("number", ""))) if record else "",
        "canonicalPath": canonical,
        "webUrl": record.get("html_url") if record else None,
        "bodyDigest": hashlib.sha256(
            (record.get("body") or record.get("description") or "").encode()
        ).hexdigest()
        if record
        else None,
        "confirmedFields": confirmed,
        "observedAt": _now(),
    }


async def _verify_project_item(provider, mutation, values, configuration, credential, data):
    created = next(iter(data.values()), {})
    identity = (created.get("item") or created.get("projectV2Item") or {}).get(
        "id", values.get("itemId")
    )
    item = (await provider.graphql(QUERIES["project_item"][0], {"id": identity}, credential)).get(
        "node"
    )
    if (
        not item
        or item.get("project", {}).get("id") != configuration["projectNodeId"]
        or (mutation == "project_archive" and not item["isArchived"])
        or (
            mutation == "project_add"
            and (item.get("content") or {}).get("id") != values["contentId"]
        )
    ):
        mismatch()
    if mutation == "project_update":
        field = next(
            (
                f
                for f in item["fieldValues"]["nodes"]
                if f.get("field", {}).get("id") == values["fieldId"]
            ),
            {},
        )
        if not all(
            field.get({"singleSelectOptionId": "optionId"}.get(k, k)) == v
            for k, v in values["value"].items()
        ):
            mismatch()
    return {
        "externalId": identity,
        "projectId": configuration["projectNodeId"],
        "observedAt": _now(),
    }


async def _verify_node(provider, mutation, request, credential, data):
    values = request.input
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
    observed = await provider.graphql(
        "query($id:ID!){node(id:$id){id " + NODE_FRAGMENTS + "}}", {"id": obj["id"]}, credential
    )
    obj = observed.get("node") or {}
    confirmed = [key for key in ("title", "body") if key in values]
    for key in confirmed:
        expected = values[key]
        if obj.get(key) != expected and not (
            key == "body" and obj.get(key) == expected + "\n\n" + action_marker(request)
        ):
            mismatch()
    if (mutation == "ready" and obj.get("isDraft") is not False) or (
        mutation == "resolve" and obj.get("isResolved") is not True
    ):
        mismatch()
    return {
        "externalId": obj["id"],
        "webUrl": obj.get("url"),
        "confirmedFields": confirmed,
        "observedAt": _now(),
    }


async def _verify_merge(provider, values, configuration, credential):
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


async def _verify_commit(request, obj, data, canonical):
    if obj["object"]["sha"] != data["object"]["sha"]:
        mismatch()
    from .artifacts import associate_test_revision

    await associate_test_revision(request, obj["object"]["sha"])
    return {
        "externalId": obj["object"]["sha"],
        "baseSha": request.input["baseSha"],
        "canonicalPath": canonical,
        "observedAt": _now(),
    }


def _same_sha(_operation: str, obj: Any, values: dict[str, Any]) -> list[str]:
    if obj["object"]["sha"] != values["sha"]:
        mismatch()
    return []


def _published(_operation: str, obj: Any, values: dict[str, Any]) -> list[str]:
    if obj.get("draft") is not False or obj.get("tag_name") != values["expectedTag"]:
        mismatch()
    return []


def _labels_set(_operation: str, obj: Any, values: dict[str, Any]) -> list[str]:
    if sorted(label["name"] for label in obj) != sorted(values["labels"]):
        mismatch()
    return ["labels"]


def _uploaded(_operation: str, obj: Any, values: dict[str, Any]) -> list[str]:
    if (
        obj.get("name") != values["name"]
        or obj.get("state") != "uploaded"
        or obj.get("digest") != "sha256:" + values["sha256"]
    ):
        mismatch()
    return []


def _matched_fields(operation: str, obj: Any, values: dict[str, Any]) -> list[str]:
    """Every requested value the re-read holds (a body may carry the action marker after it),
    and every requested member present in its list."""
    record = obj if isinstance(obj, dict) else {}
    confirmed = []
    for key in (key for key in CONFIRMED_VALUES if key in values):
        field = (
            "new_name"
            if operation == "repository.label.update" and key == "name" and "new_name" in values
            else key
        )
        actual, expected = record.get(key), values[field]
        marked = (
            key in {"body", "description"}
            and isinstance(actual, str)
            and actual.startswith(expected + "\n\n<!-- audoryn-action:")
        )
        if actual != expected and not marked:
            mismatch()
        confirmed.append(field)
    for key in (key for key in CONFIRMED_MEMBERS if key in values):
        names = {
            v.get("login", v.get("slug", v.get("name")))
            for v in record.get(MEMBER_SOURCES.get(key, key), [])
        }
        if not set(values[key]).issubset(names):
            mismatch()
        confirmed.append(key)
    if operation == "repository.review.submit" and (
        record.get("commit_id") != values["commit_id"]
        or record.get("state") != REVIEW_STATES[values["event"]]
    ):
        mismatch()
    return confirmed


CHECKS: dict[str, Check] = {
    "repository.branch.create": _same_sha,
    "repository.branch.update": _same_sha,
    "repository.release.publish": _published,
    "repository.issue.labels.set": _labels_set,
    "repository.release.asset.upload": _uploaded,
}
