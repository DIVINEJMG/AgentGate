import { useEffect, useRef, useState } from 'react';
import HomeHero from './HomeHero';
import HomeJourney from './HomeJourney';
import HomeOperatingView from './HomeOperatingView';
import ProductShowcase from './ProductShowcase';
import SolutionsShowcase from './SolutionsShowcase';
import SecurityShowcase from './SecurityShowcase';
import PricingShowcase from './PricingShowcase';
import ResourcesShowcase from './ResourcesShowcase';
import LegalShowcase from './LegalShowcase';
import CompanyShowcase from './CompanyShowcase';
import humanOversightImage from './assets/human-oversight.webp';
import operationsImage from './assets/operations-work.webp';
import researchImage from './assets/research-work.webp';
import './public.css';
import './public-brand.css';
import './home-page.css';
import './home-composition.css';
import './public-navigation.css';

export type PublicRoute = 'home' | 'product' | 'solutions' | 'security' | 'pricing' | 'resources' | 'company' | 'privacy' | 'terms';
type TargetRoute = PublicRoute | 'login' | 'signup' | 'app';

const NAV: { route: PublicRoute; label: string }[] = [
  { route: 'product', label: 'Product' },
  { route: 'solutions', label: 'Solutions' },
  { route: 'security', label: 'Security' },
  { route: 'resources', label: 'Resources' },
  { route: 'pricing', label: 'Pricing' },
  { route: 'company', label: 'Company' },
];

type NavDestination = { label: string; route: PublicRoute; detail: string; sectionId?: string; solutionIndex?: number };
const NAV_MENUS: Partial<Record<PublicRoute, { intro: string; links: NavDestination[] }>> = {
  product: { intro: 'A clear place for workers, work and authority.', links: [
    { label: 'Overview', route: 'product', detail: 'See the complete operating layer.' },
    { label: 'Workforce', route: 'product', sectionId: 'product-workforce', detail: 'Define roles, jobs and supervision.' },
    { label: 'Governance', route: 'security', sectionId: 'security-boundary', detail: 'Understand the control path.' },
    { label: 'Integrations', route: 'product', sectionId: 'product-integrations', detail: 'Connect work to your tools.' },
  ] },
  solutions: { intro: 'Start with a responsibility that already matters.', links: [
    { label: 'Support', route: 'solutions', sectionId: 'solution-index', solutionIndex: 2, detail: 'Prioritize the inbox.' },
    { label: 'Research', route: 'solutions', sectionId: 'solution-index', solutionIndex: 1, detail: 'Turn sources into a brief.' },
    { label: 'Operations', route: 'solutions', sectionId: 'solution-index', solutionIndex: 0, detail: 'Keep the daily picture current.' },
    { label: 'Development', route: 'solutions', sectionId: 'solution-index', solutionIndex: 5, detail: 'Review repository work.' },
  ] },
  resources: { intro: 'Find the detail you need to evaluate Audoryn.', links: [
    { label: 'Product', route: 'product', detail: 'How the system works.' },
    { label: 'Security', route: 'security', detail: 'What controls every action.' },
    { label: 'Pricing', route: 'pricing', detail: 'Capacity and plan availability.' },
  ] },
  company: { intro: 'Learn about the product and its public notices.', links: [
    { label: 'About', route: 'company', detail: 'Why SOT is building Audoryn.' },
    { label: 'Privacy', route: 'privacy', detail: 'How product data is handled.' },
    { label: 'Terms', route: 'terms', detail: 'How Audoryn may be used.' },
  ] },
};

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
  const [scrolled, setScrolled] = useState(false);
  const [activeMenu, setActiveMenu] = useState<PublicRoute | null>(null);
  const [destination, setDestination] = useState<NavDestination | null>(null);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const navButtons = useRef<Partial<Record<PublicRoute, HTMLButtonElement | null>>>({});
  const panelRef = useRef<HTMLDivElement>(null);

  function clearHoverTimer() { if (hoverTimer.current) clearTimeout(hoverTimer.current); hoverTimer.current = null; }
  function clearCloseTimer() { if (closeTimer.current) clearTimeout(closeTimer.current); closeTimer.current = null; }
  function scheduleMenu(route: PublicRoute) {
    clearHoverTimer(); clearCloseTimer();
    if (!NAV_MENUS[route]) { setActiveMenu(null); return; }
    hoverTimer.current = setTimeout(() => setActiveMenu(route), activeMenu ? 140 : 350);
  }
  function scheduleClose() { clearHoverTimer(); clearCloseTimer(); closeTimer.current = setTimeout(() => setActiveMenu(null), 260); }
  function navigateMain(nextRoute: PublicRoute) {
    clearHoverTimer(); clearCloseTimer(); setActiveMenu(null); setDestination(null);
    if (nextRoute === route) window.scrollTo({ top: 0, behavior: 'auto' });
    onNavigate(nextRoute);
  }
  function navigateSub(next: NavDestination) {
    clearHoverTimer(); clearCloseTimer(); setActiveMenu(null); setDestination({ ...next });
    onNavigate(next.route);
  }

  useEffect(() => {
    document.title = TITLES[route];
    window.scrollTo({ top: 0, behavior: 'auto' });
    setMenuOpen(false);
    setActiveMenu(null);
  }, [route]);

  useEffect(() => {
    if (!destination || destination.route !== route) return;
    const frame = requestAnimationFrame(() => {
      if (destination.sectionId) (document.getElementById(destination.sectionId) ?? document.querySelector(`.${destination.sectionId}`))?.scrollIntoView({ block: 'start', behavior: 'auto' });
      else window.scrollTo({ top: 0, behavior: 'auto' });
    });
    return () => cancelAnimationFrame(frame);
  }, [destination, route]);

  useEffect(() => () => { clearHoverTimer(); clearCloseTimer(); }, []);

  useEffect(() => {
    if (route !== 'home') return;
    const update = () => setScrolled(window.scrollY > 8);
    update();
    window.addEventListener('scroll', update, { passive: true });
    return () => window.removeEventListener('scroll', update);
  }, [route]);

  return <div className='public-site'>
    <header className={`public-header${route === 'home' ? ` public-header-home${scrolled ? ' is-scrolled' : ''}` : ''}${activeMenu ? ' has-dropdown' : ''}`} onPointerLeave={scheduleClose} onKeyDown={(event) => { if (event.key === 'Escape' && activeMenu) { clearHoverTimer(); clearCloseTimer(); navButtons.current[activeMenu]?.focus(); setActiveMenu(null); } }} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) scheduleClose(); }}>
      <button className='public-wordmark' onPointerEnter={() => { clearHoverTimer(); setActiveMenu(null); }} onClick={() => onNavigate('home')} aria-label='Audoryn home'><img src='/audoryn-mark.png' alt='' aria-hidden='true' />Audoryn</button>
      <nav className='public-nav' aria-label='Public navigation'>
        {NAV.map((item) => <button key={item.route} ref={(element) => { navButtons.current[item.route] = element; }} className={route === item.route || activeMenu === item.route ? 'active' : ''} aria-expanded={NAV_MENUS[item.route] ? activeMenu === item.route : undefined} aria-controls={NAV_MENUS[item.route] ? 'public-nav-panel' : undefined} onPointerEnter={(event) => { if (event.pointerType === 'mouse') scheduleMenu(item.route); }} onFocus={clearCloseTimer} onKeyDown={(event) => { if (event.key === 'ArrowDown' && NAV_MENUS[item.route]) { event.preventDefault(); clearHoverTimer(); clearCloseTimer(); setActiveMenu(item.route); requestAnimationFrame(() => panelRef.current?.querySelector<HTMLButtonElement>('button')?.focus()); } }} onClick={() => navigateMain(item.route)}>{item.label}</button>)}
      </nav>
      <div className='public-header-actions' onPointerEnter={() => { clearHoverTimer(); setActiveMenu(null); }}>
        <button className='public-signin' onClick={() => onNavigate('login')}>Sign in</button>
        <button className='public-cta small' onClick={() => onNavigate('signup')}>Get started</button>
      </div>
      <button className='public-menu-button' onClick={() => setMenuOpen((open) => !open)} aria-expanded={menuOpen} aria-label='Open navigation'>{menuOpen ? 'Close' : 'Menu'}</button>
      {activeMenu && NAV_MENUS[activeMenu] && <div id='public-nav-panel' className='public-nav-panel' ref={panelRef} onPointerEnter={clearCloseTimer}>
        <div className='public-nav-panel-inner'>
          <div className='public-nav-panel-intro'><span>AUDORYN / {activeMenu.toUpperCase()}</span><strong>{activeMenu.charAt(0).toUpperCase() + activeMenu.slice(1)}</strong><p>{NAV_MENUS[activeMenu].intro}</p><button type='button' onClick={() => navigateMain(activeMenu)}>Explore {activeMenu} <span aria-hidden='true'>↗</span></button></div>
          <div className='public-nav-panel-links'>{NAV_MENUS[activeMenu].links.map((link, index) => <button key={link.label} type='button' onClick={() => navigateSub(link)}><span className='public-nav-panel-number'>0{index + 1}</span><span><strong>{link.label}</strong><small>{link.detail}</small></span><span className='public-nav-panel-arrow' aria-hidden='true'>↗</span></button>)}</div>
        </div>
      </div>}
      {menuOpen && <div className='public-mobile-menu'>
        {NAV.map((item) => <button key={item.route} onClick={() => onNavigate(item.route)}>{item.label}</button>)}
        <span />
        <button onClick={() => onNavigate('login')}>Sign in</button>
        <button className='strong' onClick={() => onNavigate('signup')}>Get started</button>
      </div>}
    </header>

    <main>
      {route === 'home' && <HomePage onNavigate={onNavigate} />}
      {route === 'product' && <ProductPage onNavigate={onNavigate} />}
      {route === 'solutions' && <SolutionsPage onNavigate={onNavigate} selectedIndex={destination?.route === 'solutions' ? destination.solutionIndex : undefined} />}
      {route === 'security' && <SecurityPage onNavigate={onNavigate} />}
      {route === 'pricing' && <PricingPage onNavigate={onNavigate} />}
      {route === 'resources' && <ResourcesPage onNavigate={onNavigate} />}
      {route === 'company' && <CompanyShowcase onNavigate={onNavigate} />}
      {route === 'privacy' && <LegalShowcase kind='privacy' onNavigate={onNavigate} />}
      {route === 'terms' && <LegalShowcase kind='terms' onNavigate={onNavigate} />}
    </main>

    <Footer onNavigate={onNavigate} />
  </div>;
}

function HomePage({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const root = document.querySelector<HTMLElement>('.home-page');
    if (!root || !('IntersectionObserver' in window)) return;
    const elements = root.querySelectorAll<HTMLElement>('[data-home-reveal]');
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
    elements.forEach((element) => observer.observe(element));
    root.classList.add('home-motion-ready');
    return () => { observer.disconnect(); root.classList.remove('home-motion-ready'); };
  }, []);

  return <div className='home-page'>
    <HomeHero onNavigate={onNavigate} />

    <section className='home-product public-section' data-home-reveal>
      <div className='public-container'>
        <SectionIntro kicker='THE PRODUCT' title='An operating system for your AI workforce.' body='Audoryn gives businesses one place to create workers, define work, connect tools and supervise the moments where human judgment matters.' />
        <div className='public-three'>
          <TextColumn number='01' title='Create workers' body='Give AI clear roles, responsibilities, schedules and a named human supervisor.' />
          <TextColumn number='02' title='Assign work' body='Turn business objectives into durable jobs that can be scheduled, queued and inspected.' />
          <TextColumn number='03' title='Stay in control' body='Capabilities, policy, approvals and incident controls remain separate from AI reasoning.' />
        </div>
      </div>
    </section>

    <HomeJourney />

    <section className='public-section public-container' data-home-reveal>
      <SectionIntro kicker='OPERATING VIEW' title='One place to understand what your AI workforce is doing.' body='See workers, queued jobs, governance decisions and the moments that need human attention.' />
      <HomeOperatingView />
    </section>

    <section className='home-interlude' data-home-reveal>
      <div className='public-container home-interlude-inner'>
        <div className='home-interlude-copy'>
          <p className='public-kicker'>THE AUDORYN PRINCIPLE</p>
          <h2><span>AI can propose.</span><span>People stay in control.</span></h2>
          <p>Every external action meets a boundary the model cannot rewrite. The result is useful autonomy with a clear line of responsibility.</p>
          <div className='home-interlude-decisions'><span>ALLOW</span><span>HOLD FOR REVIEW</span><span>DENY</span></div>
        </div>
        <div className='home-interlude-aperture' aria-hidden='true'><span /><span /><span /><i /></div>
      </div>
    </section>

    <section className='public-section public-container home-control' data-home-reveal>
      <div className='home-human'>
        <div className='home-human-copy'>
          <p className='public-kicker'>HUMAN OVERSIGHT</p>
          <h2>Autonomous where appropriate.<br />Human where it matters.</h2>
          <p>Workers can keep moving through routine work. The decisions that need judgment return to a person with the context to act.</p>
          <span>APPROVAL <i /> EXCEPTION <i /> REVIEW</span>
        </div>
        <div className='home-human-image'><img src={humanOversightImage} alt='Two colleagues reviewing a decision together at a table' loading='lazy' /></div>
      </div>
      <p className='public-kicker home-control-list-label'>CONTROL / THE BOUNDARIES IN PRACTICE</p>
      <div className='public-control-list'>
        <ControlRow title='Capability controls' body='Define the actions an AI worker may attempt.' />
        <ControlRow title='Policy' body='Make authorization deterministic instead of leaving it to model judgment.' />
        <ControlRow title='Approvals' body='Require people before sensitive work can continue.' />
        <ControlRow title='Supervision' body='Route exceptional autonomous work to a named human.' />
        <ControlRow title='Audit' body='Preserve what happened, who acted and why a decision was made.' />
      </div>
    </section>

    <section className='public-section public-container' data-home-reveal>
      <SectionIntro kicker='CONNECTIONS' title='Works where your company already works.' body='Provider connections expose bounded technical operations. They do not become AI permissions by themselves.' />
      <div className='home-connections'>
        <div className='home-connections-heading'><span>CONNECTED SYSTEMS</span><span>CONTROLLED ACTION PATH</span></div>
        <div className='home-connections-flow'>
          <div className='public-integrations' aria-label='Supported workplace systems'>
            {['GitHub','Slack','Gmail','Google Drive','Google Calendar','REST / MCP'].map((name) => <span key={name}>{name}</span>)}
          </div>
          <div className='home-connections-boundary' aria-hidden='true'><span>POLICY<br />BOUNDARY</span><i /></div>
          <div className='home-connections-result'>
            <span>AVAILABLE ACTION</span>
            <strong>Authority is checked before execution.</strong>
            <small>CAPABILITY <b>→</b> POLICY <b>→</b> APPROVAL</small>
          </div>
        </div>
      </div>
    </section>

    <section className='home-solutions public-section' data-home-reveal>
      <div className='public-container'>
        <div className='home-solutions-heading'>
          <SectionIntro kicker='SOLUTIONS' title='Built for different kinds of work.' body='Start with a defined responsibility. Give the worker room to move, with a person and a policy around the actions that matter.' />
          <span>SELECTED APPLICATIONS / 01 — 06</span>
        </div>
        <div className='home-solution-feature-grid'>
          <button className='home-solution-feature home-solution-feature-main' onClick={() => onNavigate('solutions')}>
            <span className='home-solution-image'><img src={operationsImage} alt='A logistics operations floor with a supervisor overseeing the work' loading='lazy' /></span>
            <span className='home-solution-copy'><small>01 / OPERATIONS</small><strong>Keep recurring work moving. Keep exceptions visible.</strong><span>Scheduled work, connected tools and a clear handoff when conditions change.</span><em>Explore operations <b>↗</b></em></span>
          </button>
          <button className='home-solution-feature home-solution-feature-secondary' onClick={() => onNavigate('solutions')}>
            <span className='home-solution-image'><img src={researchImage} alt='Research materials being reviewed at a worktable' loading='lazy' /></span>
            <span className='home-solution-copy'><small>02 / RESEARCH</small><strong>Turn open questions into inspectable work.</strong><span>Collect and structure findings while people remain responsible for the result.</span><em>Explore research <b>↗</b></em></span>
          </button>
        </div>
        <div className='home-solution-more' aria-label='More solution areas'>
          {['Customer support','Sales','Marketing','Software development'].map((name, index) => <button key={name} onClick={() => onNavigate('solutions')}><span>0{index + 3}</span><strong>{name}</strong><em>↗</em></button>)}
        </div>
      </div>
    </section>

    <section className='home-trust public-section public-container' data-home-reveal>
      <div className='home-trust-intro'>
        <p className='public-kicker'>TRUST / BY DESIGN</p>
        <h2>Control is part of the architecture.</h2>
        <p>The boundary around an action stays visible from identity through the final record.</p>
        <button className='public-text-action' onClick={() => onNavigate('security')}>Read about security <span>→</span></button>
      </div>
      <ol className='home-trust-track'>
        <li><span>01</span><strong>Identity</strong><p>Human and AI identities remain distinct.</p></li>
        <li><span>02</span><strong>Authorization</strong><p>Every external action is checked against capability and policy.</p></li>
        <li><span>03</span><strong>Approval</strong><p>Sensitive work can pause for a person.</p></li>
        <li><span>04</span><strong>Audit</strong><p>The decision and outcome stay connected in a record.</p></li>
      </ol>
    </section>

    <ClosingCta onNavigate={onNavigate} />
  </div>;
}

function ProductPage({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  return <ProductShowcase onNavigate={onNavigate} />;
}

function SolutionsPage({ onNavigate, selectedIndex }: { onNavigate: (route: TargetRoute) => void; selectedIndex?: number }) {
  return <SolutionsShowcase onNavigate={onNavigate} selectedIndex={selectedIndex} />;
}

function SecurityPage({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  return <SecurityShowcase onNavigate={onNavigate} />;
}

function PricingPage({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  return <PricingShowcase onNavigate={onNavigate} />;
}

function ResourcesPage({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  return <ResourcesShowcase onNavigate={onNavigate} />;
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

function SectionIntro({ kicker, title, body }: { kicker: string; title: string; body?: string }) {
  return <div className='public-section-intro'><p className='public-kicker'>{kicker}</p><h2>{title}</h2>{body && <p>{body}</p>}</div>;
}

function TextColumn({ number, title, body }: { number: string; title: string; body: string }) {
  return <div><span>{number}</span><h3>{title}</h3><p>{body}</p></div>;
}

function ControlRow({ title, body }: { title: string; body: string }) {
  return <div><h3>{title}</h3><p>{body}</p><span>→</span></div>;
}

function FooterGroup({ title, items, onNavigate }: { title: string; items: [string,TargetRoute][]; onNavigate: (route: TargetRoute) => void }) {
  return <div className='public-footer-group'><strong>{title}</strong>{items.map(([label,route]) => <button key={`${title}-${label}`} onClick={() => onNavigate(route)}>{label}</button>)}</div>;
}
