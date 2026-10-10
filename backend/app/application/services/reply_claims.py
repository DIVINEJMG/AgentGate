"""Conservative checks for direct worker-state assertions, never request routing.

This is a narrow linguistic guard, not a proof of unrestricted natural-language
truth. Outcomes are rendered separately from backend evidence references.
"""
import re

QUOTED = re.compile(r'```[\s\S]*?```|`[^`\n]*`|"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|(?<!\w)\x27[^\x27\n]+\x27(?!\w)')
CLAUSES = re.compile(r"[^.!?;\n]+[.!?;]?")
NON_ASSERTION = re.compile(
    r"\b(?:if|whether|unless|suppose|supposing|hypothetically|will|would|could|might|intend|plan|please)\b|"
    r"\b(?:explain|example|meaning|means|definition|phrase|quoted|quote|said|says|claiming|claimed)\b|"
    r"\b(?:not|never)\s+(?:true|think|believe|assert|assume|claim)\b",
    re.IGNORECASE,
)
RULES = {
    "execution_assertion": re.compile(
        r"\b(?:i|we|this worker|the worker)\s+(?:(?:have|has|already|successfully|just)\s+)*"
        r"(?:inspected|cloned|edited|tested|ran|pushed|committed|published|merged)\b", re.IGNORECASE),
    "outcome_assertion": re.compile(
        r"\b(?:the\s+)?(?:task|job|work|changes|tests?|pull request|branch)\s+"
        r"(?:(?:is|are|was|were|has|have|been|now|all|successfully)\s+)*"
        r"(?:completed|finished|published|merged|passed|failed|succeeded)\b", re.IGNORECASE),
    "preservation_assertion": re.compile(
        r"\b(?:(?:all|the|previous|completed|attempted)\s+)*(?:work|progress|changes)\s+"
        r"(?:(?:attempted|is|are|was|were|has|have|been|now|all)\s+)*"
        r"(?:preserved|retained|saved)\b", re.IGNORECASE),
    "capability_assertion": re.compile(
        r"\b(?:i|we|this worker|the worker)\s+(?:cannot|can't|lack|don't have|am unable to|are unable to)\s+"
        r"(?:(?:inspect|access|execute|use|read|edit|run|the|any|available|repository|execution)\s+){0,5}"
        r"(?:tools?|capabilities|github|python|repository|files?|runtime)\b|"
        r"\b(?:github\s+|execution\s+)?(?:tools?|capabilities|runtime|execution access)\s+"
        r"(?:(?:is|are|was|were)\s+)?(?:unavailable|not available|missing|not provided)\b", re.IGNORECASE),
    "absence_assertion": re.compile(
        r"\bno\s+(?:new\s+)?(?:changes|actions|work)\s+"
        r"(?:(?:were|was|have|has|been)\s+)*(?:made|executed|performed|attempted)\b", re.IGNORECASE),
}


def unsupported_assertion(answer: str) -> str | None:
    # Quoted evidence and examples are not claims by the worker. No human input
    # is examined here and no operation or authority is selected by these checks.
    prose = QUOTED.sub(" ", answer)
    prose = "\n".join(line for line in prose.splitlines() if not line.lstrip().startswith(">"))
    for clause in CLAUSES.findall(prose):
        clause = clause.strip()
        if clause.endswith("?"):
            continue
        for rule, pattern in RULES.items():
            match = pattern.search(clause)
            if match and not NON_ASSERTION.search(clause[:match.start()]):
                return rule
    return None
