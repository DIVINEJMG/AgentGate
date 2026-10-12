# Public site baseline (Phase 0)

Recorded 11 October 2026, before any redesign work, on branch `feature/public-site-v2`
(from `codex/agentgate-complete-integration` at `f45b056`).

## Checks

| Check | Result |
|---|---|
| `npm run typecheck` | Pass |
| `npm test` | 89 / 89 pass |
| `npm run build` | Pass (client 45.9 s, SSR 1 s) |

## Live site (audoryn.vercel.app, CMS release 2)

Captured with headless Chrome, reduced motion, `networkidle`. Full-page screenshots for
all nine pages at 1440×900 and 375×812 are in `frontend/artifacts/public-v2/baseline/` (ignored
by git), with the raw numbers in `baseline.json`.

| Page | Load (desktop) | `<h1>` | Height (desktop / mobile) | Links / buttons |
|---|---:|---|---:|---:|
| `/` | 6.3 s (cold) | "AUTONOMY" | 11,942 / 12,900 px | **0** / 41 |
| `/product` | 4.6 s | "Give work to AI.Keep people in control." | 6,082 / 7,986 | 1 / 30 |
| `/solutions` | 2.8 s | "Work that alreadyneeds doing." | 2,336 / 3,346 | 0 / 33 |
| `/security` | 2.6 s | "Every action hasa boundary." | 3,198 / 4,944 | 0 / 29 |
| `/pricing` | 2.4 s | "Start free.Grow with the work." | 2,977 / 4,797 | 0 / 27 |
| `/resources` | 2.3 s | "Know the system.Then put it to work." | 2,171 / 2,724 | 0 / 29 |
| `/company` | 2.0 s | "Built by SOT.Made for real work." | 4,402 / 5,419 | 5 / 27 |
| `/privacy` | 2.4 s | "Your work. Your data. Clear boundaries." | 3,297 / 4,570 | 0 / 31 |
| `/terms` | 2.1 s | "Clear terms for controlled work." | 3,045 / 4,297 | 0 / 31 |

Observations:

- Navigation is buttons: the whole home page has **zero** `<a href>` links.
- Headings that use `<br/>` read as joined words to search engines and screen readers
  ("alreadyneeds", "hasa").
- First visit to `/` downloads 2.76 MB of decoded resources, of which 1.09 MB is JavaScript
  (including three.js, 593 KB) and 176 KB CSS. Later pages reuse the cache.

## Build output (public-relevant)

| Asset | Size | Gzip |
|---|---:|---:|
| Main public JS (`index-*.js`) | 482.6 KB | 148.3 KB |
| Public CSS (`index-*.css`) | 175.5 KB | 32.9 KB |
| three.js (lazy) | 592.9 KB | 149.1 KB |
| SSR bundle (`public-site.mjs`) | 332.3 KB | — |
| Home HTML (live, includes 104 KB embedded content JSON) | 123 KB | — |

## Targets for Phase 8 (set from this baseline)

| Measure | Target |
|---|---|
| Public JS on first load | ≤ 120 KB gzip, no three.js |
| Public CSS | ≤ 25 KB gzip |
| Home HTML including embedded content | ≤ 60 KB |
| Links | Every navigation and call to action is a real `<a href>` |
| `<h1>` | One per page, reads as a sentence |
