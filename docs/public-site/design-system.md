# Public site design system (Phase 2)

> **Superseded** by [design-direction.md](design-direction.md) (approved 11 October 2026). The
> Phase 2 visuals below were replaced; the home page is now built to the new direction in
> `frontend/src/site/pages/home`. Kept for history.

Status: built 11 October 2026, waiting for the owner's visual review. Code lives in
`frontend/src/site`; nothing in the previous public site (`src/public`, removed in Phase 11) or the workspace changed.

## How to look at it

```powershell
Set-Location C:\Users\divin\AgentGate\frontend
npx vite --port 5195
# open http://localhost:5195/site-reference.html
```

The reference page is development-only (it is not in the production build). Screenshots:
`frontend/artifacts/public-v2/phase2/` (`ref-desktop-full.png`, `ref-desktop-menu.png`,
`ref-mobile-full.png`, `ref-mobile-sheet.png`, and per-section captures).

## Foundations

| Area | Choice |
|---|---|
| Colour | Paper `#f6f4ee`, ink `#141816`, brand green `#1f4a33`. Decision colours (allow green, hold amber, deny red) are used only for decisions, each with a tint and a solid mark. |
| Type | Newsreader (serif, optical sizes) for headlines; Geist for text and UI; Geist Mono for labels and record data. All self-hosted through fontsource. |
| Scale | Display 44–84 px, H1 40–68 px, H2 32–52 px, lead 17–20 px, body 16 px, labels 12 px. Fluid between 375 px and 1440 px. |
| Layout | 80 rem container, fluid gutter (16–48 px), section spacing 80–160 px, hairline rules between sections. |
| Motion | 220 ms ease; never hides content; all motion off with reduced motion. |

All text colour pairs meet WCAG AA (4.5:1) and decision marks and focus rings meet 3:1. This is
enforced by `tests/siteDesign.test.mjs`.

## Components

| Component | File | Notes |
|---|---|---|
| Site shell | `components/SiteShell.tsx` | Skip link, announcement, header, `<main>`, footer |
| Header | `components/Header.tsx` | Same style as before (centred links, full-width menu panel with introduction and numbered links). Real links; Arrow Down opens a menu, Escape closes it; Menu button and full-height sheet on touch and narrow screens |
| Footer | `components/Footer.tsx` | Brand, four link groups, legal line |
| Primitives | `components/primitives.tsx` | Section, Kicker, Headline (two-tone), SectionHeader, ActionLink, Figure ("FIG. 02 — label"), Disclosure badge, Media, Points |
| Blocks | `components/blocks.tsx` | Hero, FeatureList (columns or rows), StepPath (the control path), CardGrid (with photo cards), DecisionBadge, RecordList, IntegrationList (with the boundary panel), PlanCard, Faq, Closing |
| Decision demo | `components/DecisionDemo.tsx` | The signature figure: request, checks, gate, Allow / Hold for review / Deny as a radio group, outcome, record. Cycles while on screen until touched; static with reduced motion |
| Icons | `components/Icon.tsx` | Contract icon names mapped to Lucide |
| Content | `content/SiteContent.tsx` | Site document, vocabulary, media resolution (built-in images by seed ID) |
| Links | `navigation.tsx` | `SiteLink`: real `<a href>`, in-page navigation for site paths when enabled, full loads for the application |

## Contract additions made in this phase

Small labels the components needed, all in the shared site document so they stay editable:
`labels.exploreMenu` ("Explore {label}") and `vocabulary.decisions` (Allowed / Held for review /
Denied). Placeholder fields (`{label}`, `{count}`) are validated to contain their placeholder.

## Verified

- 132/132 frontend tests (contract 37, design 6, existing 89), typecheck, production build.
- Browser checks (desktop 1440, mobile 390): skip link first in tab order; Arrow Down / Escape
  menu behaviour and focus return; decision radio group with arrow keys; visible focus outline;
  mobile sheet opens, locks scroll and closes with Escape; no horizontal scroll; no console errors.
