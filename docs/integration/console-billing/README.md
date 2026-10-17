# Console billing channel (v1)

How AgentGate asks the Console about a workspace's billing (commercial plan, phase 5; design:
`docs/plans/commercial-payments-plan.md`). AgentGate checks who may buy; the Console owns prices,
payment providers and subscriptions. Separate from the signed reads and the entitlement channel:
its own routes, signature label, key and scopes.

- **Direction:** AgentGate -> Console, under `/api/v1/internal/agentgate/billing/v1` on the Console.
- **Signing:** `audoryn-billing-v1`: HMAC-SHA256 over the label, key id, audience, scope, method,
  target (path and query as sent), timestamp, nonce and the body's SHA-256, one per line.
  `signing-vector.json` is a test-only vector both sides reproduce (a copy is in the Console's
  `fixtures/agentgate-billing/`); its key must never be configured anywhere. Requests older than
  60 seconds and repeated nonces are refused (Redis on the Console, failing closed).
- **Scopes:** `billing.read` (GET `/catalog?organizationId=`, GET `/checkouts/{id}?organizationId=`),
  `billing.checkout` (POST `/checkouts`, POST `/manage`).
- **Answers:** `{version: 1, data}` or `{version: 1, error: {code, message}}`; codes `invalid`,
  `unauthorized`, `not_found` (also: channel off), `not_offered`, `manage_existing`,
  `rate_limited`, `unavailable`.

## Requests

| Request | Body / query | Answer |
|---|---|---|
| GET `/catalog` | `organizationId` | Public active plans with a positive price in a currency a switched-on provider serves; the providers (currencies, checkout hosts, Paddle's public client token); the workspace's current subscription |
| POST `/checkouts` | `organizationId`, `userId`, `email`, `planCode`, `billingCycle` (`monthly`/`annual`), `currency`, `requestKey` | The checkout (`id`, `status`, `provider`, `url`, amount, `expiresAt`). The same `requestKey` returns the same checkout |
| GET `/checkouts/{id}` | `organizationId` | Its status: `open`, `completed`, `expired`, `failed` |
| POST `/manage` | `organizationId` | `url`: the provider's own page (Paddle customer portal, Paystack management link) |

## Settings

| AgentGate | Console |
|---|---|
| `CONSOLE_BILLING_ENABLED` (default `false`) | `AGENTGATE_BILLING_ENABLED` (default `false`) |
| `CONSOLE_BILLING_ORIGIN` (the Console API, bare HTTPS origin) | `AGENTGATE_WEB_ORIGIN` (AgentGate's web address, for return pages) |
| `CONSOLE_BILLING_KEY_ID`, `CONSOLE_BILLING_KEY_SECRET` (base64url, 32+ bytes) | `AGENTGATE_BILLING_KEY_ID`, `AGENTGATE_BILLING_KEY_SECRET` (the same pair) |
| `CONSOLE_BILLING_AUDIENCE` (default `console`), `_TIMEOUT_SECONDS` (15) | `AGENTGATE_BILLING_AUDIENCE` (default `console`) |

Provider keys exist only on the Console: `PAYSTACK_SECRET_KEY`, `PAYSTACK_MODE`,
`PAYSTACK_WEBHOOK_IPS` (optional); `PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`,
`PADDLE_CLIENT_TOKEN`, `PADDLE_ENVIRONMENT`. Test keys are refused in production and live keys
outside it.

## Provider dashboards

- **Webhook URLs** (Console API): `https://<console-api>/api/v1/internal/billing/webhooks/paystack`
  and `.../paddle`. Paddle events to send: `transaction.completed`, `transaction.paid`,
  `subscription.*`, `adjustment.created`, `adjustment.updated`.
- **Paddle default payment link:** Paddle requires one (a public HTTPS address, never localhost;
  approved by Paddle for live). Use `https://<agentgate-web>/app/plans/pay`. People are
  always sent to Audoryn's own Plans page with the transaction (`?_ptxn=`), where Paddle.js opens
  the checkout, so in development that page can be `localhost` (Paddle Sandbox).
- **Paystack callback:** set per checkout by the Console (`/app/plans?checkout=<id>`).
