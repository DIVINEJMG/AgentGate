# Conversation selection and reply validation

Implementation files: `app/application/services/reply_claims.py`,
`conversation_semantics.py`, `intent_interpreter.py`, `ai_gateway.py`,
`ordered_ai.py`, `conversation_commands.py`, `conversations.py`, and
`app/domain/ai/providers.py`. Regression coverage is in
`tests/test_reply_validation_flow.py`, with the existing web-authority test in
`tests/test_web_research_flow.py` aligned to its bounded-correction contract.

Full assembled context goes to one AI-selected typed operation. The backend
dispatches that operation through its existing authorized service. Only
`conversation.answer` receives the compact reply context; task preparation keeps
its complex workload and evidence. No human-message keyword classifier selects
an operation and no extra AI review call was added.

Ordinary replies may discuss intentions, questions, negation, explanations and
attributed quotations. Narrow clause checks reject direct worker execution,
outcome, preservation, absence and capability assertions in reply prose. Relevant
recorded outcomes use backend fact IDs, validated locally and rendered separately.
These English-language checks are conservative heuristics, not a universal proof
that arbitrary model prose is truthful. Execution authority and verified outcomes
remain enforced independently of prose validation.

Intent correction retains WorkerCommandIntent. Reply correction retains
answer/factIds. Other structured stages retain their original schema, objective,
authority and evidence. Each model gets at most the existing initial call and one
correction; fallback does not reinterpret the stage or enlarge authority.

Logs record context profile, selected operation, destination stage, correlation,
model/connection, rejection origin and allowlisted validation rule. Prompts,
model responses, match excerpts and credentials are not logged. Exhaustion records
the validation failure count and reports response validation distinctly from
provider outage. Failed reply commands leave accepted state while the human
message remains saved.

The interrupted request with correlation
`conversation:8099d24d-afeb-4d54-9272-e682f35100d4` entered the compact reply branch.
A compact read-only diagnostic found no saved conversation command for it; the
original selection rationale and offending reply sentences cannot be recovered
from the provided logs. No routing changes were inferred from that missing data.

Run offline regression checks from backend:

```powershell
python -m pytest tests/test_reply_validation_flow.py tests/test_conversation_semantics.py tests/test_first_request_fallback.py -q
```

Then retry the read-only PR review through conversation. Verify the selected
operation and downstream evidence in logs. Mocked tests do not establish live
model interpretation quality.
