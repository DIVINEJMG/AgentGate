import { useEffect, useState } from 'react';
import './public.css';

export type PublicRoute = 'home' | 'product' | 'solutions' | 'security' | 'pricing' | 'resources' | 'company' | 'privacy' | 'terms';
type TargetRoute = PublicRoute | 'login' | 'signup' | 'app';

const NAV: { route: PublicRoute; label: string }[] = [
  { route: 'product', label: 'Product' },
  { route: 'solutions', label: 'Solutions' },
  { route: 'security', label: 'Security' },
  { route: 'resources', label: 'Resources' },
  { route: 'pricing', label: 'Pricing' },
];

const TITLES: Record<PublicRoute, string> = {
  home: 'Audoryn · AI workforce, under control',
  product: 'Product · Audoryn',
  solutions: 'Solutions · Audoryn',
  security: 'Security · Audoryn',
  pricing: 'Pricing · Audoryn',
  resources: 'Resources · Audoryn',
  company: 'Company · Audoryn',
  privacy: 'Privacy · Audoryn',
  terms: 'Terms · Audoryn',
};

export default function PublicSite({ route, onNavigate }: { route: PublicRoute; onNavigate: (route: TargetRoute) => void }) {
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    document.title = TITLES[route];
    window.scrollTo({ top: 0, behavior: 'auto' });
    setMenuOpen(false);
  }, [route]);

  return <div className='public-site'>
    <header className='public-header'>
      <button className='public-wordmark' onClick={() => onNavigate('home')} aria-label='Audoryn home'>Audoryn</button>
      <nav className='public-nav' aria-label='Public navigation'>
        {NAV.map((item) => <button key={item.route} className={route === item.route ? 'active' : ''} onClick={() => onNavigate(item.route)}>{item.label}</button>)}
      </nav>
      <div className='public-header-actions'>
        <button className='public-signin' onClick={() => onNavigate('login')}>Sign in</button>
        <button className='public-cta small' onClick={() => onNavigate('signup')}>Get started</button>
      </div>
      <button className='public-menu-button' onClick={() => setMenuOpen((open) => !open)} aria-expanded={menuOpen} aria-label='Open navigation'>{menuOpen ? 'Close' : 'Menu'}</button>
      {menuOpen && <div className='public-mobile-menu'>
        {NAV.map((item) => <button key={item.route} onClick={() => onNavigate(item.route)}>{item.label}</button>)}
        <button onClick={() => onNavigate('company')}>Company</button>
        <span />
        <button onClick={() => onNavigate('login')}>Sign in</button>
        <button className='strong' onClick={() => onNavigate('signup')}>Get started</button>
      </div>}
    </header>

    <main>
      {route === 'home' && <HomePage onNavigate={onNavigate} />}
      {route === 'product' && <ProductPage onNavigate={onNavigate} />}
      {route === 'solutions' && <SolutionsPage onNavigate={onNavigate} />}
      {route === 'security' && <SecurityPage onNavigate={onNavigate} />}
      {route === 'pricing' && <PricingPage onNavigate={onNavigate} />}
      {route === 'resources' && <ResourcesPage onNavigate={onNavigate} />}
      {route === 'company' && <CompanyPage onNavigate={onNavigate} />}
      {route === 'privacy' && <PrivacyPage />}
      {route === 'terms' && <TermsPage />}
    </main>

    <Footer onNavigate={onNavigate} />
  </div>;
}

function HomePage({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  return <>
    <section className='public-hero public-container'>
      <div className='public-hero-copy'>
        <p className='public-kicker'>AUDORYN · AN SOT PRODUCT</p>
        <h1>Put AI to work.<br />Keep people in control.</h1>
        <p className='public-lead'>Create AI workers, assign real work, connect company tools, and keep every sensitive action governed by policy and human oversight.</p>
        <div className='public-actions'><button className='public-cta' onClick={() => onNavigate('signup')}>Get started</button><button className='public-text-action' onClick={() => onNavigate('product')}>Explore product <span>→</span></button></div>
      </div>
      <p className='public-hero-note'>Autonomous work should be useful without becoming unaccountable.</p>
    </section>

    <section className='public-section public-container'>
      <SectionIntro kicker='THE PRODUCT' title='An operating system for your AI workforce.' body='Audoryn gives businesses one place to create workers, define work, connect tools and supervise the moments where human judgment matters.' />
      <div className='public-three'>
        <TextColumn number='01' title='Create workers' body='Give AI clear roles, responsibilities, schedules and a named human supervisor.' />
        <TextColumn number='02' title='Assign work' body='Turn business objectives into durable jobs that can be scheduled, queued and inspected.' />
        <TextColumn number='03' title='Stay in control' body='Capabilities, policy, approvals and incident controls remain separate from AI reasoning.' />
      </div>
    </section>

    <section className='public-section public-container public-how'>
      <SectionIntro kicker='HOW IT WORKS' title='From a role to real work, without losing the boundary.' />
      <div className='public-steps'>
        {[
          ['01','Create a worker','Define the role, responsibilities, instructions and working hours.'],
          ['02','Connect the tools it needs','Attach workplace systems through explicit provider connections.'],
          ['03','Define what it may do','Capabilities and policy make authority visible and deterministic.'],
          ['04','Assign work','Create jobs, schedules and triggers without granting new permission.'],
          ['05','Supervise exceptions','Route approvals, failures and elevated-risk work back to people.'],
        ].map(([number,title,body]) => <div className='public-step' key={number}><span>{number}</span><strong>{title}</strong><p>{body}</p></div>)}
      </div>
    </section>

    <section className='public-section public-container'>
      <SectionIntro kicker='OPERATING VIEW' title='One place to understand what your AI workforce is doing.' body='The authenticated workspace is compact and operational. The public site stays editorial; the product stays focused on work.' />
      <div className='public-operating-view' aria-label='Audoryn operating model'>
        <div className='operating-head'><span>Workforce state</span><strong>Human attention stays visible.</strong></div>
        <div className='operating-grid'>
          <div><span>Workers</span><strong>Roles, identity, schedules</strong></div>
          <div><span>Jobs</span><strong>Objectives, queue, runtime</strong></div>
          <div><span>Governance</span><strong>Policy, risk, approvals</strong></div>
          <div><span>Supervision</span><strong>Escalations, incidents, audit</strong></div>
        </div>
      </div>
    </section>

    <section className='public-section public-container'>
      <SectionIntro kicker='CONTROL' title='Autonomous where appropriate. Human where it matters.' />
      <div className='public-control-list'>
        <ControlRow title='Capability controls' body='Define the actions an AI worker may attempt.' />
        <ControlRow title='Policy' body='Make authorization deterministic instead of leaving it to model judgment.' />
        <ControlRow title='Approvals' body='Require people before sensitive work can continue.' />
        <ControlRow title='Supervision' body='Route exceptional autonomous work to a named human.' />
        <ControlRow title='Audit' body='Preserve what happened, who acted and why a decision was made.' />
      </div>
    </section>

    <section className='public-section public-container'>
      <SectionIntro kicker='CONNECTIONS' title='Works where your company already works.' body='Provider connections expose bounded technical operations. They do not become AI permissions by themselves.' />
      <div className='public-integrations' aria-label='Supported workplace systems'>
        {['GitHub','Slack','Gmail','Google Drive','Google Calendar','REST / MCP'].map((name) => <span key={name}>{name}</span>)}
      </div>
    </section>

    <section className='public-section public-container'>
      <SectionIntro kicker='SOLUTIONS' title='Built for different kinds of work.' />
      <div className='public-solution-links'>
        {['Customer support','Research','Sales','Marketing','Operations','Software development'].map((name, index) => <button key={name} onClick={() => onNavigate('solutions')}><span>0{index + 1}</span><strong>{name}</strong><em>→</em></button>)}
      </div>
    </section>

    <section className='public-section public-container'>
      <SectionIntro kicker='TRUST' title='Control is part of the architecture, not an add-on.' />
      <div className='public-trust-lines'>
        <span>Human identity ≠ AI identity</span>
        <span>Every side effect passes through authorization</span>
        <span>Sensitive actions can require approval</span>
        <span>Credentials stay outside the frontend</span>
        <span>Every decision carries an audit trail</span>
      </div>
      <button className='public-text-action after-list' onClick={() => onNavigate('security')}>Read about security <span>→</span></button>
    </section>

    <ClosingCta onNavigate={onNavigate} />
  </>;
}

function ProductPage({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  const areas = [
    ['Workers','Business-facing AI workers with roles, responsibilities, schedules, supervisors and a separate security identity.'],
    ['Jobs','Durable work definitions with objectives, requirements, priorities, triggers and completion criteria.'],
    ['Runtime','Queue-based execution with checkpoints, retries, approvals and fail-closed recovery.'],
    ['Supervision','Human handoff for failures, elevated risk and exceptional autonomous work.'],
    ['Governance','Capabilities, policy, risk, approvals, incidents and audit around every controlled side effect.'],
    ['Connections','Replaceable provider adapters for the workplace systems your company already uses.'],
  ];
  return <><PageIntro kicker='PRODUCT' title='A calm operating layer for autonomous work.' body='Audoryn separates what AI decides from what the organization allows. Workers can plan and act, but authority remains explicit.' />
    <section className='public-section public-container'>
      <div className='public-feature-rows'>{areas.map(([title,body], index) => <div key={title}><span>0{index + 1}</span><h2>{title}</h2><p>{body}</p></div>)}</div>
    </section>
    <section className='public-section public-container public-split'>
      <SectionIntro kicker='THE BOUNDARY' title='AI chooses what comes next. Audoryn decides what is allowed.' body='Natural-language instructions, memory and model reasoning never become authorization. External actions still pass through identity, capability, risk, policy and approval controls.' />
      <div className='public-simple-list'><span>AI reasoning</span><span>Durable workflow</span><span>Deterministic authorization</span><span>Human exception handling</span></div>
    </section>
    <ClosingCta onNavigate={onNavigate} />
  </>;
}

function SolutionsPage({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  const solutions = [
    ['Customer support','Triage requests, prepare responses and escalate sensitive customer situations to people.'],
    ['Research','Collect, structure and summarize information while keeping external actions governed.'],
    ['Sales','Coordinate follow-up work, research accounts and prepare drafts without silently changing CRM state.'],
    ['Marketing','Run repeatable research, content and coordination jobs with explicit publishing boundaries.'],
    ['Operations','Schedule recurring work, monitor exceptions and keep a durable record of execution.'],
    ['Software development','Coordinate repository work and issue creation while write actions remain controlled.'],
  ];
  return <><PageIntro kicker='SOLUTIONS' title='AI workers for work that already exists.' body='Audoryn starts from business responsibilities, not novelty. Each worker has a clear charter, connected tools and explicit authority.' />
    <section className='public-section public-container'><div className='public-solution-detail'>{solutions.map(([title,body], index) => <div key={title}><span>0{index + 1}</span><h2>{title}</h2><p>{body}</p><small>Start from a supervised worker blueprint</small></div>)}</div></section>
    <section className='public-section public-container public-split'><SectionIntro kicker='TEMPLATES' title='Start faster without granting permission faster.' body='Templates can suggest roles, responsibilities, jobs, integrations and policy defaults. They create draft setup only; authority stays separate.' /><button className='public-text-action' onClick={() => onNavigate('security')}>See the control model <span>→</span></button></section>
    <ClosingCta onNavigate={onNavigate} />
  </>;
}

function SecurityPage({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  const controls = [
    ['Identity separation','Human sessions and AI identities are different security principals. A user session cannot stand in for an agent credential.'],
    ['Capability-based authority','Workers receive only explicitly declared capabilities. Missing authority fails closed.'],
    ['Policy enforcement','Deterministic policy resolves whether a controlled action is allowed, denied or requires approval.'],
    ['Human approvals','Sensitive actions can pause for human review. An AI worker cannot approve its own action.'],
    ['Credential handling','Provider credentials remain outside the frontend and are referenced through protected integration boundaries.'],
    ['Incident controls','Organization, worker and integration execution can be stopped independently without destroying operational state.'],
    ['Auditability','Actions, decisions, approvals, incidents and correlation IDs preserve a trace of what happened.'],
    ['Tenant isolation','Organizations remain the boundary for membership, workers, policies, credentials and data access.'],
  ];
  return <><PageIntro kicker='SECURITY' title='Control is designed into the execution path.' body='Audoryn does not treat an AI model as the final authorization authority. Security remains deterministic, inspectable and independent of the worker’s reasoning.' />
    <section className='public-section public-container'><div className='public-security-rows'>{controls.map(([title,body]) => <div key={title}><h2>{title}</h2><p>{body}</p></div>)}</div></section>
    <section className='public-section public-container public-split'><SectionIntro kicker='FAIL CLOSED' title='When authority cannot be verified, work does not continue.' body='Kill switches, credential checks, capability requirements, policy evaluation and approval state are revalidated rather than bypassed for convenience.' /><div className='public-code-line'>Identity → Capability → Risk → Policy → Approval → Provider</div></section>
    <ClosingCta onNavigate={onNavigate} />
  </>;
}

function PricingPage({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  return <><PageIntro kicker='PRICING' title='Start small. Add capacity when the work proves itself.' body='Audoryn’s commercial layer limits product capacity; it does not change what an AI worker is authorized to do.' />
    <section className='public-section public-container'>
      <div className='public-pricing'>
        <Plan name='Free' audience='For trying Audoryn with a small managed workforce.' action='Get started' onClick={() => onNavigate('signup')} features={['Core workforce workspace','Governance and approval controls','Limited managed-worker capacity']} />
        <Plan name='Business' audience='For teams operating AI workers across recurring company work.' action='Talk to us' onClick={() => onNavigate('company')} features={['Higher workforce capacity','Organization collaboration','Production operations visibility']} />
        <Plan name='Enterprise' audience='For larger deployments with security, governance and rollout requirements.' action='Contact us' onClick={() => onNavigate('company')} features={['Enterprise rollout planning','Security and governance review','Deployment and support alignment']} />
      </div>
      <p className='public-pricing-note'>Audoryn does not advertise a checkout flow until a verified billing provider is connected. Plan state never bypasses execution authorization.</p>
    </section>
    <ClosingCta onNavigate={onNavigate} />
  </>;
}

function ResourcesPage({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  return <><PageIntro kicker='RESOURCES' title='Understand the product before you operate it.' body='The useful material is the architecture, control model and operating principles—not marketing filler.' />
    <section className='public-section public-container'><div className='public-resource-rows'>
      <Resource title='Product overview' body='How workers, jobs, runtime, supervision and connections fit together.' action='Read product' onClick={() => onNavigate('product')} />
      <Resource title='Security model' body='Identity separation, capability authority, policy, approvals, incidents and audit.' action='Read security' onClick={() => onNavigate('security')} />
      <Resource title='Solutions' body='How Audoryn applies the same operating model across different kinds of company work.' action='Explore solutions' onClick={() => onNavigate('solutions')} />
      <Resource title='Pricing approach' body='Capacity and commercial readiness without pretending an unverified checkout integration exists.' action='View pricing' onClick={() => onNavigate('pricing')} />
    </div></section>
    <section className='public-section public-container public-split'><SectionIntro kicker='DOCUMENTATION' title='Operator documentation stays close to the product.' body='The authenticated workspace already exposes operational guidance around runtime, authorization, incident recovery and API lifecycle. A dedicated public docs surface can grow when there is enough material to justify it.' /><span className='public-muted-note'>No empty documentation shell.</span></section>
    <ClosingCta onNavigate={onNavigate} />
  </>;
}

function CompanyPage({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  return <><PageIntro kicker='COMPANY' title='Audoryn is built by SOT.' body='Audoryn is an SOT product focused on making autonomous software useful inside real organizations without making human authority ambiguous.' />
    <section className='public-section public-container'>
      <div className='public-company-grid'>
        <div><span>01</span><h2>Useful autonomy</h2><p>AI workers should perform real work, not stop at demos and chat interfaces.</p></div>
        <div><span>02</span><h2>Explicit control</h2><p>Authority, approvals and incident controls should remain understandable to people.</p></div>
        <div><span>03</span><h2>Evolution without rewrites</h2><p>Stable domain boundaries, versioned APIs and replaceable adapters let the product evolve without discarding its core.</p></div>
      </div>
    </section>
    <section className='public-section public-container public-contact'>
      <p className='public-kicker'>START WITH AUDORYN</p>
      <h2>See what a controlled AI workforce looks like in practice.</h2>
      <p>Start with the Free workspace, or use your existing SOT business contact for a pilot, partnership or enterprise discussion.</p>
      <div className='public-actions'><button className='public-cta' onClick={() => onNavigate('signup')}>Get started</button><button className='public-text-action' onClick={() => onNavigate('product')}>Explore product <span>→</span></button></div>
    </section>
  </>;
}

function PrivacyPage() {
  return <><PageIntro kicker='LEGAL' title='Privacy' body='This public notice summarizes how the Audoryn product is designed to handle identity and operational data. Contractual or jurisdiction-specific terms may add to this notice.' />
    <LegalBody sections={[
      ['Product data','Audoryn processes organization, workforce, job, governance, audit and integration metadata needed to provide the service.'],
      ['Credentials','Provider credentials are not intended for public pages or frontend storage. Connected-tool credentials are handled through protected product boundaries.'],
      ['Organization isolation','Operational records are scoped to the organization and authenticated membership context that created or is authorized to access them.'],
      ['Audit and security records','Security and execution history may be retained to support auditability, incident investigation and operational reliability.'],
      ['Public website','The public website is primarily informational. It does not expose private workforce data before authentication.'],
    ]} />
  </>;
}

function TermsPage() {
  return <><PageIntro kicker='LEGAL' title='Terms' body='This page gives a plain-language product-use overview. Signed commercial agreements or other applicable terms may supersede this summary.' />
    <LegalBody sections={[
      ['Authorized use','Use Audoryn only for organizations, systems and provider accounts you are authorized to operate.'],
      ['Human responsibility','Audoryn can supervise and constrain AI workers, but organizations remain responsible for how they configure workers, policies, connected tools and approvals.'],
      ['Security boundaries','Do not attempt to bypass identity, capability, policy, approval, incident or tenant-isolation controls.'],
      ['Service evolution','Audoryn may evolve APIs, product surfaces and integrations while preserving compatibility and migration boundaries where appropriate.'],
      ['Third-party systems','Connected providers remain subject to their own availability, permissions and terms.'],
    ]} />
  </>;
}

function Footer({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  return <footer className='public-footer'>
    <div className='public-container public-footer-grid'>
      <div className='public-footer-brand'><strong>Audoryn</strong><span>An SOT Product</span></div>
      <FooterGroup title='Product' items={[['Overview','product'],['Workforce','product'],['Governance','security'],['Integrations','product']]} onNavigate={onNavigate} />
      <FooterGroup title='Solutions' items={[['Support','solutions'],['Research','solutions'],['Operations','solutions'],['Development','solutions']]} onNavigate={onNavigate} />
      <FooterGroup title='Resources' items={[['Product','product'],['Security','security'],['Pricing','pricing']]} onNavigate={onNavigate} />
      <FooterGroup title='Company' items={[['About','company'],['Privacy','privacy'],['Terms','terms']]} onNavigate={onNavigate} />
    </div>
    <div className='public-container public-footer-bottom'><span>© 2026 SOT</span><span>Audoryn · Controlled autonomous workforce infrastructure</span></div>
  </footer>;
}

function ClosingCta({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  return <section className='public-closing'><div className='public-container'><p className='public-kicker'>AUDORYN</p><h2>Give AI real work.<br />Keep authority explicit.</h2><p>Start with a small workforce and build from there.</p><button onClick={() => onNavigate('signup')}>Get started <span>→</span></button></div></section>;
}

function PageIntro({ kicker, title, body }: { kicker: string; title: string; body: string }) {
  return <section className='public-page-intro public-container'><p className='public-kicker'>{kicker}</p><h1>{title}</h1><p>{body}</p></section>;
}

function SectionIntro({ kicker, title, body }: { kicker: string; title: string; body?: string }) {
  return <div className='public-section-intro'><p className='public-kicker'>{kicker}</p><h2>{title}</h2>{body && <p>{body}</p>}</div>;
}

function TextColumn({ number, title, body }: { number: string; title: string; body: string }) {
  return <div><span>{number}</span><h3>{title}</h3><p>{body}</p></div>;
}

function ControlRow({ title, body }: { title: string; body: string }) {
  return <div><h3>{title}</h3><p>{body}</p><span>→</span></div>;
}

function Plan({ name, audience, features, action, onClick }: { name: string; audience: string; features: string[]; action: string; onClick: () => void }) {
  return <article><p className='public-kicker'>{name}</p><h2>{name}</h2><p>{audience}</p><ul>{features.map((item) => <li key={item}>{item}</li>)}</ul><button onClick={onClick}>{action} <span>→</span></button></article>;
}

function Resource({ title, body, action, onClick }: { title: string; body: string; action: string; onClick: () => void }) {
  return <div><h2>{title}</h2><p>{body}</p><button className='public-text-action' onClick={onClick}>{action} <span>→</span></button></div>;
}

function LegalBody({ sections }: { sections: [string,string][] }) {
  return <section className='public-section public-container public-legal'><p className='public-legal-note'>This page is a product-level notice and not legal advice.</p>{sections.map(([title,body]) => <div key={title}><h2>{title}</h2><p>{body}</p></div>)}</section>;
}

function FooterGroup({ title, items, onNavigate }: { title: string; items: [string,TargetRoute][]; onNavigate: (route: TargetRoute) => void }) {
  return <div className='public-footer-group'><strong>{title}</strong>{items.map(([label,route]) => <button key={`${title}-${label}`} onClick={() => onNavigate(route)}>{label}</button>)}</div>;
}
