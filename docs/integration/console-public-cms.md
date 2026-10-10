# AgentGate public content integration

## Ownership and execution

Console owns content, revisions, media verification and editorial publication. AgentGate's frontend owns rendering, layout, animation, icons and interaction rules. The customer backend has no CMS database connection or delivery job. No Docker is needed.

The frontend Node function renders the same React public components used in the browser. Authenticated application screens remain client rendered. Vercel uses `api/public-site.mjs`, the client build in `frontend/dist`, and the Vite SSR build in `frontend/server-dist`. Static assets continue to be served by the frontend host.

The build saves its HTML template as `server-dist/template.html` and removes the generated public `dist/index.html`. Vercel gives existing static files precedence over rewrites; keeping the template outside the public output ensures `/` reaches the SSR function. `npm run preview` serves the built function and assets locally on port 4173. The catch-all rewrite and function packaging require a separately authorized Vercel deployment check. See [Vercel rewrite precedence](https://vercel.com/docs/project-configuration/vercel-json).

## Runtime configuration

Set these in the frontend host environment. `.env.example` documents local placeholders. Active environment files are not modified by this implementation.

```dotenv
PUBLIC_CONTENT_ORIGIN=https://YOUR_CONSOLE_PUBLIC_STORAGE_ORIGIN
PUBLIC_SITE_ORIGIN=https://YOUR_AGENTGATE_PUBLIC_SITE
PUBLIC_CONTENT_ENABLED=false
PUBLIC_CONTENT_CONTRACT=audoryn.public.v1
PUBLIC_CONTENT_DEFAULT_LOCALE=en
PUBLIC_CONTENT_POINTER_CACHE_SECONDS=60
PUBLIC_CONTENT_FETCH_TIMEOUT_SECONDS=5
PUBLIC_CONTENT_PREVIEW_ORIGIN=https://YOUR_CONSOLE_ORIGIN
```

Use the origin of Console's existing public storage, not its administrative API. Origins must be credential-free HTTPS; localhost HTTP is accepted for local checks. The runtime bootstrap exposes only these safe settings and published content. Import authentication never enters the public bundle. Changing the origin does not require a source edit or a frontend rebuild; host environment changes require the host to restart the function with the new environment.

## Contracts and fallback

Transport schema: `frontend/content-seed/schema.json`; generated types: `frontend/src/public/content/publicContent.generated.ts`. Local component props are adapted through `ContentContext.tsx`. The contract is `audoryn.public.v1`.

`frontend/content-seed/baseline/` preserves the original source for comparison. `static-copy.json`, `collection-bindings.json` and `demonstration-baseline.json` capture its local fallback. The complete seed is `audoryn-public.seed.json`; media paths/checksums are in `media-manifest.json`. Source locations and individual copy fields are in `source-inventory.json`; the generated coverage report maps them to revisioned records and consumers. Text fragments preserve emphasis and line breaks in code-owned markup; they are individual editorial fields, not a concatenated JSX transcript.

Published navigation, global content, pages, records and media are pinned to one verified release. Required fields are validated as a set. A malformed or incomplete page is rejected as a whole; its missing fields are not filled individually from static data. Last verified content is retained on transient failures. Without verified content, the original local baseline renders. Confirmed removals return 404 and do not resurrect static pages.

Hero scenes retain autonomy/control/oversight and journey phases retain defined/policy/approval/recorded. Solutions, operating tabs, plans and demonstration records use stable identities. Pricing remains informational: no CMS value grants subscription entitlements. Demonstrations are illustrative.

## Delivery, cache and removal

The loader reads `/cms-public/current.json`, verifies immutable release/page SHA-256 checksums and resolves only required record documents. Server fetches are restricted to the configured storage origin and `/cms-public/` prefix, with redirect following disabled. Pointer requests use ETag revalidation after the configured lifetime.

Per frontend instance: at most 128 documents/16 MiB, 8 retained page sets and 32 rendered HTML entries. A page dependency graph is capped at 100 records/8 MiB; a document at 4 MiB. Browser page sets are held in an 8-entry memory cache with the pointer lifetime. Requests are deduplicated and obsolete navigation responses are rejected. No CDN, Redis or paid cache service is introduced. HTML and same-origin content responses use `no-store`; verified immutable documents are cached inside the application.

Removal propagates after a compatible current pointer is observed. Outages, offline copies and already-open tabs prevent an absolute immediate-removal guarantee. Console owns storage removal verification; AgentGate does not claim to invalidate an external CDN. Urgent removal requires confirming the new pointer, clearing/restarting frontend instance caches when necessary, and verifying the resulting 404 from the public frontend.

## Routes and metadata

Normal routes: `/`, `/product`, `/solutions`, `/security`, `/pricing`, `/resources`, `/company`, `/privacy`, `/terms`. Published editorial details use their released slug. Unknown paths return HTTP 404; released redirect resources return HTTP 301/302. Legacy public hashes remain supported. Solution selections use `?selected=<stable UUID>`; section targets use anchors. `/api`, `/app`, `/login`, `/signup`, `/workspace-preview`, `/invite`, `/auth`, `/_public` and `/_cms` are reserved.

SEO HTML, canonical URLs, robots and sitemap are generated on the frontend host. The customer backend is not involved in rendering or crawler metadata. Conditional content refresh is through `/_public/content?path=...`.

## Media and restricted preview

Existing local images remain available for fallback. Published assets are immutable and have verified metadata. Resource renderers support images, video/posters/captions, downloads and self-contained GLB. GLB downloads are lazy and bounded to 50 MiB; external dependencies and unsupported required extensions are rejected before Three.js parsing. Failure retains the supplied poster. Existing procedural graphics and code-owned branding stay in code.

`/_cms/preview` is private in intent, uncached and excluded from indexing. It accepts Console's pinned content-inspection payload through an origin-checked parent-window handshake with an in-memory nonce. Signed private URLs remain in memory and are never placed in receipts or published content. Invalid preview data displays an explicit failure. The Console iframe host must implement the `audoryn.cms.preview.ready` / `audoryn.cms.preview` handshake; that Console-side wiring is a separate acceptance gate because Console is read-only in this task. Preview reference lookup supports the currently pinned published release; historical pins that cannot be resolved fail explicitly.

## Draft importer

From `frontend`:

```powershell
node scripts/import-public-content-seed.mjs
```

This is a dry-run with zero external calls. For a later explicitly authorized import, obtain a Console administrator session token with CMS page, asset, navigation, global-content and release permissions, then set process-local `CONSOLE_IMPORT_ORIGIN` and `CONSOLE_IMPORT_TOKEN` and use `--apply`. Do not paste the token into source or a receipt.

The importer uploads and verifies media, allocates page identities, remaps references, saves draft revisions, prepares navigation/globals and creates a draft release. Console requires approved page revisions before release inclusion, so the importer saves the proposed item IDs and ordering in the receipt's `pendingReleaseItems` instead of adding unapproved items. Success reports `draft_prepared` with `releaseAssembly: pending_editorial_approval`; the release is not assembled or ready to publish. An editor must review/approve the revisions and assemble the release in Console afterward. The importer never approves, schedules, publishes or activates. `content-seed/import-receipt.json` records only IDs, fingerprints and progress. Keep it private and preserve it for retries. Lost acknowledgements are reconciled against Console inventories. Conflicting drafts stop without overwrite. If an upload never reached storage and its upload URL is lost, verification stops safely; resolve that asset through Console rather than replaying an unknown upload.

## Local acceptance commands

```powershell
Set-Location C:\Users\divin\AgentGate\frontend
npm run typecheck
npm test
npm run build
node scripts/verify-public-host.mjs
npm audit --omit=dev --audit-level=high
node scripts/import-public-content-seed.mjs
$env:PUBLIC_CMS_BROWSER_CHANNEL = 'msedge'
node scripts/verify-public-cms-browser.mjs
node scripts/verify-public-cms-browser.mjs --production
```

Browser checks use an existing Playwright installation selected through `PUBLIC_CMS_BROWSER_RUNTIME` and an installed Chrome/Edge channel; no testing dependency or browser download is required. Their local fixture serves a verified equivalent release and records screenshots/report under ignored `frontend/artifacts/public-cms/`.

Live import, editorial review, public storage configuration, preview-host wiring, Vercel deployment and production removal verification remain separately authorized steps. Local fixture success does not establish live infrastructure availability.

## Verification boundaries

The browser comparison masks procedural canvas pixels, hides the existing fixed scroll rail and makes native scrollbar colours transparent while retaining document geometry. These are nondeterministic presentation surfaces. Hero controls and document scrolling are exercised separately. Page pixels, text, links and accessibility labels are compared exactly, with one narrow rasterization exception: at most 16 pixels within the outermost six right-edge columns may differ by one colour level (1/255). This observed Edge shadow variance is recorded in the report; all other pixel differences fail. Screenshots and actual results are recorded in `public-cms-acceptance.md`.

`npx tsc --noEmit -p tsconfig.public.json` checks the public implementation independently when concurrent authenticated-workspace work blocks the full frontend checks. It does not replace the required whole-project check. Public visitors load the public entry first; authenticated application code is loaded only when entering its routes. Existing application code and API behaviour are retained.
