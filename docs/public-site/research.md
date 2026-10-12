# Public site redesign: research

Date: 11 October 2026. Input for the public frontend redesign and the new CMS content model
(option C). Scope: the same nine pages (home, product, solutions, security, pricing, resources,
company, privacy, terms) and the same top navigation style.

## Method

I visited each site at 1440×900 in a real browser, scrolled through the home page and read its
structure. Agency "best SaaS sites" roundups were read too, but they are mostly opinion; the
findings below come from what the sites actually do.

| Site | Why it was chosen |
|---|---|
| [Linear](https://linear.app) | Reference for restraint and product-led storytelling |
| [Stripe](https://stripe.com) | Reference for explaining a complex platform |
| [Vercel](https://vercel.com) | Infrastructure positioned around agents |
| [Sierra](https://sierra.ai) | Enterprise AI agents (nearest category peer) |
| [Harvey](https://www.harvey.ai) | AI for a high-trust, regulated buyer |
| [Anthropic](https://www.anthropic.com) | AI safety and trust brand |
| [Ramp](https://ramp.com) | Finance controls with agents doing work |
| [Mercury](https://mercury.com) | Trust brand with a strong visual world |
| [Cursor](https://cursor.com) | AI agent product, current top-tier visual style |

## What the best sites have in common

1. **The real product is the hero.** Linear rebuilds its app in HTML in the first screen (issue,
   activity, agent working). Harvey, Cursor, Ramp and Stripe show working product UI within one
   scroll. Nobody leads with an abstract shape.
2. **The headline is one plain sentence that says what it is.** "The product development system
   for teams and agents" (Linear). "Cursor is your coding agent for building ambitious software."
   "Build a Frontier Legal Organization" (Harvey). Stripe adds a second, muted clause in the same
   sentence for detail.
3. **Brand character comes from one strong idea, not decoration.** Harvey and Cursor put sharp
   product UI on a painterly art backdrop. Mercury puts the product inside a photographic
   landscape. Vercel is pure black and white. Ramp is black, white and one acid colour. Each
   is recognisable in one screenshot.
4. **Proof sits next to every claim.** Vercel pairs each capability with a customer fact
   ("Notion powers millions of agent conversations daily on Vercel"). Stripe and Ramp show live
   counters ("Agents at work today: expenses reviewed, violations caught"). Harvey shows numbers
   and named quotes.
5. **Typography carries trust.** Harvey and Anthropic use a serif for headlines or body:
   editorial, considered, "this is serious work". Linear and Vercel use one tight sans with
   strong contrast. All use dark ink on light (or the reverse) — never faint grey on pale.
6. **One section grammar, repeated.** Linear: headline left, short paragraph right, large
   product figure labelled "FIG 0.1", then a small feature list. Vercel: headline, proof,
   feature list. Pages differ in content but share the same rhythm, which reads as quality.
7. **Navigation is a calm top bar with descriptive menus**, often with a thin announcement bar
   above it (Harvey, Sierra, Ramp, Mercury, Vercel).
8. **Audience routing.** Harvey splits immediately into "For law firms" and "For in-house".

What even the best get wrong (avoid): Ramp's newsletter popup on scroll; Linear and Sierra
showed blank heroes for the first seconds while heavy media loaded; cookie and announcement
clutter stacked on top of each other.

## Where Audoryn is today

Live site, desktop and mobile, checked 11 October 2026.

- **The hero says nothing.** The first screen is the single word "AUTONOMY" in white on a pale
  background (low contrast, worse on mobile, where it overlaps the 3D orb) and a generic "AI
  sphere". It is also the page's only `<h1>`.
- **The hero captures the first scroll** ("move / drag to explore"), so the page doesn't move on
  the first wheel turn.
- **Content is hidden until it scrolls into view.** Sections fade in from near-invisible; in
  screenshots, and if the script is slow, they look empty.
- **Everything looks faint.** A pale green wash on every page, light-weight headlines, grey body
  text. It reads as unfinished rather than calm.
- **Every page uses the same template** (kicker "AUDORYN / X", big two-line headline, panel on
  the right), so pages don't feel designed for their purpose.
- **Strong assets worth keeping:** the product mock-ups (workforce table; the action request
  with Allow / Require approval / Deny on the security page), the brand mark, the deep green,
  and the honesty (demonstrations are labelled as illustrative).

## Audoryn's own territory (so it isn't generic)

Every AI company says "agents". Audoryn's real difference is **authority**: an action meets a
boundary the model can't rewrite, and the outcome is recorded. The research points to making
that the visual identity, not a 3D object:

- **The control path as the signature.** One recurring motif across the site: a proposed action
  travels to a gate and resolves to **Allow**, **Hold for review** or **Deny**. The three
  decision colours are the only accent colours on the site, so colour always means something.
- **"The record" as the visual language.** Proof in the style of audit entries: monospace IDs,
  timestamps, actor, policy, outcome (the key/value cards on Anthropic's site are a good
  reference). It gives honest proof without customers: we show the evidence the product
  produces, labelled as a demonstration.
- **Editorial seriousness.** A serif display face for headlines (as Harvey and Anthropic do)
  with Geist for UI and Geist Mono for record data. It signals "governance", not "startup demo".
- **Product truth in the hero.** A plain sentence headline plus a live HTML demonstration: a
  worker proposes an action → policy check → held for a person → approved → recorded. It is
  deterministic, labelled "Illustrative", and respects reduced motion.

## Proposed direction

| Area | Direction |
|---|---|
| Colour | Warm paper white and near-black ink; deep Audoryn green as the brand colour; amber (hold) and a restrained red (deny) used only for decision states. No pale wash. |
| Type | Serif display headlines; Geist for text and UI; Geist Mono for record data. Body text at full contrast. |
| Hero | One-sentence promise + supporting clause, primary and secondary actions, the live control-path demonstration. No 3D on first load. |
| Section grammar | Kicker, headline left, paragraph right; a labelled figure ("FIG. 02 — Approval"); a short feature list. Used on every page with page-specific figures. |
| Proof | Record-style evidence cards and concrete commitments ("the model never holds credentials") instead of invented customer logos or numbers. |
| Navigation | Same top bar and mega menus, rebuilt as real links; optional CMS-driven announcement bar. |
| Motion | Subtle and functional; never hides content, never captures scroll; reduced motion respected. |
| Performance | Text renders from the server; no three.js on first load; a JavaScript and CSS budget per page enforced in the build. |

## Rules carried into the plan

- No fabricated numbers, customers, logos or quotes. Counters or proof appear only from real
  data or clearly labelled demonstrations.
- Every visible word is CMS-editable through the new named content model; layout, motion and
  the decision colours stay in code.
- Accessibility: real links, one meaningful `<h1>` per page, AA contrast, keyboard access to
  menus, visible focus.

## Link to the content model

The research shows pages built from a small set of repeated section types (hero, figure,
feature list, proof/record, decision demonstration, plans, FAQ, closing call to action). That
fits the Console rule of "structured approved blocks": the new model can describe each page as
named, typed sections instead of 656 text fragments. The plan will decide between fixed
per-page schemas and an ordered list of approved section types.

## Sources

Sites visited directly: [Linear](https://linear.app), [Stripe](https://stripe.com),
[Vercel](https://vercel.com), [Sierra](https://sierra.ai), [Harvey](https://www.harvey.ai),
[Anthropic](https://www.anthropic.com), [Ramp](https://ramp.com), [Mercury](https://mercury.com),
[Cursor](https://cursor.com). Background reading:
[UXPin, best website design examples 2026](https://www.uxpin.com/studio/blog/good-website-designs-examples/),
[SaaSFrame, landing page trends 2026](https://www.saasframe.io/blog/10-saas-landing-page-trends-for-2026-with-real-examples),
[Why do SaaS websites look so similar](https://demandmyths.substack.com/p/why-do-saas-websites-look-so-similar),
[UX Planet, 250+ SaaS landing pages reviewed](https://uxplanet.org/i-reviewed-250-saas-landing-pages-avoid-these-10-common-design-mistakes-a1a8499e6ee8).

## Addendum (11 October 2026): the owner's reference set

The owner asked for a stronger "tech" feel and named OpenAI, Anthropic, Slack, Scale, MongoDB,
Upstash, Fly.io and Render. Neon, Temporal and Railway were added as similar sites. All were
visited at 1440×900.

| Site | What gives it the tech feel |
|---|---|
| OpenAI | Black, minimal; the hero is the product itself (a prompt box); large media grid |
| Anthropic | Warm paper and serif, the exception: trust through restraint |
| Slack | Product window directly under a bold centred headline; agent use cases as tabs |
| Scale | Cinematic black; one statement per screen ("Reliable AI has no shortcuts… Humans stay in the loop"); scroll-driven layered panels |
| MongoDB | Dark teal and bright green; audience switch (Builder / Business leader); bento cards with code and chat UI |
| Upstash | Dark with an emerald headline; product tabs; code blocks with language tabs; cards with live metrics; integration network diagram |
| Fly.io | Light, illustrated, playful: least relevant |
| Render | Dark; headline that types; `$ git push` terminal chip; service dashboard cards with status; visible grid frame around sections |
| Neon | Agent-first: the homepage itself is plain machine-readable text for coding agents |
| Temporal | Dark with a purple glow; "watch a workflow recover" interactive code and console |
| Railway | Serif headline on a dark painted landscape; product window under the hero; framed grid |

What the set has in common, and Audoryn lacks today:

1. **Dark first.** Nine of eleven are dark. Light is the exception (Anthropic, Slack, Fly.io).
2. **The system is shown working:** consoles, code, dashboards, status and metrics, not
   only explained.
3. **Visible structure:** hairline grid frames around sections and cards (Render, Railway, Scale).
4. **One bright accent** on dark (Upstash and MongoDB green, Render and Temporal purple).
5. **Motion with meaning:** typing headlines, a recovering workflow, streams of events.
6. **Developer artefacts:** code with language tabs, terminal prompts, JSON.
7. **Audience switch** for builders and decision makers (MongoDB).

Railway and Anthropic show that a serif can sit inside this world, and Railway's is a serif on dark.
