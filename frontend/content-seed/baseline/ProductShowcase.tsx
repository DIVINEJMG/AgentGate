import { useEffect, useState } from 'react';
import { ArrowDown, ArrowRight, Check, CirclePause, Clock3, FileText, GitBranch, Mail, ShieldCheck, Sparkles } from 'lucide-react';
import humanOversightImage from './assets/human-oversight.webp';
import './product-showcase.css';

type Destination = 'signup' | 'security';
type View = 'Workforce' | 'Jobs' | 'Approvals';

const views: View[] = ['Workforce', 'Jobs', 'Approvals'];

function WorkspacePreview() {
  const [view, setView] = useState<View>('Workforce');

  return <div className='product-workspace' aria-label='Illustrative Audoryn workspace preview'>
    <div className='product-workspace-bar'><span className='product-workspace-brand'><i /> AUDORYN <em>/</em> WORKSPACE</span><span className='product-workspace-org'>NORTHSTAR OPERATIONS <span className='product-workspace-avatar'>N</span></span></div>
    <div className='product-workspace-body'>
      <div className='product-workspace-rail'><span className='product-rail-title'>OPERATE</span>{views.map((item) => <button type='button' key={item} className={view === item ? 'active' : ''} onClick={() => setView(item)} aria-pressed={view === item}>{item}</button>)}<span className='product-rail-title product-rail-manage'>MANAGE</span><span>Integrations</span><span>Policies</span><span>Audit</span></div>
      <div className='product-workspace-main' key={view}>
        <div className='product-workspace-context'><span>WORKSPACE / {view.toUpperCase()}</span><span>EXAMPLE VIEW</span></div>
        {view === 'Workforce' && <><div className='product-preview-heading'><div><p>Workforce</p><h3>People and AI, one clear structure.</h3></div><span className='product-preview-count'>02 WORKERS</span></div><div className='product-preview-table'><div className='product-preview-th'><span>WORKER</span><span>ROLE</span><span>SUPERVISOR</span><span>STATE</span></div><div><span className='product-preview-person'><b>AR</b><strong>Research analyst<small>Insights team</small></strong></span><span>Research</span><span>Ada M.</span><span className='product-state'><i /> Active</span></div><div><span className='product-preview-person'><b>OC</b><strong>Operations coordinator<small>Operations</small></strong></span><span>Operations</span><span>Daniel K.</span><span className='product-state'><i /> Active</span></div></div><div className='product-preview-foot'><span><ShieldCheck size={14} /> Separate agent identity</span><span><Clock3 size={14} /> Defined working hours</span></div></>}
        {view === 'Jobs' && <><div className='product-preview-heading'><div><p>Jobs</p><h3>Work with a visible state.</h3></div><span className='product-preview-count'>03 WORK ITEMS</span></div><div className='product-job-list'><div><span className='product-job-icon'><FileText size={17}/></span><strong>Compile weekly research brief<small>Research analyst · Scheduled</small></strong><em className='product-job-status complete'>Completed</em></div><div><span className='product-job-icon'><GitBranch size={17}/></span><strong>Review release activity<small>Operations coordinator · Running</small></strong><em className='product-job-status running'>Running</em></div><div><span className='product-job-icon'><Mail size={17}/></span><strong>Prepare stakeholder update<small>Operations coordinator · Queued</small></strong><em className='product-job-status'>Queued</em></div></div><div className='product-preview-foot'><span><Check size={14} /> Objective and completion criteria</span><span>Run history retained</span></div></>}
        {view === 'Approvals' && <><div className='product-preview-heading'><div><p>Approvals</p><h3>Judgment returns to a person.</h3></div><span className='product-preview-count'>01 NEEDS REVIEW</span></div><div className='product-approval-preview'><span className='product-approval-flag'><CirclePause size={15}/> ACTION HELD</span><h4>Post stakeholder update</h4><p>Operations coordinator requested a Slack message. The run is paused while a reviewer checks the request and its context.</p><div><span>REQUESTED BY <strong>Operations coordinator</strong></span><span>REVIEWER <strong>Daniel K.</strong></span></div></div><div className='product-preview-foot'><span><ShieldCheck size={14} /> Policy and risk checked again before resume</span></div></>}
      </div>
    </div>
    <div className='product-workspace-caption'>Illustrative workspace view · Example data</div>
  </div>;
}

const integrations = [
  { mark: 'GH', name: 'GitHub', use: 'Repository and issue workflows' },
  { mark: 'M', name: 'Gmail', use: 'Mailbox context and recent messages' },
  { mark: 'D', name: 'Google Drive', use: 'Documents and files' },
  { mark: 'S', name: 'Slack', use: 'Team conversations' },
  { mark: 'C', name: 'Google Calendar', use: 'Calendar context' },
  { mark: '↗', name: 'Governed Browser', use: 'Approved site interactions' },
];

export default function ProductShowcase({ onNavigate }: { onNavigate: (route: Destination) => void }) {
  useEffect(() => {
    const root = document.querySelector<HTMLElement>('.product-page');
    if (!root || window.matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) return;
    const items = root.querySelectorAll<HTMLElement>('[data-product-reveal]');
    const observer = new IntersectionObserver((entries) => { entries.forEach((entry) => { if (entry.isIntersecting) { entry.target.classList.add('is-visible'); observer.unobserve(entry.target); } }); }, { threshold: .08, rootMargin: '0px 0px -5% 0px' });
    items.forEach((item) => observer.observe(item));
    root.classList.add('product-motion-ready');
    return () => { observer.disconnect(); root.classList.remove('product-motion-ready'); };
  }, []);

  return <div className='product-page'>
    <section className='product-hero'>
      <div className='public-container product-hero-inner'>
        <div className='product-hero-copy'><p className='product-eyebrow'>AUDORYN / PRODUCT</p><h1>Give work to AI.<br/><span>Keep people in control.</span></h1><p>Build a supervised AI workforce for the work your organization already does. Define the role, assign the job, connect the tools and see every important decision.</p><div className='product-hero-actions'><button className='public-cta' onClick={() => onNavigate('signup')}>Get started <ArrowRight size={15}/></button><a href='#product-workforce'>Explore the product <ArrowDown size={15}/></a></div><div className='product-hero-meta'><span>WORKERS</span><i/><span>JOBS</span><i/><span>SUPERVISION</span><i/><span>INTEGRATIONS</span></div></div>
        <div className='product-hero-visual'><div className='product-visual-halo'/><WorkspacePreview/></div>
      </div>
    </section>

    <section className='product-statement public-container' data-product-reveal><span>01 / THE WORKFORCE</span><p>A useful AI worker needs more than a prompt. Audoryn gives it a role, a job and a place in your organization.</p></section>

    <section id='product-workforce' className='product-feature public-container' data-product-reveal><div className='product-feature-copy'><p className='product-eyebrow'>WORKFORCE / 01</p><h2>Make the work legible before it begins.</h2><p>Create workers with responsibilities, working hours and a named human supervisor. Give each job an objective, requirements and a clear definition of done.</p><div className='product-feature-notes'><span><Check size={15}/> Named roles and supervisors</span><span><Check size={15}/> Scheduled and triggered jobs</span><span><Check size={15}/> A separate security identity for every worker</span></div></div><div className='product-job-visual' aria-label='Illustration of a worker and a defined job'><div className='product-job-visual-top'><span>WORKER PROFILE</span><span>01 / 02</span></div><div className='product-worker-identity'><span>AR</span><div><small>RESEARCH / INSIGHTS</small><strong>Research analyst</strong><em>Supervised by Ada M.</em></div></div><div className='product-worker-divider'/><div className='product-worker-job'><span>ASSIGNED JOB</span><strong>Compile weekly research brief</strong><p>Gather relevant sources, summarize findings and deliver a structured brief.</p><div><span>MON · 09:00</span><span>REVIEW BEFORE DELIVERY</span></div></div><div className='product-job-visual-bottom'><span><i/> ACTIVE WORKER</span><span>ROLE → JOB → QUEUE</span></div></div></section>

    <section className='product-runtime' data-product-reveal><div className='public-container product-runtime-inner'><div className='product-runtime-copy'><p className='product-eyebrow'>RUNTIME / 02</p><h2>From assignment to a traceable result.</h2><p>Jobs enter a durable queue. Runs have steps, checkpoints, retries and a recorded outcome, so work can be followed instead of guessed at.</p></div><div className='product-run-panel'><div className='product-run-head'><span>RUN / WEEKLY RESEARCH BRIEF</span><span><i/> IN PROGRESS</span></div><ol><li className='done'><span>01</span><div><strong>Read assigned context</strong><small>Sources and job requirements loaded</small></div><Check size={15}/></li><li className='done'><span>02</span><div><strong>Plan the work</strong><small>Bounded AI planning</small></div><Check size={15}/></li><li className='current'><span>03</span><div><strong>Collect observations</strong><small>Connected tools, governed access</small></div><span className='product-run-spinner'/></li><li><span>04</span><div><strong>Assess completion</strong><small>Compare result with the job criteria</small></div></li></ol><div className='product-run-tail'><span>WORK ITEM / 8F24</span><span>EXAMPLE RUN</span></div></div></div></section>

    <section className='product-intelligence public-container' data-product-reveal><div className='product-intelligence-title'><p className='product-eyebrow'>AI / 03</p><h2>Intelligence where it helps.<br/>Authority where it belongs.</h2><p>Audoryn routes planning and visual understanding through shared NVIDIA API models. The model can reason about the task; Audoryn still controls what may happen outside it.</p></div><div className='product-model-rows'><div><span className='product-model-number'>01</span><span className='product-model-icon'><Sparkles size={21}/></span><div><h3>Planning and coordination</h3><p>NVIDIA Nemotron supports bounded planning and internal reasoning for managed runs.</p></div><span className='product-model-label'>NEMOTRON</span></div><div><span className='product-model-number'>02</span><span className='product-model-icon'><FileText size={21}/></span><div><h3>Visual understanding</h3><p>NVIDIA Ising analyzes uploaded images and returns relevant findings for the task.</p></div><span className='product-model-label'>ISING</span></div></div><p className='product-model-note'>Models provide analysis, not credentials or permission to use connected tools.</p></section>

    <section className='product-supervision' data-product-reveal><div className='product-supervision-image'><img src={humanOversightImage} alt='Colleagues reviewing a decision together' loading='lazy'/></div><div className='product-supervision-copy'><div><p className='product-eyebrow'>SUPERVISION / 04</p><h2>The moments that matter come back to a person.</h2><p>Policy can allow, deny or hold an action for review. A held run waits while a human sees the context, makes a decision and leaves an auditable record.</p><button type='button' onClick={() => onNavigate('security')}>Explore the security model <ArrowRight size={15}/></button></div><div className='product-decision-line'><span>REQUEST</span><i/><span>POLICY</span><i/><span>HUMAN REVIEW</span><i/><span>RECORDED OUTCOME</span></div></div></section>

    <section className='product-integrations public-container' data-product-reveal><div className='product-integrations-heading'><div><p className='product-eyebrow'>INTEGRATIONS / 05</p><h2>Work across the tools<br/>your team already uses.</h2></div><p>Connect workplace systems to Audoryn’s governed action gateway. Each provider exposes defined capabilities; a connection alone never gives a worker permission to act.</p></div><div className='product-integration-grid'>{integrations.map((item) => <div key={item.name}><span className='product-integration-mark'>{item.mark}</span><strong>{item.name}</strong><p>{item.use}</p><ArrowRight size={15} aria-hidden='true'/></div>)}</div><p className='product-integrations-note'>Available operations depend on provider configuration, declared capabilities and policy.</p></section>

    <section className='product-close'><div className='public-container'><p className='product-eyebrow'>START WITH A DEFINED WORKER</p><h2>Bring AI into the workflow.<br/>Keep the organization in charge.</h2><p>Start small, make authority explicit and grow the workforce as the work proves useful.</p><button className='public-cta' onClick={() => onNavigate('signup')}>Get started <ArrowRight size={16}/></button></div></section>
  </div>;
}
