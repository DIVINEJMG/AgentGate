# Active local public-content configuration

Verified on 2026-10-10. Console owns content; AgentGate's frontend host reads
published artifacts directly. No CDN or customer-backend CMS jobs are used.

## Storage separation

- Neon account: **Audoryn Console**.
- Project: `Audoryn-console-staging` (`divine-wind-98873390`).
- Active branch: `production` (`br-green-field-b2p7so44`).
- Private uploads and verified sources: `audoryn-console-cms-staging`.
- Published-only public-read destination: `audoryn-console-public`.

Public origin (include the bucket path):

```text
https://br-green-field-b2p7so44.storage.c-6.eu-central-1.aws.neon.tech/audoryn-console-public
```

Console's `backend/.env` sets `CMS_PUBLIC_STORAGE_BUCKET`, `CMS_PUBLIC_ORIGIN`,
`CMS_PUBLIC_DISTRIBUTION_ENABLED=true`, and
`CMS_DELIVERY_PREPARATION_ENABLED=true`. Existing storage credentials remain
server-side; the private bucket remains private.

AgentGate's `frontend/.env.local` sets the same public origin as
`PUBLIC_CONTENT_ORIGIN`, with `PUBLIC_CONTENT_ENABLED=true`. Local site origin
is `http://localhost:5173`; Console preview origin is `http://localhost:3000`.
These local origins must be replaced by actual hosted origins when deploying.
Runtime environment settings override `.env.example`; example files alone do
not configure Vite.

## Verified result

- Published release `364a2117-9b19-4197-ae83-7b5621b7c84c`: v1 acknowledged,
  activeVersion 1, one delivery attempt.
- All 26 content documents and 3 media artifacts verified before pointer activation.
- Anonymous current pointer: HTTP 200. Private verified media: HTTP 403.
- All nine routes loaded `source=published` through AgentGate's actual loader.
- Loader measurement: 28 requests, 221,316 JSON bytes, 33 cache hits;
  repeated home load required zero new requests. Initial full nine-route check:
  8,709 ms on the local connection, not a production latency guarantee.
- Running frontend home HTML and `/_public/content?path=/` confirmed published content.

Repeat the read-only check:

```powershell
Set-Location C:\Users\divin\AgentGate\frontend
node scripts/check-public-content-live.mjs
```

Check the pointer by appending `/cms-public/current.json` to the public origin,
not to the Console UI URL.

Restart Console's backend after environment changes if the panel still says
transport disabled; its settings are cached per process. Restart AgentGate's
frontend if its dev process has not reloaded the environment.

The static fallback remains available during unavailable/unverified content.
Confirmed removals remain authoritative. Browser/offline copies and application
caches mean urgent removal cannot promise immediate deletion from every visitor.
No hosted deployment or visual redesign was performed by this configuration.
