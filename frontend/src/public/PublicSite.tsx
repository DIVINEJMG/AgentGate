import { usePublicText, usePublicContent, usePublicImage, usePublicNavigate } from './content/ContentContext';
import { headerNavigation } from './content/navigation';
import { useEffect, useRef, useState, type ReactNode } from 'react';
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
import humanOversightFallback from './assets/human-oversight.webp';
import operationsFallback from './assets/operations-work.webp';
import researchFallback from './assets/research-work.webp';
import './public.css';
import './public-brand.css';
import './home-page.css';
import './home-composition.css';
import './public-navigation.css';

export type PublicRoute = 'home' | 'product' | 'solutions' | 'security' | 'pricing' | 'resources' | 'company' | 'privacy' | 'terms';
type TargetRoute = string;



type NavDestination = { label: string; route: string; detail: string; sectionId?: string; solutionIndex?: number; selectedRecordId?: string; path?:string;openInNewTab?:boolean };


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

export default function PublicSite({ route, onNavigate,content,selectedId }: { route: PublicRoute; onNavigate: (route: TargetRoute) => void;content?:ReactNode;selectedId?:string }) {
  onNavigate=usePublicNavigate(onNavigate);
  const text = usePublicText('PublicSite');
let NAV: { route: string; label: string; path?:string }[] = [
  { route: 'product', label: text('field-product-43edd262c8', "Product") },
  { route: 'solutions', label: text('field-solutions-243cceed9d', "Solutions") },
  { route: 'security', label: text('field-security-f51f263d72', "Security") },
  { route: 'resources', label: text('field-resources-0b0314f73c', "Resources") },
  { route: 'pricing', label: text('field-pricing-0b881a7ed8', "Pricing") },
  { route: 'company', label: text('field-company-37ed9eb4cf', "Company") },
];
let NAV_MENUS: Partial<Record<string, { intro: string; links: NavDestination[] }>> = {
  product: { intro: text('field-a-clear-place-for-workers-work-and-authority-ebef5844e8', "A clear place for workers, work and authority."), links: [
    { label: text('field-overview-75646d4665', "Overview"), route: 'product', detail: text('field-see-the-complete-operating-layer-660f4e6f3b', "See the complete operating layer.") },
    { label: text('field-workforce-0ce7ac9b63', "Workforce"), route: 'product', sectionId: 'product-workforce', detail: text('field-define-roles-jobs-and-supervision-5a258417a2', "Define roles, jobs and supervision.") },
    { label: text('field-governance-40c11ffb69', "Governance"), route: 'security', sectionId: 'security-boundary', detail: text('field-understand-the-control-path-7791499c92', "Understand the control path.") },
    { label: text('field-integrations-30b24f4199', "Integrations"), route: 'product', sectionId: 'product-integrations', detail: text('field-connect-work-to-your-tools-36926ab1ec', "Connect work to your tools.") },
  ] },
  solutions: { intro: text('field-start-with-a-responsibility-that-already-matt-9d4444c944', "Start with a responsibility that already matters."), links: [
    { label: text('field-support-8c15e21f9d', "Support"), route: 'solutions', sectionId: 'solution-index', solutionIndex: 2, detail: text('field-prioritize-the-inbox-ca29e69fbf', "Prioritize the inbox.") },
    { label: text('field-research-44ac779617', "Research"), route: 'solutions', sectionId: 'solution-index', solutionIndex: 1, detail: text('field-turn-sources-into-a-brief-57443e46cd', "Turn sources into a brief.") },
    { label: text('field-operations-51a0e197be', "Operations"), route: 'solutions', sectionId: 'solution-index', solutionIndex: 0, detail: text('field-keep-the-daily-picture-current-9f13e945b7', "Keep the daily picture current.") },
    { label: text('field-development-cd242bf7cb', "Development"), route: 'solutions', sectionId: 'solution-index', solutionIndex: 5, detail: text('field-review-repository-work-24a248d307', "Review repository work.") },
  ] },
  resources: { intro: text('field-find-the-detail-you-need-to-evaluate-audoryn-82abd1aaed', "Find the detail you need to evaluate Audoryn."), links: [
    { label: text('field-product-43edd262c8', "Product"), route: 'product', detail: text('field-how-the-system-works-28f284acdc', "How the system works.") },
    { label: text('field-security-f51f263d72', "Security"), route: 'security', detail: text('field-what-controls-every-action-e5ca22261f', "What controls every action.") },
    { label: text('field-pricing-0b881a7ed8', "Pricing"), route: 'pricing', detail: text('field-capacity-and-plan-availability-76c70514b3', "Capacity and plan availability.") },
  ] },
  company: { intro: text('field-learn-about-the-product-and-its-public-notice-89338479dd', "Learn about the product and its public notices."), links: [
    { label: text('field-about-77119b02ea', "About"), route: 'company', detail: text('field-why-sot-is-building-audoryn-6068750674', "Why SOT is building Audoryn.") },
    { label: text('field-privacy-d47bcea7df', "Privacy"), route: 'privacy', detail: text('field-how-product-data-is-handled-4b695420b7', "How product data is handled.") },
    { label: text('field-terms-5948616386', "Terms"), route: 'terms', detail: text('field-how-audoryn-may-be-used-9add043234', "How Audoryn may be used.") },
  ] },
};

  const [menuOpen, setMenuOpen] = useState(false);
  const bundle = usePublicContent();
  const publishedNavigation = headerNavigation(bundle);
  if (publishedNavigation) { NAV = publishedNavigation.nav; NAV_MENUS = publishedNavigation.menus; }
  const [scrolled, setScrolled] = useState(false);
  const [activeMenu, setActiveMenu] = useState<string | null>(null);
  const [destination, setDestination] = useState<NavDestination | null>(null);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const navButtons = useRef<Partial<Record<string, HTMLButtonElement | null>>>({});
  const panelRef = useRef<HTMLDivElement>(null);

  function clearHoverTimer() { if (hoverTimer.current) clearTimeout(hoverTimer.current); hoverTimer.current = null; }
  function clearCloseTimer() { if (closeTimer.current) clearTimeout(closeTimer.current); closeTimer.current = null; }
  function scheduleMenu(route: string) {
    clearHoverTimer(); clearCloseTimer();
    if (!NAV_MENUS[route]) { setActiveMenu(null); return; }
    hoverTimer.current = setTimeout(() => setActiveMenu(route), activeMenu ? 140 : 350);
  }
  function scheduleClose() { clearHoverTimer(); clearCloseTimer(); closeTimer.current = setTimeout(() => setActiveMenu(null), 260); }
  function navigateMain(nextRoute: string) {
    clearHoverTimer(); clearCloseTimer(); setActiveMenu(null); setDestination(null);
    if (nextRoute === route) window.scrollTo({ top: 0, behavior: 'auto' });
    const item=publishedNavigation?.nav.find(item=>item.route===nextRoute);if(item?.openInNewTab)window.open(item.path,'_blank','noopener,noreferrer');else onNavigate(NAV.find(item=>item.route===nextRoute)?.path || nextRoute);
  }
  function navigateSub(next: NavDestination) {
    clearHoverTimer(); clearCloseTimer(); setActiveMenu(null); setDestination({ ...next });
    const path = next.path || (next.route === 'home' ? '/' : '/'+next.route);
    const query = next.selectedRecordId ? '?selected='+encodeURIComponent(next.selectedRecordId) : '';
    const target=path+query+(next.sectionId ? '#'+encodeURIComponent(next.sectionId) : '');if(next.openInNewTab)window.open(target,'_blank','noopener,noreferrer');else onNavigate(target);
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
      <button className='public-wordmark' onPointerEnter={() => { clearHoverTimer(); setActiveMenu(null); }} onClick={() => onNavigate('home')} aria-label={text('copy-audoryn-home-efd53f6877', "Audoryn home")}><img src='/audoryn-mark.png' alt='' aria-hidden='true' />{text('copy-audoryn-ada8d1a9d6', "Audoryn")}</button>
      <nav className='public-nav' aria-label={text('copy-public-navigation-ec37bea8fd', "Public navigation")}>
        {NAV.map((item) => <button key={item.route} ref={(element) => { navButtons.current[item.route] = element; }} className={route === item.route || activeMenu === item.route ? 'active' : ''} aria-expanded={NAV_MENUS[item.route] ? activeMenu === item.route : undefined} aria-controls={NAV_MENUS[item.route] ? 'public-nav-panel' : undefined} onPointerEnter={(event) => { if (event.pointerType === 'mouse') scheduleMenu(item.route); }} onFocus={clearCloseTimer} onKeyDown={(event) => { if (event.key === 'ArrowDown' && NAV_MENUS[item.route]) { event.preventDefault(); clearHoverTimer(); clearCloseTimer(); setActiveMenu(item.route); requestAnimationFrame(() => panelRef.current?.querySelector<HTMLButtonElement>('button')?.focus()); } }} onClick={() => navigateMain(item.route)}>{item.label}</button>)}
      </nav>
      <div className='public-header-actions' onPointerEnter={() => { clearHoverTimer(); setActiveMenu(null); }}>
        <button className='public-signin' onClick={() => onNavigate('login')}>{text('copy-sign-in-e33983587f', "Sign in")}</button>
        <button className='public-cta small' onClick={() => onNavigate('signup')}>{text('copy-get-started-39de0215be', "Get started")}</button>
      </div>
      <button className='public-menu-button' onClick={() => setMenuOpen((open) => !open)} aria-expanded={menuOpen} aria-label={text('copy-open-navigation-782ef70628', "Open navigation")}>{menuOpen ? text('copy-close-bc57bf736e', "Close") : text('copy-menu-a6bcd2a69f', "Menu")}</button>
      {activeMenu && NAV_MENUS[activeMenu] && <div id='public-nav-panel' className='public-nav-panel' ref={panelRef} onPointerEnter={clearCloseTimer}>
        <div className='public-nav-panel-inner'>
          <div className='public-nav-panel-intro'><span>{text('copy-audoryn-b88932e7f1', "AUDORYN / ")}{activeMenu.toUpperCase()}</span><strong>{activeMenu.charAt(0).toUpperCase() + activeMenu.slice(1)}</strong><p>{NAV_MENUS[activeMenu].intro}</p><button type='button' onClick={() => navigateMain(activeMenu)}>{text('copy-explore-896702223e', "Explore ")}{activeMenu} <span aria-hidden='true'>↗</span></button></div>
          <div className='public-nav-panel-links'>{NAV_MENUS[activeMenu].links.map((link, index) => <button key={link.label} type='button' onClick={() => navigateSub(link)}><span className='public-nav-panel-number'>0{index + 1}</span><span><strong>{link.label}</strong><small>{link.detail}</small></span><span className='public-nav-panel-arrow' aria-hidden='true'>↗</span></button>)}</div>
        </div>
      </div>}
      {menuOpen && <div className='public-mobile-menu'>
        {NAV.map((item) => <button key={item.route} onClick={() => onNavigate(item.route)}>{item.label}</button>)}
        <span />
        <button onClick={() => onNavigate('login')}>{text('copy-sign-in-e33983587f', "Sign in")}</button>
        <button className='strong' onClick={() => onNavigate('signup')}>{text('copy-get-started-39de0215be', "Get started")}</button>
      </div>}
    </header>

    <main>
      {content || <>
      {route === 'home' && <HomePage onNavigate={onNavigate} />}
      {route === 'product' && <ProductPage onNavigate={onNavigate} />}
      {route === 'solutions' && <SolutionsPage onNavigate={onNavigate} selectedIndex={destination?.route === 'solutions' ? destination.solutionIndex : undefined} selectedId={selectedId||destination?.selectedRecordId} />}
      {route === 'security' && <SecurityPage onNavigate={onNavigate} />}
      {route === 'pricing' && <PricingPage onNavigate={onNavigate} />}
      {route === 'resources' && <ResourcesPage onNavigate={onNavigate} />}
      {route === 'company' && <CompanyShowcase onNavigate={onNavigate} />}
      {route === 'privacy' && <LegalShowcase kind='privacy' onNavigate={onNavigate} />}
      {route === 'terms' && <LegalShowcase kind='terms' onNavigate={onNavigate} />}
      </>}
    </main>

    <Footer onNavigate={onNavigate} />
  </div>;
}

function HomePage({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  const text = usePublicText('PublicSite');
  const humanOversightImage = usePublicImage('human-oversight',humanOversightFallback);
  const operationsImage = usePublicImage('operations-work',operationsFallback);
  const researchImage = usePublicImage('research-work',researchFallback);

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
        <SectionIntro kicker={text('copy-the-product-d18e80a034', "THE PRODUCT")} title={text('copy-an-operating-system-for-your-ai-workforce-e9f52fc686', "An operating system for your AI workforce.")} body={text('copy-audoryn-gives-businesses-one-place-to-create-8fbfea5f89', "Audoryn gives businesses one place to create workers, define work, connect tools and supervise the moments where human judgment matters.")} />
        <div className='public-three'>
          <TextColumn number='01' title={text('copy-create-workers-e6be9bc7a5', "Create workers")} body={text('copy-give-ai-clear-roles-responsibilities-schedule-68cfc014c5', "Give AI clear roles, responsibilities, schedules and a named human supervisor.")} />
          <TextColumn number='02' title={text('copy-assign-work-93ca34cc0c', "Assign work")} body={text('copy-turn-business-objectives-into-durable-jobs-th-04b1993d49', "Turn business objectives into durable jobs that can be scheduled, queued and inspected.")} />
          <TextColumn number='03' title={text('copy-stay-in-control-3e8a8a025d', "Stay in control")} body={text('copy-capabilities-policy-approvals-and-incident-co-6136632e91', "Capabilities, policy, approvals and incident controls remain separate from AI reasoning.")} />
        </div>
      </div>
    </section>

    <HomeJourney />

    <section className='public-section public-container' data-home-reveal>
      <SectionIntro kicker={text('copy-operating-view-5b1e501fee', "OPERATING VIEW")} title={text('copy-one-place-to-understand-what-your-ai-workforc-7c97753a20', "One place to understand what your AI workforce is doing.")} body={text('copy-see-workers-queued-jobs-governance-decisions-2c8c31c69f', "See workers, queued jobs, governance decisions and the moments that need human attention.")} />
      <HomeOperatingView />
    </section>

    <section className='home-interlude' data-home-reveal>
      <div className='public-container home-interlude-inner'>
        <div className='home-interlude-copy'>
          <p className='public-kicker'>{text('copy-the-audoryn-principle-1faeb03124', "THE AUDORYN PRINCIPLE")}</p>
          <h2><span>{text('copy-ai-can-propose-2047e44d8b', "AI can propose.")}</span><span>{text('copy-people-stay-in-control-369b3ea794', "People stay in control.")}</span></h2>
          <p>{text('copy-every-external-action-meets-a-boundary-the-mo-879b704982', "Every external action meets a boundary the model cannot rewrite. The result is useful autonomy with a clear line of responsibility.")}</p>
          <div className='home-interlude-decisions'><span>{text('copy-allow-e9bccce23f', "ALLOW")}</span><span>{text('copy-hold-for-review-10ca7a0360', "HOLD FOR REVIEW")}</span><span>{text('copy-deny-ce4391b410', "DENY")}</span></div>
        </div>
        <div className='home-interlude-aperture' aria-hidden='true'><span /><span /><span /><i /></div>
      </div>
    </section>

    <section className='public-section public-container home-control' data-home-reveal>
      <div className='home-human'>
        <div className='home-human-copy'>
          <p className='public-kicker'>{text('copy-human-oversight-e3fdd85a55', "HUMAN OVERSIGHT")}</p>
          <h2>{text('copy-autonomous-where-appropriate-8a73501311', "Autonomous where appropriate.")}<br />{text('copy-human-where-it-matters-ca3de22304', "Human where it matters.")}</h2>
          <p>{text('copy-workers-can-keep-moving-through-routine-work-f0bf2cf950', "Workers can keep moving through routine work. The decisions that need judgment return to a person with the context to act.")}</p>
          <span>{text('copy-approval-99d403180e', "APPROVAL ")}<i /> {text('copy--exception-df50478f70', " EXCEPTION ")}<i /> {text('copy--review-1a6d339474', " REVIEW")}</span>
        </div>
        <div className='home-human-image'><img src={humanOversightImage} onError={event=>{if(event.currentTarget.dataset.fallbackUsed)return;event.currentTarget.dataset.fallbackUsed='true';event.currentTarget.src=humanOversightFallback;}} alt={text('alt-two-colleagues-reviewing-a-decision-together-94a43fafbf', "Two colleagues reviewing a decision together at a table")} loading='lazy' /></div>
      </div>
      <p className='public-kicker home-control-list-label'>{text('copy-control-the-boundaries-in-practice-c254c9f723', "CONTROL / THE BOUNDARIES IN PRACTICE")}</p>
      <div className='public-control-list'>
        <ControlRow title={text('copy-capability-controls-c7e18997a5', "Capability controls")} body={text('copy-define-the-actions-an-ai-worker-may-attempt-75b620c40a', "Define the actions an AI worker may attempt.")} />
        <ControlRow title={text('copy-policy-734f024880', "Policy")} body={text('copy-make-authorization-deterministic-instead-of-l-069ef29891', "Make authorization deterministic instead of leaving it to model judgment.")} />
        <ControlRow title={text('copy-approvals-460f0f2a62', "Approvals")} body={text('copy-require-people-before-sensitive-work-can-cont-059bfe690c', "Require people before sensitive work can continue.")} />
        <ControlRow title={text('copy-supervision-9d6252e9d1', "Supervision")} body={text('copy-route-exceptional-autonomous-work-to-a-named-254bd8077a', "Route exceptional autonomous work to a named human.")} />
        <ControlRow title={text('copy-audit-877cd6b54c', "Audit")} body={text('copy-preserve-what-happened-who-acted-and-why-a-de-7cf5200292', "Preserve what happened, who acted and why a decision was made.")} />
      </div>
    </section>

    <section className='public-section public-container' data-home-reveal>
      <SectionIntro kicker={text('copy-connections-f48b6b0484', "CONNECTIONS")} title={text('copy-works-where-your-company-already-works-8c1d95a4c2', "Works where your company already works.")} body={text('copy-provider-connections-expose-bounded-technical-e834379ec0', "Provider connections expose bounded technical operations. They do not become AI permissions by themselves.")} />
      <div className='home-connections'>
        <div className='home-connections-heading'><span>{text('copy-connected-systems-f4710b3c90', "CONNECTED SYSTEMS")}</span><span>{text('copy-controlled-action-path-e01303753e', "CONTROLLED ACTION PATH")}</span></div>
        <div className='home-connections-flow'>
          <div className='public-integrations' aria-label={text('copy-supported-workplace-systems-776d337536', "Supported workplace systems")}>
            {[text('copy-github-699730e6d9', "GitHub"),text('copy-slack-781847fa86', "Slack"),text('copy-gmail-49d402f2d4', "Gmail"),text('copy-google-drive-8377bc3c67', "Google Drive"),text('copy-google-calendar-eba9dd31a8', "Google Calendar"),text('copy-rest-mcp-50782311cb', "REST / MCP")].map((name) => <span key={name}>{name}</span>)}
          </div>
          <div className='home-connections-boundary' aria-hidden='true'><span>{text('copy-policy-da94f8a0d4', "POLICY")}<br />{text('copy-boundary-c1372cc1e4', "BOUNDARY")}</span><i /></div>
          <div className='home-connections-result'>
            <span>{text('copy-available-action-608d11c0d3', "AVAILABLE ACTION")}</span>
            <strong>{text('copy-authority-is-checked-before-execution-83900e9160', "Authority is checked before execution.")}</strong>
            <small>{text('copy-capability-b074adec9c', "CAPABILITY ")}<b>→</b> {text('copy--policy-ff8e9b4e45', " POLICY ")}<b>→</b> {text('copy--approval-0e544e9130', " APPROVAL")}</small>
          </div>
        </div>
      </div>
    </section>

    <section className='home-solutions public-section' data-home-reveal>
      <div className='public-container'>
        <div className='home-solutions-heading'>
          <SectionIntro kicker={text('copy-solutions-ef281685b9', "SOLUTIONS")} title={text('copy-built-for-different-kinds-of-work-141bd82a36', "Built for different kinds of work.")} body={text('copy-start-with-a-defined-responsibility-give-the-30063a0b8d', "Start with a defined responsibility. Give the worker room to move, with a person and a policy around the actions that matter.")} />
          <span>{text('copy-selected-applications-01-06-9497a0c82d', "SELECTED APPLICATIONS / 01 — 06")}</span>
        </div>
        <div className='home-solution-feature-grid'>
          <button className='home-solution-feature home-solution-feature-main' onClick={() => onNavigate('solutions')}>
            <span className='home-solution-image'><img src={operationsImage} onError={event=>{if(event.currentTarget.dataset.fallbackUsed)return;event.currentTarget.dataset.fallbackUsed='true';event.currentTarget.src=operationsFallback;}} alt={text('alt-a-logistics-operations-floor-with-a-superviso-d9749d26b7', "A logistics operations floor with a supervisor overseeing the work")} loading='lazy' /></span>
            <span className='home-solution-copy'><small>{text('copy-01-operations-a8e081a39b', "01 / OPERATIONS")}</small><strong>{text('copy-keep-recurring-work-moving-keep-exceptions-vi-9d2189f9a9', "Keep recurring work moving. Keep exceptions visible.")}</strong><span>{text('copy-scheduled-work-connected-tools-and-a-clear-ha-3cdf9f12b5', "Scheduled work, connected tools and a clear handoff when conditions change.")}</span><em>{text('copy-explore-operations-801811b031', "Explore operations ")}<b>↗</b></em></span>
          </button>
          <button className='home-solution-feature home-solution-feature-secondary' onClick={() => onNavigate('solutions')}>
            <span className='home-solution-image'><img src={researchImage} onError={event=>{if(event.currentTarget.dataset.fallbackUsed)return;event.currentTarget.dataset.fallbackUsed='true';event.currentTarget.src=researchFallback;}} alt={text('alt-research-materials-being-reviewed-at-a-workta-518f7f9956', "Research materials being reviewed at a worktable")} loading='lazy' /></span>
            <span className='home-solution-copy'><small>{text('copy-02-research-955524e2d5', "02 / RESEARCH")}</small><strong>{text('copy-turn-open-questions-into-inspectable-work-bcf252ad68', "Turn open questions into inspectable work.")}</strong><span>{text('copy-collect-and-structure-findings-while-people-r-5008664ccb', "Collect and structure findings while people remain responsible for the result.")}</span><em>{text('copy-explore-research-e333d55298', "Explore research ")}<b>↗</b></em></span>
          </button>
        </div>
        <div className='home-solution-more' aria-label={text('copy-more-solution-areas-a9f7646013', "More solution areas")}>
          {[text('copy-customer-support-67aac9e13a', "Customer support"),text('copy-sales-ee63665ba2', "Sales"),text('copy-marketing-cd67025fdf', "Marketing"),text('copy-software-development-aaae25dc71', "Software development")].map((name, index) => <button key={name} onClick={() => onNavigate('solutions')}><span>0{index + 3}</span><strong>{name}</strong><em>↗</em></button>)}
        </div>
      </div>
    </section>

    <section className='home-trust public-section public-container' data-home-reveal>
      <div className='home-trust-intro'>
        <p className='public-kicker'>{text('copy-trust-by-design-6e944fddd7', "TRUST / BY DESIGN")}</p>
        <h2>{text('copy-control-is-part-of-the-architecture-63aaede39d', "Control is part of the architecture.")}</h2>
        <p>{text('copy-the-boundary-around-an-action-stays-visible-f-d5b20bcb56', "The boundary around an action stays visible from identity through the final record.")}</p>
        <button className='public-text-action' onClick={() => onNavigate('security')}>{text('copy-read-about-security-a7095311a5', "Read about security ")}<span>→</span></button>
      </div>
      <ol className='home-trust-track'>
        <li><span>01</span><strong>{text('copy-identity-c277009abf', "Identity")}</strong><p>{text('copy-human-and-ai-identities-remain-distinct-5c0abb33a9', "Human and AI identities remain distinct.")}</p></li>
        <li><span>02</span><strong>{text('copy-authorization-e17f33e657', "Authorization")}</strong><p>{text('copy-every-external-action-is-checked-against-capa-a84028b421', "Every external action is checked against capability and policy.")}</p></li>
        <li><span>03</span><strong>{text('copy-approval-703a8bd7f1', "Approval")}</strong><p>{text('copy-sensitive-work-can-pause-for-a-person-b9f2703367', "Sensitive work can pause for a person.")}</p></li>
        <li><span>04</span><strong>{text('copy-audit-877cd6b54c', "Audit")}</strong><p>{text('copy-the-decision-and-outcome-stay-connected-in-a-0509f75985', "The decision and outcome stay connected in a record.")}</p></li>
      </ol>
    </section>

    <ClosingCta onNavigate={onNavigate} />
  </div>;
}

function ProductPage({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {

  return <ProductShowcase onNavigate={onNavigate} />;
}

function SolutionsPage({ onNavigate, selectedIndex,selectedId }: { onNavigate: (route: TargetRoute) => void; selectedIndex?: number;selectedId?:string }) {

  return <SolutionsShowcase onNavigate={onNavigate} selectedIndex={selectedIndex} selectedId={selectedId} />;
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
  const text = usePublicText('PublicSite');
  const bundle=usePublicContent();
  const navigation=headerNavigation(bundle,'footer');

  return <footer className='public-footer'>
    <div className='public-container public-footer-grid'>
      <div className='public-footer-brand'><strong>{text('copy-audoryn-ada8d1a9d6', "Audoryn")}</strong><span>{text('copy-an-sot-product-a0e73f58fc', "An SOT Product")}</span></div>
      {navigation ? navigation.nav.map(group=><FooterGroup key={group.route} title={group.label} items={(navigation.menus[group.route]?.links||[]).map(item=>[item.label,(item.path||item.route)+(item.selectedRecordId?'?selected='+encodeURIComponent(item.selectedRecordId):'')+(item.sectionId?'#'+encodeURIComponent(item.sectionId):''),item.openInNewTab])} onNavigate={onNavigate}/>) : <>
      <FooterGroup title={text('copy-product-6e389d51f1', "Product")} items={[['Overview','product'],['Workforce','product'],['Governance','security'],['Integrations','product']]} onNavigate={onNavigate} />
      <FooterGroup title={text('copy-solutions-9d0bd7d665', "Solutions")} items={[['Support','solutions'],['Research','solutions'],['Operations','solutions'],['Development','solutions']]} onNavigate={onNavigate} />
      <FooterGroup title={text('copy-resources-0b05d56444', "Resources")} items={[['Product','product'],['Security','security'],['Pricing','pricing']]} onNavigate={onNavigate} />
      <FooterGroup title={text('copy-company-b987c6cf29', "Company")} items={[['About','company'],['Privacy','privacy'],['Terms','terms']]} onNavigate={onNavigate} />
      </>}
    </div>
    <div className='public-container public-footer-bottom'><span>{text('copy--2026-sot-08b6351255', "© 2026 SOT")}</span><span>{text('copy-audoryn-controlled-autonomous-workforce-infra-7d3f2f219a', "Audoryn · Controlled autonomous workforce infrastructure")}</span></div>
  </footer>;
}

function ClosingCta({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  const text = usePublicText('PublicSite');

  return <section className='public-closing'><div className='public-container'><p className='public-kicker'>{text('copy-audoryn-882e539bc3', "AUDORYN")}</p><h2>{text('copy-give-ai-real-work-e3a91985c2', "Give AI real work.")}<br />{text('copy-keep-authority-explicit-e0ff65481c', "Keep authority explicit.")}</h2><p>{text('copy-start-with-a-small-workforce-and-build-from-t-30cfedbe24', "Start with a small workforce and build from there.")}</p><button onClick={() => onNavigate('signup')}>{text('copy-get-started-d9af712b77', "Get started ")}<span>→</span></button></div></section>;
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

function FooterGroup({ title, items, onNavigate }: { title: string; items: [string,TargetRoute,boolean?][]; onNavigate: (route: TargetRoute) => void }) {

  return <div className='public-footer-group'><strong>{title}</strong>{items.map(([label,route,newTab]) => <button key={`${title}-${label}`} onClick={() => newTab?window.open(route,'_blank','noopener,noreferrer'):onNavigate(route)}>{label}</button>)}</div>;
}
