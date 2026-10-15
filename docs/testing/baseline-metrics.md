# Baseline metrics (Phase 0)

Measured 15 October 2026 from the live simulation (seven scenarios), before any fix-plan
change. Every later phase reruns the same measurement and compares:

```text
python scripts/sim_metrics.py
```

Inputs:
- the fixtures in `backend/tests/fixtures/sim/`;
- the usage records in the simulation database (`.env.sim`).

Prompt sizes include the system prompt.

## Per scenario

| Scenario | Model requests | Corrections | Largest prompt (chars) | AI calls (task) | QStash (task) | DB statements (task) | Statements per step |
|---|---|---|---|---|---|---|---|
| s1 open issues | 6 | 1 | 74,863 | 4 | 4 | 454 | 91 |
| s2 create issue | 4 | 0 | 76,702 | 2 | 2 | 287 | 96 |
| s3 coding, draft PR | 14 | 0 | 77,392 | 12 | 28 | 2,967 | 93 |
| s4 bad model | 8 | 3 | 81,705 | 3 | 1 | 179 | 60 |
| s5 clarification | 5 | 0 | 81,148 | 2 | 3 | 357 | 89 |
| s6 uncertain write | 4 | 0 | 83,557 | 2 | 5 | 655 | 82 |
| s7 event trigger | 7 | 1 | 85,389 | — | — | — | — |

Notes:
- "AI calls (task)" counts the runtime's planning calls. Interpretation and preparation calls are
  recorded on the chat message and preparation records, so the totals are under "Model
  requests".
- s4's corrections were deliberate bad answers.
- s1 and s7 each had one correction caused by the system (F2 and F22).
- s7 ran as event-triggered jobs without one work item, so its task figures are not attributed.

## Per prompt kind

| Prompt | Largest (chars) | Mean (chars) |
|---|---|---|
| Interpretation (`worker_command_intent_v1`) | 85,389 | 80,020 |
| Preparation (`integration_task`) | 75,323 | 62,765 |
| Planning (`audoryn_next_action`) | 39,828 | 21,247 |

**Growth:** the interpretation prompt grew from 67,590 characters (s1) to 85,389 (s7) in one
conversation. The thread and recent results are included without compaction (F4).

## Where the size comes from (s1)

**Interpretation, 67,590 chars:**

| Part | Chars |
|---|---|
| `policiesAndApprovalBoundaries` | 21,202 |
| `relevantMemories` | 10,378 |
| `activeJobs` | 9,706 |
| `recentRuns` | 7,519 |
| `recentResults` | 6,483 |
| `thread` | 5,373 |
| Schema | 2,914 |
| System prompt | 7,273 |

**Preparation, 67,825 chars:**

| Part | Chars |
|---|---|
| `resources` (7 repositories × 84 capability names) | 24,722 |
| `conversationContext` | 20,547 |
| `capabilities` (descriptions) | 11,888 |
| Schema | 9,312 |

**Planning, about 16,000 chars:** `PLANNING RULES` 8,295, mostly browser rules for a task with no
browser tool. In s3 (coding), `AUTHORIZED TOOLS` was 8,665.

## Behaviour pinned by golden tests

`tests/test_phase0_golden.py` pins today's behaviour on the real outputs:

- **Step wording (F5):**
  - "Read #7 Simulation test." for an issue **create**.
  - "Found 1 result: README.md." for a workspace **edit**.
  - No sentence at all for metadata read, workspace open, branch create, commit create, and
    marking a PR ready.
- **Planner evidence (F1, F3):**
  - The `issues.list` result reaches the planner with no data (`providerOutput.data` is empty,
    because list outputs are discarded in `managed._planner_observations`).
  - A single issue carries 32 GitHub fields, most of them API URLs and empty summaries.
- **Planner prompt (fixed task):** 14,483 chars, of which `PLANNING RULES` is 8,293.

## Targets (from the plan)

| Metric | Baseline | Target |
|---|---|---|
| Interpretation prompt | 67,590–85,389 | ≤ 15,000, and no growth with thread length |
| Preparation prompt | up to 75,323 | ≤ 15,000 |
| Planning prompt | mean 21,247 | ≤ 10,000 typical |
| Corrections caused by the system | 2 | 0 |
| QStash per coding task (s3) | 28 | ≤ 8 |
| DB statements per runtime step | 60–96 | ≤ 60 |
| Steps needing simulator knowledge (A1–A8) | 8 | 0 |

## Code craft baseline

Measured with `python scripts/code_health.py` over `backend/app` (304 files):

| Measure | Count | Examples |
|---|---|---|
| Calls written out by hand 3+ times (same 4+ keyword arguments) | 72 | outbox `enqueue(...)` ×54, `_execution_error(...)` ×36, `append_audit(...)` ×17, `ExecutionProviderError(...)` ×11, `HumanPrincipal(...)` ×11. Some are declarations (action and capability catalogues). |
| Duplicated blocks (5+ statements, 8+ lines) | 10 | `conversation_commands.py:1544` and `:1571`; `governance_routes.py:863` and `:2149`; `adaptive.py:209`, `:264` and `:351`; the v1 and v2 routers |
| Database calls inside `for` loops | 58 | `github_routes.py:146`, `integration_foundation_routes.py:98`, `internal/runtime.py:1032` … |
| Same value serialised twice in one function | 2 | `outbox_publisher.py:70`, `adaptive.py:816` |
| Possibly dead functions | 1 | `console_reads/database.py:133 text_or_none` |
| Functions over 150 lines / over 80 lines | 37 / 123 | `conversation_commands._dispatch` (765), `smart_planner.choose` (385), `adaptive._validate` (358) |

## Phase 1 results (context engine core: evidence compacter and step narrator)

Measured 15 October 2026 by replaying scenarios 1–3 live (`scripts/sim_replay.py`) on the
simulation stack. Replays answer from the recorded transcripts, so the model's choices are the
same as the original runs; only what AgentGate shows the model changed.

**Planner prompt per call (characters), original run → replay:**

| Scenario | Calls | Change | Why |
|---|---|---|---|
| s1 open issues | 16,058 → 16,163; 17,671 → 19,269; 17,814 → 19,412; 21,059 → 20,604 | +1.6k after the list, −0.5k after the issue read | The list now carries its items (4 open issues at replay time, where the original run showed none). Issue evidence lost its API noise. |
| s2 create issue | 15,242 → 15,497; 16,946 → 17,392 | +0.4k | The write evidence now shows the title and body GitHub confirmed (`confirmedWritten`). |
| s3 coding | 26,354 → 25,931; 28,626 → 28,115; 34,251 → 30,083; 34,641 → 30,417; … 39,025 → 35,160 | −0.4k to −4.2k per call | Branch, commit, pull-request and diff evidence without noise; changed files listed by size beside the diff. |

**Evidence per step (golden test, real outputs):**

| Output | Before | After |
|---|---|---|
| `issues.list` (1 issue) | `data: {}`, marked complete | 1 short record per issue (number, title, state, labels, comments, assignees, author, body excerpt, link) |
| `issue.read` | about 3,500 chars, 32 fields | 935 chars, 14 fields |
| `branch.read` | 4,562 chars | 1,492 chars |
| `pull_request.create` / `.read` | about 16,000 chars | about 1,900 chars |

**Step wording (F5):** "Created issue #11 Simulation test.", "Edited README.md (1 file changed, 18
insertions(+), 1 deletion(-)).", "Compared the workspace with 2 changed files: README.md;
scripts/setup.sh.", "Opened draft pull request #8 …", "Created branch … at 28435fd.".

**Measurement:** every planning call now logs its characters per prompt section in the `usage`
event (`promptChars`), for example `PLANNING RULES` 8,293, `AUTHORIZED TOOLS` 8,663 and `RECORDED
OBSERVATIONS` 8,309 for a coding step. Those two fixed sections, about 17k characters, are Phase
2's target. Saving the figures on the usage record needs a column (a migration), not yet added.

**Code craft (`scripts/code_health.py` over `app/`):**

| Measure | Phase 0 | Phase 1 |
|---|---|---|
| Functions over 150 lines / over 80 | 37 / 123 | 34 / 119 |
| Calls written out by hand 3+ times | 72 | 71 (`ExecutionProviderError` built from a request: one constructor, 9 copies moved to it) |
| Duplicated blocks / queries in for loops / repeated dumps / dead functions | 10 / 58 / 2 / 1 | unchanged (not in Phase 1's code) |

Split while changed: `verify_write` (181 lines, complexity 36, now one checker per write kind),
`execute_native_action` (152 lines, complexity 28, now admission, dispatch and reconciliation steps),
`execute_request` (170 lines, now 84), `_planner_observations` (138 lines, now 47, with browser
evidence moved to the context engine) and the planner prompt (one 125-line expression, now named
sections with the fixed rules built once).

## Phase 2 results (planner contract)

Measured 15 October 2026.

**Planner prompt, fixed task (golden test):** 14,483 characters (Phase 0) → 12,663 (Phase 1) →
6,546. `PLANNING RULES` 8,293 → 2,766: browser rules only with a browser tool, GitHub rules only
with GitHub tools, the branch rule only when a GitHub tool can write.

**Live, scenario 3 (coding), per planning call:** `AUTHORIZED TOOLS` 8,663 → 4,632 (each resource
once, empty and repeated fields dropped) and `PLANNING RULES` 8,293 → 3,019.

**Live, scenario 1, planner prompt per call:** 16,163 → 9,492; 19,269 → 13,231; 20,604 → 14,587
(Phase 1 → Phase 2).

**Offline benchmark (`scripts/runtime_benchmark.py`), Phase 1 → Phase 2:**

| Task | AI tokens in | Largest prompt (chars) |
|---|---|---|
| single read | 7,169 → 4,013 | 15,177 → 8,941 |
| read then write | 11,587 → 6,653 | 16,686 → 10,159 |
| long run | 39,440 → 25,371 | 22,219 → 15,983 |
| 30-step plan | 161,217 → 112,863 | 29,896 → 23,661 |

Database statements, queue messages and outcomes are unchanged, except that each result now records
its completion check. A new task, `criteria-missing`, shows a finish without assessments being
asked once and then completing (3 AI calls).

**Corrections, live (scenario 4):** preparation: "`needs.0.scopes.0`
'github.repository.pulls.list' is not allowed here. Closest:
'github.repository.pull_requests.list'."; planning: "`scope` 'github.repository.issues.create' is
not allowed here. Allowed: github.repository.pull_requests.list." (before: "… is not valid under any
of the given schemas" with the whole answer).

**Code craft:** `adaptive._validate` (358 lines) is twelve named rules plus a finish check;
`choose_next` (191 lines) is 58, its three copied fall-back blocks now one table (`READ_INSTEAD`);
six copies of the browser-observation loop are one helper; `_parse_json` (dead) removed.

| Measure (`code_health.py`, `app/`) | Phase 0 | Phase 1 | Phase 2 |
|---|---|---|---|
| Functions over 150 / over 80 lines | 37 / 123 | 34 / 119 | 32 / 117 |
| Duplicated blocks | 10 | 10 | 8 |
| Calls written out by hand 3+ times | 72 | 71 | 70 |
| Same value serialised twice | 2 | 2 | 1 |

## Phase 3 results (conversation and preparation)

Measured 15 October 2026, live on the simulation stack.

| Prompt (characters, system included) | Phase 0 | Phase 3 |
|---|---|---|
| Interpretation, scenario 1 | 74,863 (85,476 at the longest thread) | 30,032 |
| Preparation, scenario 1 | 70,153 (75,427) | 38,054 |

Context parts, interpretation (same thread): policies 19,261 → 71 characters, jobs 15,247 → 4,237,
runs 11,649 → 2,402, results 8,805 → 2,916, memories 7,753 → 0 (each only repeated a result), thread
7,217 → 3,339. Preparation resources 24,722 → 2,452. The 15k target is not reached yet: the
interpretation rules (7.6k), capability descriptions (11.9k) and the job list remain.

Live proofs: the delayed answer (6 min 56 s) kept the message and finished the task (F17); the
event-job answer was accepted on the first call (F22); "keep that one" closed the question with no
new job (F23); PR #8 was marked ready and the merge waits for approval (F19); a failed message was
retried in one step (F12).

| Measure (`code_health.py`, `app/`) | Phase 2 | Phase 3 |
|---|---|---|
| Functions over 150 / over 80 lines | 32 / 117 | 31 / 114 |
| Database calls inside for loops | 58 | 56 |

Split while changed: `conversation_commands._dispatch` 765 → 23 lines (a family router, `_handle`,
and one handler per family; worker and job commands resolve their target once);
`prepare_integration_task` 147 → 38 (start checks, drafting and binding, event blockers, job and
task creation; the permission lookup written once); `IntegrationFoundation.resolve` 103 → 17. The
web-research limits are counted in one query instead of two. The pending-command statuses are one
constant used in four modules.

## Phase 4 results (recovery and messages)

Measured 15 October 2026, live on the simulation stack.

| Situation | Before | After |
|---|---|---|
| Approval wait | "Matched 3 policyies at highest priority 101. Safety precedence …" | "Waiting for your approval to create the issue “Uncertain write test” in DIVINEJMG/school." |
| Planner refusal | chat still said "retry is scheduled"; Retry planning reused the exhausted decision | "The AI planner declined to plan the next step, so I've paused this task. … Press Retry planning" with the control; Retry planning brings a new model request |
| Unconfirmed write | "A previous delivery may have dispatched this write …"; buttons only on the Jobs page | names the issue and the repository, with It happened / It didn't happen / Check again in the chat |
| A write asked for in so many words | ran without approval | asks first; Always allow on the card |

| Measure (`code_health.py`, `app/`) | Phase 3 | Phase 4 |
|---|---|---|
| Functions over 150 / over 80 lines | 31 / 114 | 27 / 111 |
| Duplicated blocks | 8 | 7 |
| Database calls inside for loops | 56 | 53 |

Split while changed: `smart_planner.choose` 385 → 43 lines (reuse or wait, routes tried one by one,
accept, record a failure, retry or exhaust); `_execute_runtime_message` 349 → 76 (admission,
planning failed, stopped, interrupted); `_evaluate_policies` 224 → 59 (one deny builder for five
copies); `_execute_action_step` 183 → 90 (deny and approval branches, one action-record helper);
`provision_bound_authority` reads existing profiles and policies in one query each.

## Phase 5 results (efficiency)

Measured 15 October 2026, live on the simulation stack (scenario 3 coding task to draft PR #8;
scenario 1 open issues, replayed after the last change).

| Measure | Baseline | Target | Phase 5 | Met |
|---|---|---|---|---|
| QStash messages per coding task (s3) | 28 | ≤ 8 | 0 for continuations; 6 in a run that went through a network outage (delayed retries) | Yes |
| QStash messages per read task (s1) | 4 | — | 0 | — |
| DB statements per runtime step | 60–96 (plan/execute 95–120 before Phase 5) | ≤ 60 | s3 after the changes: 62 mean (plan 69–87, execute 39–78); s1: 68 mean (plan 75–88, execute 46) | Close; explained below |
| Interpretation prompt | 85,389 | ≤ 15,000 | 24,813 | No; explained in F4 |
| Preparation prompt | 75,000+ | ≤ 15,000 | 26,696 | No; explained in F4 |
| Planning prompt | 15,000–39,000 | ≤ 10,000 typical | 8,531–13,633 | Typical yes |

Why statements per step stay a little above 60: a plan step builds the tools twice, before the
model call and again afterwards, because planning can take minutes and the authority behind each
tool may change meanwhile. That second build (about 26 statements) is a safety check and stays.
Each build now reads the task's authority in one joined query instead of about seven, recording
the chosen action reuses the re-checked catalog, and the provider executor reuses the rows the
authority check read (state, grant, credential) instead of reading them again.

What the statement trace showed (`AUDORYN_TRACE_STATEMENTS=1`, on in the simulation): before the
changes an execute step ran the authority check 4–5 times at about 8 statements each; now about 3
each.

| Measure (`code_health.py`, `app/`) | Phase 4 | Phase 5 |
|---|---|---|
| Functions over 150 / over 80 lines | 27 / 111 | 26 / 109 |
| Duplicated blocks | 7 | 7 |
| Database calls inside for loops | 53 | 52 |

Split while changed: `text_routes._generate_text` (cooling checks, recovery probe, organisation
admission, cool-down), `CountedProvider.generate_text` (bound check, progress reporter),
`CredentialManager.resolve` (renewal), the GitHub transport's `request` (pacing, redirect check,
body, error status), `_build_planner_tools` (browser origins), `_plan_next_step` (smart-planner
decision).
