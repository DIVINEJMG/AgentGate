from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ParityCase:
    id: str
    typescript_reference: str
    expected: str


def normalize_outcome(value: str) -> str:
    normalized = value.strip().upper()
    aliases = {
        "REQUIRE-APPROVAL": "REQUIRE_APPROVAL",
        "REQUIRE APPROVAL": "REQUIRE_APPROVAL",
    }
    return aliases.get(normalized, normalized)


def assert_reference_case(root: Path, case: ParityCase, python_outcome: str) -> None:
    reference = root / case.typescript_reference
    if not reference.exists():
        raise AssertionError(f"TypeScript reference is missing: {case.typescript_reference}")
    actual = normalize_outcome(python_outcome)
    expected = normalize_outcome(case.expected)
    if actual != expected:
        raise AssertionError(
            f"Parity mismatch for {case.id}: Python={actual}, TypeScript reference={expected}"
        )
