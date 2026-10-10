import { usePublicText, usePublicCollection, usePublicImage } from './content/ContentContext';
import { useEffect, useState } from 'react';
import { ArrowDown, ArrowRight, ArrowUpRight, BriefcaseBusiness, CalendarDays, Code2, FlaskConical, Headset, Megaphone } from 'lucide-react';
import operationsFallback from './assets/operations-work.webp';
import './solutions-showcase.css';



export default function SolutionsShowcase({ onNavigate, selectedIndex, selectedId }: { onNavigate: (route: 'product' | 'signup') => void; selectedIndex?: number; selectedId?:string }) {
  const text = usePublicText('SolutionsShowcase');
const solutions = usePublicCollection('SolutionsShowcase','solutions', [
  { name: text('field-operations-114313e251', "Operations"), icon: BriefcaseBusiness, line: text('field-keep-the-daily-picture-current-31f44d03fe', "Keep the daily picture current."), worker: text('field-operations-assistant-406d77b14a', "Operations Assistant"), job: text('field-daily-operations-brief-3266259037', "Daily Operations Brief"), detail: text('field-review-connected-work-context-and-prepare-a-c-ce11161426', "Review connected work context and prepare a concise internal update, with exceptions surfaced for a person."), tools: text('field-google-drive-google-calendar-968b4e5d57', "Google Drive · Google Calendar"), output: text('field-a-reviewable-daily-brief-f6fd470268', "A reviewable daily brief") },
  { name: text('field-research-ed04fe7616', "Research"), icon: FlaskConical, line: text('field-turn-source-material-into-a-clear-brief-a371fe8849', "Turn source material into a clear brief."), worker: text('field-research-assistant-e2bfbd8114', "Research Assistant"), job: text('field-repository-research-brief-cfe2c25681', "Repository Research Brief"), detail: text('field-read-issues-and-pull-requests-separate-observ-afc6df5006', "Read issues and pull requests, separate observed facts from inference and assemble a technical change brief."), tools: text('field-github-be59dc1624', "GitHub"), output: text('field-a-structured-research-brief-080dbbddde', "A structured research brief") },
  { name: text('field-customer-support-4d8eddf2b7', "Customer support"), icon: Headset, line: text('field-see-what-needs-attention-first-910242d028', "See what needs attention first."), worker: text('field-customer-support-assistant-ae15f59a69', "Customer Support Assistant"), job: text('field-inbox-triage-2c6c13c59c', "Inbox Triage"), detail: text('field-review-recent-messages-and-prepare-a-prioriti-13d1abaf75', "Review recent messages and prepare a prioritized support triage brief for the team."), tools: text('field-gmail-3071fbf302', "Gmail"), output: text('field-a-prioritized-triage-brief-6928c64c08', "A prioritized triage brief") },
  { name: text('field-sales-149b1f2a9b', "Sales"), icon: CalendarDays, line: text('field-arrive-prepared-for-the-next-meeting-758c9daac3', "Arrive prepared for the next meeting."), worker: text('field-sales-assistant-743f4736af', "Sales Assistant"), job: text('field-upcoming-meeting-brief-b41c27a588', "Upcoming Meeting Brief"), detail: text('field-review-upcoming-meetings-and-relevant-prospec-a99d73aaeb', "Review upcoming meetings and relevant prospect context to prepare supervised follow-up."), tools: text('field-google-calendar-gmail-a97a1d4f6c', "Google Calendar · Gmail"), output: text('field-an-upcoming-meeting-brief-b23938c765', "An upcoming meeting brief") },
  { name: text('field-marketing-96ab3a1ea7', "Marketing"), icon: Megaphone, line: text('field-make-campaign-context-easier-to-use-828ac93da0', "Make campaign context easier to use."), worker: text('field-marketing-coordinator-6c7d74c1b0', "Marketing Coordinator"), job: text('field-campaign-asset-review-61bbcba001', "Campaign Asset Review"), detail: text('field-review-recent-campaign-material-and-summarize-4b0ffefd2e', "Review recent campaign material and summarize what the team needs to know."), tools: text('field-google-drive-44fee82542', "Google Drive"), output: text('field-a-campaign-material-summary-85005a8c11', "A campaign material summary") },
  { name: text('field-engineering-8ea719d422', "Engineering"), icon: Code2, line: text('field-stay-close-to-repository-work-4cd6f8a016', "Stay close to repository work."), worker: text('field-developer-assistant-b26d94884b', "Developer Assistant"), job: text('field-engineering-change-review-17f3cbaf32', "Engineering Change Review"), detail: text('field-review-repository-issues-and-pull-requests-to-f8073c5ac4', "Review repository issues and pull requests to prepare a concise engineering status brief."), tools: text('field-github-be59dc1624', "GitHub"), output: text('field-an-engineering-status-brief-2f398a47ff', "An engineering status brief") },
] as const);

  const [selection, setSelection] = useState(selectedId || solutions[selectedIndex ?? 0]?.id || solutions[0].id);
  const selected = Math.max(0,solutions.findIndex(item=>item.id===selection));
  const setSelected=(index:number)=>{setSelection(solutions[index].id);const url=new URL(window.location.href);url.searchParams.set('selected',solutions[index].id);window.history.replaceState(null,'',url);};
  const operationsImage = usePublicImage('operations-work',operationsFallback);
  const solution = solutions[selected];

  useEffect(() => { if(selectedId)setSelection(selectedId);else if(selectedIndex!==undefined)setSelection(solutions[selectedIndex]?.id||solutions[0].id);else {const id=new URLSearchParams(window.location.search).get('selected');if(id)setSelection(id);} }, [selectedId,selectedIndex]);

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
      <div className='solutions-hero-photo'><img src={operationsImage} onError={event=>{if(event.currentTarget.dataset.fallbackUsed)return;event.currentTarget.dataset.fallbackUsed='true';event.currentTarget.src=operationsFallback;}} alt={text('alt-operations-team-working-in-a-supervised-envir-ae7ab6fc5d', "Operations team working in a supervised environment")} /></div>
      <div className='solutions-hero-wash' />
      <div className='public-container solutions-hero-content'><p className='solutions-kicker'>{text('copy-audoryn-solutions-0b1be8a104', "AUDORYN / SOLUTIONS")}</p><h1>{text('copy-work-that-already-d2df094031', "Work that already")}<br/>{text('copy-needs-doing-3d9b074969', "needs doing.")}</h1><p>{text('copy-start-with-a-defined-responsibility-give-an-a-156028cbf0', "Start with a defined responsibility. Give an AI worker the right context, a clear job and a person to answer to.")}</p><button type='button' onClick={exploreSolutions}>{text('copy-explore-where-to-start-aeddbfa023', "Explore where to start ")}<ArrowDown size={15}/></button></div>
      <div className='public-container solutions-hero-bottom'><span>{text('copy-one-workforce-many-kinds-of-work-673f2389dd', "ONE WORKFORCE / MANY KINDS OF WORK")}</span><span>01 — 06</span></div>
    </section>

    <section id='solution-index' className='solutions-index public-container' data-solutions-reveal>
      <div className='solutions-index-head'><div><p className='solutions-kicker'>{text('copy-where-to-start-6d600b08ce', "WHERE TO START")}</p><h2>{text('copy-choose-a-responsibility-4ad9305d6f', "Choose a responsibility.")}<br/>{text('copy-start-with-a-clear-job-c86f711a96', "Start with a clear job.")}</h2></div><p>{text('copy-six-practical-starting-points-grounded-in-aud-f95d082218', "Six practical starting points, grounded in Audoryn’s workforce templates.")}</p></div>
      <div className='solutions-explorer'>
        <div className='solutions-list' aria-label={text('copy-solution-areas-7dfd230c09', "Solution areas")}>{solutions.map((item, index) => { const Icon = item.icon; return <button key={item.name} type='button' className={selected === index ? 'is-active' : ''} aria-pressed={selected === index} onClick={() => setSelected(index)}><span className='solutions-list-number'>0{index + 1}</span><Icon size={18} strokeWidth={1.7} aria-hidden='true'/><span className='solutions-list-name'>{item.name}</span><ArrowUpRight className='solutions-list-arrow' size={16} aria-hidden='true'/></button>; })}</div>
        <div className='solutions-detail' aria-live='polite' key={solution.name}><div className='solutions-detail-top'><span>{text('copy-example-starting-point-0-b818a22524', "EXAMPLE STARTING POINT / 0")}{selected + 1}</span><solution.icon size={28} strokeWidth={1.4} aria-hidden='true'/></div><h3>{solution.line}</h3><p>{solution.detail}</p><div className='solutions-detail-rule'/><dl><div><dt>{text('copy-worker-ac44e78bdc', "WORKER")}</dt><dd>{solution.worker}</dd></div><div><dt>{text('copy-example-job-21f8eb58bc', "EXAMPLE JOB")}</dt><dd>{solution.job}</dd></div><div><dt>{text('copy-connected-context-ef272311c2', "CONNECTED CONTEXT")}</dt><dd>{solution.tools}</dd></div><div><dt>{text('copy-deliverable-38229dbb52', "DELIVERABLE")}</dt><dd>{solution.output}</dd></div></dl><span className='solutions-detail-foot'>{text('copy-defined-role-bounded-tools-human-supervision-6a47066726', "DEFINED ROLE · BOUNDED TOOLS · HUMAN SUPERVISION")}</span></div>
      </div>
      <p className='solutions-template-note'>{text('copy-templates-create-draft-roles-and-jobs-connect-624e5e51cf', "Templates create draft roles and jobs. Connections, capabilities, policy and activation remain explicit setup steps.")}</p>
    </section>

    <section className='solutions-close' data-solutions-reveal><div className='public-container solutions-close-inner'><div><p className='solutions-kicker'>{text('copy-your-first-worker-dac8ef8562', "YOUR FIRST WORKER")}</p><h2>{text('copy-start-with-one-useful-responsibility-99d93ced82', "Start with one useful responsibility.")}</h2><p>{text('copy-explore-how-the-workforce-runs-or-set-up-a-su-af9e6eaab0', "Explore how the workforce runs, or set up a supervised worker in Audoryn.")}</p></div><div><button type='button' className='solutions-primary' onClick={() => onNavigate('signup')}>{text('copy-get-started-127a298aa9', "Get started ")}<ArrowRight size={16}/></button><button type='button' className='solutions-secondary' onClick={() => onNavigate('product')}>{text('copy-see-the-product-4721ff1ee3', "See the product ")}<ArrowUpRight size={16}/></button></div></div></section>
  </div>;
}
