# Console entitlement channel (v1)

How the Console delivers plans to AgentGate (commercial plan, phase 3). Separate from the signed
reads (`docs/integration/console-reads/`) and the CMS link: its own routes, signature label, key,
settings and rate limit.

- **Contract:** `contract.v1.json` (snapshot, template, apply result, status page). A copy lives in
  the Console at `fixtures/agentgate-entitlements/`; both sides' tests read it.
- **Signing:** `audoryn-entitlements-v1`: HMAC-SHA256 over the label, key id, audience, scope,
  method, target, timestamp, nonce and the body's SHA-256. `signing-vector.json` is a test-only
  vector both sides must reproduce; its key must never be configured anywhere.
- **Scopes:** `entitlements.write` (POST /snapshots, POST /template) and `entitlements.read`
  (GET /status). A key is granted both.

## Settings

| AgentGate | Console |
|---|---|
| `CONSOLE_ENTITLEMENTS_ENABLED` (default `false`) | `AGENTGATE_ENTITLEMENTS_ENABLED` (default `false`) |
| `CONSOLE_ENTITLEMENTS_KEY_ID`, `CONSOLE_ENTITLEMENTS_KEY_SECRET` (base64url, 32+ bytes) | `AGENTGATE_ENTITLEMENTS_KEY_ID`, `AGENTGATE_ENTITLEMENTS_KEY_SECRET` (the same pair) |
| `CONSOLE_ENTITLEMENTS_PREVIOUS_KEY_ID`, `_SECRET` (rotation) | – |
| `CONSOLE_ENTITLEMENTS_AUDIENCE` (default `agentgate`) | `AGENTGATE_ENTITLEMENTS_AUDIENCE` (default `agentgate`) |
| `CONSOLE_ENTITLEMENTS_CLOCK_SKEW_SECONDS` (60), `_RATE_PER_MINUTE` (120) | `AGENTGATE_ENTITLEMENTS_ORIGIN`, `_TIMEOUT_SECONDS` (10), `_RECONCILE_MINUTES` (60) |

The key is new and not shared with the reads (`CONSOLE_READS_*` / `AGENTGATE_READS_*`).

## Behaviour

- AgentGate applies a snapshot only when its version is higher than the one it holds; the same
  version answers `already_applied`, an older one `stale`. Resending is always safe.
- The Console stages an organisation's plan in its outbox in the same transaction as the change
  (subscription, plan, plan entitlements, commercial settings) and a worker sends it within
  seconds, retrying with backoff (30 s doubling to an hour, 10 attempts).
- Every reconcile period the Console reads `GET /status` and resends any organisation whose
  applied version is behind, and the template if AgentGate holds another version.
- New workspaces start on the template (the Free plan) the moment they are created; the Console
  sends an organisation's own snapshot once an administrator sets its subscription.
- When a template arrives, every workspace that has no plan yet starts on it too (the cutover).
- Snapshots and the template may carry `planName`, the Console plan's name, which AgentGate shows
  on the workspace's "Usage & billing" page. Deploy AgentGate first: an older one refuses it.
- The cutover: `python -m app.bootstrap.entitlement_delivery` in the Console's backend stages every
  subscribed organisation's plan and the template; safe to repeat.
