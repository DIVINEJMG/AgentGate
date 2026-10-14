# Oracle handoff: onboarding, Claude and reliability fixes

Written 14 October 2026. This covers only what changed **after** PR #122 (the efficiency pass,
merged as `cfff0c3`). If Oracle is not yet set up as described in
[oracle-handoff-improvement-plan.md](oracle-handoff-improvement-plan.md) (for example, it still
uses the original database), do that first.

## 1. What is in this release

- **Stuck requests recover on their own.**
  - **Lost drain request:** a post-commit drain request could be dropped mid-flight, leaving work
    queued until someone stepped in. It is now kept alive until it finishes.
  - **Delivery watchdog:** a few minutes after any activity, the backend checks for undelivered
    outbox rows and open work whose wake-up was lost, and recovers them itself.
  - **Cost:** an idle backend makes no extra queries. The QStash sweep remains the scheduled
    backstop.
- **Reconnecting an account refreshes ongoing jobs.**
  - **What it does:** after a reconnect, ongoing jobs that pass the same checks as "Resume" get
    the account's current access.
  - **Jobs that don't pass:** they are named on the account.
  - **Runs:** a run whose account access changed since setup now waits for review instead of
    running without tools.
- **First-run setup for new workspaces** (existing workspaces are unchanged):
  - **Welcome flow:** three optional questions, then a starter worker with a welcome
    conversation, an approval rule and working hours.
  - **Home:** a five-step getting-started checklist, with guides that start only when clicked.
  - **Sample workspace:** `/workspace-preview` now works in production. It uses built-in data and
    makes no API requests.
- **Claude.** Claude Haiku 5.5 answers chat first and Claude Sonnet 5.5 plans first, both reached
  through **OpenRouter**. The free models follow as fallbacks. A direct Anthropic connection is
  built but unused (the Anthropic account could not be verified).
- **Phone layout:** the Conversations header now follows dark mode.
- **GitHub App key in one line:** the key is read from the one-line form podman env files need,
  and an unreadable key now says so ("Check GITHUB_APP_PRIVATE_KEY") instead of stopping tasks
  with an internal error. Interrupted runs also log where they failed.

## 2. Code: branch and pull request

The changes were made on `feature/efficiency-pass` after it was merged, and they are not
committed. Main contains that branch exactly (plus the merge commit), so they move cleanly onto a
new branch from main. Run these on the laptop:

```bash
cd C:/Users/divin/AgentGate
git fetch origin
git switch -c feature/onboarding-claude origin/main
git status
git add -A
git commit -F .git/AUDORYN_COMMIT_MSG.txt
git push -u origin feature/onboarding-claude
```

- **Before `git add`:** check that `git status` shows no `.env` file. Both `.env` files are
  ignored, so they shouldn't appear.
- **Then:** open a pull request from `feature/onboarding-claude` into `main`, wait for CI, and
  merge it.
- **Afterwards:** the old `feature/efficiency-pass` branch can be deleted.

## 3. Database

- **Migration `0018_organization_onboarding`** adds one table, `organization_onboarding`.
- **Already applied:** it was run on 14 October 2026 against the backup database
  (`ep-long-glitter`), which is the one Oracle uses. **Nothing to run on Oracle.**
- **Check:** in `backend` on Oracle, `alembic current` shows `0018_organization_onboarding (head)`.
- **Original database:** if it is used again after 1 November, run `alembic upgrade head` there.

## 4. Environment (Oracle `backend/.env`)

| Setting | Value | Note |
|---|---|---|
| `AI_TEXT_CONNECTIONS` | the line below | Adds `openrouter_claude` (used) and `anthropic` (ready for later). No secrets in it. |
| `AI_INTERACTIVE_ROUTES` | `["openrouter_claude:anthropic/claude-haiku-5.5","nvidia_nim:configured","nvidia_nim:openai/gpt-oss-20b","groq:qwen/qwen3.8-27b","openrouter:cohere/north-mini-code:free","nvidia_nim:z-ai/glm-5.3","nvidia_nim:poolside/laguna-xs-2.1","nvidia_nim:moonshotai/kimi-k3"]` | Chat: Claude Haiku 5.5 first |
| `AI_COMPLEX_ROUTES` | `["openrouter_claude:anthropic/claude-sonnet-5.5","nvidia_nim:configured","groq:qwen/qwen3.8-27b","nvidia_nim:moonshotai/kimi-k3","nvidia_nim:openai/gpt-oss-20b","nvidia_nim:poolside/laguna-xs-2.1"]` | Planning and task preparation: Claude Sonnet 5.5 first |
| `OPENROUTER_API_KEY` | the key with credit (secret) | Must be the same key as on the laptop, or another key with credit. Without credit, the Claude routes are skipped and the free models answer. |
| `ANTHROPIC_API_KEY` | empty | Optional. Only for the direct Anthropic connection. |
| `DELIVERY_WATCHDOG_SECONDS` | `180` | Optional; this is the default. `0` turns the watchdog off. |
| `GITHUB_APP_PRIVATE_KEY` | the key **on one line**, with `\n` where the line breaks were | podman's `--env-file` has no multi-line values, so the key on Oracle could not be read (`MalformedFraming`) and GitHub tasks stopped with an internal error. This release reads the one-line form (quotes optional). See "Fixing the GitHub key" below. |

The `AI_TEXT_CONNECTIONS` line, on one line:

```
AI_TEXT_CONNECTIONS={"groq":{"base_url":"https://api.groq.com/openai/v1","hosting_provider":"groq","credential_ref":"groq","schema_mode":"groq_strict","enabled":true,"data_allowed":true},"hf_featherless":{"base_url":"https://router.huggingface.co/v1","hosting_provider":"featherless-ai","credential_ref":"huggingface","enabled":true,"data_allowed":true,"billing_required":true,"billing_allowed":false},"hf_novita":{"base_url":"https://router.huggingface.co/v1","hosting_provider":"novita","credential_ref":"huggingface","enabled":true,"data_allowed":true,"billing_required":true,"billing_allowed":false},"anthropic":{"api_format":"anthropic_messages","base_url":"https://api.anthropic.com/v1","hosting_provider":"anthropic","credential_ref":"anthropic","schema_mode":"json_schema","enabled":true,"data_allowed":true,"billing_required":true,"billing_allowed":true},"openrouter_claude":{"api_format":"openrouter_claude","base_url":"https://openrouter.ai/api/v1","hosting_provider":"anthropic","credential_ref":"openrouter","schema_mode":"json_schema","enabled":true,"data_allowed":true,"billing_required":true,"billing_allowed":true}}
```

**Fixing the GitHub key.** With the `.pem` file downloaded from the GitHub App settings, this
prints the one-line form. Paste it after `GITHUB_APP_PRIVATE_KEY=` in Oracle's env file:

```bash
awk 'NF {sub(/\r/, ""); printf "%s\\n", $0}' audoryn.private-key.pem; echo
/, ""); printf "%s\n", $0}' audoryn.private-key.pem; echo
```

After restarting, check it. This signs a token locally and sends nothing:

```bash
podman exec audoryn-api python -c "from app.execution.providers.native.github.authentication import app_jwt; app_jwt(); print('GitHub key OK')"
```

**Do not copy** the laptop's tunnel addresses (`QSTASH_*_URL`, `UPSTASH_QSTASH_*_URL`,
`GITHUB_CALLBACK_URL`, `GITHUB_LOGIN_CALLBACK_URL`). Oracle keeps its own
`https://130.61.104.247/...` values. No settings were removed and no defaults changed.

## 5. External services

**GitHub App (Audoryn).** During testing its URLs pointed at the laptop tunnel. Set them back to
Oracle:

| Field | URL |
|---|---|
| Callback URL 1 | `https://130.61.104.247/integrations/github/callback` |
| Callback URL 2 | `https://130.61.104.247/api/v2/auth/github/callback` |
| Setup URL (post installation) | `https://130.61.104.247/api/v2/auth/github/installation/callback` |
| Webhook URL | `https://130.61.104.247/integration-events/github` |

Keep the webhook **Active** with the **Issues** event subscribed.

**QStash.**
- **No changes:** the sweep, outbox drain and heartbeat schedules already point at Oracle.
- **Messages sent to the old tunnel:** they fail there. The work they were for is recovered by
  the sweep, and by Oracle's watchdog after its first start.

**OpenRouter.**
- **Balance:** the account has $5 of credit.
- **Spending cap:** set a spending limit on the key in OpenRouter's settings if you want a hard
  cap.
- **What to expect in Activity:** `anthropic/claude-haiku-5.5` and `anthropic/claude-sonnet-5.5`,
  served by Anthropic only.

**Upstash Redis, E2B, Neon:** nothing to change.

## 6. Deploy steps

1. **Merge:** merge the pull request (section 2).
2. **Laptop:** stop the laptop backend and the Cloudflare tunnel, so only one backend uses the
   database and QStash.
3. **Pull:** on Oracle, pull `main`. There are no new dependencies.
4. **Environment:** update Oracle's `backend/.env` (section 4) and restart the backend.
5. **Frontend:** rebuild and redeploy it as usual. This release adds:
   - the welcome flow;
   - the checklist and guides;
   - the public sample workspace;
   - the phone dark-mode fix.
6. **GitHub App:** point its URLs back at Oracle (section 5).

## 7. Checks after deploying

- **Health:** `GET /health/live` returns 200, and QStash's log shows 200 for `/internal/v1/runtime/sweep`.
- **Chat:**
  - send a message in Conversations;
  - the backend log shows `AI selection accepted … connection=openrouter_claude`;
  - OpenRouter's Activity shows a `claude-haiku-5.5` request.
- **A task:** the run plans and the log shows `claude-sonnet-5.5`.
- **A new account:**
  - sign up;
  - the welcome questions appear;
  - "Set up my workspace" creates the starter worker and its welcome conversation;
  - Home shows the checklist.
- **Existing workspaces:** they show no welcome flow and no checklist.
- **Sample workspace:** `/workspace-preview` opens with sample data.
- **Stuck work:** nothing to check. The log line `Delivery watchdog …` appears only when something
  was actually recovered.

## 8. Rolling back

- **Code:** redeploy `cfff0c3` (main before this release).
- **Claude only:** remove the first entry from `AI_INTERACTIVE_ROUTES` and `AI_COMPLEX_ROUTES`
  and restart. Nemotron then answers first again.
- **Database:** `0018` only adds a table. Older code ignores it, so no downgrade is needed.

## 9. Proof

- **Backend:** 1,539 tests pass, with and without the laptop `.env`. They include new tests for
  the watchdog, reconnect refresh, onboarding, and both Claude connections.
- **Static checks:** ruff, pyright (0 errors) and the CI verify scripts pass.
- **Frontend:**
  - 156 tests, the build and the 20 workspace browser checks pass;
  - the welcome flow, checklist and guides were checked in light, dark and phone views.
- **Live:** chat answered through Claude Haiku 5.5 on OpenRouter from the laptop backend.
