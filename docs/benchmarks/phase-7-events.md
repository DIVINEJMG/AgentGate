# Phase 7: events, outbox and Redis

Written 14 October 2026, for the [improvement plan](../agentgate-improvement-plan.md).

## What changed

1. **One drain request at a time.**
   - **Before:** every commit that wrote an outbox row sent its own QStash message to drain the
     outbox (finding G in the [baseline](phase-0-baseline.md)).
   - **Now:** a commit sets a short-lived Redis marker and sends a drain request only if the
     marker was not already set. Later commits rely on the request already on its way.
   - **Why nothing is missed:** the drain clears the marker before it reads the outbox.
     - A commit that found the marker set committed before the marker was cleared, so before the
       read, and the drain sees its rows.
     - A commit after the clear sends its own request.
   - **Lost requests:** the marker expires after 60 seconds (`OUTBOX_DRAIN_COALESCE_SECONDS`), so
     a lost request holds others back for at most that long.
   - **Failed requests:** a request that fails to send clears the marker at once.
   - **Without Redis:** every commit sends its own request, as before.
   - **Backstop:** the 15-minute recovery drain is unchanged.
2. **Maintenance moved off the drain.**
   - **Before:** coding-session clean-up and the stale web-research check ran on every drain.
   - **Now:** they run on the 10-minute runtime sweep, even when the runtime is disabled. Each
     part fails on its own.
   - **Effect:** a busy drain no longer pays for them, and an idle workspace gets them every 10
     minutes instead of every 15.
3. **Live events are written once.**
   - **Before:** each event was written to the organisation stream, and again to a per-run and a
     per-worker stream that nothing read. The duplicate marker was also set twice.
   - **Now:** one marker, one stream write, one notification. That is 3 Redis commands instead
     of up to 6.
   - **Readers of one run or worker:** they filter the organisation stream with `runId` or
     `workerId` on the replay and stream endpoints. The read position moves past other runs'
     events, so a filtered reader never stalls.
   - **Connections:** a drain uses one Redis connection for all its events, not one per event.
   - **Duplicates:** a re-drained duplicate writes nothing and returns `duplicate`.

## Results (offline benchmark, all 12 tasks)

Outcomes are identical on every task. Only compute changed.

| Task | QStash messages | Drains | Redis commands | DB statements |
|---|---|---|---|---|
| single-read | 8 → 6 | 5 → 3 | 95 → 81 | 182 → 180 |
| read-then-write | 13 → 10 | 8 → 5 | 140 → 122 | 279 → 276 |
| approved-write | 11 → 8 | 7 → 4 | 118 → 101 | 227 → 224 |
| long-run (9 steps) | 43 → 34 | 26 → 17 | 410 → 368 | 855 → 846 |
| wide-results | 28 → 22 | 17 → 11 | 275 → 245 | 567 → 561 |
| always-allow | 16 → 12 | 10 → 6 | 163 → 142 | 338 → 334 |

The other six tasks fall by the same proportions.

**Since Phase 0, for the long run:**

| Measure | Phase 0 | Now |
|---|---|---|
| QStash messages | 59 | 34 |
| Deliveries | 33 (16 idle) | 17 (0 idle) |
| Drains | 26 | 17 |
| DB statements | 1,054 | 846 |
| Bytes read | 569 KB | 226 KB |
| Redis commands | 490 | 368 |

The remaining drains carry live progress to the workspace. A drain is now sent only when no
drain is already on its way.

## Proof

- **[test_events_phase7.py](../../backend/tests/test_events_phase7.py), 7 tests:**
  - commits coalescing into one request;
  - the drain clearing the marker before it reads, and a later commit asking again;
  - a failed request releasing the marker;
  - the fallback without Redis;
  - maintenance on the sweep and not on the drain;
  - one write per live event;
  - filtered readers whose position always advances.
- **Offline benchmark:** rebaselined with identical outcomes; the figures are above.
- **Full suite:** 1,409 of 1,410 backend tests passed. The one failure was
  `test_conversation_context_batched.py[organisation]`.
  - It is from phase 3, and phase 7 does not touch conversation context.
  - It passed in 18 reruns since, alone and with its neighbours.
  - The test seeds rows with tied sort keys, so it is probably order-sensitive. It is flagged
    for a separate look.

## Deploying

- **No migration.**
- **One optional setting:** `OUTBOX_DRAIN_COALESCE_SECONDS`, default 60.
- **Schedules unchanged:** the 10-minute runtime sweep, which now also runs maintenance, and the
  15-minute recovery drain.
