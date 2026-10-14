# Phase 6: integrations

Written 14 October 2026, for the [improvement plan](../agentgate-improvement-plan.md).

## What changed

1. **Accounts are checked before work starts.**
   - **Check at the start:** on a run's first planning pass, each connected account the task
     uses is checked once, at one credential read per account.
   - **Renewal ahead of expiry:** a token close to expiry is renewed then. Tokens are now
     renewed 5 minutes before they expire, instead of 30 seconds, so a long run does not meet an
     expired token midway. The lead time is set by `INTEGRATION_TOKEN_REFRESH_LEAD_SECONDS`.
   - **Accounts needing reconnection:** if an account needs reconnecting, the task waits before
     doing anything and says so: "GitHub (acme) needs reconnecting before this task can start.
     Nothing has been done yet." Before, this was found at the first action that used the
     account, after other steps had run. Resuming checks again.
   - **Temporary problems:** if a renewal is already running or coordination is down, the task
     starts anyway, and the action itself waits.
2. **Discovery is one cheap read, with a clear refresh.**
   - **Saved, not re-fetched:** what each connection offers was already saved and only fetched
     on connect or "Rediscover".
   - **Fewer queries:** the list used by chat and worker setup now takes five queries, whatever
     the number of resources. Before, it took about four per resource; with 20 resources that
     was more than 40. The output is identical.
   - **Vanished resources:** a refresh now marks resources the provider no longer returns
     (deleted, or access removed) as unavailable. Before, they stayed "healthy" for ever. They
     are never chosen for a task. If a request names one, the reply says it is no longer
     available and how to fix that.
   - **In the workspace:** the Rediscover notice says how many resources are available and how
     many are gone.
3. **Provider rate limits are waited out, together.**
   - **One wait per account:** when a provider tells one action to slow down, every action on
     that account waits until the provider's stated time. Before, only GitHub did this; Slack,
     Gmail, Calendar and Drive actions kept calling.
   - **Google:** a 403 that reports a rate limit or quota (for example `rateLimitExceeded`) is a
     wait, not "permission denied".
   - **Slack:** `token_revoked`, `invalid_auth` and similar errors ask for reconnection, instead
     of failing as a bad request. `ratelimited` is a wait.
   - **Waiting is not failing:** waiting for a provider limit or a free slot no longer uses up
     the two failure retries. Before, a third wait in a row failed a write, and the whole task
     with it. Waits have their own bound, 12 by default (`INTEGRATION_RATE_LIMIT_MAX_WAITS`).
   - **Plain messages:** for example, "GitHub is limiting requests right now. I'll continue in
     about 3 minutes. Completed steps are saved." The activity line says the same, instead of
     "Recovering the pending step".
4. **Event triggers, tested end to end.**
   - **Edits keep the trigger:** an event subscription now belongs to the job, so editing the job
     keeps its trigger. Before, any edit silently disabled it. Authority is still checked on the
     current version's own grant.
   - **Action filters:** a subscription can name an action, for example `issues.opened`, so a
     job reacts to new issues without running on every edit, label or comment. A plain `issues`
     still matches every issue event.
   - **Nothing left pending:** events that arrive while matching is not allowed, such as a
     suspended installation, are recorded as ignored.

## Which providers send events

| Provider | Event intake | Notes |
|---|---|---|
| GitHub | Yes | The GitHub App webhook. Every delivery has a signed, deduplicated inbox row, and our own writes are not treated as new events. Installation and repository removals withdraw authority. |
| Slack, Gmail, Google Calendar, Google Drive | Not yet | Jobs on these run on schedules or on request. |

**How a GitHub event becomes work:**

1. `POST /integration-events/github`: the signature is checked first, and the body is limited to
   256 KB.
2. The event goes to the inbox once per delivery. A repeated delivery does nothing.
3. A signed processing message (`/internal/v1/integrations/process`) matches subscriptions by
   repository and by event (or event and action).
4. One work item is queued per job. The event's text is passed as untrusted evidence, never as
   instructions.

Subscriptions are set with `POST …/integration-jobs/{job}/events`, which takes `resourceId`,
`eventType` (for example `issues.opened`) and `resultsThreadId`. They need the person's standing
authority for that exact resource.

## Proof

- **[test_integrations_phase6.py](../../backend/tests/test_integrations_phase6.py), 31 tests:**
  - **rate limits:** waits keep the failure budget, have their own bound and honour the
    provider's time; the wait messages and activity line; Google and Slack error mapping; one
    account's slow-down pausing its other actions but not other accounts;
  - **accounts:** renewal ahead of expiry; reconnection found before work starts; healthy or
    busy accounts checked once;
  - **discovery:** the catalog identical to the old one in 5 queries instead of more than 40;
    a refresh marking vanished resources; a vanished resource named instead of chosen;
  - **events, end to end in SQLite**, through the real signature check, inbox and matching:
    - queued once despite a repeated delivery;
    - evidence marked untrusted;
    - `issues.opened` ignoring edits;
    - an edited job still triggered;
    - no work without current authority;
    - forged signatures rejected;
    - suspended installations recorded as ignored;
    - removed repositories losing their grants.
- **Offline benchmark:** unchanged across all 12 tasks.
- **Full suite:** all 1,403 backend tests pass; frontend tests, build and the 20 workspace checks
  pass.

## Deploying

- **No migration.**
- **Two optional settings,** with the defaults above.
- **First refresh:** after deploying, a connection's first Rediscover may mark long-gone
  resources as unavailable. That is the intended correction.
