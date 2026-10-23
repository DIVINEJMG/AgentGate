# Live testing with a simulated model

The model is the only part replaced. A person, or Claude Code, answers each AI request. AgentGate's
own prompts, schema checks, corrections, policy, approvals, GitHub, E2B, Redis, the database and the
queue all run for real.

## The parts

| Part | Where | Notes |
|---|---|---|
| Model | `scripts/ai_simulator.py serve` on `127.0.0.1:8788` | Saves each request to `backend/.sim/pending/NNNN.json` and waits for `backend/.sim/answers/NNNN.json` |
| Gateway (optional, [architecture plan](../plans/architecture-scale-plan.md)) | `python -m audoryn_gateway` in `services/gateway` on `127.0.0.1:8000`, with the backend moved to `--port 8101` | Everything reaches the backend through it unchanged; see [its README](../../services/gateway/README.md) |
| Backend | `scripts/run_simulation.py` on `127.0.0.1:8000` | Loads `.env`, then `backend/.env.sim` (git-ignored), then routes every text model call to the simulator |
| Database | A Neon branch named in `.env.sim` | The launcher refuses to start without its own `DATABASE_URL` |
| Queue | `npx @upstash/qstash-cli dev` on `127.0.0.1:8080` | Same API and signatures as QStash, no quota |
| GitHub, E2B, Redis | The real services | Live calls with the laptop credentials |

The simulator connection is the only kind of model connection that may use a loopback address,
and it is refused in production (`Settings.validate_security`).

## Starting

1. Start the queue: `npx @upstash/qstash-cli dev -port=8080`. Put the printed values in
   `.env.sim`, along with the `QSTASH_*_URL` callbacks pointing at `http://127.0.0.1:8000/internal/v1/...`.
2. Start `ai-simulator`, then `agentgate-sim`. Both are in `.claude/launch.json`.
3. Use the workspace (`npm run dev` in `frontend`, which talks to `localhost:8000`).

## Answering

```text
python scripts/ai_simulator.py list
python scripts/ai_simulator.py show 3          (add --full for the whole prompt)
python scripts/ai_simulator.py answer 3 '{"content": {...}}'
```

An answer can also simulate a failure:

| Answer | What AgentGate sees |
|---|---|
| `{"content": "...", "finish_reason": "length"}` | A cut-off reply |
| `{"refusal": "..."}` | The model declines |
| `{"status": 429, "retry_after": 30}` | An HTTP error from the endpoint |
| `{"hang": true}` | No reply, so AgentGate's timeout is tested |

The simulator answers like a model, but it may use what it knows (the code, the database,
GitHub, lessons from earlier scenarios) to get a scenario through. That way each scenario runs to
the end and shows how the whole path behaves and how to fix it. Each time it needs that extra
knowledge, it notes the weakness under *Where the simulator had an advantage* in
[simulation-findings.md](simulation-findings.md). The weakness is fixed in the batch afterwards,
so that any live model can run every scenario end to end.

## Replaying

Every exchange is appended to `backend/.sim/transcript.jsonl`. To run a recorded scenario again
without a person, start the simulator with `--replay path/to/transcript.jsonl`. Each request is
answered with the recorded answer for the same schema, in order. Any request beyond the
recording waits for a person.

## Tools (Phase 0)

| Command (from `backend/`) | What it does |
|---|---|
| `python scripts/ai_simulator.py next N` | Waits for the next request after number N and prints only what is needed to answer it |
| `python scripts/sim_replay.py <scenario> [--allow-writes] [--approve]` | Runs a recorded scenario with no one answering: sends its message, answers from `tests/fixtures/sim/transcripts/`, arms its faults, waits for the outcome. Steps only a person can take are announced. |
| `python scripts/export_sim_fixtures.py` | Saves the scenarios as fixtures after a new simulation: provider outputs per step, answers with prompt sizes per section, and the catalogue (`scenarios.json`) |
| `python scripts/sim_metrics.py` | The baseline numbers every phase is compared with ([baseline-metrics.md](baseline-metrics.md)) |
| `python scripts/check_changed_quality.py` | The changed-code quality guard (also in CI): size, complexity, duplication, queries in loops and repeated work in the functions a change touches |
| `python scripts/code_health.py [folder]` | Code craft report for the whole backend: repeated call shapes, duplicated blocks, queries in loops, repeated serialisation, possibly dead functions |
| `python scripts/restart_drill.py [--drain 120] [--when TEXT]` | Restart drill ([architecture plan](../plans/architecture-scale-plan.md), Phase 0): starts the backend in place of `agentgate-sim`, gracefully restarts it while a step runs, and reports which steps finished in the drain and which were left to the sweep |

## Restart drill

This drill proves that a restart lets running steps finish. Stop `agentgate-sim` first, because the drill starts its own backend on port 8000.

1. Run `python scripts/restart_drill.py --drain 300`.
2. When it prints `ready`, start a scenario: `python scripts/sim_replay.py s1_open_issues`.
3. The drill sends the graceful stop that a process manager sends at the first `inference started` log line. That is the moment a step is waiting on its model call.
4. It prints the shutdown time and the drain lines:
   - `Draining N running step(s)` is logged when the drain starts;
   - a step that finishes within the drain ends with its normal usage line;
   - a step still running when the drain ends is logged as `Cancelled ... the sweep will recover them`.
5. The drill starts the backend again so the scenario can go on. Ctrl+C stops it.

On Windows the backend exits with code 3 after `Application shutdown complete`. That is uvicorn re-raising the stop signal, not an error.

## Two copies and the rolling restart

This drill proves that a rolling restart under load fails no request and loses no task ([architecture plan](../plans/architecture-scale-plan.md), Phase 2). Stop `agentgate-sim` first.

1. Start copy 0, which runs steps: `python scripts/run_simulation.py --port 8101`.
2. Start copy 1 with `RUNTIME_RUN_LOCAL=false`: `python scripts/run_simulation.py --port 8102`.
3. Start the gateway with both copies in `GATEWAY_CORE_UPSTREAMS`, copy 0 alone in `GATEWAY_EXECUTION_UPSTREAMS`, and a `GATEWAY_ADMIN_TOKEN` ([gateway README](../../services/gateway/README.md)).
4. Start a scenario (`python scripts/sim_replay.py s1_open_issues`) and keep some API load on port 8000.
5. While a step runs, restart copy 0, then copy 1, one at a time. Use `restart_drill.stop`/`start` from a script with `Rollout` (`audoryn_gateway.rollout`) and `pause_last="execution"`.
6. Expect:
   - no 5xx in the load;
   - `Draining N running step(s)` on copy 0, and no `Cancelled`;
   - the task completes.
   - A preparation interrupted by the restart is resumed by one copy from its checkpoint.

**With the execution process (Phase 3):**
- Run the copies with `SERVICE_ROLE=api` and `EXECUTION_CALL_URL=http://127.0.0.1:8000`, and one more process on 8201 with `SERVICE_ROLE=execution`.
- Give them all the same `INTERNAL_CALL_KEY_ID` and `INTERNAL_CALL_KEY_SECRET`.
- Point `GATEWAY_EXECUTION_UPSTREAMS` at 8201.
- Restart the execution process first, during a step; expect `Draining` there, and steps and preparations only in the execution process's log.

**Faults:** `backend/.sim/faults.json` makes chosen GitHub calls fail like a network does, so
uncertain writes can be tested. It is read on every call, and only the simulation launcher
installs it. For example:

```json
{"rules": [{"method": "POST", "path_suffix": "/issues", "mode": "dropped", "times": 1}]}
```

- `dropped`: the request never reaches GitHub.
- `lost_response`: GitHub performs the request, but the reply is lost.

**Golden tests:** `tests/test_phase0_golden.py` pins today's step wording, planner evidence and
planner prompt. When a change is intended, run it with `AGENTGATE_UPDATE_GOLDEN=1` and review
the diff of `tests/fixtures/sim/golden/phase0.json`.
