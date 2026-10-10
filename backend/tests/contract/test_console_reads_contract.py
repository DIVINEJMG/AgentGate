"""Console reads contract v1: shared schema, examples and Console's response limits."""

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

CONTRACT_DIR = Path(__file__).resolve().parents[3] / "docs" / "integration" / "console-reads"
SCHEMA = json.loads((CONTRACT_DIR / "contract.v1.json").read_text(encoding="utf-8"))
EXAMPLES = sorted((CONTRACT_DIR / "examples").glob("*.json"))
LIMITS = SCHEMA["x-limits"]
OPERATIONS = SCHEMA["x-operations"]


def _validator(definition: str) -> Draft202012Validator:
    registry = Registry().with_resource(SCHEMA["$id"], Resource.from_contents(SCHEMA))
    return Draft202012Validator(
        {"$ref": f"{SCHEMA['$id']}#/$defs/{definition}"},
        registry=registry,
        format_checker=FormatChecker(),
    )


def console_limit_violations(value: object, depth: int = 0) -> list[str]:
    """Mirror of Console's validate_evidence: any violation rejects the whole response."""
    if depth > LIMITS["maxDepth"]:
        return ["depth"]
    problems: list[str] = []
    if isinstance(value, dict):
        if len(value) > LIMITS["maxObjectKeys"]:
            problems.append("object keys")
        for key, item in value.items():
            if not isinstance(key, str) or key.lower() in LIMITS["forbiddenKeys"]:
                problems.append(f"forbidden key {key!r}")
            problems += console_limit_violations(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > LIMITS["maxListItems"]:
            problems.append("list items")
        for item in value:
            problems += console_limit_violations(item, depth + 1)
    elif isinstance(value, str) and len(value) > LIMITS["maxStringLength"]:
        problems.append("string length")
    return problems


def test_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(SCHEMA)


def test_every_operation_has_a_data_definition_and_example() -> None:
    covered = set()
    for path in EXAMPLES:
        body = json.loads(path.read_text(encoding="utf-8"))
        if "operation" in body:
            covered.add(body["operation"])
    assert covered == set(OPERATIONS)
    for spec in OPERATIONS.values():
        assert spec["data"].removeprefix("#/$defs/") in SCHEMA["$defs"]
        assert spec["path"].startswith("/internal/console/v1/")


def test_operation_scopes_match_console_operation_map() -> None:
    # Copied from Audoryn-Console backend/app/infrastructure/agentgate/reads.py OPERATIONS.
    console = {
        "health": "observability.read",
        "snapshot": "visibility.read",
        "overview": "visibility.read",
        "users": "visibility.read",
        "user": "visibility.read",
        "organizations": "visibility.read",
        "organization": "visibility.read",
        "operations": "observability.read",
        "queues": "observability.read",
        "usage": "observability.read",
        "ai-usage": "observability.read",
        "security": "security.read",
        "notifications": "notifications.read",
        "search": "visibility.read",
        "support-user": "visibility.read",
        "support-organization": "visibility.read",
    }
    assert {name: spec["scope"] for name, spec in OPERATIONS.items()} == console


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_example_matches_contract_and_console_limits(path: Path) -> None:
    body = json.loads(path.read_text(encoding="utf-8"))
    if "error" in body:
        errors = list(_validator("error").iter_errors(body))
        assert not errors, [error.message for error in errors]
        return
    errors = list(_validator("envelope").iter_errors(body))
    assert not errors, [error.message for error in errors]
    spec = OPERATIONS[body["operation"]]
    assert urlsplit(body["requestTarget"]).path == spec["path"]
    data_errors = list(_validator(spec["data"].removeprefix("#/$defs/")).iter_errors(body["data"]))
    assert not data_errors, [error.message for error in data_errors]
    assert console_limit_violations(body["data"]) == []
    assert len(json.dumps(body).encode()) <= LIMITS["maxResponseBytes"]


def test_directory_totals_cover_returned_items() -> None:
    for name in ("users", "organizations"):
        items, total = json.loads((CONTRACT_DIR / "examples" / f"{name}.json").read_text())["data"]
        assert total >= len(items)


def test_detail_examples_carry_the_requested_id() -> None:
    user = json.loads((CONTRACT_DIR / "examples" / "user.json").read_text())
    assert user["requestTarget"].endswith(user["data"]["user"]["id"])
    organization = json.loads((CONTRACT_DIR / "examples" / "organization.json").read_text())
    assert organization["requestTarget"].endswith(organization["data"]["organization"]["id"])


@pytest.mark.parametrize(
    ("definition", "value"),
    [
        ("data_overview", {"total_users": 1}),
        ("data_users", [[], -1]),
        ("data_user", {"user": {}, "memberships": [], "activity": {}, "local_password_configured": "yes"}),
        ("data_organization", {"organization": {}, "config": {}}),
        ("data_health", {"compatible": False}),
    ],
)
def test_contract_rejects_malformed_data(definition: str, value: Any) -> None:
    assert list(_validator(definition).iter_errors(value))


def test_console_limits_reject_forbidden_keys_and_oversized_lists() -> None:
    assert console_limit_violations({"password_hash": 1}) == []
    assert console_limit_violations({"Password": "x"}) == ["forbidden key 'Password'"]
    assert console_limit_violations({"items": list(range(201))}) == ["list items"]
    assert console_limit_violations({str(n): n for n in range(101)}) == ["object keys"]
