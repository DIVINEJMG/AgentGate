from __future__ import annotations

import hashlib
from urllib.parse import quote

from ..safety import validate_changes, validate_task_branch


async def execute_git(provider, request, configuration, credential, constraints):
    payload = request.input
    root = provider.repo_path(configuration)
    repo = (await provider.api("GET", root, credential)).data
    branch = payload["branch"]
    validate_task_branch(branch, repo["default_branch"], request.work_item_id, constraints)
    ref = root + "/git/refs/heads/" + quote(branch, safe="")
    if request.operation == "repository.branch.create":
        result = await provider.api(
            "POST",
            root + "/git/refs",
            credential,
            {"ref": "refs/heads/" + branch, "sha": payload["sha"]},
        )
        return result, ref
    current = (
        await provider.api("GET", root + "/branches/" + quote(branch, safe=""), credential)
    ).data
    if current.get("protected"):
        raise ValueError("Writes to protected branches are unavailable.")
    expected = payload.get("baseSha", payload.get("expectedSha"))
    if current["commit"]["sha"] != expected:
        raise ValueError(
            "The branch moved. Observe the new revision and replan before publication."
        )
    if request.operation == "repository.branch.update":
        compare = (
            await provider.api(
                "GET", root + "/compare/" + expected + "..." + payload["sha"], credential
            )
        ).data
        if compare["status"] not in {"ahead", "identical"}:
            raise ValueError("The proposed update is not a fast-forward.")
        return await provider.api(
            "PATCH", ref, credential, {"sha": payload["sha"], "force": False}
        ), ref
    changes = payload["changes"]
    validate_changes(changes, constraints)
    commit = (await provider.api("GET", root + "/git/commits/" + expected, credential)).data
    tree = (
        await provider.api(
            "GET", root + "/git/trees/" + commit["tree"]["sha"] + "?recursive=1", credential
        )
    ).data
    if tree.get("truncated"):
        raise ValueError(
            "The base tree exceeds inspection limits. Narrow the change before publishing."
        )
    modes = {entry["path"]: entry["mode"] for entry in tree["tree"]}
    if any(modes.get(change["path"]) in {"120000", "160000"} for change in changes):
        raise ValueError("Symlink/submodule changes cannot be published by this action.")
    entries = [
        {
            "path": c["path"],
            "mode": modes.get(c["path"], "100644"),
            "type": "blob",
            **({"sha": None} if c.get("delete") else {"content": c["content"]}),
        }
        for c in changes
    ]
    new_tree = (
        await provider.api(
            "POST",
            root + "/git/trees",
            credential,
            {"base_tree": commit["tree"]["sha"], "tree": entries},
        )
    ).data
    marker = "Audoryn-Action: " + hashlib.sha256(request.idempotency_key.encode()).hexdigest()
    new_commit = (
        await provider.api(
            "POST",
            root + "/git/commits",
            credential,
            {
                "message": payload["message"] + "\n\n" + marker,
                "tree": new_tree["sha"],
                "parents": [expected],
            },
        )
    ).data
    # A competing branch update causes GitHub's non-force update to reject this
    # commit because it is no longer a descendant. Never overwrite that update.
    return await provider.api(
        "PATCH", ref, credential, {"sha": new_commit["sha"], "force": False}
    ), ref
