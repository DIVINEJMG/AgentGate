from dataclasses import dataclass
from typing import Literal

PolicyOutcome = Literal["ALLOW", "DENY", "REQUIRE_APPROVAL"]

_EFFECT_WEIGHT: dict[PolicyOutcome, int] = {
    "ALLOW": 1,
    "REQUIRE_APPROVAL": 2,
    "DENY": 3,
}


@dataclass(frozen=True, slots=True)
class PolicyCandidate:
    outcome: PolicyOutcome
    priority: int
    name: str = ""
    policy_id: str = ""


def resolve_policy_precedence(candidates: tuple[PolicyCandidate, ...]) -> PolicyOutcome:
    if not candidates:
        return "DENY"
    highest_priority = max(candidate.priority for candidate in candidates)
    contenders = [candidate for candidate in candidates if candidate.priority == highest_priority]
    return max(
        contenders,
        key=lambda item: (
            _EFFECT_WEIGHT[item.outcome],
            item.name,
            item.policy_id,
        ),
    ).outcome
