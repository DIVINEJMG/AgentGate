# Phase 2: schedules

Written 13 October 2026, for the [improvement plan](../agentgate-improvement-plan.md).

## What changed

1. **Scheduled jobs start on time.**
   - **Before:** only the runtime sweep queued scheduled work, so a run started up to 10 minutes late.
   - **Arming:** now, whenever a job's next run time is set (when the schedule is saved, and after
     each run is queued or skipped), one QStash message is sent, delayed to exactly that time.
   - **On delivery:** `/internal/v1/runtime/schedule-due` queues just that job.
   - **Deduplication:** arming the same time twice is deduplicated.
   - **Longer than 7 days:** QStash's free plan allows delays of up to 7 days (setting
     `QSTASH_MAX_DELAY_SECONDS`). A later run is reached in hops: the wake-up re-arms itself until
     the time arrives.
   - **Lost messages:** the sweep stays as the backstop if a message is lost or QStash is down.
   - **Cost:** one QStash message per scheduled run.
2. **The sweep reads every schedule in one query.**
   - **Before:** it loaded up to 200 active jobs, then each job's full current revision, one by one.
   - **Now:** one query reads only the schedule settings of every active job. Due runs are handled
     earliest first.
   - **Bug fixed:** the 200 jobs were taken in `updated_at` order, which queueing a run does not
     change. In an organisation with more than 200 active jobs, schedules on the rest would never
     run.
3. **Each run is queued once.** The sweep and an on-time wake-up can arrive together. So each due
   job is locked and its schedule re-read before queueing, and a run already queued is left alone.
4. **"Queue one catch-up" now means one.**
   - **Before:** after an outage, the next run time was set from the missed run instead of from
     now. A backend down for three days queued three back-to-back daily runs; with on-time
     wake-ups they would have fired immediately.
   - **Now:** one run covers the missed period, and the schedule resumes from now. "Skip" is
     unchanged.
5. **People can see why.** Each schedule records its last queued run (when it was due, and when it
   could start) and its last skipped run. The Automation panel says when a run was moved to the
   worker's working hours, or skipped as missed. The next run time was already shown.

## Proof

- **[test_schedules.py](../../backend/tests/test_schedules.py) checks:**
  - **Clock changes:**
    - a daily schedule runs once every local day for a year across clock changes, in five time
      zones, including half-hour changes;
    - a missing local time (02:30 on the spring-forward night) runs at 03:30.
  - **Other cadences:** weekly and interval cadences.
  - **The sweep:**
    - one query whatever the number of jobs;
    - jobs beyond the old 200 limit;
    - no double queueing.
  - **Missed runs:** one catch-up, and skip with its reason.
  - **Wake-ups:**
    - only the right job is queued;
    - paused jobs are ignored;
    - delays and hops are correct;
    - a failed wake-up falls back to the sweep;
    - the endpoint waits briefly when early and re-arms when far ahead.
- **The offline benchmark** has a new task, `scheduled-read`. A daily job due now is started by
  its on-time wake-up and completes, and the next day's wake-up is armed. The six existing tasks
  are unchanged.

## Not in this phase

Answering "when will this run?" in chat belongs to phase 3 (how the AI replies), using the
`nextDueAt`, `lastQueued` and `lastSkipped` data recorded here.

## Deploying

- **No migration.**
- **The new endpoint** `/internal/v1/runtime/schedule-due` sits under the same internal path and
  QStash signing as `/execute`. Its URL is derived from `QSTASH_RUNTIME_EXECUTE_URL`.
- **No new QStash schedule** is needed.
- **New optional setting:** `QSTASH_MAX_DELAY_SECONDS` (default 604800, 7 days) is in
  `.env.example`.
- **Existing schedules** get their first on-time wake-up after the next sweep queues them, or when
  they are next saved.
