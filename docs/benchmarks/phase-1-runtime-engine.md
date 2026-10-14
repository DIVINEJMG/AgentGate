# Phase 1: the runtime engine

Written 13 October 2026, for the [improvement plan](../agentgate-improvement-plan.md). Measured
with the offline benchmark from [phase 0](phase-0-baseline.md).

**The guard held:** every task's outcome is identical to phase 0. That covers statuses, steps,
provider calls, approvals and results.

## Before and after (offline benchmark)

| Task | QStash messages | Deliveries (idle) | DB statements | Rows read | Bytes read | Redis |
|---|---|---|---|---|---|---|
| single-read | 10 → 8 | 5 (2) → 3 (0) | 213 → 206 | 118 → 113 | 43 → 36 KB | 105 → 95 |
| read-then-write | 17 → 13 | 9 (4) → 5 (0) | 328 → 315 | 209 → 200 | 83 → 65 KB | 160 → 140 |
| approved-write | 13 → 11 | 6 (1) → 4 (0) | 261 → 251 | 155 → 149 | 54 → 48 KB | 128 → 118 |
| denied-write | 10 → 8 | 5 (2) → 3 (0) | 213 → 206 | 128 → 123 | 45 → 38 KB | 105 → 95 |
| long-run (8 reads) | 59 → 43 | 33 (16) → 17 (0) | 1,054 → 963 | 769 → 673 | 569 → 236 KB | 490 → 410 |
| wide-results (5 × 20 KB) | 38 → 28 | 21 (10) → 11 (0) | 691 → 639 | 463 → 415 | 1,334 → 422 KB | 325 → 275 |

AI calls and tokens are unchanged, as intended: this phase does not touch what the AI sees.

## What changed

1. **One wake-up per step.**
   - **Before:** each step was woken twice, directly and through the outbox. One of the two
     always found nothing to do.
   - **Now:** the step sends its wake-up itself once its lease is released, in its resource's
     queue partition.
   - **The outbox event stays:** it is marked `"wakeup": "runtime"` and only updates the workspace.
   - **If the send fails:** the wake-up is saved in the outbox, whose drain retries it. The sweep
     is still the last backstop.
   - **Other sources are unchanged:** chat preparation and integration events still wake work
     through the outbox.
2. **Step outputs read once, not every phase.**
   - **Deciding:** what to do is decided from step headers (index, kind, status).
   - **Execute and resume:** these load only the current step.
   - **Planning:** it reuses the evidence already built for finished steps, kept in memory by step
     and last change. It reads full outputs only for steps it hasn't seen.
   - **Finishing:** this reads every output once.
   - **Safety:** an output that hasn't been loaded raises an error if read, so a mistake fails
     loudly in tests instead of querying quietly.
3. **Admission reads one row.**
   - **Before:** it locked and read up to 1,000 queued work items, payloads included.
   - **Now:** the same order (priority, age in five-minute periods, organisations with active work
     yielding) is computed in SQL, and only the first id is read.
   - **Proof:** forty random queues give the same choice as the previous code.
   - **Waiting:** an item that isn't first now waits 5, 5, 10, 10, 20, 20, then 30 seconds before
     asking again, instead of every 5 seconds.
   - **Bug fixed:** every wait used to reuse one QStash deduplication key, so QStash could drop a
     repeated wait message, leaving the item to the 10-minute sweep. Each wait now has its own key.
4. **Duplicates are recognised first.**
   - A stale or duplicate delivery is now recognised from the work item alone, before the job,
     worker, run or steps are loaded.
   - **One difference:** a duplicate no longer fails a task whose job was deactivated meanwhile.
     The real delivery for that step still does.
5. **Slow planning keeps its full time; silent connections don't.** This was the owner's decision.
   - **Streaming models:** a model that keeps sending output may use the whole 2,000 s decision
     budget.
   - **Silent streams:** a stream silent for `PLANNER_STREAM_SILENCE_SECONDS` (default 300) is
     treated as a failed connection, like an unreachable endpoint. That frees the slot, and the
     existing failure handling retries later or moves to the next model.
   - **Unchanged:** models that don't stream, and short interactive requests.

## Tool list (done after phase 1, at the owner's request)

**What it was:** building the AI's tool list took 13 queries. The capability catalog was read
three times per build, the stored-credential check made one query per connection, and the policy
check read the agent again.

**What changed:**
- **One snapshot:** each build now reads the catalog once into a snapshot.
- **One credential query:** stored credentials are checked in one query for all connections.
- **No repeat agent read:** the policy check is given the agent it already has.

**Result:** a build makes 7 queries.

**Kept:** the second build after the AI answers. It re-checks that authority hasn't changed while
the AI was thinking.

**Proof:** the benchmark now records the exact tools and exclusions the AI is given at every
planning step, and they are identical. Database statements on the long run went from 963 to 855,
and from 1,054 before phase 1.

## What this phase did not change, and where it goes

- **Drains:** a QStash drain message is still sent for almost every commit (26 in the long run).
  That is phase 7.

## Deploying

- **No migration** in this phase.
- **New setting:** `PLANNER_STREAM_SILENCE_SECONDS` (default 300) is in `.env.example`.
- **Restart:** deploy the backend as usual. In-flight runs continue: older outbox continuation
  events still wake their steps, and duplicate wake-ups are still recognised and ignored.
