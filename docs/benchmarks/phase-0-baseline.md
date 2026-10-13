# Phase 0: measurement and baseline

Written 13 October 2026, for the [improvement plan](../agentgate-improvement-plan.md). This phase
changes no behaviour. It adds the counters and the fixed task set that every later phase is
measured against.

## What was added

### Usage records (production)

Each runtime step, chat message and background preparation now records what it used:

- QStash messages sent;
- database statements and rows (rows returned by reads, or changed by writes);
- Redis commands;
- AI calls, failed calls, tokens in and out, and the models used;
- time taken.

These are saved one row per unit of work in a new table, `usage_records` (migration
`0016_usage_records`), and logged as a `usage` line.

- **Kinds:**
  - `runtime.step` (with the phase: `plan`, `execute`, `resume`, `advance` or `finish`; no phase means the delivery did no work);
  - `runtime.delivery` (a delivery turned away as busy or over capacity);
  - `conversation.message` (with the command family as the phase, for example `conversation.answer`);
  - `preparation.task` and `preparation.worker`.
- **Logged only:** the outbox drain and the runtime sweep belong to no single organisation.
- **Turning it off:** `USAGE_RECORDS_ENABLED=false` stops the saving; the log line stays.
- **Cost:** one small insert per step or message.
- **If saving fails:** it is logged and dropped, and the work itself is never affected.
- **Code:**
  - [usage.py](../../backend/app/observability/usage.py) holds the meter;
  - [usage_records.py](../../backend/app/infrastructure/database/usage_records.py) saves it;
  - hooks sit in the database engine, the QStash client, the Redis clients used by the runtime, and the AI inference wrapper.

**To see real usage:**

```bash
python scripts/usage_report.py --hours 24
```

It is read-only. It totals by kind and phase, then lists the most expensive work items.

### Offline runtime benchmark (the fixed task set)

[runtime_benchmark.py](../../backend/scripts/runtime_benchmark.py) runs six fixed tasks through
the real runtime:

- the `/execute` and outbox drain handlers;
- the managed step loop;
- the smart planner;
- policies, approvals and the outbox.

Only the edges are replaced:

- **Database:** SQLite.
- **QStash:** an in-memory queue, delivered one at a time in due order.
- **Redis:** in memory.
- **AI:** scripted answers.
- **Integration:** a `bench` record store with canned results.

It takes about 10 seconds and never touches Neon, Upstash or an AI provider.

```bash
python scripts/runtime_benchmark.py            # table
python scripts/runtime_benchmark.py --json     # full outcomes and counts
```

**The guard:** [test_runtime_benchmark.py](../../backend/tests/test_runtime_benchmark.py) checks
two things against the saved [baseline](../../backend/tests/fixtures/runtime_benchmark_baseline.json):

- **Outcomes stay identical:** statuses, steps, provider calls, approvals and results.
- **Compute matches the baseline:** a rise fails as a regression, and a fall must be recorded
  deliberately with `--write-baseline`. So each phase's improvement shows up as a reviewed change
  to the baseline file.

**Limits of the offline numbers:**

- **Counts are deterministic** between runs. Bytes vary by a few bytes, from stored timings.
- **Not real PostgreSQL:** statement counts match the code's queries, but timings and bytes are
  only indicative.
- **Bytes stand in for transfer:** bytes read is the closest offline measure of Neon transfer.
- **Delivery order:** QStash delivers here one at a time. In production, overlapping deliveries
  can also show up as `busy`.
- **Paths not covered:**
  - the integration-foundation path (native GitHub and similar), which the benchmark runs with the foundation off;
  - the browser;
  - chat.

  Chat and integration tasks are added with phases 3 and 6.

## Baseline (offline, before any change)

| Task | QStash messages | Deliveries (idle) | Drains | DB statements | Rows read | Bytes read | Redis | AI calls | AI tokens in (est.) |
|---|---|---|---|---|---|---|---|---|---|
| single-read: read, report | 10 | 5 (2) | 5 | 213 | 118 | 43 KB | 105 | 2 | 6,088 |
| read-then-write | 17 | 9 (4) | 8 | 328 | 209 | 83 KB | 160 | 3 | 9,946 |
| approved-write: approval, then write | 13 | 6 (1) | 7 | 261 | 155 | 54 KB | 128 | 2 | 6,247 |
| denied-write: policy denies writing | 10 | 5 (2) | 5 | 213 | 128 | 45 KB | 105 | 2 | 6,133 |
| long-run: 8 reads, report | 59 | 33 (16) | 26 | 1,054 | 769 | 569 KB | 490 | 9 | 37,019 |
| wide-results: 5 reads of 20 KB each | 38 | 21 (10) | 17 | 691 | 463 | 1,334 KB | 325 | 6 | 27,115 |

Every task finishes with the intended outcome:
- **Approval:** the approved write runs once, after approval.
- **Policy:** the denied write is never offered to the AI, and the run ends as "attention".

## What the numbers show

1. **About half of all deliveries do nothing (plan finding A).**
   - **How:** each action is woken twice, directly and through the outbox. One wake-up does the
     work and the other finds nothing to do.
   - **In the long run:** 16 of 33 deliveries were idle, and the 8-read task sent 59 QStash
     messages, about 7 per action.
   - **Why it matters:** on QStash's free 1,000 messages a day, that is roughly 135 actions a day
     across all users.
   - **Fixed in:** phase 1.
2. **Every phase reloads all earlier step outputs (finding B).**
   - **Wide-results:** 100 KB of results made the database return 1.3 MB, about 13 times the data.
   - **Long-run:** 8 small reads returned 569 KB.
   - **Growth:** this grows with the square of the run's length.
   - **Fixed in:** phase 1.
3. **About 130 database statements per action.** About 105 Redis commands for a one-action task.
   Phase 1 targets the statements; phase 7 targets the Redis writes per event.
4. **A drain message for almost every commit (finding G).** The long run had 26 drains for 8
   actions. Phase 7.
5. **One wasted approval check.** While an approval was pending, a duplicate wake-up re-checked
   it. Phase 1 (single wake-up) and phase 4.
6. **A correction to finding C.**
   - **The plan said:** turning on the smart planner removes prompt size limits, so whole pages go to the AI.
   - **Measured:** the largest prompt stayed at about 19,500 characters, even with 100 KB of
     results. Step evidence is still bounded when it is put into the prompt; only individual text
     fields lose their clipping.
   - **What changes:** prompt size is a smaller problem than stated. Phase 3b keeps the budget
     work, but its priority drops. The plan is updated.

## Live baseline (needs the owner)

The offline set proves the runtime's own cost. AI quality and real provider behaviour need real
runs.

Run these in the workspace on the backup database, then save the output of
`python scripts/usage_report.py --hours 2`:

1. Chat: "hello".
2. Chat: "what are my workers doing?"
3. Chat: a question answered from saved results.
4. A GitHub read, for example "list the open pull requests in <repo>".
5. A GitHub change with approval, for example "open an issue titled …"; approve it.
6. A browser read of a public page.
7. Web research on a current topic.
8. A scheduled job, due in the next few minutes.
9. An action a policy denies.
10. A request that needs a clarification (two possible repositories).
11. A long multi-step task (five or more actions).
12. A task cancelled halfway.

These use the free AI allowances and send real QStash messages, so they are run only when the
owner chooses.

## Deploying

- **Migration:** run `alembic upgrade head` on each environment's database, which adds
  `usage_records`. It is already applied to the backup database (`Audoryn-staging-Backup`).
- **Code without the migration:** usage saving fails quietly with a logged warning, and
  everything else works.
- **Setting:** `USAGE_RECORDS_ENABLED` (default `true`) is in `.env.example`.
