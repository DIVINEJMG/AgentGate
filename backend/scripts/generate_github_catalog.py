from pathlib import Path

from app.execution.providers.native.github.catalog import assembled_catalog

rows = [
    "# GitHub action catalog",
    "",
    "Generated from `assembled_catalog()`; regenerate after catalog changes. Exact grants and organization policy apply in addition to these defaults. No unrestricted HTTP or GraphQL action is exposed.",
    "",
    "Reads are safe to retry within runtime budgets. Writes require canonical verification; uncertain writes reconcile first or pause. Coding command replay is never inferred safe from a missing process result. See [operations and rollout](github-integration.md).",
    "",
    "| Scope | Permission | Inputs (required marked *) | Approval default | Verification / reconciliation |",
    "|---|---|---|---|---|",
]
for a in assembled_catalog():
    fields = ", ".join(("**" + k + "***" if k in a.required else k) for k in a.fields) or "none"
    approval = "required" if a.approval else "policy only"
    if a.operation == "repository.commit.create":
        approval += "; required for deletion/workflow files"
    if a.operation == "repository.review.submit":
        approval += "; COMMENT is routine"
    verification = (
        "bounded observed response" if not a.write else "canonical object read; " + a.reconciliation
    )
    if ".workspace." in a.scope:
        verification = "isolated state / durable command evidence; no GitHub publication"
    rows.append(
        "| `"
        + a.scope
        + "` | `"
        + a.permission
        + "` | "
        + fields
        + " | "
        + approval
        + " | "
        + verification
        + " |"
    )
(Path(__file__).resolve().parents[2] / "docs/github-actions.md").write_text("\n".join(rows) + "\n")
print("Catalog actions:", len(assembled_catalog()))
