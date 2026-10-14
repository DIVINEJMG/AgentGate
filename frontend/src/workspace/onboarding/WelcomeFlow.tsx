import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  ArrowLeft, ArrowRight, Check, Clock3, Code2, Eye, Globe2, LifeBuoy, Loader2, MessagesSquare, Microscope, PlayCircle,
  ShieldCheck, Sparkles, Workflow, Zap,
} from 'lucide-react';
import type { AuthUser } from '../../platform/client';
import type { OrganizationAccess } from '../../lib/identityApi';
import type { ApiVersion } from '../../lib/systemApi';
import { setUpWorkspace, type Care, type Goal, type Onboarding, type ToolChoice } from '../../lib/onboardingApi';
import { ToolMark } from '../connections/Chrome';
import { initials, workerHue } from '../ui';

type Step = 'name' | 'goal' | 'tools' | 'care' | 'building' | 'done';

const GOALS: Array<{ id: Goal; title: string; detail: string; worker: string; icon: typeof Code2 }> = [
  { id: 'engineering', title: 'Engineering', detail: 'Issues, pull requests, code questions and changes for review.', worker: 'Engineering Assistant', icon: Code2 },
  { id: 'support', title: 'Support', detail: 'Sorting requests, drafting replies and tracking open questions.', worker: 'Support Assistant', icon: LifeBuoy },
  { id: 'research', title: 'Research', detail: 'Finding and summarising information, always with sources.', worker: 'Research Assistant', icon: Microscope },
  { id: 'operations', title: 'Operations', detail: 'Daily summaries, open tasks and follow-ups that keep work moving.', worker: 'Operations Assistant', icon: Workflow },
];
const GENERAL_WORKER = 'Workspace Assistant';

const TOOLS: Array<{ id: ToolChoice; name: string; detail: string }> = [
  { id: 'github', name: 'GitHub', detail: 'Code and issues' },
  { id: 'slack', name: 'Slack', detail: 'Team messages' },
  { id: 'gmail', name: 'Gmail', detail: 'Email' },
  { id: 'google_calendar', name: 'Google Calendar', detail: 'Meetings' },
  { id: 'google_drive', name: 'Google Drive', detail: 'Documents' },
  { id: 'web_research', name: 'Web search', detail: 'Public websites' },
];

const CARE: Array<{ id: Care; title: string; detail: string; example: string; icon: typeof Eye; recommended?: boolean }> = [
  { id: 'visible', title: 'Ask before anything others can see', detail: 'Reading and preparing run freely. Comments, messages and emails wait for you, unless you asked for them.', example: 'Posting a comment on an issue asks you first.', icon: Eye, recommended: true },
  { id: 'changes', title: 'Ask before any change', detail: 'Workers only read on their own. Every change waits for your approval.', example: 'Adding a label or editing a file asks you first.', icon: ShieldCheck },
  { id: 'risky', title: 'Only ask for risky actions', detail: 'Everyday writes run without asking. Anything that can’t be undone still waits for you.', example: 'Deleting or merging asks you first.', icon: Zap },
];

const PUBLIC_MAIL = /^(gmail|googlemail|yahoo|ymail|outlook|hotmail|live|msn|icloud|me|mac|aol|proton|protonmail|pm|gmx|zoho|yandex|mail)\./i;

/** A sensible workspace name: the company from a work email, otherwise "<first name>'s workspace". */
export function suggestedWorkspaceName(user: AuthUser): string {
  const domain = user.email?.split('@')[1] ?? '';
  if (domain && !PUBLIC_MAIL.test(domain)) {
    const label = domain.split('.')[0].replace(/[-_]+/g, ' ');
    if (label.length >= 2) return label.replace(/\b\w/g, (letter) => letter.toUpperCase());
  }
  const first = (user.name || user.email?.split('@')[0] || '').split(/[\s._-]+/)[0];
  return first ? `${first.charAt(0).toUpperCase()}${first.slice(1)}’s workspace` : 'My workspace';
}

function browserTimezone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; }
}

export default function WelcomeFlow({ user, organization, apiVersion, onCreateOrganization, onFinished, onSampleData }: {
  user: AuthUser;
  organization: OrganizationAccess | null;
  apiVersion: ApiVersion;
  onCreateOrganization: (name: string) => Promise<void>;
  /** destination: the welcome conversation, or home. */
  onFinished: (onboarding: Onboarding, destination: 'conversation' | 'home') => void;
  onSampleData: () => void;
}) {
  const [step, setStep] = useState<Step>(organization ? 'goal' : 'name');
  const [name, setName] = useState(() => suggestedWorkspaceName(user));
  const [goal, setGoal] = useState<Goal | null>(null);
  const [tools, setTools] = useState<ToolChoice[]>([]);
  const [care, setCare] = useState<Care | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Onboarding | null>(null);
  const [progress, setProgress] = useState(0);
  const timezone = useMemo(browserTimezone, []);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const firstName = (user.name || user.email?.split('@')[0] || '').split(/\s+/)[0];

  useEffect(() => { headingRef.current?.focus(); }, [step]);
  // Once the workspace exists (it is created by the first step) the questions follow.
  useEffect(() => { if (organization && step === 'name') setStep('goal'); }, [organization, step]);

  const questions: Step[] = ['goal', 'tools', 'care'];
  const number = questions.indexOf(step) + 1;
  const workerName = GOALS.find((item) => item.id === goal)?.worker ?? GENERAL_WORKER;

  async function createWorkspace(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (trimmed.length < 2) { setError('Give your workspace a name of at least 2 characters.'); return; }
    setBusy(true); setError(null);
    try { await onCreateOrganization(trimmed); }
    catch { setError('Audoryn couldn’t create the workspace. Try again.'); }
    finally { setBusy(false); }
  }

  async function build(answers: { goal: Goal | null; tools: ToolChoice[]; care: Care | null }) {
    if (!organization) return;
    setStep('building'); setError(null); setProgress(0);
    const ticker = window.setInterval(() => setProgress((value) => Math.min(value + 1, 3)), 450);
    try {
      const done = await setUpWorkspace(apiVersion, organization.id, {
        goal: answers.goal ?? undefined, tools: answers.tools, care: answers.care ?? undefined, timezone,
      });
      setProgress(4); setResult(done);
      window.setTimeout(() => setStep('done'), 350);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Setup didn’t finish. Your answers are kept; try again.');
      setStep('care');
    } finally { window.clearInterval(ticker); }
  }

  const next = () => { setError(null); if (step === 'goal') setStep('tools'); else if (step === 'tools') setStep('care'); else void build({ goal, tools, care }); };
  const back = () => { setError(null); setStep(step === 'care' ? 'tools' : 'goal'); };
  const skip = () => {
    if (step === 'goal') setGoal(null);
    if (step === 'tools') setTools([]);
    if (step === 'care') { setCare(null); void build({ goal, tools, care: null }); return; }
    next();
  };

  return <div className='ob-screen'>
    <aside className='ob-story' aria-hidden={step === 'name' ? undefined : 'true'}>
      <div className='ob-brand'><img src='/audoryn-mark.png' alt='' />Audoryn</div>
      {step === 'name'
        ? <div className='ob-story-copy'><p className='ob-kicker'>Welcome{firstName ? `, ${firstName}` : ''}</p><h2>Your AI workers, with you in charge.</h2><p>Two minutes of setup and your first worker is ready to talk to. Nothing runs until you ask.</p></div>
        : <WorkerPreview name={workerName} goal={goal} tools={tools} care={care} timezone={timezone} highlight={step} />}
      <button type='button' className='ob-sample' onClick={onSampleData}><PlayCircle size={16} />Explore a sample workspace first</button>
    </aside>

    <main className='ob-main'>
      {questions.includes(step) && <div className='ob-progress' aria-label={`Question ${number} of 3`}>
        {questions.map((item, index) => <span key={item} data-state={index + 1 < number ? 'done' : index + 1 === number ? 'current' : undefined} />)}
      </div>}

      {step === 'name' && <form className='ob-panel' onSubmit={createWorkspace}>
        <p className='ob-eyebrow'>New workspace</p>
        <h1 ref={headingRef} tabIndex={-1}>What should we call your workspace?</h1>
        <p className='ob-lead'>It’s where your people, workers, approvals and history live. You can rename it later.</p>
        <label className='ob-field'><span>Workspace name</span>
          <input value={name} onChange={(event) => setName(event.target.value)} maxLength={80} autoComplete='organization' autoFocus />
        </label>
        {error && <p className='ob-error' role='alert'>{error}</p>}
        <div className='ob-actions'><span className='ob-signed'>Signed in as <strong>{user.name || user.email}</strong></span>
          <button className='ws-button ws-button-primary ob-continue' type='submit' disabled={busy}>{busy ? <><Loader2 size={15} className='ob-spin' />Creating…</> : <>Continue<ArrowRight size={15} /></>}</button></div>
      </form>}

      {step === 'goal' && <Question number={1} headingRef={headingRef} title='What should your workers help with?' lead='We’ll set up a first worker for it. You can add more workers for anything else later.'
        footer={<Footer onBack={undefined} onSkip={skip} onNext={next} nextLabel='Continue' disabled={!goal} />}>
        <div className='ob-options ob-grid' role='radiogroup' aria-label='What workers help with'>
          {GOALS.map((item) => <Option key={item.id} selected={goal === item.id} onSelect={() => setGoal(item.id)} icon={<item.icon size={20} strokeWidth={1.7} />} title={item.title} detail={item.detail} />)}
        </div>
      </Question>}

      {step === 'tools' && <Question number={2} headingRef={headingRef} title='Which tools do you use?' lead='Pick any. Your worker will suggest things to do with them, and you connect each one when you’re ready.'
        footer={<Footer onBack={back} onSkip={skip} onNext={next} nextLabel={tools.length ? 'Continue' : 'Continue without tools'} />}>
        <div className='ob-tools' role='group' aria-label='Tools you use'>
          {TOOLS.map((tool) => {
            const on = tools.includes(tool.id);
            return <button key={tool.id} type='button' className='ob-tool' aria-pressed={on} onClick={() => setTools((current) => on ? current.filter((item) => item !== tool.id) : [...current, tool.id])}>
              {tool.id === 'web_research' ? <span className='ws-tool-mark'><Globe2 size={20} strokeWidth={1.7} /></span> : <ToolMark provider={tool.id} />}
              <span><strong>{tool.name}</strong><small>{tool.detail}</small></span>
              <i className='ob-check' aria-hidden='true'>{on && <Check size={12} strokeWidth={3} />}</i>
            </button>;
          })}
        </div>
      </Question>}

      {step === 'care' && <Question number={3} headingRef={headingRef} title='How careful should your workers be?' lead='This sets your first approval rule. You can change it any time in Policies.'
        footer={<Footer onBack={back} onSkip={skip} onNext={next} nextLabel='Set up my workspace' disabled={!care} />}>
        <div className='ob-options' role='radiogroup' aria-label='How careful workers should be'>
          {CARE.map((item) => <Option key={item.id} selected={care === item.id} onSelect={() => setCare(item.id)} icon={<item.icon size={20} strokeWidth={1.7} />}
            title={<>{item.title}{item.recommended && <span className='ob-badge'>Recommended</span>}</>} detail={item.detail} note={item.example} />)}
        </div>
        {error && <p className='ob-error' role='alert'>{error}</p>}
      </Question>}

      {step === 'building' && <div className='ob-panel ob-building' role='status' aria-live='polite'>
        <p className='ob-eyebrow'>Almost there</p>
        <h1 ref={headingRef} tabIndex={-1}>Setting up your workspace</h1>
        <ul className='ob-build-list'>
          {[`Creating ${workerName}`, 'Adding your approval rule', `Setting working hours (${timezone})`, 'Opening your welcome conversation'].map((label, index) =>
            <li key={label} data-state={index < progress ? 'done' : index === progress ? 'active' : undefined}>
              <i aria-hidden='true'>{index < progress ? <Check size={13} strokeWidth={3} /> : index === progress ? <Loader2 size={14} className='ob-spin' /> : null}</i>{label}
            </li>)}
        </ul>
      </div>}

      {step === 'done' && result && <div className='ob-panel ob-done'>
        <div className='ob-done-mark' aria-hidden='true'><Sparkles size={22} /></div>
        <p className='ob-eyebrow'>You’re all set</p>
        <h1 ref={headingRef} tabIndex={-1}>Meet {result.setup?.workerName ?? workerName}</h1>
        <p className='ob-lead'>Your first worker is ready and has left you a message. It won’t do anything until you ask.</p>
        <dl className='ob-summary'>
          <div><dt>Worker</dt><dd>{result.setup?.workerName ?? workerName}</dd></div>
          <div><dt>Approvals</dt><dd>{CARE.find((item) => item.id === (result.answers?.care ?? 'visible'))?.title}</dd></div>
          <div><dt>Working hours</dt><dd>Mon–Fri, 9:00–17:00 · {result.answers?.timezone ?? timezone}</dd></div>
          {Boolean(result.answers?.tools?.length) && <div><dt>To connect</dt><dd>{result.answers?.tools?.map((id) => TOOLS.find((tool) => tool.id === id)?.name).join(', ')}</dd></div>}
        </dl>
        <div className='ob-actions'>
          <button type='button' className='ws-button ws-button-quiet' onClick={() => onFinished(result, 'home')}>Go to home</button>
          <button type='button' className='ws-button ws-button-primary ob-continue' onClick={() => onFinished(result, 'conversation')}><MessagesSquare size={15} />Say hi to {result.setup?.workerName ?? workerName}</button>
        </div>
      </div>}
    </main>
  </div>;
}

function Question({ number, title, lead, children, footer, headingRef }: { number: number; title: string; lead: string; children: ReactNode; footer: ReactNode; headingRef: React.RefObject<HTMLHeadingElement | null> }) {
  return <section className='ob-panel' aria-labelledby='ob-question'>
    <p className='ob-eyebrow'>Question {number} of 3 · optional</p>
    <h1 id='ob-question' ref={headingRef} tabIndex={-1}>{title}</h1>
    <p className='ob-lead'>{lead}</p>
    {children}
    {footer}
  </section>;
}

function Footer({ onBack, onSkip, onNext, nextLabel, disabled }: { onBack?: () => void; onSkip: () => void; onNext: () => void; nextLabel: string; disabled?: boolean }) {
  return <div className='ob-actions'>
    {onBack ? <button type='button' className='ws-button ws-button-quiet' onClick={onBack}><ArrowLeft size={15} />Back</button> : <span />}
    <div className='ob-actions-end'>
      <button type='button' className='ws-button ws-button-quiet' onClick={onSkip}>Skip</button>
      <button type='button' className='ws-button ws-button-primary ob-continue' disabled={disabled} onClick={onNext}>{nextLabel}<ArrowRight size={15} /></button>
    </div>
  </div>;
}

function Option({ selected, onSelect, icon, title, detail, note }: { selected: boolean; onSelect: () => void; icon: ReactNode; title: ReactNode; detail: string; note?: string }) {
  return <button type='button' role='radio' aria-checked={selected} className='ob-option' onClick={onSelect}>
    <span className='ob-option-icon' aria-hidden='true'>{icon}</span>
    <span className='ob-option-copy'><strong>{title}</strong><small>{detail}</small>{note && <em>{note}</em>}</span>
    <i className='ob-radio' aria-hidden='true' />
  </button>;
}

/** The worker being assembled from the answers so far. */
function WorkerPreview({ name, goal, tools, care, timezone, highlight }: { name: string; goal: Goal | null; tools: ToolChoice[]; care: Care | null; timezone: string; highlight: Step }) {
  const hue = workerHue(name);
  const careTitle = CARE.find((item) => item.id === (care ?? 'visible'))?.title ?? '';
  return <div className='ob-preview'>
    <p className='ob-kicker'>Your first worker</p>
    <div className='ob-card' style={{ ['--hue' as string]: String(hue) }}>
      <div className='ob-card-head'>
        <span className='ob-avatar'>{initials(name)}</span>
        <span><strong>{name}</strong><small>{goal ? GOALS.find((item) => item.id === goal)?.title : 'General'} · Ready</small></span>
      </div>
      <dl>
        <div data-focus={highlight === 'tools' ? 'true' : undefined}><dt>Tools</dt><dd>{tools.length ? <span className='ob-chips'>{tools.map((tool) => <span key={tool}>{TOOLS.find((item) => item.id === tool)?.name}</span>)}</span> : <span className='ob-muted'>Connect any time</span>}</dd></div>
        <div data-focus={highlight === 'care' ? 'true' : undefined}><dt>Approvals</dt><dd>{careTitle}</dd></div>
        <div><dt><Clock3 size={13} />Hours</dt><dd>Mon–Fri, 9:00–17:00 · {timezone}</dd></div>
      </dl>
    </div>
    <p className='ob-note'>Nothing is scheduled and nothing runs until you ask.</p>
  </div>;
}
