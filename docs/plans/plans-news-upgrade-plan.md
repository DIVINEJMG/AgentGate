# Plans page, in-app news and upgrade prompts: plan

Written 16 October 2026 after research and the owner's decisions. Mostly UI, in AgentGate's
workspace app, with the content managed in the Console. Built and checked in this order:
Plans page, upgrade prompts, news feed, Console screens.

## 1. Owner's decisions (16 October 2026)

| # | Decision |
|---|---|
| 1 | Plans get their **own in-app page** (`/app/plans`); "Usage & billing" keeps usage and a "See plans" button |
| 2 | In-app news comes from **CMS changelog entries** (product updates; one source for the website and the app) **and Console Announcements** (events and time-bound notices) |
| 3 | News appears in a **"What's new" panel** opened from a top-bar bell with an unread count; only entries marked **major** open a one-time modal |
| 4 | Upgrade prompts appear **at a limit**, **near a limit (80%)**, **when a feature is not in the plan**, and in **promotional announcements** |
| 5 | People who cannot pay see the same prompt with **"Ask an admin to upgrade"**, which notifies the owners and admins (at most once a day) |
| 6 | Each plan's **tagline, highlights and badge are Console-managed**; the limits table comes from the plan's real entitlements |
| 7 | News reaches **everyone by default**, optionally limited to plans and roles |
| 8 | Read and dismissed state is kept **per person, on the server** |

### Rules carried over

- Nothing commercial is hard-coded: plans, prices, limits, marketing text and announcements are
  Console data.
- The CMS link and the signed reads are not touched; new data flows have their own channel.
- A prompt never interrupts running work, never appears on first open, mid-onboarding or after an
  error, and always has an easy "Not now".
- Efficient, no duplicates, and nothing here changes how tasks run end to end.

## 2. Research summary

- Plans belong on a dedicated comparison page with a monthly/annual switch and the current plan
  marked; checkout starts there.
- Upgrade prompts convert at the moment of intent (trying the thing a higher plan unlocks, or
  reaching a limit), soft before hard: a quiet banner near a limit, a modal at it that says what is
  blocked and what upgrading adds, with frequency limits and cool-downs after a dismissal.
  Capacity caps rather than feature walls (Linear's model, and ours). Prompts that block work the
  person was entitled to finish are resented.
- People rarely visit a changelog, so news lives in the app: a persistent "What's new" entry with
  an unread badge, a modal only for major releases, tagged entries (New / Improvement / Fix), short
  entries that lead with what the person can now do, published once and delivered everywhere.

Sources: Eleken paywall examples, marketingskills paywall-upgrade guide, Vercel community reports,
usagepricing (Linear), ProductLift changelog tools, Featurebase release notes, Linear changelog,
Intercom changes.

## 3. What gets built

### 3.1 Plans page (`/app/plans`)

- Cards side by side: name, tagline, optional badge ("Most popular"), price with a monthly/annual
  switch (showing the annual saving) and a currency picker, 3-6 highlights, and "Current plan" or
  "Choose <plan>" (checkout as built in phase 5).
- A comparison table below, generated from each plan's entitlements (workers, standing jobs,
  tasks, AI tokens, coding time, web searches, storage, members, connections, included features).
- Checkout return and payment confirmation land here; paying workspaces get "Manage billing" here.
- "Usage & billing" keeps the usage view and gains a "See plans" button; its plan picker moves.
- Reachable from Usage & billing, every upgrade prompt, the command palette and promotions.

**Built (16 October 2026):** `/app/plans` (`workspace/pages/PlansPage.tsx`, `plans.css`): hero with a
monthly/annual switch (best saving shown) and currency picker; cards with the badge and current
plan, price (annual shown per month and billed yearly), the headline limits; the comparison table
from the plan service's names (`entitlements.comparison()`); checkout, confirmation and Paddle's
checkout on this page (`/app/plans/pay/<id>`; Paystack returns to `/app/plans?checkout=<id>`);
"Manage billing" for paying workspaces. The Console's catalogue now includes the Free plan, the
limits and the display order. "Usage & billing" keeps usage and a "See plans" panel; the old plan
picker is removed and number/money formatting is shared (`lib/planFormat.ts`). Taglines,
highlights and badges show when the Console has them (part 4). Checked in the sample workspace at
desktop and phone widths.

### 3.2 Upgrade prompts

| Trigger | Prompt | Frequency |
|---|---|---|
| A limit at 80% or more (`near` in the usage summary) | Slim banner at the top of the app: "You've used 82% of this month's AI tokens" + "See plans" | Once per limit per period; after a dismissal, at most weekly |
| New work refused by the plan (HTTP 402 from the API) | Modal: what was blocked, the meter that triggered it, what each higher plan adds, "Upgrade" / "Not now" | Each time new work is refused; never during running work |
| A feature the plan does not include | Inline panel where the feature would be: "Available on Team" + "See plans" | Always (it replaces a dead end) |
| A promotional announcement targeted at the workspace's plan | Shown in the news feed (and as a major modal if marked so) with an Upgrade button | As the announcement says |

- Members without `organizations.manage` see "Ask an admin to upgrade": AgentGate posts an inbox
  notice to the workspace's owners and admins, at most once a day per workspace and limit.
- The 402 messages stay as they are (plain text from the plan service); the modal adds context.

**Built (16 October 2026):** the rules are on the server (`services/plan_prompts.py`): the banner
is the fullest limit at 80% or more that this person has not dismissed in the last 7 days, only
while the plan is enforced; dismissals are kept in `user_content_state` (migration `0022`). "Ask
an admin to upgrade" writes an audit record (`commercial.upgrade.requested`, the pattern
invitations use) at most once a day per workspace and limit; administrators see recent requests as
a banner until they dismiss them. A 402 names its limit in `X-Plan-Limit`; the app opens the modal
from any refused request (`audoryn:plan-limit`), with the meter and the plans that give more (from
the catalogue). "Available on <plan>" shows on the Browser and GitHub tool pages
(`PlanFeatureNotice`, `workspace/upgrade.tsx`). The banner stays off the Plans page.
Promotional announcements come with the news feed (3.3).

### 3.3 In-app news ("What's new")

- A bell in the top bar with an unread count; a side panel with a timeline of entries: tag
  (New / Improvement / Fix / Event / Notice), title, date, short body, optional image and link.
- Sources, merged by date:
  - **CMS changelog entries** (the existing `changelog` content type), read through the existing
    public content delivery, which AgentGate's website already uses. No CMS file changes.
  - **Console Announcements** (existing: kind, title, body, audience, start/end, severity, button,
    dismissible), read from the Console by AgentGate over a new signed read (below), cached.
- An entry marked **major** opens once as a modal for each person it reaches.
- Audience: everyone by default; optionally limited to plans (`planCodes`) and roles.
- Read and dismissed state per person in AgentGate.

**Built (17 October 2026):** a bell in the top bar (and the phone bar) with the unread count opens a
"What's new" timeline (`workspace/news.tsx`). Sources:
- **CMS changelog:** the website server's new `/_public/changelog` lists the published pages of
  type `changelog` from its verified release (each read through the same checks as any page;
  the kicker is the tag: New / Improvement / Fix). **No CMS change**: the CMS already gives
  changelog entries a title, summary, date, kicker and image. A "major" flag was not added to
  the CMS; an announcement marked Major (which can link to the changelog entry) opens the window
  instead.
- **Console announcements:** AgentGate reads them over the billing channel (`GET /content`, new
  scope `content.read`), keeps the last good answer for 60 s, checks each one (links only to
  `/app/...` or HTTPS), and shows each person those for their plan and role.
- Read and dismissed state per person (`news:changelog:<id>`, `news:announcement:<id>` in
  `user_content_state`); only the last 30 days count as unread; opening the panel marks them read;
  dismissible announcements can be dismissed. A Major announcement opens once per person as a
  window. Promotions (`upgrade`) carry "See plans".

### 3.4 Console screens

- **Plans:** a "Plan page" section per plan: tagline, highlights (up to 6), badge, and whether it
  shows on the Plans page (the existing public-visibility switch). Saved with a reason and audited.
- **Announcements:** the existing screen gains "Show in the workspace app", "Major (open as a
  modal)", the plan and role audience, and an optional "Upgrade" button.
- **CMS changelog:** the existing changelog editor gains a "Major" flag and a tag, if its schema
  does not already carry them (to be checked first; added as optional fields only).

**Built (17 October 2026):**
- **Plans → Plans page** section per plan: tagline, up to 6 highlights, badge; saved with a
  reason and audited (`billing.plan.page.updated`); stored in `console.billing_plan_pages`
  (migration `0011`, its own table so the plan's existing create and update are untouched); the
  catalogue AgentGate reads carries them.
- **Announcements:** "Workspace app" options: show in What's new, Major (open once as a window),
  "See plans" button, limited to plan codes and roles. They live in the existing `audience`
  field, now checked by the Console (unknown options, plan codes and roles refused; Major only
  with in-app). No announcement column changed.
- **CMS:** unchanged (see 3.3).

## 4. Data and channels

| Where | Change | Migration |
|---|---|---|
| Console | Plan marketing fields: `tagline`, `highlights` (list), `badge` on `billing_plans` | `0011` (owner's approval) |
| Console | Announcements: `in_app`, `major`, `plan_codes`, `roles`, `upgrade_cta` (or within the existing `audience` JSON if it fits) | in `0011` if columns are needed |
| AgentGate | `user_content_state`: per person, per item (`changelog:<id>`, `announcement:<id>`, `prompt:<key>`): `read_at`, `dismissed_at` | `0022` (owner's approval) |
| AgentGate -> Console | A signed read for the workspace's announcements and plan page content, on the billing channel's key with a new scope `content.read` (label `audoryn-billing-v1`), cached in AgentGate for 60 s | none |

The catalogue the billing channel already returns gains each plan's tagline, highlights, badge and
entitlements (for the comparison table), so the Plans page needs one request.

## 5. Design

- The existing workspace look: dark surfaces, the green accent, the current type and spacing.
- Plans page: the recommended plan lifted with an accent border and soft glow, a faint grid or
  gradient background band, tabular figures, a sliding monthly/annual switch, smooth hover and
  focus states.
- News panel: a vertical timeline with coloured tags, relative dates, image thumbnails.
- Modals: centred, focused, the triggering meter at the top, plan comparison condensed below.
- Works at phone width; keyboard and screen-reader accessible (focus trapped in modals, Escape
  closes, live regions for banners); respects reduced motion.

## 6. Proof

- Tests: catalogue content, audience filtering, read/dismiss state, prompt frequency rules, the
  admin notification limit, the 402 modal trigger, and the Console validation and audit.
- Browser checks on the sample workspace and the simulator: each prompt, the news panel, a major
  modal, and the Plans page with a sandbox checkout.
- The existing suites, verify scripts and the reads/CMS tests unchanged.
- Handoff updated with the migrations and settings.

## 7. Announcement media (owner's request, 17 October 2026)

The major-announcement window looked like a template: an empty grid band above the text. The
owner wants it to read like the product announcements at mature tech companies.

**Research.**
- Linear's changelog leads each entry with a wide hero image (about 16:9).
- Vercel's changelog covers have light and dark variants.
- ChatGPT's launch window is one short message, one primary action and media on top.
- GitHub labels entries Release, Improvement or Retired.
- Common guidance: a modal only for big news, one primary action, an easy close, a muted looping
  video with a still poster instead of a GIF (the still when reduced motion is preferred), and
  "seen" recorded once per person.

**Owner's decisions.**

| # | Decision |
|---|---|
| 1 | Media comes from the Console's **CMS asset library** (already uploaded and verified). The CMS is only read, never changed |
| 2 | **Media on top**: a full-width 16:9 picture or video, then tag, date, title, short text and buttons |
| 3 | **Still image, short looping video (with a poster), and an optional dark-mode image** |
| 4 | Several unread major announcements show as **one window with pages and dots** (Next / Back) |

**What gets built.**
- **Console, Announcements screen:** a "Media" section that chooses from verified assets: image,
  dark-mode image (optional), video (optional) with its poster, and alt text (required when media
  is set). A live preview of the window.
- **Console, publishing the media:** the chosen assets are copied to public storage under
  `announcements/` (the same way releases publish site media, with the checksum checked). The
  content read sends their https addresses, size and type, and the alt text. Media is kept in a
  new `media` field on announcements (migration `0012`, owner's approval).
- **AgentGate checks:** it shows media only from the Console's public storage address, only images
  and MP4/WebM, and only within size limits; anything else falls back.
- **Workspace window:** 16:9 media on top (`object-fit: cover`, no layout jump):
  - the video is muted and loops; it plays only while the window is open and shows the poster when
    reduced motion is preferred;
  - the dark-mode image is shown in dark theme;
  - pages with dots, plus Next and Back (arrow keys too), for several major announcements; closing
    marks every page read;
  - one primary action, an optional "Learn more" and "Not now".
- **Fallback when no media is set:** a designed cover instead of the empty band: the Audoryn mark,
  the tag (New / Event / Notice / Offer) in large type over the grid, and a soft accent. It is
  drawn by the app, so it needs no file.
- **Panel:** entries with media show a 16:9 thumbnail; the changelog uses the CMS entry's image.
- **Proof:** Console tests (asset must be verified, image or video type, alt text required,
  published copy checksum), AgentGate tests (only the storage origin and allowed types), and
  browser checks at desktop and phone widths, light and dark, with reduced motion.

**Fix (17 October 2026, branch `feat/remove-unfinished-upload` in both repos):** the editor saved
media only when an image was chosen, so a video on its own was skipped without a message. Now an
image, a video or both are accepted, in the Console, in AgentGate's check and in the window. A
video with no image plays muted and looping. Under reduced motion it shows its first frame,
paused. No migration and no settings.
