# Oracle handoff: chat messages that returned a hidden 500

Written 14 October 2026, for the pull request from `fix/chat-task-misroute-500` (made from main
at `56a1419`). It is separate from `fix/e2b-control-file-rewrite`: both need merging, and the
order doesn't matter. It follows *Audoryn Oracle Update and Deployment Rules — Illustrated
Edition*.

**Target:**
- **AgentGate Oracle:** `130.61.104.247`, `/opt/audoryn/AgentGate`, `audoryn-api.service`.
- **Not touched:** frontend, Vercel, Console, QStash, the database, `/etc/audoryn/audoryn.env`.

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

## 4. Steps (each one only after approval)

1. **Merge** both pull requests once CI passes: this one and `fix/e2b-control-file-rewrite`.
2. **Check, then pull** (rules sections 3 and 12). `origin/main` must have moved past `56a1419`.
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

## 5. Checks after deploying

- **Send the coding request again.** It should be accepted as a task, not return a 500.
- **Watch the log:**

```bash
podman logs --since 15m audoryn-api 2>&1 | grep -E "Conversation operation failed|operation selected|AI inference failed|Coding action failed|Planner model accepted"
```

- **If a 500 still happens:** the `Conversation operation failed … at=` line names the place.

## 6. Rolling back

Run `podman tag localhost/audoryn-api:rollback-56a1419 localhost/audoryn-api:latest`, then
restart.

## 7. Proof

- **New test:** `test_chat_task_misroute.py` checks that a one-shot job becomes a new task, that
  an ordinary job still runs now, that a refusal keeps its status, and that unexpected failures
  are logged with their place but no content.
- **Conversation suites:** 117 pass.
- **Static checks:** ruff and pyright are clean.
