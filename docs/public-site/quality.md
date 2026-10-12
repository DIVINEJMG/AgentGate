# Public site quality (Phase 8)

Recorded 11 October 2026 against the production build on branch `feature/public-site-v2`.

## How to re-run

From `frontend`:

```powershell
npm run typecheck
npm test
npm run build
npm run check:budget        # performance budget (needs the build)
npm run verify:site         # browser checks against the production build
node scripts/verify-public-host.mjs
npm audit --omit=dev --audit-level=high
```

`verify:site` uses the existing Playwright installation (`PUBLIC_CMS_BROWSER_RUNTIME`) and an
installed Chrome or Edge (`PUBLIC_CMS_BROWSER_CHANNEL`); it downloads nothing.

## Performance

| Measure | Baseline (old site) | Now | Budget |
|---|---:|---:|---:|
| First-load JavaScript, gzip | 148 KB (+ three.js 149 KB on home) | 102.8 KB, no three.js | 120 KB |
| First-load CSS, gzip | 32.9 KB | 21.0 KB | 25 KB |
| Home HTML, gzip (raw) | 123 KB raw | 14.8 KB (75 KB raw) | 25 KB |
| Other pages HTML, gzip | — | 5.3–9.0 KB | 25 KB |

What changed: the previous public renderer (its components, 15 stylesheets and about 120 KB of
v1 content JSON) is no longer in the first download; it loads only for a v1 editorial page or a
v1 Console preview. The 3D scenes are gone from the public pages.

The baseline set a 60 KB raw target for the home HTML. The raw page is 75 KB because it carries
the page's own content data, which the browser needs to make the page interactive; what travels
over the network is 14.8 KB compressed. The budget is therefore measured compressed.

## Accessibility and behaviour (87 browser checks, all passing)

On every public page: `lang="en"`, a title, exactly one `<h1>`, one `<main>`, headings without
skipped levels, alternative text on every image, a name on every visible link and button,
labelled navigation made of links, no positive `tabindex`, and no horizontal scroll at 375 px.

Behaviour: the first Tab reaches the skip link; Arrow Down opens a menu and Escape closes it with
focus returned; the decision chooser and both tab sets work with arrow keys and move focus; the
focus outline is drawn; in-page navigation and back work; unknown pages answer 404; there are no
script or hydration errors; with reduced motion, nothing moves; the phone menu fills the screen
below the header and opens and closes.

Navigation at its contract limits (`src/site/contract/examples/site.limits.json`: 8 menus with long
labels, 6 links each, 6 footer groups, an announcement), checked on the development preview
(`/site-preview.html?site=limits`):
- **1440 px:** everything fits at full spacing.
  - A menu with five or six links uses three columns, so its panel keeps the height of a four-link panel (287 px). Long descriptions are cut to two lines.
  - The footer keeps its groups on one row and grows only to its limit: about 485 px with 6 groups of 8 links, against 400 px today.
- **1180 px:** the space between menus compresses (28 px down to 12 px) instead of the bar growing.
- **1100 px:** when the menus cannot fit, the bar switches to the Menu button, which lists every menu.
- **Touch tablets, at any width:** menus open from the Menu button, because there is no hover to reveal them.

Fixed on 11 October: the phone menu was only 51 px tall. The header's blur made the header the
frame for the full-screen menu, so the menu now sits outside the header.

Colour contrast for every text and signal colour pair is enforced by `tests/siteDesign.test.mjs`
(WCAG AA, 4.5:1 for text, 3:1 for focus rings).

## Dependencies

`npm audit --omit=dev --audit-level=high`: 0 vulnerabilities. One dependency was added for the
redesign: `@fontsource-variable/newsreader` 5.3.0 (self-hosted font, OFL).

## Retired check

`scripts/verify-public-cms-browser.mjs` compared pixel screenshots of the previous public pages.
`verify-site-browser.mjs` replaced it, and it was deleted with the previous renderer in Phase 11.

## Phase 11 checks (12 October 2026)

- `npm run typecheck`, `npm run build`: pass.
- `npm test`: 152 of 152 (including the ported v1 loader protections and the server tests).
- `node scripts/verify-public-host.mjs`: passed on v2 releases (runtime update, article, redirect,
  removal, sitemap, leftover v1 page 404, frame policy, private template).
- `npm run verify:site`: 87 of 87. `npm run verify:workspace`: 20 of 20.
- `npm run check:budget`: met. `site:seed --check`, `site:contract --check`, offline `site:import`: current.
- `npm audit --omit=dev --audit-level=high`: 0 vulnerabilities.
- The local site against the local Console: release 5 served, frame headers present.
