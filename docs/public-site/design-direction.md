# Public site design direction

Status: **approved** by the owner on 11 October 2026. Home page built (below the navigation) and
waiting for the owner's review; preview at `/site-preview.html` on the dev server. It replaces the Phase 2 visuals below the navigation bar; the navigation bar
stays exactly as on audoryn.vercel.app today. Research: [research.md](research.md) and its
addendum on the owner's reference sites.

## 1. The idea in one paragraph

Audoryn's own colours stay the identity: cream and deep forest green. The site alternates
between **light "explain" surfaces**, where people read and understand, and **deep forest "system"
surfaces**, where Audoryn is shown working: the gateway, live checks, records, policy as code.
On the system surfaces, a small set of signal colours (used the way Upstash, MongoDB, Render and
Temporal use theirs: sparingly, only for things that are live or decided) makes the product feel
alive. One rule gives the colour meaning: **AI is violet, authority is green**. What the AI
proposes is drawn in violet; what Audoryn allows, holds or denies is drawn in the decision colours.

## 2. Colour

### Base: the owner's colours (kept)

| Token | Value | Use |
|---|---|---|
| Cream | `#F7F7F3` | Page background (light surfaces) |
| Cream raised | `#FCFCF9` | Cards, windows on light surfaces |
| Sage wash | `#EEF3EC` | Alternate light band |
| Sage line | `#D8E1D7` | Hairlines and grid on light |
| Ink | `#171715` | Text on light |
| Ink muted | `#5F625C` | Secondary text on light (5.8:1) |
| Forest | `#26382F` | Deep panels, footer |
| Brand green | `#284D35` | Primary buttons, links on light |
| Night forest | `#0E1712` | System surfaces (darker step of the same green) |
| Night raised | `#142019` | Cards and windows on system surfaces |
| Night line | `#24372B` | Hairlines and grid on system surfaces |
| On night | `#E9EFEA` / `#9DB0A3` | Text / secondary text on system surfaces |

### Signal colours (new, used sparingly)

| Token | On night | On light (text) | Meaning, and the only places it appears |
|---|---|---|---|
| Signal | `#3DDC97` mint | `#1A7A4C` | Live and allowed: status dots, the active path, "Allow", cursors, key numbers |
| Hold | `#F5B83D` amber | `#8A5A00` | Held for a person |
| Deny | `#FF8A73` coral | `#A63A2B` | Denied, blocked |
| Model | `#A99BFF` violet | `#5B4FD1` | The AI layer only: what a model proposes, reasons or plans |

Every pair above meets WCAG AA for text (4.5:1); the lowest is 4.97.

### Rules

1. Signal colours are **lines, dots, labels, code tokens, chart strokes and small glyphs**, never
   large fills or page backgrounds.
2. A soft glow (one blurred shadow at low opacity) is allowed **only on a live element on a
   system surface**: a status dot, the moving signal, the cursor. Nowhere on light surfaces.
3. Gradients appear only as light given off by a live element on night forest. No decorative
   gradient backgrounds and no gradient text.
4. Light surfaces stay calm: cream, ink and brand green, with decision colours only on decisions.
5. Colour never carries meaning alone: every decision also has a word and an icon.

## 3. Type

| Role | Face | Style |
|---|---|---|
| Headlines | **Geist** | Weight 500, tight tracking (−0.035em), large; the precise, technical voice of Render, Scale and Upstash |
| Statements | **Newsreader** (serif) | Used only for the few full-screen principle statements, as Railway and Anthropic do, for gravitas |
| Text | Geist | 16–18 px, comfortable line height |
| System | **Geist Mono** | Labels, IDs, timestamps, code, metrics, figure numbers |

## 4. Structure and the signature

- **The blueprint frame.** The page sits inside visible hairline columns (left and right edges of
  the container). Every section boundary is a horizontal hairline, and a small `+` mark sits where
  lines cross, as on Render, Railway and Scale. On light it is sage; on night it is night line.
- **The signal path.** One mint line is the site's signature. It enters the hero window, passes
  through the gateway's checks, and splits into allow, hold and deny. The same path reappears in
  "how it works", in the security page and in the closing section, always meaning "an action
  moving under control".
- **Labelled figures.** Visuals carry a mono label (`FIG. 03 · THE GATEWAY`).
- **Illustrative honesty.** Every demonstration, number and record shows an "Illustrative" badge.
  No customer logos, no invented metrics presented as real.

## 5. Motion

| Allowed | Not allowed |
|---|---|
| A headline word that types or cycles (where actions happen: GitHub, Slack, Gmail…) | Scroll hijacking, pinned scroll scenes |
| The signal moving along its path; checks ticking in sequence | Content hidden until it scrolls in |
| The event stream receiving records; status dots pulsing | 3D, particles, parallax |
| Subtle lift on hover for cards and windows | Bouncing, spinning, confetti |

All motion stops with reduced-motion settings and pauses when off screen or hovered.

## 6. Home page, section by section (below the navigation)

| # | Section | Surface | What it shows |
|---|---|---|---|
| 1 | Hero | Cream into night | Headline "Put AI workers to work." with a typing line ("…every action in **Slack** checked before it runs"), two buttons. Below it a wide **control-plane window** straddling into a night band: workers on the left; one request in the middle passing its checks along the signal path to a decision; the live event stream on the right |
| 2 | Works with | Night | Provider marks in a quiet row: GitHub, Slack, Gmail, Drive, Calendar, browser |
| 3 | Principle | Cream, full screen | Serif statement: "AI can propose. People decide." with the three decision chips |
| 4 | How it works | Cream | Four stages on the signal path (Work → Policy → Approval → Record), each with a small live UI fragment |
| 5 | Policy as code | Night | Code window with tabs (Policy · Capability · Record JSON), with the AI's proposal in violet and the gateway's decision in green |
| 6 | Operating model | Cream | Bento of four framed panels: worker card, job queue, policy decision, supervision inbox |
| 7 | Human oversight | Sage wash | The existing photograph with an approval card laid over it |
| 8 | The record | Night | Full-width console: stream of records plus a row of illustrative counters (decisions, held, denied) |
| 9 | Connections | Cream | Diagram: providers → gateway → actions, with the boundary statement |
| 10 | Solutions | Cream | Tabs by function; each shows its worker, job, tools and deliverable as a small card |
| 11 | Security | Night | Four safeguards with icons and a link to the security page |
| 12 | Closing | Night | The signal path ends in "Allow"; headline and buttons |

## 7. Other pages (outline; each gets its own section map before it is built)

| Page | Idea |
|---|---|
| Product | Hero with the full workspace window; workforce (worker profile UI), runtime (run timeline that advances), AI models (violet layer feeding the green gateway), supervision (approval UI), integrations grid |
| Solutions | Function explorer: pick an area, see its worker run a job end to end in a small window |
| Security | Night hero "Every action has a boundary." with the gateway animation; the four gates in depth; decision explorer; safeguards; audit console |
| Pricing | Calm cream page: plan cards, comparison table, "capacity changes, authority does not", questions |
| Resources | Guides as framed cards, the article library, operator note |
| Company | Story, portfolio, founder, closing |
| Privacy, Terms | Reading layout with a sticky contents list; no signal colours |
| 404 | Night: the signal path ends in "Not found" |

## 8. What carries over, what changes

- **Kept:** the navigation bar, the approved copy, the v2 content model approach (named fields,
  validation, editor labels, defaults as seed), links, accessibility and honesty rules, tests.
- **Rebuilt:** all visual components below the navigation.
- **Content model:** grows where the new design needs it (for example the typing words, policy
  code lines, counters). New copy for those goes to the owner for review, as before.

## 9. Order of work after approval

1. Build the home page below the navigation and send screenshots (desktop and mobile).
2. Adjust with the owner until approved.
3. Product and Solutions, then Security and Pricing, then the remaining pages, each with
   screenshots before moving on.
