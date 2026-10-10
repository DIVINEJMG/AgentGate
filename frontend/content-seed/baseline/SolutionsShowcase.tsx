import { useEffect, useState } from 'react';
import { ArrowDown, ArrowRight, ArrowUpRight, BriefcaseBusiness, CalendarDays, Code2, FlaskConical, Headset, Megaphone } from 'lucide-react';
import operationsImage from './assets/operations-work.webp';
import './solutions-showcase.css';

const solutions = [
  { name: 'Operations', icon: BriefcaseBusiness, line: 'Keep the daily picture current.', worker: 'Operations Assistant', job: 'Daily Operations Brief', detail: 'Review connected work context and prepare a concise internal update, with exceptions surfaced for a person.', tools: 'Google Drive · Google Calendar', output: 'A reviewable daily brief' },
  { name: 'Research', icon: FlaskConical, line: 'Turn source material into a clear brief.', worker: 'Research Assistant', job: 'Repository Research Brief', detail: 'Read issues and pull requests, separate observed facts from inference and assemble a technical change brief.', tools: 'GitHub', output: 'A structured research brief' },
  { name: 'Customer support', icon: Headset, line: 'See what needs attention first.', worker: 'Customer Support Assistant', job: 'Inbox Triage', detail: 'Review recent messages and prepare a prioritized support triage brief for the team.', tools: 'Gmail', output: 'A prioritized triage brief' },
  { name: 'Sales', icon: CalendarDays, line: 'Arrive prepared for the next meeting.', worker: 'Sales Assistant', job: 'Upcoming Meeting Brief', detail: 'Review upcoming meetings and relevant prospect context to prepare supervised follow-up.', tools: 'Google Calendar · Gmail', output: 'An upcoming meeting brief' },
  { name: 'Marketing', icon: Megaphone, line: 'Make campaign context easier to use.', worker: 'Marketing Coordinator', job: 'Campaign Asset Review', detail: 'Review recent campaign material and summarize what the team needs to know.', tools: 'Google Drive', output: 'A campaign material summary' },
  { name: 'Engineering', icon: Code2, line: 'Stay close to repository work.', worker: 'Developer Assistant', job: 'Engineering Change Review', detail: 'Review repository issues and pull requests to prepare a concise engineering status brief.', tools: 'GitHub', output: 'An engineering status brief' },
] as const;

export default function SolutionsShowcase({ onNavigate, selectedIndex }: { onNavigate: (route: 'product' | 'signup') => void; selectedIndex?: number }) {
  const [selected, setSelected] = useState(selectedIndex ?? 0);
  const solution = solutions[selected];

  useEffect(() => { if (selectedIndex !== undefined) setSelected(selectedIndex); }, [selectedIndex]);

  function exploreSolutions() {
    document.getElementById('solution-index')?.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      block: 'start',
    });
  }

  useEffect(() => {
    const root = document.querySelector<HTMLElement>('.solutions-page');
    if (!root || window.matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) return;
    const observer = new IntersectionObserver((entries) => { entries.forEach((entry) => { if (entry.isIntersecting) { entry.target.classList.add('is-visible'); observer.unobserve(entry.target); } }); }, { threshold: .1 });
    const items = root.querySelectorAll<HTMLElement>('[data-solutions-reveal]');
    items.forEach((item) => observer.observe(item));
    root.classList.add('solutions-motion-ready');
    return () => { observer.disconnect(); root.classList.remove('solutions-motion-ready'); };
  }, []);

  return <div className='solutions-page'>
    <section className='solutions-hero'>
      <div className='solutions-hero-photo'><img src={operationsImage} alt='Operations team working in a supervised environment' /></div>
      <div className='solutions-hero-wash' />
      <div className='public-container solutions-hero-content'><p className='solutions-kicker'>AUDORYN / SOLUTIONS</p><h1>Work that already<br/>needs doing.</h1><p>Start with a defined responsibility. Give an AI worker the right context, a clear job and a person to answer to.</p><button type='button' onClick={exploreSolutions}>Explore where to start <ArrowDown size={15}/></button></div>
      <div className='public-container solutions-hero-bottom'><span>ONE WORKFORCE / MANY KINDS OF WORK</span><span>01 — 06</span></div>
    </section>

    <section id='solution-index' className='solutions-index public-container' data-solutions-reveal>
      <div className='solutions-index-head'><div><p className='solutions-kicker'>WHERE TO START</p><h2>Choose a responsibility.<br/>Start with a clear job.</h2></div><p>Six practical starting points, grounded in Audoryn’s workforce templates.</p></div>
      <div className='solutions-explorer'>
        <div className='solutions-list' aria-label='Solution areas'>{solutions.map((item, index) => { const Icon = item.icon; return <button key={item.name} type='button' className={selected === index ? 'is-active' : ''} aria-pressed={selected === index} onClick={() => setSelected(index)}><span className='solutions-list-number'>0{index + 1}</span><Icon size={18} strokeWidth={1.7} aria-hidden='true'/><span className='solutions-list-name'>{item.name}</span><ArrowUpRight className='solutions-list-arrow' size={16} aria-hidden='true'/></button>; })}</div>
        <div className='solutions-detail' aria-live='polite' key={solution.name}><div className='solutions-detail-top'><span>EXAMPLE STARTING POINT / 0{selected + 1}</span><solution.icon size={28} strokeWidth={1.4} aria-hidden='true'/></div><h3>{solution.line}</h3><p>{solution.detail}</p><div className='solutions-detail-rule'/><dl><div><dt>WORKER</dt><dd>{solution.worker}</dd></div><div><dt>EXAMPLE JOB</dt><dd>{solution.job}</dd></div><div><dt>CONNECTED CONTEXT</dt><dd>{solution.tools}</dd></div><div><dt>DELIVERABLE</dt><dd>{solution.output}</dd></div></dl><span className='solutions-detail-foot'>DEFINED ROLE · BOUNDED TOOLS · HUMAN SUPERVISION</span></div>
      </div>
      <p className='solutions-template-note'>Templates create draft roles and jobs. Connections, capabilities, policy and activation remain explicit setup steps.</p>
    </section>

    <section className='solutions-close' data-solutions-reveal><div className='public-container solutions-close-inner'><div><p className='solutions-kicker'>YOUR FIRST WORKER</p><h2>Start with one useful responsibility.</h2><p>Explore how the workforce runs, or set up a supervised worker in Audoryn.</p></div><div><button type='button' className='solutions-primary' onClick={() => onNavigate('signup')}>Get started <ArrowRight size={16}/></button><button type='button' className='solutions-secondary' onClick={() => onNavigate('product')}>See the product <ArrowUpRight size={16}/></button></div></div></section>
  </div>;
}
