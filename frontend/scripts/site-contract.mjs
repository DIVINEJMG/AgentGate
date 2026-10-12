// Source of truth for the public website content contract (audoryn.public.v2).
//
// Writes src/site/contract/contract.v2.json (JSON Schema, with editor titles and help text)
// and src/site/contract/contract.generated.ts (TypeScript types). Console receives a copy of
// the JSON Schema and must accept and reject the same documents.
//
//   node scripts/site-contract.mjs          regenerate
//   node scripts/site-contract.mjs --check  fail if the generated files are stale
import {readFileSync, writeFileSync, mkdirSync} from 'node:fs';
import path from 'node:path';

export const CONTRACT = 'audoryn.public.v2';
const root = path.resolve(import.meta.dirname, '..');
const outDir = path.join(root, 'src/site/contract');

// Plain text: no markup characters, no control characters (so no line breaks), no
// leading or trailing spaces. Line breaks and emphasis are design decisions.
const PLAIN = '^[^\\s<>\\u0000-\\u001f\\u007f](?:[^<>\\u0000-\\u001f\\u007f]*[^\\s<>\\u0000-\\u001f\\u007f])?$';
const HREF = '^(?:/(?!/)[^\\s\\\\<>\\u0000-\\u001f]*|https://[^\\s\\\\<>\\u0000-\\u001f]+)$';
const KEY = '^[a-z][a-z0-9-]{0,62}$';
const UUID = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
const DATE = '^\\d{4}-\\d{2}-\\d{2}$';
// Code lines: like plain text, but leading spaces (indentation) and empty lines are allowed.
const CODE = '^[^<>\\u0000-\\u001f\\u007f]*$';

// ---- builders -------------------------------------------------------------------------------
const OPTIONAL = Symbol('optional');
const opt = schema => ({[OPTIONAL]: true, schema});
const ref = (name, title, description) => ({$ref: `#/$defs/${name}`, ...(title ? {title} : {}), ...(description ? {description} : {})});
const withHelp = (schema, title, description) => ({...schema, title, ...(description ? {description} : {})});
const text = (max, title, description) => withHelp({type: 'string', minLength: 1, maxLength: max, pattern: PLAIN}, title, description);
const list = (items, min, max, title, description) => withHelp({type: 'array', items, minItems: min, maxItems: max}, title, description);
// Text that must contain a placeholder such as {count}, replaced by the site.
const template = (max, placeholder, title, description) => withHelp({type: 'string', minLength: 1, maxLength: max, pattern: `^(?=.*\\{${placeholder}\\})` + PLAIN.slice(1)}, title, description);
const enumOf = (values, title, description) => withHelp({type: 'string', enum: values}, title, description);
const key = (title = 'Key', description = 'Stable identifier used for links and anchors. Lowercase letters, numbers and hyphens. Do not change it once published.') =>
  withHelp({type: 'string', pattern: KEY}, title, description);
function object(title, description, fields) {
  const properties = {}, required = [];
  for (const [name, field] of Object.entries(fields)) {
    if (field[OPTIONAL]) properties[name] = field.schema;
    else {properties[name] = field; required.push(name);}
  }
  return {type: 'object', title, ...(description ? {description} : {}), additionalProperties: false, properties, required};
}

// ---- shared vocabulary ----------------------------------------------------------------------
export const ICONS = ['workers', 'jobs', 'governance', 'supervision', 'operations', 'research', 'support', 'sales', 'marketing', 'engineering', 'identity', 'credentials', 'pause', 'record', 'capability', 'policy', 'approval', 'audit', 'connection', 'browser', 'document', 'shield', 'check', 'clock', 'compass', 'scale', 'boxes', 'lock', 'queue', 'model'];
export const PROVIDERS = ['github', 'gmail', 'google-drive', 'slack', 'google-calendar', 'browser', 'web-research', 'rest-mcp'];
export const DECISIONS = ['allow', 'review', 'deny'];
export const PAGES = ['home', 'product', 'solutions', 'security', 'pricing', 'resources', 'company', 'privacy', 'terms', 'article'];

const defs = {
  Link: object('Link', 'A labelled link. Use a site path such as /pricing or a full https:// address.', {
    label: text(80, 'Label', 'The words people click.'),
    href: withHelp({type: 'string', minLength: 1, maxLength: 500, pattern: HREF}, 'Destination', 'A site path starting with / (for example /security#control-path) or an https:// address.'),
  }),
  Image: object('Image', 'An image from the Console media library.', {
    asset: withHelp({type: 'string', pattern: UUID}, 'Image', 'Choose a verified image from the media library.'),
    alt: text(300, 'Alternative text', 'Describe what the image shows for people who cannot see it.'),
  }),
  Heading: object('Section heading', 'The text that opens a section.', {
    kicker: opt(text(60, 'Kicker', 'Short label above the headline, for example "Human oversight". Shown in small capitals.')),
    headline: text(140, 'Headline', 'One sentence. Keep it short; the design decides line breaks.'),
    headlineDetail: opt(text(200, 'Headline detail', 'Optional second clause shown in a quieter colour after the headline.')),
    body: opt(text(700, 'Introduction', 'One short paragraph under the headline.')),
  }),
  Feature: object('Feature', null, {
    id: key(),
    title: text(80, 'Title'),
    body: text(400, 'Description'),
    icon: opt(enumOf(ICONS, 'Icon', 'Optional symbol shown with the feature.')),
  }),
  Step: object('Step', 'One stage of a sequence.', {
    id: key(),
    label: text(40, 'Stage label', 'Short name for the stage, for example "Policy".'),
    title: text(120, 'Title'),
    body: text(400, 'Description'),
  }),
  Card: object('Card', 'A linked item.', {
    id: key(),
    kicker: opt(text(60, 'Kicker')),
    title: text(100, 'Title'),
    body: text(400, 'Description'),
    link: ref('Link', 'Link'),
    icon: opt(enumOf(ICONS, 'Icon')),
    image: opt(ref('Image', 'Image')),
  }),
  Faq: object('Question', null, {
    id: key(),
    question: text(200, 'Question'),
    answer: text(1200, 'Answer'),
  }),
  Outcome: object('Decision outcome', 'What happens for one policy decision.', {
    label: text(40, 'Decision label', 'For example "Allow", "Hold for review" or "Deny".'),
    title: text(120, 'Title'),
    body: text(400, 'Explanation'),
    note: opt(text(200, 'Guarantee', 'Optional one-line guarantee, for example "The provider is never called."')),
  }),
  Check: object('Check', 'One check the action passes through.', {
    id: key(),
    label: text(80, 'Check'),
    detail: opt(text(160, 'Detail')),
  }),
  DecisionDemo: object('Decision demonstration', 'An illustrative action request that resolves to allow, hold for review or deny. Always labelled as illustrative.', {
    disclosure: text(160, 'Disclosure', 'Required notice that this is an example, for example "Illustrative example · not live data".'),
    worker: text(80, 'Worker', 'The AI worker making the request.'),
    action: text(120, 'Requested action'),
    target: text(80, 'Target system', 'Where the action would happen, for example "Slack · #leadership".'),
    checks: list(ref('Check'), 2, 6, 'Checks', 'The checks the request passes through, in order.'),
    outcomes: object('Outcomes', 'Text for each of the three decisions.', {allow: ref('Outcome'), review: ref('Outcome'), deny: ref('Outcome')}),
    record: object('Record', 'The record kept after the decision.', {
      reference: text(40, 'Record reference', 'An example reference, for example "REC-2048".'),
      summary: text(200, 'Record summary'),
    }),
  }),
  Record: object('Record entry', 'An illustrative audit record.', {
    id: key(),
    reference: text(40, 'Reference'),
    actor: text(80, 'Actor', 'Who or what acted.'),
    action: text(160, 'Action'),
    policy: text(120, 'Policy applied'),
    decision: enumOf(DECISIONS, 'Decision'),
    outcome: text(200, 'Outcome'),
    time: text(40, 'Time label', 'For example "09:42" or "2 min ago".'),
  }),
  Integration: object('Integration', null, {
    id: key(),
    provider: enumOf(PROVIDERS, 'Provider', 'Decides the mark shown next to the name.'),
    name: text(60, 'Name'),
    use: text(160, 'What it is used for'),
  }),
  Layer: object('Operating layer', 'One layer of the operating model.', {
    id: key(),
    title: text(60, 'Title'),
    subtitle: text(80, 'Subtitle'),
    headline: text(140, 'Headline'),
    body: text(500, 'Description'),
    path: list(text(30, 'Stage'), 3, 3, 'Path', 'Exactly three short stage names, for example Request, Policy, Decision.'),
    signal: text(80, 'Signal', 'One short closing statement.'),
    icon: enumOf(ICONS, 'Icon'),
  }),
  Solution: object('Solution', 'One starting point for an AI worker.', {
    id: key('Key', 'Stable identifier used in links such as /solutions#support. Do not change it once published.'),
    area: text(40, 'Area', 'For example "Customer support".'),
    title: text(120, 'Title'),
    body: text(400, 'Description'),
    worker: text(80, 'Worker', 'The example worker role.'),
    job: text(100, 'Example job'),
    tools: list(text(40, 'Tool'), 1, 6, 'Connected tools'),
    deliverable: text(120, 'Deliverable'),
    icon: enumOf(ICONS, 'Icon'),
    image: opt(ref('Image', 'Image')),
  }),
  Plan: object('Plan', 'Plans are informational. Nothing here changes what an organization is entitled to.', {
    key: key('Plan key', 'Stable plan identifier, for example "free". Must match the product plan.'),
    name: text(40, 'Name'),
    audience: text(160, 'Who it is for'),
    priceMinor: withHelp({type: 'integer', minimum: 0, maximum: 100000000}, 'Price', 'Price in the smallest currency unit, for example 4900 for $49.00.'),
    currency: withHelp({type: 'string', pattern: '^[A-Z]{3}$'}, 'Currency', 'Three-letter code, for example USD.'),
    period: enumOf(['month', 'year'], 'Billing period'),
    workerLimit: withHelp({type: 'integer', minimum: 0, maximum: 100000}, 'Managed workers', 'Maximum managed workers shown for the plan.'),
    availability: enumOf(['free-signup', 'unavailable', 'contact'], 'Availability', 'free-signup links to sign-up; unavailable shows the notice; contact requires an action link.'),
    status: text(40, 'Status label', 'For example "Available now" or "Catalog plan".'),
    features: list(text(120, 'Feature'), 0, 8, 'Included'),
    action: opt(ref('Link', 'Button', 'Required when availability is contact.')),
  }),
  Clause: object('Clause', 'One numbered part of a legal document.', {
    id: key(),
    heading: text(120, 'Heading'),
    paragraphs: list(text(2000, 'Paragraph'), 1, 12, 'Paragraphs'),
    points: list(text(500, 'Point'), 0, 12, 'Points'),
  }),
  ArticleBlock: object('Article section', 'One headed part of an article.', {
    id: key(),
    heading: text(140, 'Heading'),
    paragraphs: list(text(3000, 'Paragraph'), 0, 30, 'Paragraphs'),
    points: list(text(500, 'Point'), 0, 30, 'Points'),
    image: opt(ref('Image', 'Image')),
    links: list(ref('Link'), 0, 10, 'Links'),
  }),
  Announcement: object('Announcement', 'Optional bar above the navigation.', {
    text: text(140, 'Text'),
    link: opt(ref('Link', 'Link')),
  }),
};

// ---- section types (approved blocks) ----------------------------------------------------------
const heading = {kicker: opt(defs.Heading.properties.kicker), headline: defs.Heading.properties.headline, headlineDetail: opt(defs.Heading.properties.headlineDetail)};
const intro = {...heading, body: opt(defs.Heading.properties.body)};
const sections = {
  HeroSection: object('Hero', 'The first screen of the page.', {
    ...heading,
    body: text(500, 'Introduction', 'One or two sentences under the headline.'),
    primaryAction: ref('Link', 'Main button'),
    secondaryAction: opt(ref('Link', 'Second button')),
    typing: opt(object('Typing line', 'Optional line under the headline where one word cycles, for example the places work happens.', {
      template: template(160, 'place', 'Sentence', 'Must contain {place}, for example "Every action in {place} is checked before it runs."'),
      places: list(text(40, 'Place'), 2, 8, 'Places', 'The words that cycle into {place}. The first one is shown when motion is off.'),
    })),
  }),
  HeadingSection: object('Statement', 'A heading with an optional paragraph.', {...intro}),
  ProseSection: object('Text section', 'A heading followed by paragraphs.', {
    ...heading,
    paragraphs: list(text(1200, 'Paragraph'), 1, 6, 'Paragraphs'),
    link: opt(ref('Link', 'Link')),
  }),
  FeatureSection: object('Feature list', null, {...intro, items: list(ref('Feature'), 1, 8, 'Features'), link: opt(ref('Link', 'Link'))}),
  StepSection: object('Sequence', 'Ordered stages, shown as a path.', {...intro, steps: list(ref('Step'), 2, 6, 'Stages')}),
  CardSection: object('Linked cards', null, {...intro, items: list(ref('Card'), 1, 8, 'Cards')}),
  FigureSection: object('Figure section', 'A heading, a labelled figure drawn by the site, and short points.', {
    ...intro,
    figureLabel: text(60, 'Figure label', 'Short caption above the figure, for example "Worker profile".'),
    points: list(text(160, 'Point'), 0, 6, 'Points'),
    image: opt(ref('Image', 'Image')),
    link: opt(ref('Link', 'Link')),
  }),
  DecisionSection: object('Decision section', 'A heading and the illustrative decision demonstration.', {...intro, demo: ref('DecisionDemo', 'Demonstration')}),
  RecordSection: object('Record section', 'A heading and illustrative audit records.', {
    ...intro,
    disclosure: text(160, 'Disclosure', 'Required notice that the records are examples.'),
    records: list(ref('Record'), 1, 8, 'Records'),
    metrics: opt(list(object('Metric', 'An illustrative number shown above the records.', {id: key(), label: text(40, 'Label'), value: text(20, 'Value', 'Shown as written, for example "1,284".')}), 0, 4, 'Metrics', 'Optional illustrative counters. Covered by the disclosure.')),
  }),
  CodeSection: object('Code section', 'A heading and an illustrative code window with tabs, showing what the AI proposed and what Audoryn decided.', {
    ...intro,
    disclosure: text(160, 'Disclosure', 'Required notice that the code is a simplified example.'),
    tabs: list(object('Code tab', null, {
      id: key(),
      label: text(30, 'Tab label', 'For example "Policy" or "Record".'),
      language: enumOf(['yaml', 'json', 'text'], 'Language', 'Used for colouring.'),
      lines: list(withHelp({type: 'string', maxLength: 160, pattern: CODE}, 'Line', 'One line of code. Leading spaces are kept for indentation.'), 1, 40, 'Lines'),
    }), 1, 4, 'Tabs'),
    proposal: text(200, 'AI proposal', 'What the model asked to do, shown in the AI colour.'),
    decision: object('Decision', 'What Audoryn decided.', {decision: enumOf(DECISIONS, 'Decision'), text: text(240, 'Explanation')}),
  }),
  IntegrationSection: object('Integrations', null, {
    ...intro,
    items: list(ref('Integration'), 1, 12, 'Integrations'),
    boundary: object('Boundary', 'The path every connected action passes before it runs.', {
      title: text(120, 'Boundary statement', 'For example "Authority is checked before execution."'),
      stages: list(text(30, 'Stage'), 3, 3, 'Stages', 'Exactly three stage names, for example Capability, Policy, Approval.'),
    }),
    note: text(300, 'Boundary note', 'Explains that a connection is not permission.'),
  }),
  WorkerProfileSection: object('Worker profile', 'A heading and an illustrative worker profile card.', {
    ...intro,
    figureLabel: text(60, 'Figure label', 'Short caption above the card, for example "Worker profile".'),
    disclosure: text(160, 'Disclosure'),
    profile: object('Example worker', null, {
      role: text(60, 'Role'),
      team: text(60, 'Team'),
      supervisor: text(60, 'Supervisor'),
      job: text(200, 'Assigned job'),
      schedule: text(40, 'Schedule', 'For example "Mon · 09:00".'),
      checkpoint: text(60, 'Checkpoint', 'For example "Review before delivery".'),
      status: text(30, 'Status'),
      path: list(text(30, 'Stage'), 3, 3, 'Path', 'Three stages, for example Role, Job, Queue.'),
    }),
    points: list(text(160, 'Point'), 0, 6, 'Points'),
  }),
  LayerSection: object('Operating model', null, {...intro, layers: list(ref('Layer'), 4, 4, 'Layers', 'Exactly four layers: workers, jobs, governance and supervision.')}),
  SolutionSection: object('Solutions', null, {...intro, items: list(ref('Solution'), 1, 12, 'Solutions'), note: text(300, 'Template note', 'Explains what a template does and does not set up.')}),
  PlanSection: object('Plans', null, {
    ...intro,
    plans: list(ref('Plan'), 1, 8, 'Plans'),
    notice: object('Availability notice', 'Shown with the plans while paid plans cannot be bought.', {title: text(80, 'Title'), body: text(400, 'Text')}),
  }),
  FaqSection: object('Questions', null, {...intro, items: list(ref('Faq'), 1, 20, 'Questions')}),
  WorkspaceDemoSection: object('Workspace demonstration', 'An illustrative view of the Audoryn workspace. Always labelled as illustrative.', {
    ...intro,
    disclosure: text(160, 'Disclosure'),
    organization: text(60, 'Example organization'),
    workers: list(object('Example worker', null, {id: key(), name: text(60, 'Role'), team: text(60, 'Team'), supervisor: text(60, 'Supervisor'), status: text(30, 'Status')}), 1, 4, 'Workers'),
    jobs: list(object('Example job', null, {id: key(), title: text(80, 'Job'), worker: text(60, 'Worker'), trigger: text(40, 'Trigger'), status: enumOf(['queued', 'running', 'completed', 'held'], 'Status')}), 1, 5, 'Jobs'),
    approval: object('Example approval', null, {title: text(80, 'Action held'), body: text(300, 'Context'), requestedBy: text(60, 'Requested by'), reviewer: text(60, 'Reviewer')}),
  }),
  RunSection: object('Run timeline', 'A heading and an illustrative run with its steps.', {
    ...intro,
    disclosure: text(160, 'Disclosure'),
    runTitle: text(80, 'Run title'),
    steps: list(object('Run step', null, {id: key(), title: text(80, 'Step'), detail: text(160, 'Detail'), state: enumOf(['done', 'active', 'waiting'], 'State')}), 2, 8, 'Steps'),
    points: list(text(160, 'Point'), 0, 6, 'Points'),
  }),
  ProfileSection: object('Profile', 'A person or organization profile.', {
    ...heading,
    name: text(80, 'Name'),
    role: text(80, 'Role'),
    location: opt(text(60, 'Location')),
    paragraphs: list(text(1200, 'Paragraph'), 1, 4, 'Paragraphs'),
    link: opt(ref('Link', 'Link')),
  }),
  ClosingSection: object('Closing call to action', null, {...intro, primaryAction: ref('Link', 'Main button'), secondaryAction: opt(ref('Link', 'Second button'))}),
  LegalSection: object('Legal document', null, {
    title: text(140, 'Title'),
    summary: text(400, 'Summary'),
    effectiveDate: withHelp({type: 'string', pattern: DATE}, 'Effective date', 'YYYY-MM-DD'),
    version: text(40, 'Version'),
    scopeNote: text(600, 'Scope note', 'How this document relates to any signed agreement.'),
    clauses: list(ref('Clause'), 1, 30, 'Clauses'),
    related: ref('Link', 'Related document', 'Link to the companion legal document.'),
  }),
  ArticleSection: object('Article', null, {
    kicker: opt(text(60, 'Kicker')),
    title: text(140, 'Title'),
    summary: text(400, 'Summary'),
    published: opt(withHelp({type: 'string', pattern: DATE}, 'Published', 'YYYY-MM-DD')),
    author: opt(text(80, 'Author')),
    image: opt(ref('Image', 'Image')),
    sections: list(ref('ArticleBlock'), 1, 40, 'Sections'),
  }),
};

// ---- page templates ----------------------------------------------------------------------------
// Section keys are fixed per page; the site decides layout and order.
const S = name => ref(name);
const templates = {
  home: {
    hero: S('HeroSection'), activity: S('RecordSection'), decision: S('DecisionSection'), product: S('FeatureSection'), controlPath: S('StepSection'), policy: S('CodeSection'),
    operatingModel: S('LayerSection'), oversight: S('FigureSection'), controls: S('FeatureSection'), connections: S('IntegrationSection'),
    solutions: S('CardSection'), closing: S('ClosingSection'),
  },
  product: {
    hero: S('HeroSection'), workspace: S('WorkspaceDemoSection'), workforce: S('WorkerProfileSection'), runtime: S('RunSection'),
    intelligence: S('FeatureSection'), supervision: S('FigureSection'), integrations: S('IntegrationSection'), closing: S('ClosingSection'),
  },
  solutions: {hero: S('HeroSection'), solutions: S('SolutionSection'), closing: S('ClosingSection')},
  security: {
    hero: S('HeroSection'), decision: S('DecisionSection'), controlPath: S('StepSection'), safeguards: S('FeatureSection'),
    record: S('RecordSection'), closing: S('ClosingSection'),
  },
  pricing: {hero: S('HeroSection'), plans: S('PlanSection'), sameModel: S('FeatureSection'), questions: S('FaqSection'), closing: S('ClosingSection')},
  resources: {hero: S('HeroSection'), guides: S('CardSection'), library: S('HeadingSection'), operators: S('ProseSection')},
  company: {
    hero: S('HeroSection'), about: S('ProseSection'), portfolio: S('CardSection'), why: S('ProseSection'),
    founder: S('ProfileSection'), closing: S('ClosingSection'),
  },
  privacy: {document: S('LegalSection')},
  terms: {document: S('LegalSection')},
  article: {article: S('ArticleSection')},
};
const optionalSections = {resources: ['library']};
// Link anchors. Every present section is anchored at its key in kebab case (controlPath →
// control-path); the items of these lists are also anchored at their own id.
const itemAnchors = {solutions: {solutions: 'items'}, privacy: {document: 'clauses'}, terms: {document: 'clauses'}, article: {article: 'sections'}};

const pageDefs = {};
for (const [page, keys] of Object.entries(templates)) {
  const name = page[0].toUpperCase() + page.slice(1) + 'Page';
  const sectionFields = Object.fromEntries(Object.entries(keys).map(([k, v]) => [k, optionalSections[page]?.includes(k) ? opt(v) : v]));
  pageDefs[name] = object(`${page[0].toUpperCase() + page.slice(1)} page`, null, {
    contract: withHelp({type: 'string', const: CONTRACT}, 'Contract'),
    page: withHelp({type: 'string', const: page}, 'Page'),
    sections: object('Sections', null, sectionFields),
  });
}

const navFields = {
  id: key(),
  label: text(40, 'Label'),
  href: defs.Link.properties.href,
  description: opt(text(160, 'Description', 'Shown under the label in dropdown menus.')),
  newTab: opt(withHelp({type: 'boolean'}, 'Open in a new tab')),
};
const navItem = object('Navigation item', null, navFields);
const site = object('Site', 'Text used on every page: brand, actions, navigation and interface labels.', {
  contract: withHelp({type: 'string', const: CONTRACT}, 'Contract'),
  page: withHelp({type: 'string', const: 'site'}, 'Page'),
  brand: object('Brand', null, {
    name: text(40, 'Product name'),
    owner: text(60, 'Owner line', 'For example "An SOT product".'),
    tagline: text(120, 'Tagline', 'Shown in the footer.'),
    legal: text(80, 'Legal line', 'For example "© 2026 SOT".'),
  }),
  actions: object('Actions', 'The sign-in and get-started actions in the navigation.', {signIn: ref('Link', 'Sign-in link'), getStarted: ref('Link', 'Get-started button')}),
  announcement: opt(ref('Announcement', 'Announcement bar')),
  navigation: object('Navigation', 'Built-in navigation, used when Console has not published navigation.', {
    header: list(object('Menu', null, {...navFields, intro: opt(text(160, 'Menu introduction')), children: list(navItem, 0, 6, 'Links')}), 1, 8, 'Header menus'),
    footer: list(object('Footer group', null, {id: key(), label: text(40, 'Group title'), children: list(navItem, 1, 8, 'Links')}), 1, 6, 'Footer groups'),
  }),
  labels: object('Interface labels', 'Short labels for navigation and accessibility.', {
    home: text(60, 'Home link label', 'Read by screen readers on the logo link.'),
    mainNavigation: text(60, 'Main navigation label'),
    footerNavigation: text(60, 'Footer navigation label'),
    openMenu: text(30, 'Open menu'),
    closeMenu: text(30, 'Close menu'),
    skipToContent: text(60, 'Skip to content'),
    illustrative: text(40, 'Illustrative badge', 'Badge shown on demonstrations.'),
    onThisPage: text(40, 'On this page', 'Heading for the legal page contents.'),
    exploreMenu: template(60, 'label', 'Explore link in menus', 'Must contain {label}, for example "Explore {label}".'),
  }),
  vocabulary: object('Vocabulary', 'Small labels used inside demonstrations, plans and records. "{count}" is replaced with a number.', {
    worker: text(30, 'Worker'), team: text(30, 'Team'), supervisor: text(30, 'Supervisor'), status: text(30, 'Status'),
    trigger: text(30, 'Trigger'), job: text(30, 'Job'), exampleJob: text(40, 'Example job'), connectedTools: text(40, 'Connected tools'),
    deliverable: text(40, 'Deliverable'), requestedBy: text(40, 'Requested by'), reviewer: text(30, 'Reviewer'),
    reference: text(30, 'Reference'), actor: text(30, 'Actor'), action: text(30, 'Action'), policy: text(30, 'Policy'),
    decision: text(30, 'Decision'), outcome: text(30, 'Outcome'), time: text(30, 'Time'), target: text(30, 'Target'), checks: text(30, 'Checks'),
    schedule: text(30, 'Schedule'), assignedJob: text(40, 'Assigned job'), recordedAs: text(40, 'Recorded as'), worksWith: text(40, 'Works with label', 'Label in front of the provider marks, for example "Works with".'), proposedBy: text(40, 'Proposed by label', 'For example "Proposed by the model".'), gateway: text(40, 'Gateway name', 'Shown in the title bar of the hero demonstration, for example "Action gateway".'),
    jobStatus: object('Job status names', null, {queued: text(30, 'Queued'), running: text(30, 'Running'), completed: text(30, 'Completed'), held: text(30, 'Held for review')}),
    decisions: object('Decision names', 'Shown on records and badges.', {allow: text(30, 'Allowed'), review: text(30, 'Held for review'), deny: text(30, 'Denied')}),
    stepState: object('Run step states', null, {done: text(30, 'Done'), active: text(30, 'In progress'), waiting: text(30, 'Waiting')}),
    perMonth: text(30, 'Per month', 'Shown after a monthly price.'),
    perYear: text(30, 'Per year', 'Shown after a yearly price.'),
    workerLimit: template(60, 'count', 'Worker limit', 'Must contain {count}, for example "Up to {count} managed workers".'),
    chooseDecision: text(60, 'Decision chooser label', 'Read by screen readers on the decision buttons.'),
    chooseLayer: text(60, 'Layer chooser label'),
    chooseSolution: text(60, 'Solution chooser label'),
    ownerLogo: text(60, 'Owner logo description', 'Alternative text for the SOT logo.'),
    published: text(30, 'Published'), by: text(20, 'By'), readArticle: text(40, 'Read article'),
    effective: text(30, 'Effective', 'Label before the effective date of a legal document.'), version: text(30, 'Version'), backToResources: text(40, 'Back to resources', 'Link at the top of an article.'),
  }),
  notFound: object('Page not found', null, {headline: text(120, 'Headline'), body: text(300, 'Text'), action: ref('Link', 'Button')}),
  unavailable: object('Content unavailable', 'Shown when content cannot be loaded.', {headline: text(120, 'Headline'), body: text(300, 'Text')}),
});

function unique(...groups) {
  const merged = {};
  for (const group of groups) for (const [name, value] of Object.entries(group)) {
    if (name in merged) throw new Error(`Duplicate contract type name: ${name}`);
    merged[name] = value;
  }
  return merged;
}

const schema = {
  $schema: 'https://json-schema.org/draft/2020-12/schema',
  $id: 'https://audoryn.app/contracts/audoryn.public.v2.json',
  title: 'Audoryn public website content',
  description: 'Content contract audoryn.public.v2. Each document is one page (or the shared site document). Plain text only; layout, styling and motion belong to the website.',
  'x-contract': CONTRACT,
  'x-pages': Object.fromEntries(Object.entries(templates).map(([page, keys]) => [page, Object.keys(keys)])),
  'x-optional-sections': optionalSections,
  'x-anchors': {
    sections: 'Each present section is a link anchor named after its key in kebab case, for example controlPath becomes control-path. Exception: the resources library section is shown, with its anchor, only while articles are published.',
    items: itemAnchors,
  },
  oneOf: [ref('Site'), ...Object.keys(pageDefs).map(ref)],
  $defs: unique(defs, sections, {Site: site}, pageDefs),
};

// ---- TypeScript ---------------------------------------------------------------------------------
function tsType(node) {
  if (node.$ref) return node.$ref.split('/').pop();
  if (node.const !== undefined) return JSON.stringify(node.const);
  if (node.enum) return node.enum.map(v => JSON.stringify(v)).join(' | ');
  if (node.type === 'array') {const inner = tsType(node.items); return /[ |]/.test(inner) && !inner.startsWith('{') ? `Array<${inner}>` : `${inner}[]`;}
  if (node.type === 'object') {
    const req = new Set(node.required);
    return '{' + Object.entries(node.properties).map(([k, v]) => `${k}${req.has(k) ? '' : '?'}: ${tsType(v)};`).join(' ') + '}';
  }
  return node.type === 'integer' ? 'number' : node.type;
}
const ts = [
  '// Generated by scripts/site-contract.mjs from the audoryn.public.v2 contract. Do not edit.',
  `export const CONTRACT = ${JSON.stringify(CONTRACT)};`,
  `export const PAGES = ${JSON.stringify(PAGES)} as const;`,
  'export type PageName = typeof PAGES[number];',
  `export const ICONS = ${JSON.stringify(ICONS)} as const;`,
  'export type IconName = typeof ICONS[number];',
  `export const PROVIDERS = ${JSON.stringify(PROVIDERS)} as const;`,
  `export const DECISIONS = ${JSON.stringify(DECISIONS)} as const;`,
  'export type Decision = typeof DECISIONS[number];',
  `export const PAGE_SECTIONS = ${JSON.stringify(schema['x-pages'])} as const;`,
  `export const ITEM_ANCHORS: {[page: string]: {[section: string]: string} | undefined} = ${JSON.stringify(itemAnchors)};`,
  ...Object.entries(schema.$defs).map(([name, node]) => `export type ${name} = ${tsType(node)};`),
  `export type PageDocument = ${Object.keys(pageDefs).join(' | ')};`,
  'export type ContentDocument = Site | PageDocument;',
  "export type PageOf<P extends PageName> = Extract<PageDocument, {page: P}>;",
  '',
].join('\n');

const files = [[path.join(outDir, 'contract.v2.json'), JSON.stringify(schema, null, 2) + '\n'], [path.join(outDir, 'contract.generated.ts'), ts]];
if (process.argv.includes('--check')) {
  const stale = files.filter(([file, body]) => {try {return readFileSync(file, 'utf8') !== body;} catch {return true;}});
  if (stale.length) {console.error('Stale contract files:', stale.map(([f]) => path.relative(root, f)).join(', '), '\nRun: node scripts/site-contract.mjs'); process.exit(1);}
  console.log('Contract files are current.');
} else {
  mkdirSync(outDir, {recursive: true});
  for (const [file, body] of files) writeFileSync(file, body);
  console.log('Wrote', files.map(([f]) => path.relative(root, f)).join(', '));
}
