from __future__ import annotations

import hashlib
import hmac
import json

from app.bootstrap.settings import settings
from app.execution.providers.integration_hooks import VerifiedEvent

ALLOWED_EVENTS = {
    "installation",
    "installation_repositories",
    "issues",
    "issue_comment",
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
    "push",
    "workflow_run",
    "check_run",
    "check_suite",
    "release",
}


def verify_event(*, headers: dict, body: bytes) -> VerifiedEvent:
    if not settings.github_events_enabled or not settings.github_webhook_secret:
        raise PermissionError("GitHub event intake is disabled or unconfigured.")
    expected = (
        "sha256="
        + hmac.new(
            settings.github_webhook_secret.get_secret_value().encode(), body, hashlib.sha256
        ).hexdigest()
    )
    headers = {k.lower(): v for k, v in headers.items()}
    if not hmac.compare_digest(expected, headers.get("x-hub-signature-256", "")):
        raise PermissionError("Invalid GitHub webhook signature.")
    event_type, delivery = headers.get("x-github-event", ""), headers.get("x-github-delivery", "")
    if event_type not in ALLOWED_EVENTS or not delivery or len(delivery) > 255:
        raise ValueError("Unsupported GitHub event or invalid delivery identity.")
    data = json.loads(body)
    installation = data.get("installation", {}).get("id")
    if not isinstance(installation, int):
        raise PermissionError("Event has no verified installation identity.")
    repository = data.get("repository", {})
    object_data = next(
        (
            data[k]
            for k in (
                "comment",
                "review",
                "issue",
                "pull_request",
                "workflow_run",
                "check_run",
                "release",
            )
            if isinstance(data.get(k), dict)
        ),
        {},
    )
    # Keep only evidence needed for matching/planning. Signatures establish origin,
    # not authority; issue bodies and comments still contain untrusted instructions.
    evidence = {
        "action": data.get("action"),
        "installationId": str(installation),
        "repositoryId": str(repository.get("id", "")),
        "repositoryName": repository.get("full_name"),
        "objectId": str(object_data.get("id", "")),
        "number": object_data.get("number"),
        "url": object_data.get("html_url"),
        "title": str(object_data.get("title", ""))[:1000],
        "body": str(object_data.get("body", ""))[:10000],
        "senderId": str(data.get("sender", {}).get("id", "")),
        "senderLogin": str(data.get("sender", {}).get("login", "")),
        "suspended": data.get("action") == "suspend",
        "removedRepositoryIds": [str(r["id"]) for r in data.get("repositories_removed", [])],
        "permissions": data.get("installation", {}).get("permissions", {}),
        "projectId": data.get("projects_v2_item", {}).get("project_node_id"),
    }
    if event_type == "push":
        evidence["objectId"] = str(data.get("after", ""))
        evidence["commitMessages"] = [
            str(c.get("message", ""))[:1000] for c in data.get("commits", [])[:100]
        ]
    evidence["externalActionId"] = str(object_data.get("external_id", ""))[:255]
    return VerifiedEvent(
        delivery_id=delivery,
        account_id="installation:" + str(installation),
        event_type=event_type,
        resource_external_id=str(repository.get("id", "")),
        evidence=evidence,
        external_object_id=evidence["objectId"] or None,
    )
