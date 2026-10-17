# Commercial plan, phase 5: payments (Paystack and Paddle, ready but off)

Written 16 October 2026 after the owner's decision: **Paystack** for customers paying in naira and
**Paddle** (merchant of record) for everyone else, side by side. The owner is in Nigeria, not yet
registered, with global customers; registration, bank accounts and provider accounts are done by
the owner in parallel. Everything is built and proven with the providers' **test modes** (Paystack
test keys, Paddle Sandbox) and stays **off** until the owner enables it in the Console.

## 1. Rules

1. **The Console controls prices.** Each plan's price per currency and per billing cycle, which
   provider serves which currency, and whether a provider is on are Console data, changed by an
   administrator and audited. Providers never hold the source price: the Console creates the
   provider's price when it is first sold and a new one when the Console's price changes.
2. **Nothing is granted by a browser.** A plan changes only after the provider's signed webhook has
   been verified **and** the payment has been confirmed by calling the provider's API (amount,
   currency, plan and checkout must match what the Console offered).
3. **No card data touches Audoryn.** Checkout is hosted by the provider.
4. **The CMS link and the signed reads are not touched.** The checkout command between AgentGate
   and the Console is its own signed channel, with its own label, key, scopes and rate limit.
5. Existing rules stand: running tasks finish at any plan change; Free never expires.

## 2. How it works

```
AgentGate (workspace owner/admin)          Console                               Provider
"Usage & billing" -> Upgrade
  signed POST /checkout  ---------------->  checks plan, price, currency route
                                            creates checkout record (expires)
                                            ensures provider price  ------------>  plan/price
                                            starts hosted checkout  ------------>  checkout URL
  <-------------------------------- URL (host checked against the provider's)
person pays on the provider's page
                                            POST /billing/webhooks/<provider>  <--  signed event
                                            verify signature, de-duplicate,
                                            confirm with provider API  -------->  truth
                                            subscription change (audited, ledger)
                                            -> entitlement snapshot to AgentGate (phase 3)
"Usage & billing" shows the new plan within seconds
```

- **Currency route:** the workspace chooses a currency the Console offers; the Console's route
  picks the provider (for example NGN -> Paystack, USD/EUR/GBP -> Paddle).
- **Renewal / failed payment / cancellation / refund** arrive as webhooks and become subscription
  changes: renewal moves the period; a failed renewal is `past_due` (the Console's grace applies);
  a cancellation at period end sets `cancelAtPeriodEnd`; when a paid subscription ends, the
  workspace goes back to the plan new workspaces start on (Free), never to "no plan".
- **Manage billing:** a signed request returns the provider's own management page (Paddle customer
  portal, Paystack subscription management link) for card changes and cancellation.

## 3. Data (Console migration `0010_payments`)

| Table / column | Purpose |
|---|---|
| `billing_plan_prices` (plan, currency, monthly, annual) | Prices in currencies other than the plan's own (the plan's own currency and prices stay on the plan) |
| `billing_provider_prices` (provider, plan, currency, cycle, amount, provider refs, archived) | The provider price made for one Console price; a changed Console price gets a new one |
| `billing_checkouts` (id, organisation, plan, cycle, currency, amount, provider, status, provider reference, expires) | Every checkout the Console started; a payment must match one |
| `commercial_settings.payment_providers` | Per provider: on/off and the currencies it serves |
| `billing_subscriptions.provider_synced_at` | The provider time of the last applied event; older events never overwrite newer state |
| actor columns made nullable (`created_by`, `updated_by`, `requested_by`, `recorded_by`) | Empty means "recorded from the payment provider" (audit records name the provider) |

## 4. Security

| Threat | Control |
|---|---|
| Forged webhook | Signature over the **raw body**, constant-time compare: Paystack HMAC-SHA512 (`x-paystack-signature`, secret key); Paddle HMAC-SHA256 over `ts:body` (`Paddle-Signature`, per-destination secret), timestamp within 5 minutes. Optional source-IP allowlist (Paystack publishes its webhook IPs). Body limit 256 KB. Unsigned or invalid: 401, nothing stored |
| Replayed or duplicated webhook | Unique (provider, event id) with the payload hash; a repeat answers 200 and does nothing. Provider times order events (`provider_synced_at`) |
| Tampered webhook content (valid signature, wrong meaning) | The webhook only says "look"; the Console fetches the transaction/subscription from the provider API and checks amount, currency, plan price and checkout id against its own checkout record |
| Paying less than the price / another plan | The Console sets the amount and the provider price; the browser sends only plan code, cycle and currency. Mismatch: payment recorded as `failed`, plan unchanged, audit and log warning |
| Someone buying for another workspace | Only AgentGate (signed, scope `billing.checkout`) can start a checkout, after checking the person's `organizations.manage` permission in that workspace; the checkout record binds organisation and plan |
| Open redirect / phishing | Return URLs are built by the Console from its configured AgentGate web origin, never from the request; AgentGate opens a checkout URL only if its host is the provider's (`checkout.paystack.com`, the Paddle payment page on Audoryn's own domain) |
| Stolen or leaked keys | Keys only in environment settings (`SecretStr`), never in the database, logs, audit or responses; separate keys per provider and per channel; **test keys refused in production and live keys refused outside it** (key prefix checked against the configured mode) |
| Checkout spam / cost abuse | Rate limits per organisation in AgentGate and in the Console; open checkouts expire (1 hour) and are reused for the same request |
| Silent failures | Every provider event and decision is in the webhook log and ledger; failures are `failed` with a sanitised message, visible in the Console |
| Outages | Webhooks are idempotent and providers retry; a provider being down never changes a plan |

## 5. Settings (all off by default)

Console: `PAYSTACK_SECRET_KEY`, `PAYSTACK_MODE` (`test`|`live`), `PAYSTACK_WEBHOOK_IPS` (optional);
`PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_CLIENT_TOKEN` (public), `PADDLE_ENVIRONMENT`
(`sandbox`|`production`); `AGENTGATE_WEB_ORIGIN` (return pages); `AGENTGATE_BILLING_*` (inbound
command key). AgentGate: `CONSOLE_BILLING_*` (origin, key). A provider is used only when its keys
are set **and** an administrator has switched it on in the Console.

## 6. Proof

Tests on both sides (signatures with the providers' documented formats, every refusal, replays,
mismatched amounts, out-of-order events, the full lifecycle); a simulator run with both providers'
test modes once the owner's test accounts exist; the existing reads, CMS and entitlement tests
unchanged.
