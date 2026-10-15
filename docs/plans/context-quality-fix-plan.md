# Plan: context engine, code quality, and the simulation fixes

Written 15 October 2026. Inputs:
- [simulation-findings.md](../testing/simulation-findings.md): 24 findings, Q1 and advantages
  A1–A8, from seven live scenarios.
- Your two additions:
  1. A smart **context planner and compacter**.
  2. **Code quality and efficiency down to the code level**, while every feature keeps working.

The idea: most findings are symptoms of two root causes. **Fix the causes once, and the findings
fall out of that work**, instead of patching 24 symptoms one by one.

## 1. Goal and rules

**Goal:** any live model runs every simulated scenario end to end, at lower cost, on code that is
smaller, clearer and faster than today.

**Rules for every change:**
1. **Behaviour is preserved unless a finding says it is wrong.** The seven scenarios are the
   acceptance test: each must still pass, now without simulator advantages (A1–A8).
2. **Prove, don't assume.** Each change ships with:
   - unit tests;
   - a replay of the scenario that found the problem;
   - the full backend suite and CI verify scripts;
   - a before/after measurement (section 5).
3. **Refactor where we fix.** A large function is split when a fix touches it, not in a separate
   big-bang rewrite. Untouched code stays as it is.
4. **No new dependencies; no migration without your approval; desktop UI unchanged.** Small
   additions to existing screens (buttons on paused tasks) are listed for your approval.
5. One phase per branch, made from the latest main. Each phase gets its own pull request, commit
   message file and Oracle handoff, plus a handoff after each merge.

## 2. The two root causes

| Root cause | What it looks like today | Findings it explains |
|---|---|---|
| **No owner for "what the model sees"** | Each prompt is built by hand in its own module (intent, preparation, planner, corrections). Evidence is cut by size, not meaning (`runtime/evidence.py` `bounded_evidence`). Step text comes from one generic `describe_output` for reads and writes alike. | F1, F2, F3, F4, F5, F6, F7, F9, F13, F19, F22, A1–A8 |
| **Large, mixed-responsibility functions** | 37 functions over 150 lines, 123 over 80. The worst sit exactly where the findings are: `conversation_commands._dispatch` (765 lines), `smart_planner.choose` (385), `adaptive._validate` (358), `_execute_runtime_message` (349), `execute_coding_action` (314), `managed._execute_authorized` (246). Rules and their exceptions drift apart; transactions span network calls; failure paths are copied with different wording. | F10, F12, F14, F16, F17, F18, F20, F21, F22, F23, F24, F11 |

## 3. Foundation A: the context engine (planner + compacter)

A single module, `app/application/context/`, owns everything a model is sent. Prompts become
**typed sections with a budget**, assembled by role, and every section is measured.

### 3.1 Parts

| Part | What it does | Replaces / fixes |
|---|---|---|
| **Section registry** | Every prompt piece is a named section (`human_message`, `worker`, `active_jobs`, `tools`, `saved_plan`, `observations`, `rules.browser`, `rules.github`, `schema` …) with a role list, a priority and a character budget | Hand-built strings in `intent_interpreter`, `integration_foundation`, `adaptive._prompt`, `conversation_semantics` |
| **Context planner** | Picks the sections a call needs from facts, never from keywords. Examples: no browser rules when no browser tool is authorised; the schema sent **once**; capability names listed once, not per repository; history bounded by relevance; the task's branch prefix and work-item ID included when a write tool needs them | F4 (67 KB to an estimated 10–15 KB per call), F7, A6, A7 |
| **Evidence compacter** | Shape-aware projections of provider output, per operation. For an issue: number, title, state, labels, comment count, body excerpt, URL. For a list: one such record per item. For a PR: head SHA, node ID, draft, mergeable, base and head. For a write: the fields written plus the verification result. IDs and revisions are never cut, and truncation is always marked. Generic size-bounding stays only as the fallback. | F1, F3, F6, F9 (diff built in git format), the 3 KB profile noise |
| **Step narrator** | Plain step text from the operation's kind: "Created #7 …", "Edited README.md", "Opened draft PR #8", "Read #2 …", "Found 3 issues …" | F5 (and the user-facing progress lines) |
| **Conversation compacter** | Older turns become a short rolling summary plus fact IDs; recent turns stay verbatim. Results from earlier tasks are referenced, not pasted. | F4 (interpretation context), F19 support (earlier PRs and branches stay findable by ID) |
| **Correction composer** | Turns a validation failure into one plain instruction naming the wrong value and the valid choices ("unknown scope X; closest: Y"; "planStep: revised steps start at 3"), without echoing the whole answer | F13, F2, A1 |
| **Measurement** | Characters (and estimated tokens) per section per call, written to the usage record and the log | Proof for every phase; stops prompt growth from going unnoticed |

### 3.2 How it is introduced safely

1. **Golden tests first.** Record today's prompts for the scenario transcripts (sections, sizes,
   content). Every change shows a reviewed diff against them.
2. **The compacter goes in behind the same evidence shape.** `providerOutput.data` stays a
   dict, so planners and validators do not change at the same time.
3. **Roles move one at a time:** planner, then preparation, then interpretation, then
   corrections. Each step is replayed against its scenarios.

## 4. Foundation B: code craft (how the code is written)

Code quality here means **how the code is written**:
- **efficient:** no wasted work or round trips;
- **no duplicates:** one helper, not copies;
- **sharp:** the simplest correct form, named for what it does;
- **fitted:** structures that suit the data.

Error handling is one part of it, not the point.

### 4.1 Principles

| Principle | What it means in this codebase | Measured today (`code_health.py`, `app/`) |
|---|---|---|
| **One way to do a thing** | A repeated construction becomes one helper: building an identity from a membership, raising a provider error from a request, writing an audit entry, enqueuing an outbox event, building a step result | **72** calls written out by hand 3+ times with the same 4+ arguments. For example: `enqueue(...)` ×54, `_execution_error(...)` ×36, `append_audit(...)` ×17, `ExecutionProviderError(...)` ×11, `HumanPrincipal(...)` ×11. Some are declarations (action catalogues) and stay. |
| **No copied logic** | A block that exists twice exists once | **10** duplicated blocks of 8–18 lines (in `conversation_commands`, `governance_routes`, `adaptive`, `browser`, and the v1/v2 routers) |
| **No wasted round trips** | Load a collection in one query instead of a query per item; read only the columns used; do not re-read what is in hand | **58** database calls inside `for` loops, possible N+1. Each costs 0.2–0.6 s from the laptop. Plus 60–96 statements per runtime step. |
| **No repeated work** | Compute once and reuse: serialisation, parsing, fingerprints, prompt sections | 2 values serialised twice in one function; prompts rebuilt in full every call |
| **Fitted data structures** | Sets for membership tests, dicts for lookups, generators for streams; typed records (dataclasses or pydantic) instead of loose dicts passed through many layers | Evidence and prompt parts travel as untyped dicts (one cause of F1) |
| **Sharp functions** | One job each; a name that says what it does; no flag arguments that switch behaviour; early returns instead of deep nesting | 37 functions over 150 lines, 123 over 80; `_dispatch` is 765 lines with one branch per command family |
| **No dead code** | Code nothing calls is deleted, not kept "just in case" | 1 found (`console_reads/database.py:133 text_or_none`); more expected as functions are split |
| **Rules with their exceptions** | Each validation rule is a small named function containing its exceptions, tested for reject and allow | F22 (a rule contradicting its own message), F24 |
| **No transaction across a network call** | Save, call, save the result in a new transaction | F17 |
| **One message catalogue** | Plain user-facing wording in one place | F10, F12, F14, F20, F21 |

### 4.2 How it is applied

- **Refactor where we fix.** When a phase changes a function, that function leaves the change
  meeting the principles: duplicates replaced by the shared helper, loops batched, the function
  split into named steps. Untouched code is left alone.
- **Shared helpers first.** A phase that touches a repeated construction introduces its helper
  once (in the layer that owns it) and moves **every** copy to it, not only the one being fixed,
  so the duplication count only goes down. The first expected helpers:
  - `principal_for(membership)`;
  - `provider_error(request, code, message, retryable)`;
  - an event helper per outbox topic;
  - `audit(...)` with defaults for the common case.
- **Efficiency is proved.** Every phase reports DB statements per step, queries in loops and
  prompt sizes before and after (section 5).

### 4.3 Enforcement (no new dependencies)

`scripts/check_changed_quality.py` runs in CI on every pull request. It checks **only the
functions a change touches**. A touched function fails when it:
- is over 150 lines (a warning over 80);
- breaks ruff's complexity (`C901`), branch (`PLR0912`) or statement (`PLR0915`) limits;
- contains a block duplicated elsewhere;
- contains a database call inside a `for` loop;
- serialises the same value twice.

It **warns** when a touched function writes out by hand a call that appears in 3+ places (a
missing helper).

Ruff's existing rule set (simplification `SIM`, refurbishing `FURB`, modernising `UP`,
performance `PERF`, bug-prone patterns `B` and more) keeps running on everything. CI now also
covers `scripts/`.

`scripts/code_health.py` reports the whole backend, for the before/after numbers in each phase.
**Pyright strict** applies to `app/application/context/` and every new module.

## 5. Measurement (before and after every phase)

Each scenario replay records:

| Metric | Today (from the simulation) | Target |
|---|---|---|
| Interpretation prompt | ~67,000 chars | ≤ 15,000 |
| Planning prompt | 15,000–39,000 chars | ≤ 10,000 typical |
| AI calls per task (scenario 1) | 6 (one wasted correction) | 4 |
| Planner corrections per task | up to 1 per revision (F2) | 0 with correct answers |
| DB statements per runtime step | 90–130 | ≤ 60 |
| QStash messages per coding task | 28 | ≤ 8 (F11) |
| Steps needing simulator knowledge (A1–A8) | 8 | 0 |
| Calls written out by hand 3+ times (`code_health.py`) | 72 | down every phase |
| Duplicated blocks | 10 | 0 |
| Database calls inside `for` loops | 58 | down every phase, 0 in touched code |

## 6. Phases

Each phase is one branch and one pull request, and stands alone: the system works after every
merge.

### Phase 0: Safety net and baselines (no behaviour change)

- **Scenario fixtures:** save the seven scenario transcripts as sanitised fixtures
  (`tests/fixtures/sim/`), and add a **replay runner** that drives the simulator `--replay` and
  checks each scenario's outcome.
- **Golden tests** for today's prompt builders and evidence output.
- **Baseline report:** the section 5 metrics per scenario.
- **Simulator fixes:**
  - answer HTTP errors before streaming starts (the 429 limitation);
  - the viewer race.
- **Ruff rules switched on** for changed files only (4.2).

### Phase 1: Context engine core and evidence

- Section registry, measurement, and the evidence compacter for GitHub (issues, lists, PRs,
  branches, commits, writes) and coding (edit, diff in git format).
- Step narrator.
- **Fixes:** F1, F3, F5, F6, F9. **Proof:** replay scenarios 1, 2, 3; compare prompt sizes.
- **Status (15 October): done.** `app/application/context/` holds the evidence compacter, the
  narrator and prompt sections (the planner prompt is built from them and measured per section).
  Section roles, priorities and budgets arrive with the context planner in Phase 2. Proof and
  numbers: `docs/testing/baseline-metrics.md`, "Phase 1 results". The scenario 3 replay stopped,
  as expected, at `branch.create`: it replays the original task's branch name, which the new task
  may not use without approval (F7, Phase 2).

### Phase 2: Planner contract

- The context planner for the planning role:
  - drop browser rules when not needed;
  - add the branch prefix and work-item ID;
  - show the revision numbering;
  - keep a step that completed without `planStepDone`.
- The correction composer for planning.
- Completion-criteria enforcement on finish.
- Split `adaptive._validate` into named rules.
- **Fixes:** F2, F7, F13 (planning part), F24; removes A1, A2, A7. **Proof:** replay scenarios
  1, 3, 4, 7.
- **Status (15 October): done.** `app/application/context/planning.py` (rules and tools from
  facts), `corrections.py` (the correction composer, also used for preparation's schema
  failures, so F13 is fixed for both), the plan rules and kept work, the criteria check, and
  `adaptive._validate` split into named rules (`ACTION_RULES`). Prompt sizes per section are saved
  on usage records (migration `0019_usage_prompt_chars`). Scenarios 1, 3 and 4 replayed live;
  scenario 7's finish is proved on its recorded answer, because a live replay needs a person to open
  a GitHub issue. Numbers: `docs/testing/baseline-metrics.md`, "Phase 2 results".

### Phase 3: Conversation and preparation

- Save the human message before the model call; no transaction across calls.
- Conversation compacter and interpretation context planner.
- After a clarification answer, re-interpret (so "keep that one" can mean no new work).
- Fix the event-rule exception.
- Branch grants for named PRs and branches, grounded in the person's words.
- Split `conversation_commands._dispatch` by family.
- **Fixes:** F4, F12 (retry), F13 (preparation part), F15, F17, F18, F19, F22, F23; removes A3,
  A8. **Proof:** replay scenarios 1, 4, 5, 7; a delayed-answer replay for F17.
- **Status (15 October): done, F4 partly.** Conversation compacter and preparation context
  (`app/application/context/conversation.py`, `preparation.py`); the message saved before any model
  call and `ReleasingGateway`; retry endpoint; event-job facts; declining closes the pending
  request; grounded branch and pull-request grants; `_dispatch` (765 lines) split into one handler
  per family. Proofs were live: scenario 1, the delayed answer (F17), scenario 7's facts and decline
  (answered as the model from the prompt alone), scenario 5 (PR #8 ready; merge awaits approval) and
  the retry (F12). Scenario 4's replay found A9 and A10. Numbers: `docs/testing/baseline-metrics.md`,
  "Phase 3 results".

### Phase 4: Recovery and messages

- User-facing message catalogue.
- Approval for every external write, with **Always allow** (Q1).
- Paused states each with a message and a control in the chat:
  - Retry planning;
  - Check again / It didn't happen / It happened;
  - retry on a failed interpretation.
- "Retry planning" starts a fresh planning decision.
- Split `smart_planner.choose`.
- **Fixes:** F10, F14, F16, F20, F21, F12 (UI). **Proof:** replay scenarios 4, 5, 6.
- Includes small additions to the chat and Work queue; listed for your approval first.
- **Status (15 October): done.** Message catalogue (`app/application/messages.py`); every visible
  write asks first, with Always allow on the chat's approval card; chat controls on worker messages
  (Retry, Retry planning, It happened / It didn't happen / Check again) and Retry planning on the
  Work queue; `restart_planning`; `smart_planner.choose` split (385 → 43 lines). Proved live:
  scenario 1 with a refusal (pause, Retry planning, completion) and scenario 6 (approval wording,
  the named uncertain write with its buttons). Numbers: `docs/testing/baseline-metrics.md`,
  "Phase 4 results".

### Phase 5: Efficiency

- Run due steps in-process on a serving backend, keeping QStash for delays and recovery.
- Cut statements per step (query review of `execute_step`, `_execute_authorized`, task activity).
- Split `_execute_runtime_message` and `execute_coding_action` where touched.
- Q1 decision applied.
- **Fixes:** F11, the database cost. **Proof:** replay scenario 3 (coding) and compare QStash
  messages, statements and time.
- **Status (15 October): done.** In-process steps (F11); one joined authority read reused by the
  provider executor; the post-planning catalog reused; the schema sent once (F4); planning slots
  that free themselves (F25), plain words when retries run out (F26), waiting for a planning turn
  without spending retries (F27), GitHub requests that wait for the installation's turn. Proved on
  scenarios 1 and 3. Numbers and the targets not fully met: `docs/testing/baseline-metrics.md`,
  "Phase 5 results".

**Already done:** F8, the coding budget leak, was fixed during the simulation. It ships with the
first phase's pull request, together with the simulator, which is still on
`feature/ai-simulator`.

## Release notes: dependencies, environment, database

- **Dependencies:** none added or upgraded (backend `pyproject.toml` only turns on strict pyright
  for `app/application/context`; frontend `package.json` unchanged).
- **Environment (`backend/.env` / `/etc/audoryn/audoryn.env`): nothing to change.**
  - New setting `RUNTIME_RUN_LOCAL`, default `true`: a due runtime step runs in the serving
    process instead of a QStash wake-up. Set it to `false` only to restore the old behaviour.
  - New optional `AUDORYN_TRACE_STATEMENTS=1`: development aid that lists which code issued each
    unit's database statements in the usage log. Leave it unset in production (the simulation
    launcher sets it).
  - AI text connections accept `"simulator": true` for the local simulator only; the backend
    refuses to start in production with such a connection.
- **Database:** migration `0019_usage_prompt_chars` (adds `usage_records.prompt_chars`, JSONB,
  default `{}`) was applied on 15 October 2026 to the original database Oracle uses
  (`Audoryn-staging-Backup`, `ep-long-glitter`) from the laptop. Nothing to run on Oracle; there
  `alembic current` should show `0019_usage_prompt_chars (head)`. Undo:
  `alembic downgrade 0018_organization_onboarding`.
- **Data (15 October 2026, at the user's request):** the `production` branch of
  `Audoryn-staging-Backup` (Oracle's database, `ep-long-glitter`) now holds an exact copy of the
  `sim` branch: every row of all 59 tables (6,342 rows; production had 3,851 and nothing written
  after `sim` was branched on 14 October, so nothing was lost). Copied in one transaction; schemas
  and row counts verified equal afterwards. **Rollback:** Neon snapshot
  `production-before-sim-copy-2026-10-15` (`snap-billowing-bar-b9s5kn24`) holds `production` as it
  was. The `sim` branch is unchanged and still used by the simulation.
- **CI:** the backend job fetches full history, runs ruff on `scripts` too, and runs the
  changed-code quality guard (`scripts/check_changed_quality.py`) against the base branch.
- **Git-ignored:** `.sim/` (the simulator's local exchanges). `.env.sim` was already covered by
  `.env.*`.

## 7. Order and why

1. **Phase 0 first.** Without replays and baselines, "all features still work" cannot be
   proven.
2. **Phases 1–2 next.** They remove most of what a live model trips on, and shrink every prompt,
   so later phases are cheaper to test.
3. **Phases 3–4 depend on 1–2.** The message catalogue and the conversation compacter reuse the
   context engine.
4. **Phase 5 last.** It changes how steps are scheduled, so it goes in once behaviour is pinned
   down by replays.

## 8. Decisions (15 October 2026)

1. **Q1, external writes:**
   - Every external write asks for approval, including writes the person asked for in so many
     words.
   - The approval card gains an **"Always allow"** choice. Approval decisions already accept
     `alwaysAllow` (`governance_routes._always_allow`), so this extends that and exposes it. It saves a standing permission for that
     exact write type on that resource for this worker, visible in the worker's settings and
     revocable there.
   - Built in Phase 4 with the other approval controls.
2. **Ruff rules:** complexity and size rules apply to **changed files only**, and only to the
   functions a change touches.
3. **Phase 4 screens:** approved. Adding Retry and outcome buttons to chat messages and the Work
   queue is fine.
4. **Delivery:** implement **the whole plan before going back to Oracle**. Phases are still built
   and proved one at a time on branches, but Oracle is not touched until all phases are done.

## 9. Done means

- All seven scenarios pass on replay, **with no simulator advantage needed** (A1–A8 resolved).
- Every finding is closed or explicitly deferred with a reason.
- Every section 5 metric is at or under target, or the gap is explained.
- The full backend suite, CI verify scripts, ruff (with the new rules on touched files) and
  pyright are clean.
- A final live check on Oracle with the real model, using the scenario 1, 3 and 7 messages.
