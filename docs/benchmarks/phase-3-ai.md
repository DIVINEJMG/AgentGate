# Phase 3: how the AI decides, replies and uses integrations

Written 13 October 2026, for the [improvement plan](../agentgate-improvement-plan.md).

**How this was proven:** the owner chose to prove changes offline and run one live check at the
end, once the system is in good shape. Each change below is proven with SQLite tests or
simulated runs. The offline benchmark's outcomes stayed identical where behaviour was meant to
stay the same.

## 3a. Chat replies

1. **The chat context is read in a fixed 20 queries.**
   - **Before:** every chat message rebuilt the context with per-row queries:
     - each job's revision twice;
     - each recent run's full step outputs, just to make 700-character summaries;
     - each result's version, each policy's revision, and each approval's conversation.
   - **Measured:** that was 35 queries with 4 jobs and 79 with 12, growing with the
     organisation.
   - **Now:** 20 queries at any size. Job definitions, step outputs and result bodies are read
     only by the JSON parts used.
   - **Proof:** [test_conversation_context_batched.py](../../backend/tests/test_conversation_context_batched.py)
     compares the context with the previous assembler (kept in `tests/legacy_conversation_context.py`)
     and finds it identical, for organisation and worker threads.
2. **An ordinary reply takes one AI call instead of two.**
   - **How:** the call that decides what the message is can now write the reply in the same
     answer when it chooses "just answer".
   - **Checks:** that reply goes through exactly the checks a separate reply does: no claims of
     work done without evidence, and only backend facts cited, which the backend renders.
   - **Fallback:** if it is missing or fails a check, the previous separate reply call runs. So a
     reply can't get worse than before, and a good one costs one call, about half the AI time
     and tokens.
   - **Proof:** [test_one_call_reply.py](../../backend/tests/test_one_call_reply.py).
3. **Your message appears as soon as you send it.** The "working on a reply" indicator already
   existed. If sending fails, the message is removed and put back in the box.

**Not done:** streaming replies word by word (owner's decision: later, now that replies are one
call).

## 3b. Planning

- **Completion check per criterion.**
  - **What it does:** when finishing, the AI marks each completion criterion as met (citing the
    recorded steps that prove it), not met, or not verifiable.
  - **Enforced by the backend:**
    - cited steps must exist;
    - a "met" with no real evidence becomes "not verifiable";
    - anything short of all met makes the result "needs attention" instead of "completed".
  - **Where it shows:** the check is saved on the result, and the Results page shows it under
    "Completion check".
  - **Older answers:** a model answer without the check behaves as before.
  - **Proof:** [test_planning_quality.py](../../backend/tests/test_planning_quality.py) and two new
    benchmark tasks:
    - `criteria-proven` stays completed;
    - `criteria-unproven`, which cites a step that doesn't exist, becomes "needs attention".
- **A short result for every earlier action.** The planner already kept every action's identity
  and the full evidence of the last six steps. Older steps now keep a one-line result too, so a
  long run doesn't forget what it found.
- **Already in place:** "what has been tried" (the action history) and prompt budgets with "read
  more" guidance existed. Phase 0 showed prompts stay bounded.
- **Model per step type:** kept as one order, as the owner decided. Revisit with live numbers.
- **Cost:** the check and history lines add about 5% to each planning prompt. In the benchmark,
  tokens in for an 8-step run went from 37,019 to about 38,700.

## 3c. Integrations

- **One clear question, with the options.**
  - **Before:** when it wasn't clear which account or resource to use, the person was asked
    "Which github account and exact resource should I use…?" with no list.
  - **Now:** the question lists the resources that can do the job (up to 8, then "and N more"),
    so one reply settles it.
- **Missing permissions in plain words.**
  - **Before:** "acme/api cannot currently provide: github.repository.metadata.read…"
  - **Now:** "acme/api can't do what this task needs yet: read repository identity, default branch
    and access state. Its connection doesn't include that access. Reconnect acme with that
    permission in Integrations, then reply here to continue."
  - **Kept for support:** the exact permission names stay at the end.
  - **No connection can do it:** the message now says so, instead of asking which account to use.
- **Integration tools before the browser.** Task preparation already used only integration tools.
  The planner now has an explicit rule to prefer an authorized integration tool over the browser
  whenever one can do the step.
- **Proof:** [test_integration_messages.py](../../backend/tests/test_integration_messages.py).

## Deploying

- **No migration** and no new settings.
- **Workspace:** the frontend shows the completion check and the immediate message; rebuild it.
