# Public site redesign and content model v2: implementation plan

Status (11 October 2026), branch `feature/public-site-v2`: Phases 0–1 done (copy approved);
Phase 2 replaced by the approved [design direction](design-direction.md); **Phase 3 done**
(delivery, metadata, preview, gradual switch-over; see [content-model.md](content-model.md#delivery-phase-3-11-october-2026));
Phases 4–6 done and approved. **Phases 7–8 done**: every public page, articles and the 404 page
are served by the new site locally; quality pass recorded in [quality.md](quality.md).
**Phases 9–10 done**: v2 seed and create-or-update importer (`npm run site:seed`,
`npm run site:import`), and the Console handoff, [console-cms-v2-handover.md](console-cms-v2-handover.md).
Next: the owner's Console requirements, then Console phases C1–C5.
Research: [research.md](research.md). Baseline: [baseline.md](baseline.md).

## 1. Goal

Rebuild the AgentGate public site (`frontend/src/public`) as a stable, standard, distinctive
site, and replace the way it reads Console CMS content, so that future redesigns and content
changes cannot silently break the connection.

Two outcomes, delivered together:

1. **A redesigned public site** in the direction agreed from the research: the control path
   (Allow / Hold for review / Deny) as the signature, "the record" as the visual language, serif
   headlines, paper-white and ink with the deep Audoryn green, a live HTML product demonstration
   in the hero, no 3D on first load. Same nine pages, same top navigation style.
2. **A new content contract, `audoryn.public.v2`:** every page is described by named, typed
   sections with clear editor labels, instead of 656 text fragments keyed by component name and
   hash.

## 2. Fixed decisions

| Decision | Source |
|---|---|
| Option C: change the content model, not only the look | Owner, 11 October |
| Same pages: home, product, solutions, security, pricing, resources, company, privacy, terms | Owner |
| Same top navigation style (top bar with descriptive dropdown menus) | Owner |
| Research direction accepted in full | Owner |
| Order: AgentGate frontend first → handoff document → Console CMS → seed import → both together locally | Owner |
| Owner adds Console-side requirements when the Console step starts | Owner |
| Local only; no staging or production work in this plan | Standing rule |
| No commits or pushes unless the owner asks | Standing rule |

## 3. Why v1 has to go

Measured in the analysis of 11 October:

- Every visible word is a `text(fieldId, fallback)` call; fields are grouped by **component
  name** and named from the **original wording** (`copy-get-started-39de0215be`). Renaming a
  component or adding a field breaks the link.
- A page uses CMS content only if the release contains **every** field the code expects;
  otherwise the whole page silently falls back to built-in text (only a `console.warn`).
- Editors see 17 hidden `/content-fields/...` pages with labels like "field sot cm", sentence
  fragments, trailing spaces and layout values (`"#product-workforce"`, `"01 / OPERATIONS"`).
- The import script only does a first import; it stops on existing content.

## 4. The v2 content model

### 4.1 Principles

1. **One page document per page.** The page's own record holds all of its content. No hidden
   copy pages, no cross-page copy dictionaries.
2. **Named sections with fixed keys per page** (a page template). The frontend knows exactly
   which sections a page has; editors change content, list items and optional sections, not the
   design. This follows the Console rule of "structured approved blocks".
3. **Whole sentences only.** A headline is one field, optionally with a second muted clause
   (`headline` + `headlineDetail`) for the two-tone style. Line breaks, numbering ("01"),
   separators and anchors are computed by the design, never stored as content.
4. **Every field has an editor title and help text** in the schema (JSON Schema `title` and
   `description`). Console's schema-driven editor already displays `title`; editors will see
   "Hero headline", not "field sot cm".
5. **Plain text only.** No HTML, Markdown or styling in content. Links, media and lists are typed.
6. **Required means required; optional means optional.** Validation is the schema, not "every
   string the code once contained". Optional fields that are empty simply don't render.
7. **Defaults are content documents too.** The built-in fallback for each page is a v2
   document stored in the repository and validated by the same schema. The seed for the Console
   is generated from exactly those files, so the fallback and the seed can never disagree.
8. **Honesty rules are in the contract.** Demonstrations carry a required disclosure. There are
   no fields for invented metrics, customer logos or quotes; a future `proof` block will require
   a source reference.

### 4.2 Shape (outline; the exact schema is Phase 1 output)

```text
PageDocument (audoryn.public.v2)
  contract: "audoryn.public.v2"
  page: "home" | "product" | ... | "terms" | "resource"
  sections: { <fixed section keys for that page>: <typed section> }

Shared section types (approved blocks)
  hero            kicker?, headline, headlineDetail?, body, primaryAction, secondaryAction?
  sectionHeader   kicker?, headline, headlineDetail?, body?
  featureList     header, items[ {title, body, icon?} ] (min/max per page)
  figure          header, caption, demonstration reference
  decisionDemo    disclosure, request {worker, action, target}, checks[], outcomes {allow, review, deny}
  recordList      disclosure, records[ {reference, actor, action, policy, decision, timeLabel} ]
  solutionList    items[ {id, name, summary, worker, job, tools[], output, image?} ]
  planList        items[ {planKey, name, audience, price, currency, period, availability, capacity, features[], action} ]
  resourceList    items[ {id, title, summary, topic, link} ]
  legalDocument   effectiveDate, version, clauses[ {id, heading, paragraphs[]} ]
  faq             items[ {question, answer} ]
  closingCta      headline, headlineDetail?, body?, action
  media           asset reference + required alt text (existing verified asset system)

Global resources (kept from v1, reshaped)
  site            brandName, tagline, legalLine, signInAction, getStartedAction, announcement?
  navigation      header and footer menus (existing Console navigation resource; unchanged)
  seo             per page (existing Console page SEO fields; unchanged)
```

Each page template lists its section keys and which are optional. Example (home):
`hero (decisionDemo)`, `product (featureList)`, `controlPath (figure)`, `oversight`,
`connections`, `solutions (solutionList, references solutions page items)`, `trust`,
`closingCta`.

### 4.3 Contract ownership and versioning

- **AgentGate owns the contract** (it renders it): `docs/public-site/contract.v2.json` (JSON
  Schema with titles and help text), page templates, valid and invalid examples. Console gets a
  copy under `fixtures/` and its validation must accept every valid example and reject every
  invalid one (the same pattern that worked for the admin reads).
- **Compatible change** (adding an optional field, an optional section, a new icon): stays v2.
  Old content keeps working; the new element just doesn't render until content exists.
- **Breaking change** (new required field, removed or renamed field): either publish content
  first and deploy the frontend second, or bump to v3. A check script compares the frontend's
  schema with the live release and fails before deploy if they disagree. No more silent fallback.

### 4.4 Fallback behaviour

| Situation | Result |
|---|---|
| Release has a valid v2 page | Render it |
| Release has no page for the route, or only a v1 page | Render the built-in v2 default for that page, log `public_content.default_used` with the reason |
| Release page is invalid v2 | Keep the last valid published version if held in memory; otherwise the default. Log the validation error path (never content) |
| Confirmed removal (page deleted from a v2 release) | 404, as today |
| Release pointer unreachable | Keep the last verified release (as today) |

The verified-delivery protections of v1 stay: storage-origin restriction, `/cms-public/` prefix,
no redirects, SHA-256 checksums, size and reference limits, ETag revalidation, bounded caches.
Only the payload format and the readability of the code change.

### 4.5 Transition

- The new frontend accepts only v2. While the Console still publishes v1, the new site shows its
  built-in v2 defaults, which are the new approved copy, so it looks complete.
- The currently deployed site keeps reading v1 until the owner deploys the new frontend.
- When Console publishes the first v2 release, the new site switches to it automatically.
- v1 code and seed files are removed from AgentGate only after Console has published v2 locally
  (final phase), so there is always a working pair.

## 5. Design system

| Token group | Content |
|---|---|
| Colour | Paper `#F7F5F0`-range background, ink near-black text, deep Audoryn green (brand), decision colours: allow (green), hold (amber), deny (restrained red). Decision colours are used only for decision states. Exact values checked for WCAG AA in Phase 2. |
| Type | Serif display for headlines (candidate: Newsreader, OFL, self-hosted via fontsource like Geist today), Geist for text and UI, Geist Mono for record data. Small set of sizes; body at full contrast. |
| Layout | 12-column container, consistent section rhythm: kicker → headline left / paragraph right → labelled figure ("FIG. 02 — Approval") → short feature list. |
| Components | Header (links, same mega-menu style, keyboard and touch), announcement bar, footer, Kicker, SectionHeader, Figure, FeatureList, RecordCard, DecisionBadge, DecisionDemo, PlanCard, Faq, ClosingCta, Media. |
| Motion | Purposeful only; content is visible without JavaScript; no scroll capture; `prefers-reduced-motion` turns animation off. |

## 6. Phases

Each phase ends with its checks green and a short report to the owner before the next starts.
"Checks" always includes `npm run typecheck`, `npm test` and `npm run build` in `frontend`.

### Phase 0: Baseline and branch

- Create a branch from the current state (name agreed with the owner; no commit without asking).
- Record the baseline: screenshots of all nine pages (desktop and mobile), page weights, current
  test results, the live release version. Store under ignored `frontend/artifacts/public-v2/`.
- Confirm `npm test`, typecheck and build pass before any change.

### Phase 1: Contract v2 and default content

- Write `contract.v2.json` (all section types, page templates, titles, help text, limits).
- Generate TypeScript types from it; write a readable validator (schema plus the semantic rules:
  unique IDs, decision coverage, plan keys, legal dates, link safety).
- Write the new copy as v2 default documents for all nine pages plus `site`, following the
  research direction and the honesty rules. **Owner reviews the copy** at the end of this phase.
- Valid and invalid examples; tests that every default and example behaves as expected.

### Phase 2: Design foundation

- Tokens, fonts, grid, base CSS (one structured stylesheet set, not per-page duplication).
- Header with real links and the same dropdown style, mobile menu, announcement bar, footer.
- The shared components from §5 rendered from v2 types.
- A development-only style reference page to review the system before pages are built.
  **Owner reviews the look** at the end of this phase.

### Phase 3: Content runtime v2

- Rewrite the loader and validation as readable modules, keeping every v1 delivery protection
  and porting its tests (checksums, limits, origin, redirects, retention, removal).
- Typed page hooks (`usePage('home')`), fallback per §4.4, structured logging.
- SSR head: title, description, canonical, Open Graph, robots, structured data from v2 SEO.
- Restricted preview (`/_cms/preview`) accepting v2 payloads with the same origin and nonce
  handshake.
- Sitemap and robots unchanged in behaviour.

### Phase 4: Home page

- Hero: one-sentence promise, actions, live decision demonstration (deterministic, labelled
  illustrative, reduced-motion safe, pure HTML/CSS/SVG).
- Remaining home sections from the template; no scroll capture, no hidden-until-scrolled content.

### Phase 5: Product and Solutions

- Product: workforce, jobs, supervision, integrations, each with a labelled figure built from the
  demonstration records.
- Solutions: solution list with stable IDs; `?selected=<id>` and section anchors keep working.

### Phase 6: Security and Pricing

- Security: the control path in depth (identity → capability → policy → approval → record) with
  the decision demonstration and record list.
- Pricing: plans from content, informational only (no entitlement implied), `contact` plans
  require a contact link, free plan routes to sign-up.

### Phase 7: Resources, Company, Privacy, Terms, details and 404

- Resources directory and the editorial detail page (`ResourcePage`) on v2, including verified
  media (images, video with captions, downloads, GLB model loaded lazily only on that page).
- Company page; Privacy and Terms as legal documents with effective date and version.
- Designed 404 and "content unavailable" states.

### Phase 8: Quality pass

- Performance budgets enforced in the build (targets set in Phase 0 from the baseline; aim:
  no three.js and no copy-dictionary JSON in the first load, page CSS split, home HTML well under
  today's 123 KB).
- Accessibility: one meaningful `<h1>` per page, landmarks, AA contrast, keyboard menus, focus
  visible, reduced motion.
- Mobile at 375 px and tablet; no horizontal scroll.
- Update the browser verification scripts (`verify-public-host.mjs`; the previous
  `verify-public-cms-browser.mjs` was replaced by `verify-site-browser.mjs`) for v2 and run them locally.
- `npm audit --omit=dev --audit-level=high`.

### Phase 9: Seed and import v2

- `build-public-seed` generates the v2 seed from the default documents (one source).
- New importer that creates **or updates** draft revisions, globals and navigation by stable
  keys, records a receipt, and never approves, schedules or publishes (editors still do that in
  Console). Dry run by default; `--apply` only with explicit approval and a process-local token.
- Tests with a fake Console API, including retries and conflicts.
- It is run against the local Console only after the Console phases support v2.

### Phase 10: Console handoff document

`docs/public-site/console-cms-v2-handover.md`, covering:

- the contract (schema, page templates, examples) and how Console must validate it;
- editor experience: labels and help from the schema, required/optional, list limits,
  asset pickers with required alt text, announcement bar;
- preview handshake for v2;
- publishing: releases must carry v2 pages; retiring v1 copy pages (`/content-fields/...`);
- importer API calls the Console must support (create or update draft revisions by key);
- acceptance checks Console must pass, and the local end-to-end procedure.

The owner adds Console-side requirements at this point.

### Console phases (outline; detailed plan written at handoff)

| Phase | Work |
|---|---|
| C1 | v2 contract in Console: model, schema endpoint with titles and help, validation against the fixtures |
| C2 | Editor experience for v2 page templates, plus owner-requested Console changes |
| C3 | v2 preview in the Console iframe host |
| C4 | Releases with v2 pages; retire v1 copy pages |
| C5 | Run the v2 importer against local Console; editors review, approve and publish |

### Phase 11: Both together locally, then cleanup (done, 12 October 2026)

- Local Console publishes a v2 release; local AgentGate frontend renders it. Verify each page,
  preview, removal (404), redirect, unreachable storage, invalid page fallback, and the
  schema drift check.
- Remove v1 renderer code, v1 seed files and v1 tests from AgentGate.
- Final full checks in both repositories. Deployment remains the owner's step.

As built:
- **Together locally:** Console C5 published v2 releases 3 and 5 from the local Console; the local
  site served them (release 5 carries the owner's Pricing edit, "Starts free.").
- **One server:** `src/site/server.tsx` now answers every request (`publicResponse`), and is the
  SSR entry for the build, the Vite dev server and `api/public-site.mjs`. It took over
  `/_public/preview-reference` and `/robots.txt`. `/_public/content` and `/_public/config` (v1) are
  gone; they now return 404.
- **Frame policy:** every HTML response sends `Content-Security-Policy: frame-ancestors 'none'` and
  `X-Frame-Options: DENY` (public pages and the application shell). `/_cms/preview` sends
  `frame-ancestors <PUBLIC_CONTENT_PREVIEW_ORIGIN>` only (`'none'` when it is not set).
- **Removed:** `src/public/` (the previous renderer), `src/site/preview/LegacyPreview.tsx`, the v1
  seed files in `content-seed/` (the v2 seed stays), the v1 seed and check scripts
  (`build-public-seed`, `capture-public-content`, `check-public-content-live`,
  `import-public-content-seed`, `public-content-fixture`, `report-public-content-coverage`,
  `seed-content-comparison`, `seed-importer`, `validate-public-seed`, `verify-public-cms-browser`)
  and their tests (`publicCms`, `publicSeedImporter`, `publicSeedValidation`).
  `seed-console-request.mjs` and `seed-request-error.mjs` stay: the v2 importer uses them.
- **Moved:** the three seed images to `src/site/assets/`; browser helpers to `src/site/runtime.ts`.
- **Preview:** a v1 revision now shows a message instead of the previous renderer.
- **Ported tests** (v1 loader protections not yet covered in v2): configuration origins, bucket-path
  origins, duplicate pages and assets, unsafe release paths, identity mismatch, aborted requests,
  old hash links, and the server (frame policy, application shell, endpoints, robots, sitemap).
- **`verify-public-host.mjs`** now runs on v2 releases built from the seed
  (`scripts/site-release-fixture.mjs`), including a leftover v1 page that must be 404.
- **`verify-site-browser.mjs`** asks the system for a free port: Vite read `port: 0` as 5173.
- **Not removed:** the `three` and `@types/three` dependencies, now unused (owner decision).

## 7. Risks and how the plan handles them

| Risk | Handling |
|---|---|
| New site goes live before Console has v2 content | It shows its own complete v2 defaults (the approved copy) |
| Frontend and published content disagree after a future change | Optional-only compatible changes; drift check before deploy; clear logs instead of silent fallback |
| Losing v1's delivery security while rewriting | Port every v1 loader test before deleting v1 code |
| Design drifts into generic | Owner reviews copy (Phase 1) and look (Phase 2) before pages are built |
| Made-up proof or numbers | No contract fields for them; demonstrations require disclosure |
| Breaking the authenticated workspace | Public code stays under `src/site`; app routes and bundles are not touched; full typecheck and build every phase |
| Console editor can't show the new model well | Handoff specifies titles and help text; Console phases may add a template-aware editor |

## 8. Out of scope

- Deployment to Vercel, Oracle or any hosted environment.
- New pages beyond the nine, blog/changelog features, analytics, cookie tooling.
- Billing or entitlement behaviour (pricing stays informational).
- The AgentGate efficiency pass (next roadmap item after this work).
