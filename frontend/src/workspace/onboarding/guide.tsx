import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ArrowLeft, ArrowRight, Compass, Copy, X } from 'lucide-react';
import type { ChecklistStepId, Onboarding } from '../../lib/onboardingApi';
import { formatRoute, navigate, useRoute, type WorkspaceRoute } from '../routes';

/** One step of a guide: what to point at (a CSS selector), and where that is. */
interface GuideStep { title: string; body: ReactNode; target?: string; route?: WorkspaceRoute }
export type GuideId = ChecklistStepId;

interface OnboardingContextValue {
  onboarding: Onboarding | null;
  refresh: () => void;
  dismiss: (dismissed: boolean) => void;
  startGuide: (id: GuideId) => void;
}
const OnboardingContext = createContext<OnboardingContextValue | null>(null);
export const useOnboarding = () => useContext(OnboardingContext);

const TOOL_NAMES: Record<string, string> = { github: 'GitHub', slack: 'Slack', gmail: 'Gmail', google_calendar: 'Google Calendar', google_drive: 'Google Drive' };

function CopyExample({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return <span className='ob-example'>
    <q>{text}</q>
    <button type='button' className='ws-button ws-button-sm' onClick={() => { void navigator.clipboard?.writeText(text).then(() => setCopied(true)).catch(() => {}); }}>
      <Copy size={13} />{copied ? 'Copied' : 'Copy'}
    </button>
  </span>;
}

function guideSteps(id: GuideId, onboarding: Onboarding | null): { title: string; steps: GuideStep[] } {
  const worker = onboarding?.setup?.workerName ?? 'your worker';
  const chat: WorkspaceRoute = onboarding?.setup?.workerId
    ? { page: 'conversations', workerId: onboarding.setup.workerId, threadId: onboarding.setup.threadId }
    : { page: 'conversations' };
  const example = onboarding?.examples?.[0] ?? 'Summarise the open issues in my repository';
  const tool = (onboarding?.answers?.tools ?? []).find((item) => item in TOOL_NAMES);
  switch (id) {
    case 'connect': return { title: 'Connect a tool', steps: [
      tool
        ? { route: { page: 'connections', view: 'tool', id: tool.replace('_', '-') }, target: '.ws-tool-hero .ws-button-primary', title: `Connect ${TOOL_NAMES[tool]}`,
            body: 'Click Connect and sign in. The credential is stored encrypted, and workers only get the access you approve.' }
        : { route: { page: 'connections', view: 'directory' }, target: '.ws-tool-grid', title: 'Pick a tool',
            body: 'Every tool your workers can use is listed here. Open one to see exactly what workers could do with it before you connect.' },
      { target: '.ws-ability-list, .ws-tool-grid', title: 'See what workers could do',
        body: 'Each ability shows its risk. Your approval rules decide what runs on its own and what asks you first.' },
      { title: 'Then just ask', body: <>Once it’s connected, ask {worker} in Conversations. For example: <CopyExample text={example} /></> },
    ] };
    case 'say_hi': return { title: 'Say hi to your worker', steps: [
      { route: chat, target: '.cf-transcript', title: `${worker} wrote to you`, body: 'Your worker left a welcome message with a few ideas of what to ask.' },
      { target: '.cf-composer', title: 'Type your reply here', body: <>Say hi, or ask what it can do. Press Enter to send. Try: <CopyExample text='What can you help me with?' /></> },
      { target: '[data-guide="nav-conversations"]', title: 'Find it again', body: 'Conversations is always in the sidebar, and each worker has its own conversations.' },
    ] };
    case 'first_task': return { title: 'Run a first task', steps: [
      { route: chat, target: '.cf-composer', title: 'Ask for real work', body: <>Describe the outcome you want in your own words. For example: <CopyExample text={example} /></> },
      { title: 'Watch it work', body: 'Your worker reads first, plans its steps and shows its progress right in the conversation. Anything that needs a person waits for you.' },
      { target: '[data-guide="nav-runs"]', title: 'Every run, step by step', body: 'Runs shows each task’s plan and progress, and Results collects the finished work.' },
    ] };
    case 'approval': return { title: 'Review an approval', steps: [
      { route: { page: 'inbox' }, target: '[data-guide="nav-inbox"]', title: 'Approvals arrive in your Inbox', body: 'When a worker wants to do something that needs a person, it waits here. The badge shows how many are waiting.' },
      { target: '.ws-page', title: 'Decide in one click', body: 'Each request says what the worker wants to do, where and why. Approve it, decline it, or edit it first.' },
      { title: 'Trust an action? Always allow it', body: 'Choose “Always allow” on an approval and that exact action won’t ask again. Everything else still follows your rules.' },
      { target: '[data-guide="nav-policies"]', title: 'Your rules live in Policies', body: 'The approval rule you chose during setup is here. Change it whenever you like.' },
    ] };
    case 'schedule': return { title: 'Set a schedule or a trigger', steps: [
      { route: { page: 'jobs' }, target: '[data-guide="nav-jobs"]', title: 'Jobs are work that repeats', body: 'A job is a task a worker owns. It can run at set times or start the moment something happens.' },
      { target: '.ws-page', title: 'Open a job to set its timing', body: 'Choose daily, weekly or every few hours, or start it when something happens, such as a new issue.' },
      { route: chat, target: '.cf-composer', title: 'Or just ask', body: <>Tell your worker when to work and it sets the job up for you: <CopyExample text='Every weekday at 9:00, summarise the new issues in my repository' /></> },
    ] };
  }
}

function visibleElement(selector: string): HTMLElement | null {
  for (const element of Array.from(document.querySelectorAll<HTMLElement>(selector))) {
    const rect = element.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0 && getComputedStyle(element).visibility !== 'hidden') return element;
  }
  return null;
}

export function OnboardingProvider({ onboarding, refresh, dismiss, children }: { onboarding: Onboarding | null; refresh: () => void; dismiss: (dismissed: boolean) => void; children: ReactNode }) {
  const [active, setActive] = useState<{ id: GuideId; index: number } | null>(null);
  const startGuide = useCallback((id: GuideId) => setActive({ id, index: 0 }), []);
  const value = useMemo(() => ({ onboarding, refresh, dismiss, startGuide }), [onboarding, refresh, dismiss, startGuide]);
  return <OnboardingContext.Provider value={value}>
    {children}
    {active && <GuideOverlay guide={guideSteps(active.id, onboarding)} index={active.index}
      onIndex={(index) => setActive({ ...active, index })} onClose={() => { setActive(null); refresh(); }} />}
  </OnboardingContext.Provider>;
}

/** Points at one thing at a time. The page stays usable underneath; nothing is clicked for you. */
function GuideOverlay({ guide, index, onIndex, onClose }: { guide: { title: string; steps: GuideStep[] }; index: number; onIndex: (index: number) => void; onClose: () => void }) {
  const step = guide.steps[index];
  const route = useRoute();
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [searching, setSearching] = useState(true);
  const card = useRef<HTMLDivElement>(null);
  const [cardSize, setCardSize] = useState({ width: 340, height: 200 });

  useEffect(() => { if (step.route && formatRoute(step.route) !== formatRoute(route)) navigate(step.route); }, [step]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setRect(null); setSearching(Boolean(step.target));
    if (!step.target) return;
    let frame = 0; let element: HTMLElement | null = null;
    const started = performance.now();
    const track = () => { if (element?.isConnected) setRect(element.getBoundingClientRect()); };
    const find = () => {
      element = visibleElement(step.target as string);
      if (element) {
        const box = element.getBoundingClientRect();
        if (box.top < 64 || box.bottom > window.innerHeight - 24) element.scrollIntoView({ block: 'center', behavior: 'smooth' });
        track(); setSearching(false);
      } else if (performance.now() - started < 2500) frame = requestAnimationFrame(find);
      else setSearching(false);
    };
    frame = requestAnimationFrame(find);
    window.addEventListener('resize', track); window.addEventListener('scroll', track, true);
    const timer = window.setInterval(track, 400);
    return () => { cancelAnimationFrame(frame); window.removeEventListener('resize', track); window.removeEventListener('scroll', track, true); window.clearInterval(timer); };
  }, [step]);

  useLayoutEffect(() => {
    if (card.current) setCardSize({ width: card.current.offsetWidth, height: card.current.offsetHeight });
    card.current?.focus({ preventScroll: true });
  }, [index, rect === null]);

  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'ArrowRight' && index < guide.steps.length - 1) onIndex(index + 1);
      if (event.key === 'ArrowLeft' && index > 0) onIndex(index - 1);
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [index, guide.steps.length, onClose, onIndex]);

  const phone = window.innerWidth < 640;
  const pad = 8;
  let position: React.CSSProperties | undefined;
  if (rect && !phone) {
    const below = rect.bottom + 14 + cardSize.height < window.innerHeight;
    const top = below ? rect.bottom + 14 : Math.max(12, rect.top - 14 - cardSize.height);
    const right = rect.left + rect.width + 16 + cardSize.width < window.innerWidth;
    const besides = rect.height > window.innerHeight * 0.5 || rect.width < 260;
    position = besides && right
      ? { top: Math.min(Math.max(12, rect.top), window.innerHeight - cardSize.height - 12), left: rect.right + 16 }
      : { top, left: Math.min(Math.max(12, rect.left), window.innerWidth - cardSize.width - 12) };
  }
  const last = index === guide.steps.length - 1;

  return <div className='ob-guide' role='presentation'>
    {rect ? <div className='ob-spotlight' style={{ top: rect.top - pad, left: rect.left - pad, width: rect.width + pad * 2, height: rect.height + pad * 2 }} />
      : !searching && <div className='ob-scrim' onClick={onClose} />}
    <div ref={card} className='ob-guide-card' data-placement={rect && !phone ? 'anchored' : phone ? 'sheet' : 'center'} style={position}
      data-edge={phone && rect && rect.top + rect.height / 2 > window.innerHeight / 2 ? 'top' : undefined}
      role='dialog' aria-modal='false' aria-labelledby='ob-guide-title' tabIndex={-1}>
      <header>
        <span>{guide.title}</span>
        <button type='button' className='ws-icon-button' aria-label='Close guide' onClick={onClose}><X size={15} /></button>
      </header>
      <h3 id='ob-guide-title'>{step.title}</h3>
      <div className='ob-guide-body'>{step.body}</div>
      <footer>
        <span className='ob-dots' aria-label={`Step ${index + 1} of ${guide.steps.length}`}>{guide.steps.map((_, item) => <i key={item} data-on={item === index ? 'true' : undefined} />)}</span>
        <span className='ob-guide-nav'>
          {index > 0 && <button type='button' className='ws-button ws-button-sm ws-button-quiet' onClick={() => onIndex(index - 1)}><ArrowLeft size={13} />Back</button>}
          <button type='button' className='ws-button ws-button-sm ws-button-primary' onClick={() => last ? onClose() : onIndex(index + 1)}>{last ? 'Done' : <>Next<ArrowRight size={13} /></>}</button>
        </span>
      </footer>
    </div>
  </div>;
}

/** "Show me how" on an empty page: starts that guide. */
export function GuideButton({ id, label = 'Show me how' }: { id: GuideId; label?: string }) {
  const context = useOnboarding();
  if (!context) return null;
  return <button type='button' className='ws-button ws-button-sm' onClick={() => context.startGuide(id)}><Compass size={14} />{label}</button>;
}
