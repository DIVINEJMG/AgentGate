# Content model v2 (`audoryn.public.v2`)

Status: Phase 1 complete (11 October 2026). This describes the contract the redesigned site reads.
The Console handoff (Phase 10) builds on this document.

## Files

| File | Purpose |
|---|---|
| `frontend/scripts/site-contract.mjs` | **Source of truth.** Defines every type, field, limit, editor title and help text |
| `frontend/src/site/contract/contract.v2.json` | Generated JSON Schema (draft 2020-12). Console gets a copy |
| `frontend/src/site/contract/contract.generated.ts` | Generated TypeScript types |
| `frontend/src/site/contract/validate.ts` | Validator: schema plus rules a schema can't express |
| `frontend/src/site/contract/examples/` | A valid article and 27 invalid cases both systems must reject |
| `frontend/src/site/content/defaults/*.json` | Built-in content for the shared site document and the nine pages |
| `frontend/scripts/site-copy-report.mjs` | Writes [copy-review.md](copy-review.md) from the defaults |
| `frontend/tests/siteContract.test.mjs` | 37 tests |

Regenerate after changing the contract: `node scripts/site-contract.mjs`. The tests fail if the
generated files are stale.

## Documents

There are two kinds of document, each a single JSON object:

- **Site** (`"page": "site"`): brand, sign-in and get-started actions, optional announcement bar,
  built-in navigation, interface labels, a vocabulary of small labels used inside
  demonstrations and plans, and the not-found and unavailable messages.
- **Page** (`"page": "home"`, `"product"`, …, `"article"`): `{contract, page, sections}`, where
  `sections` has a fixed set of named keys for that page.

| Page | Sections (in display order) |
|---|---|
| home | hero, decision, product, controlPath, operatingModel, oversight, controls, connections, solutions, closing |
| product | hero, workspace, workforce, runtime, intelligence, supervision, integrations, closing |
| solutions | hero, solutions, closing |
| security | hero, decision, controlPath, safeguards, record, closing |
| pricing | hero, plans, sameModel, questions, closing |
| resources | hero, guides, library (optional), operators |
| company | hero, about, portfolio, why, founder, closing |
| privacy, terms | document |
| article | article (editorial pages; any number, each with its own slug) |

Each section key is also its anchor: `controlPath` → `#control-path`. Solution items add their
own anchors (`/solutions#customer-support`).

## Section types (approved blocks)

Hero, Statement, Text section, Feature list, Sequence, Linked cards, Figure section, Decision
section (with the illustrative decision demonstration), Record section, Integrations, Operating
model, Worker profile, Workspace demonstration, Run timeline, Solutions, Plans, Questions,
Profile, Closing call to action, Legal document, Article.

Shared parts: Link, Image (media-library asset plus required alternative text), Feature, Step,
Card, Question, Outcome, Check, Record entry, Integration, Layer, Solution, Plan, Clause,
Article section, Announcement.

## Rules

1. **Plain text only.** No markup characters, no line breaks, no leading or trailing spaces.
   Emphasis and line breaks belong to the design. A two-tone headline is two fields:
   `headline` and `headlineDetail`.
2. **Links** are a site path (`/pricing#plans`) or a credential-free `https://` URL.
3. **Keys** (`id`, plan `key`) are lowercase, hyphenated and unique within their list. They are
   used in links and must not change once published.
4. **Images** reference a verified media-library asset and must have alternative text.
5. **Demonstrations and example records** must carry a disclosure ("Illustrative …").
6. **Plans are informational.** A free-signup plan must cost 0; a contact plan needs a link.
   Nothing in content changes entitlements.
7. **Dates** are real calendar dates (`YYYY-MM-DD`).
8. **Limits** on every text length and list size are in the schema.
9. **Errors** report a field path and a code, never content.

## Compatibility

- Adding an optional field, optional section or new icon keeps `v2`: older content still
  validates, and the new element simply doesn't render until content exists.
- Adding a required field, or renaming or removing one, is a breaking change: publish content
  that already satisfies the new shape first, then deploy the site, or move to `v3`.
- The site never mixes published and built-in text inside a page: a page is either the valid
  published document or the built-in one.

## Media

Built-in content uses the three seed images by their stable IDs (the `media` list of
`content-seed/audoryn-public.v2.seed.json`); the site maps those IDs to the files in
`src/site/assets/` when showing built-in content. Published content
uses verified assets from the release.

## Delivery (Phase 3, 11 October 2026)

| File | Purpose |
|---|---|
| `frontend/src/site/content/loader.ts` | Loads verified releases (same protections as the current site) |
| `frontend/src/site/content/bundle.ts` | Turns a release or preview payload into a page bundle |
| `frontend/src/site/content/config.ts` | Reads the existing `PUBLIC_CONTENT_*` settings |
| `frontend/src/site/server.tsx` | Server rendering, metadata, `/_public/site`, sitemap, preview shell |
| `frontend/src/site/SiteApp.tsx` | Renders a bundle on the server and in the browser |
| `frontend/src/site/preview/CmsPreview.tsx` | Console preview: v2 in the new design, v1 as before |
| `frontend/src/site/routes.ts` | Which paths the new site owns (`BUILT_PAGES`) |
| `frontend/tests/siteLoader.test.mjs` | 23 tests for delivery, fallback, metadata and preview |

How Console content reaches a page:

- A page in the release is used when its envelope says `rendererContract: "audoryn.public.v2"`,
  it has one `PublicContent` block whose payload is a valid v2 document for that page, and every
  image it uses is a verified asset in the same release.
- The shared site document is the release's `global_content` resource with key `public-site`.
  Console navigation (`header`, `footer`) is applied only together with a valid site document,
  so navigation written for the previous site never points into the new pages.
- Page metadata comes from the envelope's SEO fields (`metaTitle`, `metaDescription`,
  `canonicalUrl`, `openGraphTitle`, `openGraphDescription`, `openGraphImageAssetId`, `index`,
  `structuredData`), with built-in titles and descriptions as the fallback.

Fallback, as decided in the plan:

| Situation | Result | Logged as |
|---|---|---|
| Valid v2 page | Published page | — |
| Nothing published yet (empty release or content off) | Built-in page | `content_disabled` or `default_used:page_not_published` |
| Page removed from a release that has pages | 404, never the built-in copy | `removed` |
| Page is still v1 | Built-in page | `default_used:page_not_v2` |
| Invalid v2 page | Last valid published page held, else built-in | `default_used:page_invalid` (field paths only) |
| Storage unreachable | Last verified release, else built-in | the error code |
| Redirect resource | 301 or 302 | — |
| Unknown path | 404 | — |

Built-in content is English, so a page showing it declares `lang="en"` whatever locale was
requested.

Since Phase 11, `frontend/src/site/server.tsx` answers every request; the previous renderer is
gone. A release page still written for v1 is 404 and never listed. The sitemap lists the nine
fixed pages (respecting "do not index") plus published v2 article pages.
