# Simulation findings

Findings from live testing with a simulated model ([ai-simulation.md](ai-simulation.md)). Only the
model was simulated; AgentGate, GitHub, E2B, Redis, the database and the queue were live.

**How these are handled:**
- Every finding is written down here when it is found.
- Fixes are made in one batch after the simulation, so each scenario tests the same code and the
  recorded transcripts stay replayable.
- **A finding that blocks the next scenario is fixed at once** and marked *fixed during
  simulation*.
- Each fix is proved by replaying the scenario that found it.

**The goal:** any live model must be able to run each scenario end to end. The system has to be
good enough that success does not depend on the model being clever.

The simulator (Claude Code) can read AgentGate's code, database and GitHub, and it learns from
earlier scenarios; a live model sees only its prompt. So:
- **The simulator keeps going.** It uses that extra knowledge to get each scenario through, so
  the whole path is exercised and we learn how to fix each weak point.
- **It notes every place it needed that knowledge** (or changed how it answered because of an
  earlier lesson) under *Where the simulator had an advantage* below. Each one is fixed in the
  batch like any other finding, even though the scenario passed.

**Severity:**
- **Blocker:** stops a scenario.
- **High:** wrong result, lost data, or a safety gap.
- **Medium:** wasted AI calls, credits or steps.
- **Low:** noise or wording.

## Summary

| # | Finding | Severity | Found in | Status |
|---|---|---|---|---|
| F1 | The issue-list result reaches the planner as one summary line; the issues themselves are lost. A model that picked only `issues.list` cannot finish the task. | High | Scenario 1 | **Fixed in Phase 1** |
| F2 | Plan revisions renumber steps in a way the prompt never explains, the correction message disagrees with the saved plan shown, and a completed step is dropped unless the model sets `planStepDone` | High | Scenario 1 | **Fixed in Phase 2** |
| F3 | Single-issue evidence carries about 3 KB of GitHub noise | Low | Scenario 1 | **Fixed in Phase 1** |
| F4 | The chat interpretation prompt is 67 KB for a one-line question; planning prompts are about 16 KB | Medium | Scenario 1 | **Reduced in Phases 3 and 5** (interpretation 85k → 25k, preparation 75k → 27k; target 15k not reached, gap explained) |
| F5 | Write steps are described as reads: "Read #7 …" (issue create), "Found 1 result: README.md." (workspace edit), "Read #8 …" (PR create) | Low | Scenarios 2, 3 | **Fixed in Phase 1** |
| F6 | Write evidence does not show the written content, so a model cannot prove a "body says …" criterion and may report a successful write as needing attention | Medium | Scenario 2 | **Fixed in Phase 1** |
| F7 | The branch-name rule needs the work-item ID, but the prompt never gives it to the model | High | Scenario 3 | **Fixed in Phase 2** |
| F8 | The daily coding budget leaks: ended tasks and failed attempts keep their 30-minute reservations, so coding stops for the day after about four attempts | Blocker | Scenario 3 | **Fixed during simulation** |
| F9 | The workspace diff text is malformed when a file has no final newline | Medium | Scenario 3 | **Fixed in Phase 1** |
| F10 | The approval wait reason shown to the person is internal jargon with a typo ("Matched 3 policyies …") | Low | Scenario 3 | **Fixed in Phase 4** |
| F11 | Every runtime step costs one QStash message (28 for one coding task) | Medium | Scenario 3 | **Fixed in Phase 5** |
| F12 | When the model fails chat interpretation twice, the person must resend the message themselves, and the reply is technical | Medium | Scenario 4 | **Fixed** (backend Phase 3, chat Retry button Phase 4) |
| F13 | Schema corrections do not name the wrong value: an unknown scope or an unauthorized tool comes back only as "… is not valid under any of the given schemas" | High | Scenario 4 | **Fixed in Phase 2** (planning and preparation corrections) |
| F14 | After a model refusal the task pauses (`waiting_ai`), but the chat still says "retry is scheduled", the job page says "Ready to run", and the only way on ("Retry planning") is hidden in Run history | High | Scenario 4 | **Fixed in Phase 4** |
| F15 | The chat's "Accepted" card for a task appears below messages sent after it | Low | Scenario 4 | **Fixed in Phase 3** |
| F16 | "Retry planning" cannot work after a refusal: it reuses the exhausted planning decision and fails at once without calling a model | High | Scenario 4 | **Fixed in Phase 4** |
| F17 | A chat message holds its database transaction open while waiting for the model; a slow answer (326 s) lost the connection, the request failed with 500, and the person's message was not saved | High | Scenario 5 | **Fixed in Phase 3** |
| F18 | The "where it failed" log for a 500 shows only library frames, not the AgentGate line | Medium | Scenario 5 | **Fixed in Phase 3** |
| F19 | A task cannot change a branch or PR from an earlier task even when the person names it; the grant (`constraints.branches`) exists but the preparation prompt never explains it | High | Scenario 5 | **Fixed in Phase 3** |
| F20 | When a write is refused by a safety rule, the task ends at once with the raw rule text ("Use this task's branch prefix codex/1e81a73a/ …"); the person is not told what to do | Medium | Scenario 5 | **Fixed in Phase 4** |
| F21 | When a write is paused as uncertain, the chat message is technical and does not say what to check or where the buttons are | Medium | Scenario 6 | **Fixed in Phase 4** |
| F22 | The event-request rule rejects a correct "an existing job already does this" answer, although its own message allows it | High | Scenario 7 | **Fixed in Phase 3** |
| F23 | A clarification answer that declines new work ("keep that one") still goes to preparation, which cannot end without creating a job, so a duplicate is made | High | Scenario 7 | **Fixed in Phase 3** |
| F24 | A finish with no completion-criteria assessment is accepted as "completed" | Medium | Scenario 7 | **Fixed in Phase 2** |
| F25 | A model-call slot left by a stopped process blocks planning for up to half an hour | Medium | Phase 5 replay | **Fixed in Phase 5** |
| F26 | A task whose automatic planning retries ran out says it is "waiting for capacity" but nothing wakes it | Low | Phase 5 replay | **Fixed in Phase 5** |
| F27 | A task waiting for another task's planning slot uses up its retries and pauses | Medium | Phase 5 replay | **Fixed in Phase 5** |
| F28 | Rejecting an approval leaves its task "waiting for approval" for good | Medium | Oracle deploy check | **Fixed**, proved in the simulator |
| F29 | A one-off browser task asked for in chat asks for a GitHub account instead | High | Oracle live check | Open |
| F30 | The Activity page crashes when it opens an action recorded by the runtime | High | Oracle live check | **Fixed** (Phase A of the Oracle findings plan) |
| F31 | A merge GitHub carried out is reported as failed ("incomplete action evidence"); the cause is not logged | High | Oracle live check | **Fixed** (Phase A of the Oracle findings plan) |
| F32 | Empty reads all say "Found nothing: the result is empty", hiding what was read | Low | Oracle live check | **Fixed** (Phase A of the Oracle findings plan) |
| F33 | A cut-off planner answer moves straight to a slower fallback model, and the reason is not recorded | Medium | Oracle live check | **Fixed** (Phase A of the Oracle findings plan) |
| F34 | A browser connection's "Live checks" never open the site or try the login | High | Browser setup review | Open |
| F35 | The planner is not told which login fields a browser connection stores | High | Browser setup review | Open |
| F36 | The setup suggests a one-time-code secret, but nothing turns it into a code | Medium | Browser setup review | Open |
| F37 | A job for a site that needs a login is created without the sign-in actions | High | Browser setup review | Open |
| Q1 | An explicitly requested issue was created without an approval card; confirm this is the intended policy | Question | Scenario 2 | Needs your decision |

---

## F1: List results lose their items before the planner sees them

- **Severity:** High (raised from Medium; see advantage A3). With `issue.read` authorized it
  costs an extra GitHub call and planning step per item. Without it, a live model cannot
  summarise the issues at all.
- **Found in:** Scenario 1, "What are the open issues in DIVINEJMG/school?", work item
  `181d53d6`, simulator request 0004.
- **What happened:** `github.repository.issues.list` returned one open issue. The planner's
  evidence for that step was:
  ```json
  "summary": "Found 1 result: #2 TE.",
  "providerOutput": {"data": {}, "evidenceLimits": {"truncated": false, ...}}
  ```
  Labels, body and comment count were missing, although the evidence says nothing was cut
  (`truncated: false`). To summarise the issue, the model had to read it again with
  `issue.read`.
- **Why it matters:**
  - With N open issues, the task needs N extra GitHub calls and N extra planning steps, which
    means N more AI calls.
  - The evidence also misleads: it claims to be complete while being empty.
- **Suggested fix:**
  - Keep a short per-item record in list evidence: number, title, state, labels, comment count,
    and the first ~300 characters of the body.
  - Mark the evidence as truncated when items are dropped.
  - Find where `providerOutput.data` is emptied for list operations.
- **Proof after the fix:** replay scenario 1. The planner should be able to finish after the
  list alone, with no `issue.read`.
- **Fixed in Phase 1:** `app/application/context/evidence.py` `compact_output` keeps every list item
  (an issue or pull request as number, title, state, labels, comment count, assignees, author, a
  300-character body excerpt and its link). A shortened body is listed in `omittedPaths`. Proved by
  replaying scenario 1: the planner prompt after `issues.list` holds all four open issues.

## F2: Plan revision numbering is not explained, and the correction disagrees with the prompt

- **Severity:** High (raised from Medium; see advantages A1 and A2). A wasted correction call per
  revision, and a likely cause of Sonnet's `calls=2`. The simulator only got past it by reading
  the code.
- **Found in:** Scenario 1, simulator requests 0004 and 0005.
- **What happened:**
  1. The model revised the plan (adding "Read issue #2") and set `planStep: 2`.
  2. AgentGate rejected it: *"Set planStep to the number of the pending plan step this action
     works on. Pending steps: [3, 4, 5]."*
  3. Cause (`AdaptiveRuntimePlanner._with_plan`): revised steps are numbered from the highest
     existing number + 1, and steps not marked done are dropped. The model had not set
     `planStepDone: true` on step 1, so step 1 was dropped, and its resent copy became step 3.
  4. The correction prompt still showed the old **SAVED PLAN** (steps 1 and 2, current step 1)
     beside the error naming steps 3–5.
- **Why it matters:**
  - Nothing in PLAN RULES tells the model how revised steps are numbered, so a correct-looking
    revision fails.
  - A completed action's step is silently dropped when `planStepDone` was not set.
- **Suggested fix:**
  - State the numbering in PLAN RULES, for example: "revised steps are numbered from {next} on;
    send only steps not yet done".
  - In the correction, show the revised plan the error refers to.
  - **Required:** a step whose action completed is not silently dropped because `planStepDone`
    was left out. Either the runtime marks it done when the step's tool completed, or the prompt
    shows the step as "completed, not marked done" so the model can decide.
- **Proof after the fix:** replay scenario 1. The revision is accepted on the first call.
- **Fixed in Phase 2:** the saved plan shows a step whose action completed without `planStepDone`
  ("[pending; action 1 completed, not marked done]"), the plan rules say revised steps are numbered
  from the next number in the order sent, a revision keeps done steps and steps with a completed
  action (saved as done; before, `_save_plan` deleted them), and the correction names the revised
  numbers ("Your revised steps are numbered 3 to 5 …"). Proved by replaying scenario 1.

## F3: Issue evidence carries GitHub API noise

- **Severity:** Low (tokens on every later planning step).
- **Found in:** Scenario 1, simulator request 0006.
- **What happened:** the `issue.read` evidence includes the full `user` object (about a dozen
  API URLs), `reactions`, `sub_issues_summary`, `issue_dependencies_summary` and similar
  fields. That is about 3 KB of a 3.5 KB record, repeated in every later planning prompt.
- **Suggested fix:**
  - Reduce `user`, `assignees` and `closed_by` to `login`.
  - Drop the API URL fields and empty summaries from issue and pull-request evidence.
  - Keep `html_url`/`webUrl` for references.
- **Proof after the fix:** replay scenario 1 and compare planner prompt sizes.
- **Fixed in Phase 1:** GitHub evidence drops API links, empty counters and signature text, and
  shows accounts and repositories by name. Issue evidence is 935 characters (was about 3,500); a
  pull request is about 1,900 (was 16,000).

## F4: Prompts are much larger than the request needs

- **Severity:** Medium (the largest AI-credit cost per message).
- **Found in:** Scenario 1, simulator requests 0001–0006.
- **What happened:**
  - The chat interpretation prompt was **67,590 characters** (about 17k tokens) for "What are
    the open issues in DIVINEJMG/school". It includes the full worker context, active jobs,
    history and the whole intent schema, written out twice (system rules and
    `AUTHORITATIVE_JSON_SCHEMA`).
  - Task preparation was also about 68 KB, mostly 7 repositories × 84 capability names.
  - Each planning prompt was about 16 KB, mostly browser rules for a task with no browser tools.
- **Why it matters:** with Claude on OpenRouter this is most of the per-message cost, and the
  same text is sent on every turn.
- **Suggested fix (to investigate):**
  - Send the schema once.
  - Leave out browser rules when no browser tool is authorized.
  - In preparation, list capability names once instead of per repository.
  - Bound the history included in interpretation.
- **Proof after the fix:** replay scenario 1 and record prompt sizes before and after; the
  result must be the same.
- **Partly fixed in Phase 3:** `app/application/context/conversation.py` (the conversation
  compacter) gives interpretation and preparation references, not pasted bodies: policies made for
  earlier tasks as one count, jobs by name and how they run, the latest 6 runs and results,
  memories that only repeat a result left out, and the thread as 8 recent turns plus one line per
  older turn. `app/application/context/preparation.py` lists each resource once with its tools as
  `all` or the missing ones, and capabilities as `{scope: description}`. Live (scenario 1):
  interpretation 85,476 → 30,032 characters, preparation 75,427 → 38,054. Still above the 15k
  target: the interpretation rules (7.6k), the capability descriptions (11.9k) and the job list
  are next.
- **Phase 5:** the JSON schema reaches the model once. A provider that enforces it natively
  (`json_schema` response format, Claude's output format) no longer also gets it pasted into the
  prompt (`AITextRequest.schema_instruction`, `model_profiles.place_schema`); others still do. The
  interpretation context is smaller too (times to the minute, no "active" status on active jobs,
  plain scope names, a result's title left out when it repeats its job's name). Measured on the
  scenario 1 replay: interpretation 24.8k (system 7.6k, context 17.2k), preparation 26.7k (system
  2.2k, request 24.5k), planning 8.5k–13.6k.
- **Gap (accepted, 15 October):** the 15k target is not met. What remains is the routing rules
  (7.6k), the workspace context (jobs, results, thread) and, for preparation, the 84 GitHub tool
  descriptions. Cutting those risks a wrong route or a missing tool, which replays cannot detect
  (their answers are recorded), so they stay until the live check shows whether size matters.

## F5: A create action is reported as a read

- **Severity:** Low (wrong wording in progress messages and the run record).
- **Found in:** Scenario 2, "Create an issue in DIVINEJMG/school titled “Simulation test” …",
  work item `087ed8f1`, simulator request 0010.
- **What happened:** `github.repository.issues.create` created issue #7. The step's summary,
  shown in chat progress and in `actionHistory`, was *"Read #7 Simulation test."*
- **Why it matters:**
  - The person sees "Read" for something AgentGate created.
  - The planner reads the same summary, so it may think the issue already existed.
- **Suggested fix:** give write operations their own summary verb ("Created #7 …", "Commented on
  #7 …", "Opened draft PR #8 …"). The GitHub result summariser probably uses the canonical
  re-read (`verify_write`) to describe the step.
- **Proof after the fix:** replay scenario 2; the step summary reads "Created #7 Simulation
  test."
- **Fixed in Phase 1:** `app/application/context/narrator.py` takes the verb from the operation:
  "Created issue #11 Simulation test." (proved by replaying scenario 2), "Edited README.md (1 file
  changed, …)", "Opened draft pull request #8 …", "Created branch … at 28435fd".

## Q1: Explicitly requested writes run without an approval card

- **Type:** a policy question, not a defect.
- **Found in:** Scenario 2.
- **What happened:**
  1. The capability `github.repository.issues.create` has `approvalRecommendation: "none"`.
  2. Preparation recorded the human's exact words as the write's `request_excerpt`.
  3. The issue was created at once, with no approval card.
  4. Publishing a pull request did ask for approval in earlier live tests.
- **Decision needed:**
  - Should a write the person asked for in so many words run without a second confirmation?
    That is the current behaviour.
  - Or should every external write (issue, comment, label) show an approval card?
- **Effect on testing:** the approval flow will be exercised in the pull-request scenario
  instead.
- **Done in Phase 4:** every write other people can see asks first, including writes the person
  asked for in so many words; the chat's approval card has **Always allow** (as the Inbox already
  had), which saves a policy for that worker, resource and kind of action, listed and removable in
  Policies. Proved live: scenario 6's issue now waited for approval.

## F6: Write evidence does not show what was written

- **Severity:** Medium (a successful task may be reported as needing attention).
- **Found in:** Scenario 2, simulator request 0010 (advantage A5).
- **What happened:**
  - After `issues.create`, the planner's evidence was: number, title, state, author, link,
    `createdAt`, and `verified: true`. The body was not included.
  - The completion criterion "Its body says it was created by a simulated run" could only be
    marked *met* because the simulator remembered the body it had sent.
  - A model following the rules ("finish only when recorded evidence supports the criteria")
    would mark it *not_verifiable*, and the task would end as **attention** although it worked.
- **Suggested fix:**
  - Include in write evidence the fields that were written and verified (for an issue: a body
    excerpt and its labels), or a field-by-field verification list ("body: matches request").
  - Apply the same to comments, pull requests and commits.
- **Proof after the fix:** replay scenario 2 with a model that cites only evidence; the run ends
  *completed*.
- **Fixed in Phase 1:** GitHub's write verification records the requested fields its re-read
  matched (`confirmedFields`), and the planner sees those values as `confirmedWritten` (body
  excerpt up to 600 characters). Proved by replaying scenario 2.

## F7: The planner must name branches after a work-item ID it is never shown

- **Severity:** High (a live model cannot follow the branch rule, so publishing may be refused or
  use a wrong name).
- **Found in:** Scenario 3, "In DIVINEJMG/school, add a README section about setup and a small
  helper script, then open a draft PR.", work item `55ea0379`, simulator request 0013.
- **What happened:** the planning rules say *"GitHub task branches use codex/<first eight
  work-item ID characters>/<task-name>"*. The planner prompt contains no work-item ID (searched
  for `workItem`, `work_item` and the ID itself). The simulator found it by querying the database
  (advantage A7).
- **Suggested fix:**
  - Put the exact branch prefix in the prompt, for example `TASK_BRANCH_PREFIX:
    codex/55ea0379/`, or the work-item ID itself.
  - Better still: let the runtime fill in the prefix, so the model chooses only the
    `<task-name>` part.
- **Proof after the fix:** replay scenario 3; `branch.create` uses the right prefix without
  outside knowledge.
- **Note (Phase 2 replay):** `branch.create` waits for approval whatever the branch name: the
  workspace policy requires it ("Matched 3 policyies …", F10). The Phase 1 note blaming the old
  name was wrong.
- **Fixed in Phase 2:** the planner job carries the work-item ID, and when a GitHub tool can write,
  the rules give the task's own prefix ("This task's GitHub branches start with codex/53fe95b9/ …").
  Proved by replaying scenario 3: the branch request used `codex/53fe95b9/readme-setup`.

## F8: The daily coding budget leaked and stopped coding for the day (fixed during simulation)

- **Severity:** Blocker (scenario 3 could not open a workspace).
- **Found in:** Scenario 3, work item `55ea0379`. `workspace.open` returned
  `rate_limited` with *"Organization coding-time budget is exhausted; this task will wait."*
- **What happened:**
  - Every coding task reserves a full `CODING_TASK_ACTIVE_SECONDS` (1,800 s) against
    `CODING_ORGANIZATION_DAILY_SECONDS` (7,200 s) when its workspace opens.
  - The reservation was given back only when the workspace closed normally.
  - Today's counter stood at **7,200 / 7,200**. Two finished tasks (`partial_completion`) still
    had paused sessions holding 1,800 s each, though they had used 486 s and 755 s. Two more
    reservations belonged to no session at all.
  - So about 1,240 s of real use blocked all coding until midnight UTC.
- **Fix (made now, because it blocked the scenario):**
  - `app/execution/coding/service.py` no longer trusts the running counter.
    `committed_seconds()` works out the day's use from the sessions: a session still working
    keeps its reservation; one whose task ended, or whose time was released, counts only what it
    used; a reservation with no session counts nothing.
  - `reserve()` and `rollover_budget()` correct the saved counter to that figure before checking
    it, so any earlier leak heals itself.
- **Tests:** `test_coding_budget_follows_sessions.py` covers ended tasks counting only their use,
  the live leaked state no longer blocking, and working tasks still filling the day. The existing
  next-day rollover test now builds its full day from working sessions.
- **Proof:** scenario 3 continues after the backend restart.

## F9: The workspace diff text is malformed when a file has no final newline

- **Severity:** Medium (a model reading the diff, or reusing it as a patch, is misled; the
  `changes` list used for publishing is correct).
- **Found in:** Scenario 3, work item `55ea0379`, step 7 (`workspace.diff`), simulator request
  0020.
- **What happened:** README.md had no final newline. The exported `diff` contained
  `-# school+# school` on one line, and the next file's header ran straight into the last line of
  the previous file: `…prints the next steps.--- a/scripts/setup.sh`. The per-edit preview (step 5)
  was correct and showed `\ No newline at end of file`.
- **Suggested fix:** build the exported diff the way the edit preview does (git's format, with the
  "No newline at end of file" marker and a newline between files).
- **Proof after the fix:** replay scenario 3; the step 7 diff applies cleanly with `git apply`.
- **Fixed in Phase 1:** `app/execution/coding/patch.py` builds the diff in git's format (`diff --git`
  headers, new and deleted file modes, the "No newline at end of file" marker). A test applies it
  with `git apply`.

## F10: The approval wait reason is jargon with a typo

- **Severity:** Low.
- **Found in:** Scenario 3, waiting on approval of `pull_request.create`.
- **What happened:** the task's waiting reason was *"Matched 3 policyies at highest priority
  101. Safety precedence resolves ties as DENY > REQUIRE_APPROVAL > ALLOW."*
- **Suggested fix:** show the person what is waiting and why, for example "Waiting for your
  approval to open the draft pull request in DIVINEJMG/school". Keep the policy detail in the
  audit record. Fix "policyies".
- **Fixed in Phase 4:** the wait and the chat say "Waiting for your approval to create the issue
  “Uncertain write test” in DIVINEJMG/school." (`app/application/messages.py`); the policy reason
  stays in the step's record, and "policyies" is fixed. Proved live in scenario 6.

## F11: Every runtime step costs a QStash message

- **Severity:** Medium (limits how much work fits in QStash's daily quota).
- **Found in:** Scenario 3: 32 runtime steps and 28 QStash messages for one coding task.
- **What happened:** each plan and execute phase ends by publishing the next step's wake-up
  through QStash, even on a long-running server. A coding task of about 12 actions uses about
  28 messages, so roughly 35 such tasks fit in the free 1,000 a day, before chat and schedules.
- **Suggested fix:** on a serving backend, run the next step in-process when the work item is
  due now (like the local outbox drain). Use QStash only for delayed wake-ups (rate-limit
  waits, retries) and as the recovery backstop.
- **Proof after the fix:** replay scenario 3 and compare QStash messages per task.
- **Fixed in Phase 5:** on a serving backend a step that is due now runs in the same process
  (`api/internal/runtime.run_here`, used by `request_runtime_execution_detailed`; setting
  `runtime_run_local`). QStash keeps delayed wake-ups (retries, admission waits), full capacity and
  recovery; a step lost with the process is recovered by the sweep, as an accepted QStash delivery
  was. Proved live: scenario 3 used 0 QStash messages for its continuations (baseline 28), and a
  backend restarted mid-task picked the task up again through its watchdog; scenario 1 used 0.

## F12: A failed chat interpretation needs the person to resend

- **Severity:** Medium (one model glitch costs the person an action and some confusion).
- **Found in:** Scenario 4, "List All open Pull request in the DIVINEJMG/school", simulator
  requests 0025 (invalid JSON) and 0026 (unknown `family` in the correction).
- **What happened:**
  1. The first answer was invalid JSON. AgentGate asked for a correction with a clear reason
     (*"malformed_json … AI provider returned invalid JSON"*). Good.
  2. The correction was also invalid, so AgentGate stopped (*"AI selection exhausted …
     attempted=1 configured=1"*).
  3. The person was told: *"The AI responses could not pass response validation. Your message is
     saved; this reply attempt did not execute a task. Please retry."*
- **Why it matters:**
  - Nothing ran, which is safe.
  - But the person has to send the same message again.
  - The wording is technical ("response validation").
  - In production, other routes are tried first, so this mainly happens when every route fails.
- **Suggested fix:**
  - Offer a **Retry** button on the saved message (the message is already stored).
  - Or retry once automatically after a short pause.
  - Use plain wording: "I couldn't work out what to do with that message. Retry, or rephrase it."
- **Proof after the fix:** replay requests 0025–0026; the person can retry with one click.
- **Fixed in Phase 3 (backend):** the reply is plain ("I couldn't work out what to do with that
  message, so nothing was run. Your message is saved: retry it, or rephrase it.") and
  `POST …/conversations/{thread}/messages/{message}/retry` replies again to the saved message.
  Proved live: two invalid answers, then a retry of the same message was accepted. The chat's
  Retry button comes with the approved Phase 4 buttons.
- **Chat (Phase 4):** a failed reply carries a Retry control; the chat's Retry button replies again
  to the saved message.

## F13: Schema corrections do not say which value is wrong

- **Severity:** High (a live model is likely to fail the correction too, so the task stops or
  stalls).
- **Found in:** Scenario 4, simulator requests 0028→0029 (preparation) and 0030→0031
  (planning).
- **What happened:**
  - Preparation used a scope that does not exist (`github.repository.pulls.list`). The correction
    named the missing `completion_criteria` clearly. For the scope it said only *"{'provider':
    'github', … 'scopes': ['github.repository.pulls.list']} is not valid under any of the given
    schemas"*.
  - The planner chose an unauthorized write (`github.repository.issues.create`). It was blocked
    (good), but the correction repeated the whole answer followed by *"… is not valid under any
    of the given schemas"*, with no mention of the scope.
- **Why it matters:** `oneOf`/`anyOf` schema errors hide the real cause. A model sees "something
  is invalid" and may resend the same value; the second failure then ends the task.
- **Suggested fix:** check these cases before generic schema validation, and send plain
  corrections:
  - "Unknown scope `github.repository.pulls.list`. Closest valid: `github.repository.pull_requests.list`."
  - "`github.repository.issues.create` is not an authorized tool for this task. Authorized:
    `github.repository.pull_requests.list`."
  - Do not echo the whole rejected object.
- **Proof after the fix:** replay requests 0028 and 0030; the correction names the value.
- **Fixed in Phase 2:** `app/application/context/corrections.py` turns schema failures into plain
  lines naming the value, the closest valid one and the choices, without echoing the answer. Proved
  live by replaying scenario 4: "`needs.0.scopes.0` 'github.repository.pulls.list' is not allowed
  here. Closest: 'github.repository.pull_requests.list'." and "`scope`
  'github.repository.issues.create' is not allowed here. Allowed: github.repository.pull_requests.list."

## F14: A paused task after a model refusal is hard to find and resume

- **Severity:** High (the person cannot tell the task stopped, or how to continue).
- **Found in:** Scenario 4, work item `496f0411`, simulator request 0032 (refusal).
- **What happened:**
  1. A planning attempt failed as `provider_unavailable` (request 0031). AgentGate retried, which
     is good, and posted *"AI planner is temporarily unavailable; retry is scheduled."*
  2. The retry got a refusal. The work item and run moved to `waiting_ai`, with reason *"The
     planning service declined this request. Your task is paused; completed work remains saved."*
  3. What the person saw:
     - **Chat:** still *"AI planner is temporarily unavailable; retry is scheduled."* No message
       said the task had paused.
     - **Job page:** "Ready to run · Active", with **Run now** (which, for a chat task's one-shot
       job, is refused).
     - **Work queue:** a waiting item with no action.
     - The only control, **Retry planning**, is in Run history → the run.
  4. The background sweep does not pick up `waiting_ai` items, so nothing resumes on its own.
- **Suggested fix:**
  - Post a chat message when a task pauses for planning, with a **Retry planning** control on it
    (as the chat already does for `waiting_ai` preparation commands).
  - Show the same control on the Work queue item.
  - Consider one automatic retry with a different route, or after a delay, before pausing: a
    refusal from one model does not mean every model will refuse.
- **Proof after the fix:** replay request 0032; the chat says the task paused and offers Retry.
- **Fixed in Phase 4:** a paused task posts "The AI planner declined to plan the next step, so I've
  paused this task. … Press Retry planning to ask again." with a Retry planning control in the chat,
  and the Work queue shows Retry planning for a task waiting on the AI. (The pause message was
  being dropped: it reused the earlier "retry scheduled" message's key.) Proved live.

## F15: The "Accepted" card appears below later messages

- **Severity:** Low (ordering), **needs checking**.
- **Found in:** Scenario 4 (screenshot): the card *"Accepted — I accepted your integration
  task…"* shows below the 10:51 and 10:52 PM messages that came after it.
- **Suggested fix:** check whether live command cards are pinned to the end of the thread on
  purpose. If not, order them by their creation time.
- **Fixed in Phase 3:** the receipt card shows only while its reply is the newest message, so a
  later result is never shown above it (`WorkerChatPage.tsx`). A failed send whose message the
  server saved is no longer put back in the composer (no duplicate on resend).

## F16: "Retry planning" reuses the exhausted planning decision

- **Severity:** High (the only recovery control for a paused task does nothing useful).
- **Found in:** Scenario 4, work item `496f0411`, run `7fb06362`, after the refusal (request
  0032).
- **What happened:**
  1. The person pressed **Retry planning** in Run history. `POST …/runs/7fb06362…/continue`
     answered `202`.
  2. The route (`continue_run_v1`, `runtime_result_routes.py`) only sends a runtime wake-up. It
     does not reset the paused work item or its saved planning decision.
  3. The step ran (`aiCalls: 0`, 26 s) and stopped in `SmartPlanner.choose` with *"The saved
     planning decision budget is exhausted."*, because the decision had been marked `exhausted`
     by the refusal.
  4. The person then saw: *"The planning request stopped waiting for a response. Your task and
     completed work are saved; the remaining work needs attention."* That is misleading: no
     model was asked.
- **Suggested fix:**
  - When a person retries planning, start a new planning decision (fresh deadline and
    attempts) for the current step, and put the work item back in the queue.
  - Keep the old decision for the record.
  - Report the real reason if planning still cannot run.
- **Proof after the fix:** press Retry planning on a refused task; a new model request arrives
  and the task continues.
- **Fixed in Phase 4:** `smart_planner.restart_planning` starts the decision afresh in a new
  recovery round (new deadline, every route tried again; earlier attempts stay on record) and puts
  the task back in the queue. Proved live: after a refusal, Retry planning brought a new model
  request and the task completed. (A first version reset the round to 0 and clashed with the
  attempts' unique key; the live run caught it and the test session now enforces that key.)

## F17: A slow model answer loses the chat message

- **Severity:** High (the person's message disappears and the reply is a 500).
- **Found in:** Scenario 5, "Please merge the new branch into main.", simulator request 0033.
  The answer (a clarification question) arrived after 326 s.
- **What happened:**
  1. `POST …/conversations/73e3eed3…/messages` started a database transaction, then waited
     326 s for the interpretation.
  2. Meanwhile the database connection was dropped, probably by an idle-in-transaction or
     pooler timeout. The exact limit still needs confirming.
  3. The next statement failed. The request ended `500` after 339.7 s with
     `Conversation operation failed error_type=PendingRollbackError`.
  4. The human message is saved in the same transaction, so it was rolled back. The thread has no
     trace of it, although failure replies elsewhere say "Your message is saved".
- **Why it matters:**
  - The simulator was slow, but live models can also take minutes: `AI_INTERACTIVE_ATTEMPT_SECONDS`
    is 250 s by default, and planning allows far longer.
  - Any slow answer risks the same failure.
- **Suggested fix:**
  - Commit the human message (and any accepted state) **before** calling the model.
  - Do not hold a transaction or pooled connection across the model call: release it, then open
    a new one for the result.
  - If the reply step fails, the saved message can still be retried (see F12).
- **Proof after the fix:** replay request 0033 with a delayed answer (for example 6 minutes); the
  message stays saved and the reply arrives.
- **Fixed in Phase 3:** the message is committed before any model call, and the conversation's
  gateway (`ReleasingGateway`) ends the transaction before each model call, with invocation
  records in their own short transactions. Proved live: the message was visible from another
  session at once, the answer came 6 min 56 s later, and the reply and task were saved.

## F18: The 500 log does not show the AgentGate line

- **Severity:** Medium (slows finding the cause of a 500).
- **Found in:** Scenario 5. The new log line showed only SQLAlchemy frames
  (`engine/base.py:680 _invalid_transaction < … _execute_clauseelement`).
- **Suggested fix:** in `conversation_routes._http_error`, log the last frames that are inside
  `app/` (skipping library frames), plus the exception type of the first cause in the chain.
- **Proof after the fix:** replay F17; the log names the conversation service line.
- **Fixed in Phase 3:** `conversation_routes.app_frames` logs the innermost `app/` frames and the
  first cause's type.

## F19: A named branch from an earlier task cannot be changed

- **Severity:** High (any follow-up on earlier work fails: "merge the PR you opened", "fix the
  branch", "update PR #8").
- **Found in:** Scenario 5, "please merge the new branch into main", then the clarification
  "merge codex/55ea0379/readme-setup in DIVINEJMG/school (draft pull request #8)". Work item
  `1e81a73a`, simulator requests 0035–0037.
- **What worked first:**
  - The clarification question was asked.
  - The person's answer was joined to the original request **without another AI call**
    (*"please merge the new branch into main / Human clarification: …"*).
  - Preparation and the PR read ran.
- **What happened next:**
  - `pull_request.ready` on PR #8 failed: *"Use this task's branch prefix codex/1e81a73a/ or an
    explicitly granted branch."* (`safety.validate_task_branch`).
  - A task may only write to `codex/<its own work-item ID>/…` or to branches listed in its
    grant's `constraints.branches`.
  - The preparation schema has `constraints.branches`, but with no description. No rule tells
    the model to grant a branch the person explicitly named. The simulator did not set it, and a
    live model would not either.
- **Suggested fix:**
  - In preparation, explain `constraints.branches`: "when the human names an existing branch or
    pull request to change, list that branch here".
  - Accept the grant only when the branch, or the PR number that resolves to it, is grounded in
    the human's words (`grounded_in`).
  - Optionally, let the runtime resolve "PR #8" to its head branch and grant it after the
    person's explicit request.
- **Proof after the fix:** replay requests 0035–0037; PR #8 is marked ready and the merge asks
  for approval.
- **Fixed in Phase 3:** the GitHub constraint schema describes `branches` and the new
  `pullRequests` grant; `grounded_constraints` keeps only branches and pull requests the person
  named; `validate_task_branch` allows a granted pull request's head branch. Proved live: PR #8
  was marked ready, and the merge waits for approval.

## F20: A refused write ends the task with raw rule text

- **Severity:** Medium.
- **Found in:** Scenario 5, after F19.
- **What happened:**
  - The failed write ended the task as `partial_completion` straight away. The planner was not
    asked again.
  - The person was told: *"I could not complete the remaining work: Use this task's branch prefix
    codex/1e81a73a/ or an explicitly granted branch. Already completed actions remain saved; no
    rollback is claimed."*
  - They cannot tell what went wrong or what to do.
- **Suggested fix:**
  - Translate safety refusals into plain language with the way forward. For example: "PR #8 was
    opened by an earlier task, so this task is not allowed to change it. Ask me to merge PR #8
    explicitly and I'll request permission for that branch."
  - Consider letting the planner see the refusal and finish with that explanation, instead of
    ending at once.
- **Proof after the fix:** replay request 0037; the reply says why and what to ask for.
- **Fixed in Phase 4:** refusals are explained with the way forward ("This task may only change its
  own new branches, so I didn't change that branch. To change an existing branch or pull request,
  ask me again and name it …"); the rule text stays in the record. Tested; with F19 fixed, the
  scenario 5 refusal no longer occurs live.

## F21: The uncertain-write message does not say what to do

- **Severity:** Medium.
- **Found in:** Scenario 6 (uncertain write), work item `2ab06143`.
- **What happened:** after the three automatic checks, the chat said: *"A previous delivery may
  have dispatched this write. Its outcome cannot be reconciled, so automatic replay is paused.
  Completed steps remain saved."* It does not say:
  - which write it was;
  - what to look for on GitHub;
  - where the **Check again / It didn't happen / It happened** buttons are (Jobs → Work queue).
- **Suggested fix:**
  - Use plain wording that names the write. For example: "I couldn't confirm whether the issue
    'Uncertain write test' was created in DIVINEJMG/school. Please check, then tell me: It
    happened / It didn't happen / Check again."
  - Show the same three buttons on the chat message, not only on the Jobs page.
- **Proof after the fix:** replay scenario 6; the chat names the write and offers the buttons.

**Scenario 6 also proved** (no defect): the write was held as `dispatching` and never blindly
resent. Three automatic checks searched GitHub (`checking_outcome` 1→2→3), then the task paused.
**Check again** looked once more and paused again. **It didn't happen** sent the write as new,
creating exactly one issue (#9). Both choices are in the audit log
(`integration.outcome.settled`, `check` then `not_done`).
- **Fixed in Phase 4:** "I couldn't confirm whether the issue “Uncertain write test” was created in
  DIVINEJMG/school. Please check DIVINEJMG/school, then tell me: It happened, It didn't happen, or
  Check again." with the three buttons on the chat message. Proved live in scenario 6.

## F22: The event-request rule rejects a correct answer

- **Severity:** High (pushes the model towards a duplicate job or an unnecessary question).
- **Found in:** Scenario 7, "When a new issue is opened in DIVINEJMG/school, summarise it here.",
  simulator requests 0042→0043.
- **What happened:**
  1. An active job already does exactly this: `runs: "each time the github event issues.opened
     happens"`, *"When a new issue is opened in the DIVINEJMG/school repository … post a summary
     here"*.
  2. Following REPLY_RULES, the model answered with `conversation.answer` and
     `requested_start: on_event`, saying the existing job already covers it.
  3. `validate_route_proposal` (`conversation_semantics.py`) rejects every
     `conversation.answer` with `requested_start == "on_event"`, unconditionally.
  4. Its correction says *"Unless an existing job's `runs` is that event, set it up with
     integration.execute, or ask …"*. The code never checks that exception.
- **Why it matters:** the correct answer costs a correction call. The model is then steered to
  create a duplicate event job (two summaries per issue) or to ask a needless question.
- **Suggested fix:** allow `conversation.answer` with `on_event` when an active job in the
  context has a `runs` value for that same event and resource. Reject only when none does.
- **Proof after the fix:** replay request 0042; the answer is accepted on the first call.
- **Fixed in Phase 3:** each active job that runs on an event is a backend fact
  (`job:<id>:event`); a chat answer citing one is accepted. Proved live: accepted on the first call.

## F23: Declining in a clarification answer still creates the work

- **Severity:** High (creates work the person said no to).
- **Found in:** Scenario 7, simulator requests 0043→0044.
- **What happened:**
  1. After F22, the corrected answer asked: "Should I keep just that one, or add a second job
     alongside it?"
  2. The person answered **"keep that one"**.
  3. The answer was joined to the request and sent to preparation (*"… / Human clarification:
     keep that one"*), as for any clarification.
  4. The preparation schema (`integration_task`) requires an objective, criteria and at least one
     need, and has no way to say "nothing new is needed". A model can only set up the event job
     again, a duplicate of the one the person asked to keep.
- **Suggested fix:**
  - After a clarification answer, interpret it again (intent stage, with the question and
    answer), so it can become `conversation.answer` ("OK, I'll keep the existing job") or a
    cancel.
  - Or give preparation an explicit "no new work needed" outcome with a reply.
- **Proof after the fix:** replay requests 0042–0044 with the answer "keep that one"; no new job
  is created and the chat confirms the existing job stays.
- **Fixed in Phase 3:** a chat answer with `resumeCommandId` closes that pending request
  (`cancelled`, "Not needed: the person declined it") and nothing is created. Proved live with
  "keep that one": no job was created.

## F24: Finishing without assessing the completion criteria is accepted

- **Severity:** Medium (a model can claim completion without checking its evidence).
- **Found in:** Scenario 7, simulator requests 0047 and 0048. Both finishes had `"criteria": []`
  and `resultStatus: "completed"`, and both runs ended **completed**.
- **What the rules say:** *"When finishing, assess every completion criterion in criteria … Use
  resultStatus completed only when every criterion is met."* The validator does not enforce it.
- **Suggested fix:**
  - When the job has completion criteria, require one assessment per criterion on a finish.
  - Treat a missing one as `not_verifiable`, so the result is **attention**, not completed.
  - Use the existing correction path to ask for the missing assessments once.
- **Proof after the fix:** replay requests 0047–0048; the finish is corrected or ends as
  attention.

**Scenario 7 also proved** (no defect), live:
- GitHub delivered `issues.opened` for #10 to the laptop through the tunnel, and the signature was
  verified.
- The binding without the repository was recorded `unmatched` with no queue message (the QStash
  cut).
- Both listening jobs started within 20 s, read the issue and posted summaries.
- The original job posts to its own results conversation (`f9cb501b…`), by design.

---
- **Fixed in Phase 2:** a finish must assess every completion criterion (matched by text, then by
  count). A missing one is asked for once; on the last call it counts as not verifiable, so the
  result is attention. Proved on the recorded scenario 7 answer (test); a live replay of scenario 7
  needs a person to open a GitHub issue.

## F25: A model-call slot left by a stopped process blocks planning

- **Severity:** Medium (after a deploy or crash during a model call, planning for that account and
  organisation is turned away as "rate limited" until the slot expires).
- **Found in:** Phase 5, scenario 3 replay: the backend was restarted mid-call; every later planning
  attempt failed with "The model account is at its concurrency limit" before reaching the model.
- **What happened:** account and organisation slots were held in Redis for the call's whole limit
  (planner timeout + 10 s: about 33 minutes in production, 40 in the simulation). A process that
  stops mid-call never releases its slot, so the slot stays until that time passes.
- **Fixed in Phase 5:** a slot lives 90 s in Redis and is renewed every 30 s while its call runs
  (`workloads.SLOT_TTL_SECONDS`, `RedisCoordinator.renew_slot`), up to the same overall limit; a slot
  left by a stopped process frees itself within 90 s. The text-route split also releases the
  organisation slot when the budget reservation fails (the split had briefly lost that release;
  `test_first_request_budget_blocks_before_transport` caught it).

## F26: Exhausted planning retries say "waiting for capacity" with nothing to wake the task

- **Severity:** Low (the chat already carries Retry planning; the words implied it would continue
  on its own).
- **Found in:** Phase 5: a network outage used up the automatic retries of two tasks.
- **Fixed in Phase 5:** the message adds "Press Retry planning to try again." when no retry is
  scheduled.

## F27: Waiting for another task's planning slot uses up a task's retries

- **Severity:** Medium (two tasks in one organisation: the second pauses and needs Retry planning
  whenever the first one's planning call takes longer than about two minutes).
- **Found in:** Phase 5, scenarios 1 and 3 replayed together: scenario 1 was turned away with
  "Organization planning capacity is full" while scenario 3's planner call ran, spent its three
  automatic retries (15, 30, 60 s) and paused.
- **Fixed in Phase 5:** AgentGate's own slot rejections are marked (`budget_scope="concurrency"`)
  and the runtime waits for its turn ("Waiting for its turn to plan: another task is planning
  now.") without spending a retry; F25 guarantees a slot cannot stay taken by a stopped process.

## F28: A rejected approval leaves its task waiting for good

- **Severity:** Medium (the task never reaches a final state, and the chat never says the action
  was rejected).
- **Found in:** the read-only check of Oracle's database before deploying PR #129: three tasks
  (`ac73fcb3`, `53fe95b9`, and `fed7ea24`, the PR #8 merge) still showed "waiting for approval",
  although all three approvals had been rejected on 15 October.
- **What happened:**
  - Deciding an approval (`governance_routes._decide_approval`) woke the task only for
    **approve**.
  - The recovery sweep (`run_sweep`) resumed a waiting task only when its approval was
    **approved**.
  - The runtime already closes a rejected step ("Human approval rejected the action."), but
    nothing ever ran it.
- **Fix:**
  - Either decision wakes the task.
  - The sweep also resumes tasks whose approval was rejected, so the runtime closes the step.
  - The sweep's approval check now reads the runs, steps and approvals in three queries for all
    waiting tasks, instead of three queries per task.
  - Once deployed, the sweep closes the three tasks above with "Human approval rejected the
    action."; nothing is performed.
- **Proved in the simulator (15 October):** the watchdog's sweep closed the three tasks whose
  approvals had been rejected (`ac73fcb3`, `fed7ea24`, `53fe95b9`) with "Human approval rejected
  the action."

## F29: A browser task asked for in chat asks for a GitHub account

- **Severity:** High (a one-off browser task from the chat cannot run at all).
- **Found in:** the Oracle live check, worker "Bestworth Admin", 15 October 18:25 UTC: *"Using
  the browser integration Navigate to the bestworthproductsltd.ng. Read out all products in the
  product section"*.
- **What happened:**
  1. Interpretation was right: `integration.execute` with `integrationProvider: "browser"`.
  2. Task preparation only lets the model choose native integrations: browser and web research
     are left out of its catalog and schema (`IntegrationFoundation.interpret`:
     `if m.provider not in {"browser", "web_research"}`, there since 10 October). The model had to
     write a GitHub need (`github.repository.metadata.read`) and said so in the objective: "The
     browser integration is not a declared capability in the catalog".
  3. That worker has no GitHub account shared with it, so the chat asked to "Connect or share the
     GitHub account", twice (once more after the reply).
- **Why the same work ran from Runs/Jobs:** the worker "Aud", created with `worker.create`, has a
  saved job whose revision carries the browser capability; scheduled and manual runs use the
  legacy browser path, which works ("Daily Product Catalog Readout" completed).
- **Not a regression** of the context and quality plan; this path never supported the browser.
- **Fix direction (to plan):** let one-off chat tasks use the browser (preparation offers the
  browser resources and their origins), or route such a request to a saved job for the worker
  instead of a native integration task; and never ask for an account of a provider the person
  did not name.
- **Fixed (Phase C, decision D3):**
  - The interpreter is told that a one-off website task is `integration.execute` with
    `integrationProvider: "browser"`.
  - Such a task becomes a one-time job of the worker that starts at once. It goes through the
    existing job path (sites, browser actions and approvals as for any job), reports its progress
    and result in the conversation, and is archived when done. A one-time job runs only once.
  - Preparation never asks to connect an account of a provider the request did not name. It asks
    which connection or website to use instead.
- **Proved in the simulator** (scenario s8, below): the request "Use the browser: go to
  …/test-site/files …" was accepted as "I started a one-time task for Repo Issue Monitor on
  127.0.0.1", with no GitHub question.

## F30: The Activity page crashes on an action recorded by the runtime

- **Severity:** High (the page is unusable on wide screens).
- **Found in:** the Oracle live check: `Uncaught TypeError: Cannot read properties of undefined
  (reading 'length')`.
- **What happened:**
  - On wide screens the page opens the newest action (`ActivityPage.tsx`, "open the newest action
    so the detail pane is never empty").
  - The detail reads `item.policy.matchedPolicies.length` (line 137).
  - Actions the runtime records keep only `{outcome, reason}` as their policy
    (`ManagedRuntimeExecutor._action_record_payload`), and the API passes it through unchanged
    (`governance_routes._action_record`), so `matchedPolicies` is missing.
  - Today's newest actions (the merge and the coding task) are runtime actions, so the page
    crashes as it loads.
- **Long-standing:** both shapes were the same before PR #129.
- **Unconfirmed:** a 404 in the same console log; which request it was is not yet known.
- **Fix direction:** the API returns a complete policy for every action (`winningPolicy: null`,
  `matchedPolicies: []` when not recorded), and the page treats a missing list as empty.
- **Fixed:**
  - Every action and approval the API returns carries the full policy
    (`governance_routes._recorded_policy`: `winningPolicy` and `matchedPolicies` default to none).
  - The Activity and Inbox pages treat a missing list as empty.
  - On the simulation database, all 133 actions (every one recorded by the runtime) now come back
    complete.
  - The 404 was not one of the page's requests: actions, agents and the capability catalog all
    have their routes.

## F31: A merge GitHub carried out is reported as failed

- **Severity:** High (the person is told the task failed although the pull request was merged;
  the result says "needs attention").
- **Found in:** the Oracle live check, task `ed774388` (merge PR #8), 15 October.
- **What happened:**
  - The merge was approved and **GitHub merged it** at 17:50:33 UTC (`merged_by: audoryn[bot]`).
  - One second later the step failed with "GitHub returned incomplete action evidence.": the
    message the GitHub provider gives any `KeyError` raised during an action.
  - The action was recorded as `failed`, with `result: null`.
- **Why the cause is unknown:** the provider turns the `KeyError` into that message and keeps the
  original only as the error's cause; nothing logs the key or the line.
- **What was ruled out:**
  - Replayed locally with GitHub's real responses for this pull request and this task's grant
    (`branches: [codex/55ea0379/readme-setup]`, `pullRequests: [8]`), the merge and its
    verification succeed.
  - That held both with the test HTTP double and through the real HTTP client and transport.
  - So the missing key is in something the replay does not cover: most likely a response GitHub
    returned at that moment (for example the pull request read straight after the merge), or the
    installation-token path.
- **Oracle's log** (`podman logs`, 15 October): the approved merge ran in the `resume` step, which
  ended `failed` after 8.1 s with no warning or error line at all; the cause cannot be recovered
  from this occurrence. The merge itself had been planned by the fallback model (see F33).
- **Two problems:**
  1. A write that may have happened is marked failed instead of "outcome uncertain" with the
     existing checks (It happened / It didn't happen / Check again) and reconciliation.
  2. The real cause is not logged.
- **Fix direction:** an error raised after a write was sent is treated as an uncertain outcome
  and reconciled (the merge would then be found merged); provider errors log the cause's type
  and AgentGate frames (`app_frames`), never values; then find the key from the next occurrence.
- **Fixed:**
  - The GitHub provider notes when it has sent a request that changes GitHub
    (`provider._changes_github`).
  - A missing key or an unreadable value after that point is "verification failed", which a
    durable write turns into "outcome uncertain" with the existing re-check. Before it, it is a
    plain failure.
  - Either way the log names the error type and the AgentGate lines, never values.
- **Proved in the simulator:** a new fault (`missing_field`) let GitHub create an issue and then
  dropped `number` from the response. The log read `GitHub action could not be read
  operation=repository.issue.create error_type=KeyError after_write=True
  at=execution/providers/native/github/execution.py:297 canonical_path < …`. The step paused as
  uncertain, the re-check found the real issue #14, and the task completed.

## F32: Empty reads all say "Found nothing: the result is empty"

- **Severity:** Low (wording; the evidence itself is complete).
- **Found in:** the same merge task: reviews, check runs and the commit status were each shown
  as "Found nothing: the result is empty."
- **What happened:** the step narrator (`app/application/context/narrator.py`, Phase 1) gives
  every empty list the same sentence. For the commit status it also hides that the state was
  `pending` with no checks.
- **Fix direction:** name what was empty ("No reviews", "No check runs") and keep a state the
  record carries ("Status pending; no checks reported").
- **Fixed:** the narrator names what was empty from the record's own field or the scope ("No
  reviews.", "No check runs.") and keeps a state ("State: pending; no statuses."). Tested on the
  three reads of the merge task.

## F33: A cut-off planner answer moves straight to the fallback model

- **Severity:** Medium (that step took 64 s instead of about 5 s; repeated, it slows every task and
  shifts work to a weaker model).
- **Found in:** the Oracle live check, task `ed774388`, step 4 (planning the merge).
- **What happened:**
  - Sonnet (`openrouter_claude`) answered, but the answer was rejected as `incomplete_output`
    after one call (planner attempt `225a7bf8`).
  - The planner moved to the next route: Nemotron (`nvidia_nim`), which took two calls and 60 s
    and planned the merge.
  - Every other planning step of this task was accepted from Sonnet in 2–5 s.
- **Why `incomplete_output`:** the stream ended with a finish reason other than `stop` or
  `length` (`app/infrastructure/ai/streaming.py`), for example no finish reason at all if the
  stream was cut. That reason is treated as final for the route, so the same model is not asked
  again. The finish reason itself is not recorded, so which case it was is unknown.
- **Fix direction:** record the finish reason (a fixed code, never the text) on the attempt; treat
  an answer cut off without a finish reason as a retryable transport fault on the same model,
  once, before falling back.
- **Fixed:**
  - Every provider reports how a cut-short answer ended, as a fixed code (`finish_code`: `none`
    when there was no finish reason, `other` for anything unknown).
  - The code is kept on the planner attempt (`failureDetails.finishReason`).
  - An answer with no finish reason is asked of the same model once more, within the attempt's
    two calls, before any fallback.
- **Proved in the simulator:**
  - A new simulator answer (`"finish_reason": "none"`, `sim_replay --cut-planning 1`) sent a
    dropped stream.
  - AgentGate asked the same model again at once, and the planner attempt was accepted after 2
    calls with no fallback.

## Browser setup review (15 October 2026)

The browser connection's settings were traced from the setup form (Connections → Directory →
Governed Browser) to where the browser uses them.

**Already real end to end (keep as is):**
- start page and name;
- allowed and blocked sites, allowed and blocked paths: checked on every page opened and every
  direct page read (`app/execution/browser/policy.py`);
- the private network switch: when off, localhost, internal and cloud metadata addresses are
  refused (`egress.py`);
- the file upload and download switch: those actions exist only when on;
- the login: stored encrypted, filled into the form at the browser by `browser.auth.login` and
  hidden from page reads.

Tests cover the site, path and network rules (`test_f30_11_20.py`, `test_f30_31_40.py`,
`test_f31_21_30.py`). The gaps are around them.

## F34: A browser connection's "Live checks" never open the site or try the login

- **Severity:** High (a broken connection shows as "Connected").
- **What happens:**
  - For a browser, the check (`PlaywrightBrowserProvider.check_health`) only parses the site rules and
    asks whether the browser engine is running.
  - It never opens the start page, never confirms the page is inside the allowed sites, and
    never tries the stored login.
  - So a wrong address, an unreachable site, a start page the rules block, or a wrong password
    all pass. The screen says each line completes "when Audoryn gets a real answer", which is
    not true for the browser.
- **Fix direction:** the check opens the start page through the same site rules, reports the page
  it reached, and when a login is stored tries it (with the person watching the result), never
  showing the values.
- **Fixed:**
  - The check opens the start page in a short browser session through the connection's own site
    rules, and reports the page it reached, whether it is inside the allowed sites, and how long
    it took.
  - A new **Test sign-in** button tries the stored login on that page. It reports one of these:
    signed in, login form still showing, no login stored, no login form, or fields that do not
    fit. Values never leave the browser.
  - The check result is now saved; before, it was never written back to the connection.
- **Proved in the simulator** (a new test site on the simulator, real Chromium):
  - The right login gave "Signed in: reached Dashboard - Test site." (Connected).
  - A wrong password gave "After signing in, the login form is still showing…" (Degraded).

## F35: The planner is not told which login fields a connection stores

- **Severity:** High (a sign-in can only work by guessing).
- **What happens:**
  - `browser.auth.login` asks the planner to say which stored field goes into which box
    (`bindings[].credentialKey`).
  - The planner is never told the field names. It can guess `username` and `password` (what the
    two boxes save), but anything entered under "Other login fields" can never be used.
- **Fix direction:** give the planner the stored field names (never their values) with the
  connection's tools, and check a chosen field exists before the action runs.
- **Fixed:**
  - Connecting, and every check, records the stored login's field names (never the values) as
    the connection's `loginFields`.
  - The planner's sign-in tool lists them. A proposal that binds another field is corrected at
    planning time with the real names.
  - The browser checks every bound field exists before typing anything; a missing one names the
    fields the login has.
- **Proved in the simulator:** both test-site connections were checked and recorded
  `loginFields = username,password`.

## F38: After a form is submitted, the next page is read while it is still loading

- **Severity:** Medium (the model can be shown a half-loaded page after any form submit, a sign-in
  included).
- **Found in:** the simulator, while proving F35. The same successful Test sign-in reported
  "reached Loading http://127.0.0.1:8788/test-site/home" instead of "Dashboard - Test site".
- **Why:** `submit_form` waited a fixed 50 ms after submitting, then read the page. A submit that
  opens the next page (a POST, then a redirect) is often not finished by then.
- **Fixed:**
  - Before submitting, the browser starts watching for a page request from the main frame. A
    request starts within milliseconds of a submit, however slowly the site then answers.
  - If one starts within 1.5 s, the browser waits for that page to load, or for the request to
    fail, within the action's own timeout. Otherwise the form is treated as same-page, which costs
    at most 1.5 s.
  - A slow page is still read as it stands, and a blocked page is still refused.
- **First version, replaced:** it waited at most 1.5 s for the next page to *commit*. Under load
  (the full test suite running alongside), the two-step sign-in read "Loading …/test-site/code"
  again.
- **Proved in the simulator:**
  - The Test sign-in reports "Signed in: reached Dashboard - Test site." again. Three repeated
    runs with the full test suite running alongside were all correct.
  - A new slow test login (`slow@example.com`) answers after 3 s. With the new wait it reached
    "Dashboard - Test site". With the first version's wait it read "Loading …/test-site/login" and
    still reported *signed in*, which was a false success.

## F36: The setup suggests a one-time-code secret, but nothing turns it into a code

- **Severity:** Medium (a login with two-factor codes fails, after the setup implied it would
  work).
- **What happens:**
  - The "Other login fields" example shows `otpSecret`.
  - Nothing generates a time-based code from it; the sign-in would type the secret itself, and
    the login would fail.
- **Fix direction:** generate the current code from a stored secret at the browser when a binding
  asks for it (the code, like the secret, never reaches the model), or remove the example until
  that exists.
- **Fixed:**
  - A stored `otpSecret` is offered to sign-ins as the field `oneTimeCode`. It can be a base32
    key or an `otpauth://` link with its algorithm, digits and period.
  - When `oneTimeCode` is bound, the browser types the current code (RFC 6238, Python's standard
    library, no new dependency). The secret itself can never be bound, and the code is hidden
    from page text like the stored values.
  - A secret that is not valid is refused when the login is read.
  - Test sign-in fills a code box on the login form, or on a code page after it. It reports:
    a code is needed but no secret is stored, or the code was not accepted.
- **Proved:**
  - The RFC 6238 test vectors (SHA1, SHA256, SHA512) all pass.
  - The simulator's test site now has a two-step login (password, then a code page). With the
    right secret it gave "Signed in: reached Dashboard - Test site.", with a wrong secret "The
    one-time code was not accepted", and with none "The site asks for a one-time code".

## F37: A job for a site that needs a login is created without the sign-in actions

- **Severity:** High (the task stops at the login page).
- **Found in:** the Bestworth job (15 October): it reached the login page and stopped: "The
  dashboard was not reached… This needs the typing and submit capabilities enabled, or an
  already authenticated session."
- **What happens:** when a worker or job is drafted, its browser actions come from what the model
  asks for (`worker_autonomy`). It is not told the connection has a stored login, so a job for
  a site that needs signing in can be created read-only, without `browser.auth.login`, typing
  or submit.
- **Fix direction:** tell the drafting step which browser connections have a stored login; a job
  whose work starts behind that login gets the sign-in action (with approval as the policies
  say); and the job's page shows which browser actions it has.
- **Fixed:**
  - Choosing a job's browser actions now sees which browser connections hold a login, with the
    connection, its sites and the field names (never values).
  - The resolver is told that work behind such a login gets `browser.auth.login`, which fills and
    submits the login form itself. The model still chooses: no rule forces sign-in onto every
    job for that site.
  - A worker's Jobs tab lists each job's browser actions ("Browser: sign in, navigation open…").
  - Once granted, the job's planner gets the login's field names (F35) and any one-time code
    (F36).
- **Proved:**
  - A test checks both cases: the resolver is shown the stored login when there is one, and
    nothing when there is none.
  - On the simulation database, the real test-site connection reached the resolver as
    `storedLogins: [{connection: "Test site (right login)", origins: [127.0.0.1:8788],
    loginFields: [username, password]}]`, with `browser.auth.login` offered and kept. No value
    was in the prompt.

Related: F29 (one-off browser tasks from chat cannot run) and F30 (the Activity page crash, which
also hits browser actions).

## Browser rules and file actions, checked live (15 October 2026)

The setup promises three things. Each was checked with AgentGate's real browser runtime
(Chromium) against new pages on the simulator's test site (`/test-site/bounce`, `/ping`,
`/hits`, `/files`, `/report.csv`, `/upload`), plus a public site (example.com).

| Promise | Result |
|---|---|
| Allowed and blocked sites: opening a blocked site, an unlisted site or a blocked path | Refused, with the rule's reason |
| A redirect from an allowed page to a blocked or unlisted site | Refused |
| A direct page read, including through such a redirect | Refused |
| Private network off: opening 127.0.0.1 or 169.254.169.254, or a direct read of 127.0.0.1 | Refused |
| A page's own background requests (scripts, images, `fetch`) | **Not checked by AgentGate** (F39) |
| File switch off / on | No file actions / upload and download offered |
| Download and upload in the browser | Work: report.csv downloaded, uploaded back, "Received report.csv" |
| A download saved, loaded back by the same worker, refused for another | Works |
| A person's own file uploaded by a task | **Not possible** (F40) |
| The person opening a file a task downloaded | **Not possible** (F41) |

## F39: A page's own requests skip the site rules and the private-network rule

- **Severity:** High (the private-network promise is not kept by AgentGate itself).
- **What happens:**
  - The browser checks every page it opens: pages, frames, popups and redirects.
  - Every other request goes straight out (`route.continue_()` in `_route_request`). That covers
    a page's scripts, images, styles and `fetch` calls.
  - Live: a script on the allowed test page reached an unlisted site (example.org).
  - A public page's requests to 127.0.0.1 failed, but because Chromium's own local-network
    protection stopped them, not AgentGate. That depends on the Chromium version and its
    settings.
- **Fix direction:**
  - Apply the private-network rule and the blocked-sites list to every request. Keep a
    per-session cache of each host's answer, so each host is looked up once.
  - Keep a page's own resources from unlisted sites (fonts, scripts from a CDN) allowed; refusing
    them would break most sites.
  - Make the setup text say precisely what is checked.
- **Also noted (low):** each check looks up the address, then the browser connects with its own
  lookup. A host that changes its answer in between could still get through.
- **Fixed:**
  - Every request a page makes now gets a check: scripts, styles, `fetch` and WebSockets (newly
    routed). It refuses blocked sites and, when private network is off, private and cloud
    metadata addresses.
  - Each host is looked up once per session and remembered (up to 512 hosts, a few KB).
  - The RAM rule runs first, unchanged: images, media and fonts are still cancelled before any
    check.
  - Other sites a page loads from stay allowed. Refusals are logged and counted in the page
    state (`refusedPageRequests`).
- **Proved live (Chromium):**
  - A public page's `fetch` and WebSockets to 127.0.0.1 and localhost were refused by AgentGate
    (4 refusals); nothing reached the server.
  - On the allowed page, the blocked site's `fetch` and WebSocket were refused. The unlisted
    site and the allowed site went through, and the allowed request reached the server.
  - Every image was still cancelled by the RAM rule.

## F40: A person's file cannot be given to a task to upload

- **Severity:** High for upload work (the upload action exists, but there is nothing to upload).
- **What happens:**
  - An upload needs the file's `artifactId`.
  - The task's planner is given the job, worker, trigger, tools and observations, but never the
    files attached to the request. No job or trigger carries them.
  - So a task can only upload a file it downloaded itself in the same run.
- **Fix direction:**
  - The files attached to a chat request travel with the task (name, type, size and id, never
    the contents). The planner sees them as files it may upload.
  - The existing rules still apply: the file belongs to that worker or run.
  - This links to Phase C (chat tasks).
- **Fixed (Phase C):**
  - The files attached to a chat request travel with the task in its trigger (name, type, size
    and id, never the contents), and the planner sees them as files it may upload.
  - An attached file is bound to the worker that will use it. A file another worker holds is
    left out.
- **Proved in the simulator (s8):** the attached `stock-update.csv` was uploaded with the
  upload form, and the site replied "Received stock-update.csv (237 bytes)".

## F41: A file a task downloads cannot be opened by the person

- **Severity:** Medium.
- **What happens:**
  - The download is saved with the run.
  - The result saved at the end always has `artifactIds: []` and one text paragraph.
  - The Runs page shows only a count ("1 artifact").
  - The Results page has an "open attachment" button, but nothing ever fills it.
- **Fix direction:** the result lists the run's files as attachments (name, type, size), opened
  with the existing signed link.
- **Fixed:**
  - A run that downloaded files lists them on its result, as attachment blocks and
    `artifactIds`. Screenshots and other evidence are left out.
  - The lookup runs only when the run had a download step, so other runs add no database query
    (the runtime benchmark is unchanged).
- **Proved on the simulation database:**
  - A download saved with a real run was listed and the screenshot was not.
  - The Results page's signed link opened the file's real contents.
- **Also in chat (Phase C):** the result message in the conversation carries the files, and the
  chat's result card shows a button per file that opens it with a signed link. Proved in s8:
  the chat message carried `files: [report.csv]`.

## F42: The simulator writes files to production's object storage

- **Severity:** Medium (simulation must not touch production).
- **Found while proving F41.**
- **What happens:** `.env.sim` sets only the database. Object storage
  (`OBJECT_STORAGE_PROVIDER`, `UPSTASH_BLOB_BUCKET`) comes from `backend/.env`, which is
  production. So files the simulation saves (browser downloads and screenshots) go to the
  production bucket, while their database rows go to the sim branch.
- **Written by the checks on 15 October:** three test files of 29 bytes each, under
  `org/650c94c4…/browser/…` (report.csv twice, page.png once). They have no production database
  rows. They are not deleted yet; that needs approval.
- **Fix direction:**
  - The simulation uses its own storage: a separate bucket or folder, set in `.env.sim`.
  - The simulation start refuses to run when storage is production's, as it already refuses a
    missing sim database.
- **Decision (16 October 2026):** keep the shared storage for now. Clean up the simulation's
  files in the production bucket, and give the simulation its own storage, before the app is
  released.

## F43: AI-managed browser work never uses a connection the person set up

- **Severity:** High (sign-in, login fields, one-time codes and file actions from F34–F37 and F40
  cannot reach chat tasks or AI-created jobs).
- **Found in:** the Phase C simulation (scenario s4, 16 October): a chat task on the test site.
- **What happened:**
  - The chat task became a one-time job as intended.
  - But the run's tools came from a new "Managed web · 127.0.0.1" connection (private network
    off, file actions off), not the person's "Test site (right login)" connection (login stored,
    file actions on). So the planner had no download or upload action.
- **Why:** `browser_integration_autonomy_suitable` lets AI-managed work reuse a connection only
  when:
  1. It does not allow private network addresses (a deliberate safety rule).
  2. It is AgentGate's own managed connection, or its owner set `loadVisualResources` to
     `"false"` explicitly.

  The Connections setup never sets that key. When it is missing the browser already blocks
  images (the policy's default), so these connections are lean in practice but never count as
  lean. Every chat task and AI-created job therefore gets its own managed connection, with no
  login and no file actions.
- **Also:** the capability resolver offers scopes from every connection covering the site,
  including ones the runtime will not admit for AI work. The job can be granted actions (here,
  download and upload) that its run never sees.
- **This is likely why the Bestworth login job stopped at its login page (F37):** its run used a
  managed connection without the stored login.
- **Fixed (decision, 16 October):**
  - A connection the person set up is reused for AI-managed work on exactly the sites their
    message names. It counts as lean when it does not turn on images (the setup's default).
  - A person's private-network connection is reused only for a site the person named;
    AgentGate's own managed connections never reach private addresses.
  - When both cover the same sites, the person's connection is used and AgentGate's own one is
    skipped. This applies at connect time and when the planner's tools are built.
  - The capability resolver offers only connections the run will admit, so a job is never
    granted actions its run cannot see.
- **Proved in the simulator (s8):** the run's tools came from "Test site (right login)", with
  download and upload.

## F44: A browser step is described as "Found 6 results"

- **Severity:** Medium (the planner and the person read a wrong summary; a download's file is
  not named).
- **Found in:** the Phase C simulation (s8). Opening the files page was described as "Found 6
  results." and the download as "Found 3 results.".
- **Why:** the narrator counted the one list in a browser step's output (`realtimeEvents`), as
  for a read of records. The download's file never appeared in what the planner saw.
- **Fixed:**
  - Browser steps have their own wording: "Opened Files - Test site (…).", "Downloaded
    report.csv (29 bytes; file id …).", "Chose stock.csv (45 bytes) in the upload field.", and
    for other steps "Now on <page>.".
  - The download's file id lets a later step use the file.

## Scenario s8: a browser task from chat, with a file to upload and a file to download (16 October 2026)

- **Request:** "Use the browser: go to http://127.0.0.1:8788/test-site/files, download the
  report, then upload the attached stock-update.csv with the upload form and tell me what the
  page says." The CSV was attached through the real attachment service, and the test site's
  connection was reconnected with file actions on.
- **Path, all through AgentGate's real services, with the model's answers given through the
  simulator:**
  1. Interpretation chose `integration.execute` with the browser.
  2. The one-time job was created, its actions resolved from the person's connection, and it
     started.
  3. The run opened the page.
  4. It downloaded report.csv (approved).
  5. It chose the attached file (approved).
  6. It submitted the form (approved).
  7. It finished.
- **Outcome:**
  - The site replied "Received stock-update.csv (237 bytes)".
  - The run completed, and the one-time job was archived.
  - The result lists report.csv, and the chat message carries it as a file to open.
- **Found while running it:** F43 and F44.
- **Noted, to check on Oracle:**
  - While an earlier task was planning, a new chat message was refused with "local AI request
    capacity is busy … try again shortly". The message was kept. It is not known yet whether
    production's concurrency setting makes this likely.
  - Opening a test-site connection's settings again needs a disconnect and reconnect; there is
    no edit.


## Oracle live check after PR #130 (16 October 2026)

All checks worked except the three below.

## F45: Signing in to a site whose login form has no method is refused

- **Severity:** High (no sign-in on such sites: Test sign-in, jobs and chat tasks).
- **Found in:** Test sign-in on bestworthproductsltd.ng: "Browser authentication refuses GET form
  submission because credentials could enter the URL."
- **Why:** `BrowserRuntime.authenticate` reads the form's `method` attribute; with none, a
  browser's default is GET, so it refuses. Sites whose own JavaScript sends the login form leave
  `method` out, and their password never goes into a URL. The simulator's test site uses
  `method="post"`, so this was not caught.
- **Fix direction:** check what really happens instead of the attribute. Let the submit run, and
  refuse any request whose URL contains one of the stored login values (the route handler sees
  every request). That fully stops a real GET login leaking the password, and allows JavaScript
  logins.
- **Fixed:**
  - The form's `method` is no longer checked. The secrets typed into a login (the password, a
    one-time code, any field named like a pass, otp, secret, token or pin) are remembered for the
    session.
  - Every request whose web address carries one of them, plain or encoded, is refused before it
    is sent. A sign-in refused this way says so plainly. Usernames and emails are not secrets, so
    a profile address is never refused.
- **Proved live (Chromium, simulator test site):**
  - a normal POST login and a script login with no method both reached "Dashboard - Test site";
  - a real GET login form was refused ("would have sent the password in the web address"), and
    0 requests reached the site.

## F46: Connecting a browser shows a false "Discovering resources" failure

- **Severity:** Low (alarming, harmless).
- **What happens:** the setup's live checks call the native-integration discovery
  (`/integration-connections/{id}/discover`) for every connection. Browser connections are not
  part of that system, so it answers "This account has not been shared with you".
- **Fix direction:** skip discovery for browser connections; their site and actions are set when
  connecting.
- **Fixed:**
  - The setup marks discovery as skipped for a browser connection.
  - The connection's page shows its resources without the Rediscover button.

## F47: A merge GitHub carried out is never confirmed, and the task waits until cancelled

- **Severity:** High (the merge is done, but the task never finishes, and the person sees no
  outcome).
- **Found in:** task `a0e9a7b8` (merge PR #13 in DIVINEJMG/school, 01:07 UTC).
- **What happened:**
  1. The merge approval was given and GitHub merged the pull request.
  2. AgentGate's read-back failed: `GitHub action could not be read
     operation=repository.pull_request.merge error_type=KeyError after_write=True at
     verification.py:192 _verify_merge`.
  3. The F31 fix made it "uncertain" (`queueState: checking_outcome`, "I'll look for it again
     shortly").
  4. Every new look ran the same failing read-back, so it never settled. Four minutes later the
     person cancelled the task (`POST …/work-items/a0e9a7b8…/cancel`).
- **Why the KeyError:** `_verify_merge` compares the pull request's head with `values["sha"]`,
  the expected head commit. The merge request's `sha` is optional in GitHub's API, and the
  planner did not send it, so reading `values["sha"]` raised KeyError.
- **Fix direction:**
  - Verify the merge by what GitHub reports: the pull request is `merged`, and its head matches
    `sha` only when one was sent.
  - Make the "look again" give the person the outcome buttons, instead of repeating a read-back
    that cannot succeed.
  - Add a test with a merge that has no `sha`.
- **Correction after reading the code:** `sha` is required for a merge, so the missing key was
  `merge_commit_sha` in GitHub's answer (line 192), not `sha`.
- **Fixed:**
  - A merge is confirmed by GitHub reporting it merged at the approved head. `merge_commit_sha`,
    `html_url` and `id` are reported only when sent.
  - Any response AgentGate cannot read, of whatever shape, is now "uncertain after a write" instead
    of crashing.
  - When looking for an uncertain outcome again starts, the chat is told once ("…I'll look for it
    again shortly. It won't be sent twice.").
  - Tests cover a merge without those fields (confirmed) and a merge at another head (not
    confirmed).
- **Also seen:** two `409 Conflict` answers on approval decisions were double clicks on an
  approval already decided (harmless).

## Where the simulator had an advantage

Each row is a point where the simulator used something a live model does not have. Every one
maps to a fix: none is closed just because the simulator got past it.

| # | Scenario / request | What the simulator used | What a live model would do | Fix |
|---|---|---|---|---|
| A1 | 1 / 0005 | Read `AdaptiveRuntimePlanner._with_plan` to learn that revised steps are numbered from the highest old number + 1, then answered `planStep: 3` with only the remaining steps | Sees "Pending steps: [3, 4, 5]" next to a saved plan listing steps 1–2, and may guess wrong again. The second call then falls back to the current step without its revision | F2: **removed in Phase 2** |
| A2 | 2 / 0009 | Set `planStepDone: true` on the first action because the scenario 1 rejection taught it to | Has no memory of earlier tasks; leaves it out, so the completed step is dropped on the next revision | F2 (required part): **removed in Phase 2** |
| A3 | 1 / 0002 | Authorised `issue.read` alongside `issues.list` in preparation, which turned out to be the only way to get issue details | Commonly picks only `issues.list` for "what are the open issues". The empty list evidence then leaves it unable to summarise | F1: **removed in Phase 1** |
| A4 | 2 / 0008 | Copied the human's words programmatically into `request_excerpt` | May paraphrase or change quote style. **Checked:** `grounded_in` compares words and ignores case, quotes and punctuation, so quote styles are safe; a paraphrase drops the excerpt and the write then asks for confirmation instead of failing | None needed (verified) |
| A5 | 2 / 0010 | Marked the "body says …" criterion *met* because it remembered the body it sent | Must mark it *not_verifiable*; the run ends as attention | F6 |
| A6 | 1 / 0001–0006 | Searched the 67 KB prompts with scripts to find the human message, tools and plan | Has to read every word on every call; more chance of missing a rule, and the full cost each time | F4 |
| A7 | 3 / 0013 | Queried the database for the work-item ID to name the task branch `codex/55ea0379/…` | Cannot know the ID; guesses or breaks the branch rule | F7: **removed in Phase 2** |
| A8 | 5 / 0035 | (Not used; noted for the fix.) Granting a named branch needs `constraints.branches` in preparation; only the code (`safety.validate_task_branch`) says so | Never sets it, so every follow-up on an earlier task's branch fails | F19: **removed in Phase 3** |
| A9 | 4 / 0093 (Phase 2 replay) | After interpretation failed twice, sent the person's message again itself (the replay's "human step") | The person must notice the technical reply and resend the message; many will not | F12: **removed in Phase 3** |
| A10 | 4 / 0093 (Phase 2 replay) | When the resent message's reply failed with a dropped database connection (my answer came minutes late), restarted the answering and sent it a third time | The message is lost with a 500 and the person has to start again | F17: **removed in Phase 3** |

**Simulator limitations (not system findings):**
- A streamed reply cannot carry an HTTP 429 status once streaming has started, so a simulated
  429 on a streamed planner call arrives as a stream error (`provider_unavailable`). Real
  providers send 429 before streaming. **Fixed in Phase 0:** the simulator now holds a streamed
  reply until it is answered, so an error status is a real HTTP status.
- A replay answers from the recorded transcript, so its answers cannot react to new data. In
  the Phase 1 replay of scenario 1, the repository had 4 open issues, the evidence showed all 4,
  and the recorded final answer still said 1. Replays prove what AgentGate shows and does, not
  the model's reasoning.
- A replay answered every waiting request of a schema, so two scheduled jobs that woke during the
  Phase 3 replay of scenario 5 got its recorded planner answers. AgentGate rejected both (wrong
  resource, no tools), so nothing ran. Fixed in `sim_replay.py`: a planning request whose
  `workItemId` is another task's is left for a person.
- A pending request's files must never be moved by hand: the simulator numbered requests by its
  files, so a moved file let two requests share a number (Phase 4). Fixed: numbers are never reused.
- Scenario 5's recording holds two identical clarification answers (the first message was lost to
  F17 and resent), so a replay maps the person's clarification to the second copy. Phase 3's proof
  answered it by hand from the prompt.

**Not an advantage:** the simulator's longer time budgets
(`AI_INTERACTIVE_ATTEMPT_SECONDS` and the like) exist only because a person answers. A live model
answers within the normal limits.
