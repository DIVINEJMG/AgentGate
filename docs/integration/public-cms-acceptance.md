# Public CMS implementation acceptance

Verified locally on 10 October 2026. This report concerns AgentGate's public frontend only. Console remains the content owner; no customer-backend CMS work, database migration, cloud change, credential change, live import, publication or deployment was performed.

## Delivered files

- `frontend/content-seed/`: complete draft seed, three-photo media manifest, captured original components, typed fallback data, transport schema, inventory and source-to-contract coverage report.
- `frontend/src/public/content/`: generated transport types, local adapters, semantic validation, release loader, bounded caches, stable navigation, approved icon mappings, GLB validation and private preview adapter.
- All twelve existing public renderers: `PublicSite`, `HomeHero`, `HomeJourney`, `HomeOperatingView`, `HomeGateScene`, `ProductShowcase`, `SolutionsShowcase`, `SecurityShowcase`, `PricingShowcase`, `ResourcesShowcase`, `CompanyShowcase` and `LegalShowcase`.
- New public renderers: `PublicExperience`, `PublicPreview`, `ResourcePage`, `PublicModel`, `entry-server.tsx` and optional-media styling.
- Public entry/hosting configuration: `App.tsx`, `main.tsx`, the absolute module URL in `index.html`, Vite middleware, build/preview scripts, `api/public-site.mjs`, `vercel.json`, public environment placeholders, package scripts, public type-check configuration and generated-artifact ignore rules.
- Seed capture/generation/validation/coverage scripts, draft importer and its resumable reconciliation helper, fixture server, browser/function acceptance scripts and focused tests.
- `docs/integration/console-public-cms.md`: settings, contracts, hosting, cache/removal behaviour, importer commands and preview integration.

Authenticated workspace files, backend files and unrelated edits visible in the shared checkout were preserved.

## Content completeness and ownership

The seed contains nine pages, seventeen referenced copy records, three existing photographs, header/footer navigation and one shared branding global. The coverage report maps 656 distinct baseline fields. Copy is retained individually, including emphasis fragments, legal dates, labels and accessibility text. Stable collection IDs drive selections and ordering. No articles were invented; the new resource-detail renderer is exercised with a labelled local fixture.

Design, CSS, typography, approved branding, icon implementations, procedural geometry and animation remain code-owned. Console data cannot change customer permissions, subscriptions or entitlements. Verified published dependencies are accepted as a complete set; malformed content uses the last verified release or captured static baseline. Confirmed removal produces 404.

## Executed commands

Run from `C:\Users\divin\AgentGate\frontend` unless stated otherwise.

| Command | Actual result |
|---|---|
| `npm run typecheck` | Passed on the final public source. An earlier run observed missing/incompatible authenticated-workspace files during concurrent edits; those files were not changed here. |
| `npx tsc --noEmit -p tsconfig.public.json` | Passed. |
| `node --test tests/publicCms.test.mjs tests/publicSeedImporter.test.mjs` | 30 passed, zero failures, including typed disclosure, legacy routes and complete navigation anchors/selections. |
| `npm test` | Latest whole-suite run: 70 passed, one failed, 71 total. `tests/operationModel.test.mjs` expected one preview runtime run and received two. This unrelated operations-preview failure was left outside public scope. The additional public URL-safety/disclosure tests were run through the focused suite. |
| `npm run build` | Final client and Vite SSR builds passed. Existing Three.js chunk-size warning remains. |
| `node scripts/verify-public-host.mjs` | Passed against the built frontend function: runtime update without rebuilding, zero warm storage reads, resource detail, 301 redirect, sitemap, unknown-route 404, removal 404, uncached preview and reserved application shell. No external writes. |
| `node scripts/import-public-content-seed.mjs` | Validated all page/global schemas and local media checksums; dry-run reported zero external calls and no publication. |
| `node scripts/report-public-content-coverage.mjs` | Nine pages, 656 fields, zero unexplained inventory gaps. |
| `npm audit --omit=dev --audit-level=high` | Failed: existing `jspdf` critical advisories and `xlsx` high advisories. No dependency upgrade was performed. |
| Scoped `git diff --check` | Passed. |

## Efficiency measurements

- Final all-nine-page loader fixture: 28 storage requests, 161,488 transferred bytes and 41 cache hits. Repeating a verified page adds zero storage requests.
- Built-function scenario across several release versions: 51 storage requests and 317,927 bytes. Warm home requests add zero storage reads. The fixture measures application behaviour, not live provider latency.
- Public entry JavaScript: 482.58 KB uncompressed, about 148.29 KB gzip. Loading authenticated workspace code separately reduced the entry from the preceding approximately 937 KB build. Public entry CSS is 175.31 KB, about 32.80 KB gzip; authenticated workspace CSS is separate. Three.js and the GLB viewer remain lazy.
- Cache limits: 128 immutable documents/16 MiB, eight retained page dependency sets and 32 HTML entries per frontend instance. Graphs are capped at 100 records/8 MiB; individual documents at 4 MiB. Sitemap verification uses batches of four, requires one release and stops on incomplete evidence.

## Remaining acceptance gates

1. Review the seed and configure the public storage/site origins in the frontend host environment. Delivery is disabled by default.
2. Authorize the draft importer separately. Review and publish through Console's existing editorial workflow; the importer never publishes.
3. Wire Console's restricted parent-window preview handshake. Historical preview release references that cannot be resolved fail explicitly. Draft media without published metadata cannot claim format verification.
4. Authorize a Vercel deployment and verify its function packaging, filesystem/rewrite precedence, runtime settings, media CORS and normal URLs. Local Node acceptance cannot establish live hosting behaviour.
5. Exercise removal against the actual published pointer and frontend instances. Offline copies/outages prevent an absolute immediate-removal guarantee.
6. Resolve the unrelated operations-preview test and existing dependency audit findings in their own scope before treating the entire repository as release-ready.

No Neon SQL is needed for this frontend-only implementation.

## Rendered screens

`$env:PUBLIC_CMS_BROWSER_CHANNEL='msedge'; node scripts/verify-public-cms-browser.mjs --production` passed against the built public frontend: all nine pages at 1440, 768 and 390 px (27/27 comparisons), with no browser runtime errors. Text, links, accessibility labels, document overflow and rendered pixels passed; the report records five one-level edge pixels on mobile Terms within the documented exception. Procedural canvases and the existing timed scroll control are excluded from pixel parity.

The same browser run exercised hero controls, operating-tab keyboard navigation, four journey chapters, solution selection/deep-link reload, security demonstrations, pricing FAQ disclosure, legal anchors and mobile navigation. Storage fixture totals were 107 requests and 4,708,845 bytes, including media across repeated viewport sessions. These are fixture totals, not a per-visitor production estimate.

Generated screenshots/report: `frontend/artifacts/public-cms/` (ignored local artifacts). Examples: `product-1440-published.png`, `pricing-390-published.png`; complete results: `report.json`. No claim of live Console/Vercel acceptance is made.

After that browser run, the demonstration caption was connected directly to its typed disclosure instead of the duplicate captured copy field, and hydration was restricted to matching public entry routes. The original/fallback wording remains identical. The final 30-test run rechecked exact original/fallback/published text across all nine pages, typed-disclosure updates and legacy route selection.

The seed was also checked through Console's actual `app.domain.cms.release_graph.validate_release_graph` using its existing Python environment with `-B` (no bytecode writes). The initial check identified missing navigation section identities. Those identities were added to the AgentGate seed, and the check then passed for all pages, records, navigation and globals. Media verification flags in that dependency-graph check were explicitly local fixtures; actual upload-byte verification remains Console's responsibility during the later import. No Console files, database records or external services were changed.
