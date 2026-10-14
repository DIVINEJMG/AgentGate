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
        r"\b(?:i|we|this worker|the worker)\s+(?:cannot|can['’]t|lack|do not have|don['’]t have|am unable to|are unable to)\s+"
        r"(?:(?:inspect|access|execute|use|read|edit|run|the|any|available|repository|execution|to)\s+){0,5}"
        r"(?:tools?|capabilities|github|python|repository|files?|runtime|permissions?|access|authority|ability)\b|"
        r"\b(?:i(?:['’]m| am)|we(?:['’]re| are)|this worker is|the worker is)\s+not\s+(?:allowed|permitted|authori[sz]ed)\s+to\b|"
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


# What a connected account contains now (open issues, unread messages...). Only a fresh read knows,
# so the router is advised to check; replies themselves are not blocked by it.
ACCOUNT_STATE = re.compile(
    r"\bthere\s+(?:are|is)\s+(?:currently\s+|now\s+|still\s+|presently\s+)?"
    r"(?:no|\d+|several|some|many|a few|only\s+\d+)\s+(?:open\s+|new\s+|unread\s+|pending\s+|closed\s+)?"
    r"(?:issues?|pull requests?|prs?|messages?|emails?|events?|meetings?|files?|commits?|branches?|comments?)\b|"
    r"\b(?:repository|repo|inbox|channel|calendar|drive)\s+(?:currently\s+|now\s+)?(?:has|contains)\s+"
    r"(?:no|\d+)\b", re.IGNORECASE)


def account_state_claim(answer: str) -> bool:
    prose = QUOTED.sub(" ", answer)
    for clause in CLAUSES.findall(prose):
        match = ACCOUNT_STATE.search(clause)
        if match and not clause.strip().endswith("?") and not NON_ASSERTION.search(clause[:match.start()]):
            return True
    return False
