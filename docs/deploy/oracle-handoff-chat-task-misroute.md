# Oracle handoff: hidden chat 500s and QStash usage

Written 14 October 2026, for the pull request from `fix/chat-task-misroute-500` (made from main
at `56a1419`). It is separate from `fix/e2b-control-file-rewrite`: both need merging, and the
order doesn't matter. It follows *Audoryn Oracle Update and Deployment Rules — Illustrated
Edition*.

**Target:**
- **AgentGate Oracle:** `130.61.104.247`, `/opt/audoryn/AgentGate`, `audoryn-api.service`.
- **Not touched by the code:** frontend, Vercel, Console, the database schema,
  `/etc/audoryn/audoryn.env`. The QStash schedule changes in section 7 are optional, need
  approval, and are made in the Upstash console.

## 1. What the logs showed

| | Finding | Evidence |
|---|---|---|
| Confirmed | Every chat message after the first returned `500 Internal Server Error` with no error logged | `POST …/messages 500` three times; usage `outcome=error` |
| Confirmed | Claude (Haiku for chat, Sonnet for planning) was skipped | OpenRouter answered the first Claude call with `403` (`authentication_failed`). AgentGate then pauses that OpenRouter account for 120 seconds, so later calls show `rate_limited model_called=False` and fall back to Nemotron. Sonnet uses the same OpenRouter connection, so planning falls back too. |
| Confirmed | Nemotron read the coding request as "run an existing job now" | `operation=work.execute_now` instead of a new integration task |
| Confirmed | That job was the earlier chat task's one-shot job, which can't run again | `queue_job` refuses it with a 409 ("This job belongs to one chat request…") |
| Confirmed | The message route doesn't recognise that kind of refusal, so it became a generic 500 and logged nothing | `conversation_routes._http_error` |

## 2. What changed (code only)

- **The message becomes a new task:** when a request points "run now" at a job that carried
  one earlier chat request, it is handled as a new task, because that job can't run again.
  Ordinary jobs still run now as before.
- **No more hidden 500s:**
  - A service's own refusal keeps its status and plain message.
  - Any truly unexpected failure is logged as
    `Conversation operation failed error_type=… at=file:line function`. The location is logged,
    never the message, which may hold chat content.

**No migration, no new dependencies, no env change, no frontend change.** Rebuild the image only.

## 3. Not code: the OpenRouter 403

OpenRouter refused the Claude key with `403`. That is on the OpenRouter account, not in the app.
Check it in the OpenRouter dashboard:
- **The key's own credit limit:** the `$5` budget may be used up.
- **The key itself:** is it disabled or deleted?
- **Account credits.**

Until that's fixed, chat and planning run on Nemotron. After raising the limit or replacing the
key, no restart is needed: the pause ends within 2 minutes. Changing the key itself is a targeted
env edit under rules section 9, which needs a backed-up `sudo python3` script and a restart.

## 4. QStash: what used the daily 1,000 messages

QStash's own logs for 14 October (UTC), read-only through its API:

| Destination | Messages | What it was |
|---|---|---|
| `/outbox/drain` | 450 | ~350 "please drain" signals after commits, plus the every-15-minutes schedule (96 a day) |
| `/integrations/process` | 258 | 249 GitHub webhook events. 229 of them were for repositories no task uses (166 `workflow_run`, mostly AgentGate's own CI), so processing only marked them `unmatched`. |
| `/runtime/execute` | 149 | task step wake-ups |
| `/runtime/sweep` | 109 | the every-10-minutes schedule |
| `/runtime/heartbeat` | 19 | the hourly schedule |
| **Total** | **987** | ~79 of these went to laptop tunnel addresses (the laptop backend uses the same QStash account) |

AgentGate's usage records showed only 268, because they didn't count drains, scheduled
deliveries or publishes outside a request.

**What changed (code):**
- **The server drains its own outbox:** after a commit, a serving backend drains the outbox in
  the same process instead of asking QStash to call it back. The grouping is the same: while one
  drain is on its way, later commits rely on it. Setting: `OUTBOX_DRAIN_LOCAL`, default on.
  Scripts and tests never drain this way. Saves about 350 messages a day.
- **Unconnected-repo events skip QStash:** a GitHub event for a repository no task is connected
  to is recorded as `unmatched` when it arrives, with no queue message. That's the same outcome
  processing reached before. Installation events are always processed. Saves about 230 a day.
- **Every QStash message is counted:**
  - Scheduled deliveries count on the endpoint that receives them (sweep, drain, heartbeat).
  - The watchdog's own checks are metered.
  - A publish outside any meter is logged as `QStash message outside a meter destination=…`.

  System-level usage (no organisation) is still only logged, not saved as a row, because
  `usage_records.organization_id` is required. Saving it would need a small migration; it
  hasn't been done and needs your approval.
- **Other drains checked:** besides the endpoint, the watchdog and the after-commit hook, the
  only other drain is the `audoryn-outbox` console script, a manual development helper that
  polls every second. Nothing in the app or deploy starts it. Section 5, step 2 checks that it isn't
  running on Oracle.

**Expected:** about 400 QStash messages a day for the same work, down from about 990.

## 5. Steps (each one only after approval)

1. **Merge** both pull requests once CI passes: this one and `fix/e2b-control-file-rewrite`.
2. **Check, then pull** (rules sections 3 and 12). `origin/main` must have moved past `56a1419`.
   Also confirm the manual outbox poller isn't running (this should print nothing):

```bash
ps aux | grep -E "audoryn-outbox|outbox_worker" | grep -v grep
```

3. **Rebuild:**

```bash
cd /opt/audoryn/AgentGate || exit 1
podman image prune -f
df -h /
podman build -f backend/Containerfile -t localhost/audoryn-api:latest . || exit 1
podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
```

   The image ID must differ from `2c6a026c…`.

4. **Restart and verify:**

```bash
systemctl --user restart audoryn-api.service && sleep 20
systemctl --user is-active audoryn-api.service
podman inspect audoryn-api --format '{{.Image}}'
curl -sS -i --max-time 20 http://127.0.0.1:8000/health/ready
curl -sS -i --max-time 20 https://130.61.104.247/health/ready
```

## 6. Checks after deploying

- **Send the coding request again.** It should be accepted as a task, not return a 500.
- **Watch the log:**

```bash
podman logs --since 15m audoryn-api 2>&1 | grep -E "Conversation operation failed|operation selected|AI inference failed|Coding action failed|Planner model accepted"
```

- **If a 500 still happens:** the `Conversation operation failed … at=` line names the place.
- **Drains now happen locally:** usage lines show `"kind":"outbox.drain"` with `"phase":"local"`,
  and there are no new `/outbox/drain` messages in the QStash log apart from the schedule. To
  count drains in the last 15 minutes:

```bash
podman logs --since 15m audoryn-api 2>&1 | grep -c '"kind":"outbox.drain"'
```

## 7. Optional QStash schedule changes (Upstash console, approval required)

| Schedule | Now | Suggested | Per day |
|---|---|---|---|
| `130.61.104.247/internal/v1/outbox/drain` | every 15 min | hourly (`0 * * * *`) | 96 → 24 |
| `130.61.104.247/internal/v1/runtime/sweep` | every 10 min | every 30 min (`*/30 * * * *`) | 144 → 48 |
| `130.61.104.247/internal/v1/runtime/heartbeat` | hourly | keep | 24 |
| 5 paused `onrender.com` schedules | paused | delete (they cost nothing, it's cleanup) | 0 |

With local draining and the watchdog, the drain schedule is only a backstop. That brings the
total to about 230 messages a day. The laptop backend also uses this QStash account; stop it, or
give it its own token, when you're not testing.

## 8. Rolling back

Run `podman tag localhost/audoryn-api:rollback-56a1419 localhost/audoryn-api:latest`, then
restart.

## 9. Proof

- **QStash test:** `test_qstash_usage_cuts.py` checks local draining and its grouping, the
  setting and the not-serving path, unmatched events skipping the queue (installation events
  still queued), and the counting of scheduled and unmetered messages.
- **Full backend suite:** 1,580 pass. All CI verify scripts pass.
- **New test:** `test_chat_task_misroute.py` checks that a one-shot job becomes a new task, that
  an ordinary job still runs now, that a refusal keeps its status, and that unexpected failures
  are logged with their place but no content.
- **Conversation suites:** 117 pass.
- **Static checks:** ruff and pyright are clean.
