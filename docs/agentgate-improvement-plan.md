# AgentGate improvement plan: same or better results, less compute

Written 13 October 2026 from a reading of the code on branch `feature/efficiency-pass`. The map of
how AgentGate works today is the "AgentGate Anatomy" page shared in the conversation. This plan
covers more than compute: how the AI decides and replies, how it uses integrations, schedules,
the queue, approvals, policies and integrations.

**Status:** all phases (0 to 7) done (14 October 2026), plus an [understanding review](benchmarks/understanding-review.md); see the [phase 0 baseline](benchmarks/phase-0-baseline.md),
[phase 1](benchmarks/phase-1-runtime-engine.md), [phase 2](benchmarks/phase-2-schedules.md),
[phase 3](benchmarks/phase-3-ai.md), [phase 4](benchmarks/phase-4-approvals.md), [phase 5](benchmarks/phase-5-policies.md) and
[phase 6](benchmarks/phase-6-integrations.md) and [phase 7](benchmarks/phase-7-events.md) results. The owner chose one live check at the end instead of
a live baseline now.
Each later phase waits for the owner's go-ahead.

## Rules for every change

1. **Same or better results, proven.**
   - Every change ships with a test that runs the old and the new behaviour side by side, or a
     benchmark showing results are unchanged or better, as with the database fixes.
   - AI changes are judged on a fixed task set (phase 0), not on a single example.
2. **Authority never loosens by accident.**
   - Policies, approvals and integration scopes can only become clearer or stricter unless the
     owner explicitly decides otherwise.
   - A change that would allow more is a separate, named decision.
3. **One phase at a time.** Report what changed, and the before-and-after numbers.
4. **No new paid services and no new dependencies without approval.** The free QStash, Neon and
   Upstash limits are design constraints.

## Phase 0: measure first (small) — done

Without numbers we cannot prove "less compute, same output". Built as planned, except that the
fixed task set is in two parts:
- an offline runtime benchmark of six scripted tasks, guarded by a test;
- twelve live tasks the owner runs when chosen.

Results and corrections are in the [phase 0 baseline](benchmarks/phase-0-baseline.md).

- **Per-run counters**, saved on the run:
  - QStash messages;
  - database queries and rows read;
  - AI calls, tokens in and out, and model;
  - Redis commands;
  - time in each phase.
- **A fixed task set** of about 12 representative tasks, using the existing AI benchmark scripts
  and the GitHub workflow simulator:
  - chat only (greeting, question about status);
  - a GitHub read, and a GitHub change with approval;
  - a browser read;
  - web research;
  - a scheduled job;
  - a policy-denied action;
  - a clarification;
  - a long multi-step run.
- **A baseline report:** each task's result, steps, messages, queries, tokens and time. Every
  later phase re-runs the same set.

## Phase 1: the runtime engine (queue and step loop) (medium) — done

Done as planned, with two changes:
- the wake-up is sent directly, with the outbox as the fallback;
- slow planning keeps its full budget, and only a stream silent for 5 minutes counts as failed
  (owner's decision).

See [phase 1 results](benchmarks/phase-1-runtime-engine.md).

| Problem today | Change |
|---|---|
| Each step is woken twice (direct continuation and outbox continuation), so roughly 6 QStash messages per action, and the duplicate still loads the whole context | One wake-up per step through the outbox only, keeping the direct send as a fallback if the outbox write fails |
| Every phase reloads all earlier steps in full, so reads grow with the square of the run's length | Load step IDs and statuses; read full outputs only for the evidence the planner uses (the latest steps and referenced ones) |
| Admission locks and reads up to 1,000 queued items on every attempt, and items not first retry every 5 s | Compute the fair order in SQL and read one row. When not first, wait for a signal or a short backoff instead of a fixed 5 s loop |
| A planning step may hold its lease, slot and AI capacity for up to 2,000 s | A per-attempt limit (for example 120 s) inside an overall planning budget, so a slow model frees capacity and the next route is tried |
| Duplicate deliveries are only detected after loading everything | Check the step number and lease against the work item first; load the rest only if it will really run |

**Proof:** step-for-step identical runs on the task set, with fewer messages, queries and seconds.

## Phase 2: schedules (small to medium) — done

Done as planned, except that answering "when will this run?" in chat moves to phase 3. Two bugs
were also fixed:
- schedules beyond the first 200 active jobs never ran;
- "queue one catch-up" queued every missed run.

See [phase 2 results](benchmarks/phase-2-schedules.md).

| Problem today | Change |
|---|---|
| Scheduled jobs start up to 10 minutes late, because only the 10-minute sweep queues them | When a schedule's next time is set, send one QStash message delayed to that time, de-duplicated by job and due time. The sweep stays as the backstop |
| The sweep loads 200 jobs, then each job's revision one by one | One batched query, reading only the schedule part of the definition |
| Time zones, daylight-saving changes, working hours and missed runs have little test coverage | Tests for each, including a run that falls in a clock change |
| People cannot easily see when a job will next run, or why it did not | Show the next run time and the last skip reason on the job, and answer "when will this run?" in chat from that data |

## Phase 3: how the AI decides and replies (largest) — done

Done except:
- streaming replies, which the owner put off until later;
- model choice per step type, where the owner kept one order.

See [phase 3 results](benchmarks/phase-3-ai.md).

### 3a. When it just replies

- **Today:**
  - a normal message costs two AI calls, classify then reply;
  - both are sent a large context (all of: workers, jobs, schedules, policies, approvals, runs,
    results, memories and messages), much of it loaded one row at a time;
  - the person waits for both inside one request.
- **Change:**
  - **One call for ordinary replies:** the classification can return the reply in the same
    answer when it chooses "just answer". The reply checks (no invented claims, facts only from
    the backend) stay as they are.
  - **Context in tiers:** a compact summary for routing, with the full detail only for the
    operations that need it (task preparation, status questions). Assembled in batched queries.
  - **Faster feedback:** show "thinking" right away and stream the reply where the client
    supports it.

### 3b. How it plans tasks

| Problem today | Change |
|---|---|
| With the smart planner on, individual text fields lose their clipping. Phase 0 measured that step evidence stays bounded (largest prompt about 19,500 characters even with 100 KB of results), so this is smaller than first stated | Restore explicit budgets, with clear "truncated; read the next part" markers so the AI can ask for more. Its answers stay the same or improve on the task set. Lower priority than first planned |
| The tool list is built twice per planning step, each time with a policy check per tool | Build it once per step, and re-check only what could have changed (authority version) |
| The AI repeats reads and loops in the browser, caught only by after-the-fact rejections | Give it a short "what has been tried" summary, and stop earlier with an honest explanation |
| Finishing is judged loosely against the completion criteria | A completion check per criterion before finishing, recorded on the result as met, not met or not verifiable |
| Every step uses the same model order | Choose the route by step type: a fast model for simple reads and summaries, the strongest for planning changes and recovery. Measured on the task set before adoption |

### 3c. How it uses integrations

- **Native first:** prefer a provider's own API over the browser whenever an integration can do
  the job. This exists for some reads; make it the general rule, with the reason recorded.
- **One clarification round:** when an account or resource is ambiguous, ask once with the
  candidates listed, instead of failing step by step.
- **Plain capability gaps:** explain them in the person's terms, for example "Your GitHub
  connection can read but not open pull requests; reconnect with write access", with the fix.

## Phase 4: approvals (medium) — done

Done as planned, with the owner's decisions:
- 24-hour expiry with a reminder at 12;
- narrow, confirmed "always allow";
- editing before approving.

See [phase 4 results](benchmarks/phase-4-approvals.md).

- **Today:**
  - approve or reject only;
  - no expiry and no reminder;
  - the notification is always "not attempted";
  - a run can wait for ever.
- **Changes:**
  - **See exactly what will happen:** a clear preview of the action (diff, message or file),
    in the workspace and in chat.
  - **Edit, then approve:** for content the person wants to fix first. The edited content is what
    executes, and the change is audited.
  - **Expiry and reminders:** a configurable expiry, by default 24 hours, after which the action
    is cancelled and the run continues or stops cleanly. Reminders use the existing notification
    path.
  - **"Always allow this kind":** offered on approval. It proposes a policy, which the owner must
    confirm and which is never created silently.
  - **Approve several at once** for the same task.

## Phase 5: policies (medium) — done

Done as planned. Owner decision on starting policies: irreversible actions ask unless the request
asked for them. See [phase 5 results](benchmarks/phase-5-policies.md).

- **Today:** policies target agents and resources by ID, which breaks when things are
  re-created. With no matching policy the answer is deny, which is safe but unexplained.
- **Changes:**
  - **Selectors people understand:** a provider, a capability, a resource name pattern, and a
    risk level, alongside IDs.
  - **Explain every decision:** the deciding policy and why, shown with the denial or approval
    request. Include a plain message when the default deny applied.
  - **Try before saving:** a policy simulator answering "what would happen if this worker
    tried X on Y".
  - **Starting policies per role:** for example reads allowed, writes need approval, deletes
    denied, offered when a worker is created.
  - **Faster evaluation:** policies loaded once per step and reused across that step's checks.

## Phase 6: integrations (medium) — done

- **Health before work:** check connection health and token validity before a run starts, and
  refresh tokens ahead of expiry, so runs do not fail midway.
- **Cache discovery:** cache capability and resource discovery per connection, with a clear
  refresh.
- **Provider rate limits:** respected per provider, with friendly waiting states.
- **Event triggers:** webhooks and the event inbox documented and tested end to end for each
  provider that supports them.

## Phase 7: events, outbox and Redis (small) — done

- **Coalesce drains:** one in-flight drain request at a time, instead of one QStash message per
  commit.
- **Move maintenance off the drain:** coding-session maintenance and stale web-research checks
  run on their own schedule.
- **Fewer Redis writes per event:** write live events once and fan out when reading, cutting the
  writes from 4 per event.

## Order and size

| Phase | Size | Why this order |
|---|---|---|
| 0 Measure | Small | Needed to prove every later change |
| 1 Runtime engine | Medium | The biggest compute and message savings, with no change to AI behaviour |
| 2 Schedules | Small to medium | Fixes late starts, and reuses phase 1's delayed messages |
| 3 AI | Large | The largest quality gain; needs the phase 0 task set |
| 4 Approvals | Medium | Needs a product decision on expiry and "always allow" |
| 5 Policies | Medium | Pairs with approvals ("always allow" creates a policy) |
| 6 Integrations | Medium | Reliability of external work |
| 7 Events and Redis | Small | Cleanup; can run alongside others |

## Decisions the owner will need to make

- **Approvals:** the default expiry (24 h proposed) and what happens on expiry (cancel the
  action and stop the run, proposed).
- **"Always allow this kind":** whether it should exist at all, and who may confirm it.
- **Starting policies:** which defaults new workers should get.
- **Model choice per step type:** which routes count as fast and which as strongest, among the
  configured models.
- **Streaming replies:** whether the workspace should stream, which is a frontend change too.
