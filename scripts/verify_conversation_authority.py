from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []

conversation_files = [
    ROOT / "backend/app/application/services/conversation_commands.py",
    ROOT / "backend/app/application/services/conversations.py",
    ROOT / "backend/app/application/services/intent_interpreter.py",
]
for path in conversation_files:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("app.execution"):
                errors.append(
                    f"{path.relative_to(ROOT)} imports execution provider code directly: {module}"
                )
            if module.startswith("app.integrations"):
                errors.append(
                    f"{path.relative_to(ROOT)} imports integration provider code directly: {module}"
                )
            if module == "app.infrastructure.database.models":
                names = {alias.name for alias in node.names}
                forbidden = {
                    "IntegrationCredential",
                    "AgentCredential",
                } & names
                if forbidden:
                    errors.append(
                        f"{path.relative_to(ROOT)} imports secret credential models: {sorted(forbidden)}"
                    )

compiler = conversation_files[0].read_text(encoding="utf-8")
for required in (
    "require_permission",
    "CrossTenantReferenceError",
    "cancel_item(",
    "save_trigger_config(",
    "_create_policy(",
    "set_worker_status(",
):
    if required not in compiler:
        errors.append(f"conversation authority compiler lacks canonical guard/service: {required}")

context = (
    ROOT / "backend/app/application/services/conversation_context.py"
).read_text(encoding="utf-8")
for forbidden in (
    "IntegrationCredential",
    ".config",
    "ciphertext",
    "secret_hash",
    "storage_key",
):
    if forbidden in context:
        errors.append(f"model context includes forbidden secret-bearing field: {forbidden}")

if errors:
    for error in errors:
        print(f"Conversation authority failure: {error}", file=sys.stderr)
    raise SystemExit(1)

print("Conversation authority and side-effect boundary verified.")
