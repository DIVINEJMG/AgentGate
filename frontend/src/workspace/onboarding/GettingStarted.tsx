import { useEffect, useState } from 'react';
import { ArrowRight, Check, Compass, MessagesSquare, PartyPopper, Plug, PlayCircle, Repeat2, ShieldCheck, Sparkles, X } from 'lucide-react';
import type { ChecklistStep, ChecklistStepId } from '../../lib/onboardingApi';
import { linkProps, parseRoute, routeBase } from '../routes';
import { useOnboarding } from './guide';

const ICONS: Record<ChecklistStepId, typeof Plug> = {
  connect: Plug, say_hi: MessagesSquare, first_task: Sparkles, approval: ShieldCheck, schedule: Repeat2,
};

export function openSampleWorkspace() {
  window.open('/workspace-preview', '_blank', 'noopener');
}

/** The getting-started checklist on Home, for workspaces set up through the welcome flow. */
export default function GettingStarted() {
  const context = useOnboarding();
  const [hiding, setHiding] = useState(false);
  // Progress is worked out from what exists; read it again whenever Home opens.
  useEffect(() => { context?.refresh(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const onboarding = context?.onboarding;
  const checklist = onboarding?.checklist;
  if (!context || onboarding?.state !== 'ready' || onboarding.checklistDismissed || !checklist || hiding) return null;
  const done = checklist.complete === checklist.total;
  const next = checklist.steps.find((step) => !step.complete);
  const percent = Math.round((checklist.complete / checklist.total) * 100);
  const hide = () => { setHiding(true); context.dismiss(true); };

  return <section className='ob-start' aria-labelledby='ob-start-title' data-done={done ? 'true' : undefined}>
    <header className='ob-start-head'>
      <span className='ob-start-icon' aria-hidden='true'>{done ? <PartyPopper size={18} /> : <Compass size={18} />}</span>
      <div>
        <h2 id='ob-start-title'>{done ? 'You’re all set up' : 'Get started with Audoryn'}</h2>
        <p>{done ? 'Your workspace is ready for real work. You can hide this now.' : `${checklist.complete} of ${checklist.total} done · about 10 minutes in all`}</p>
      </div>
      <button type='button' className='ws-icon-button' aria-label='Hide getting started' title='Hide' onClick={hide}><X size={16} /></button>
    </header>
    <div className='ob-meter' role='progressbar' aria-label='Getting started progress' aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${percent}%` }} /></div>
    {!done && <ol className='ob-steps'>
      {checklist.steps.map((step) => <StepRow key={step.id} step={step} current={step.id === next?.id} onGuide={() => context.startGuide(step.id)} />)}
    </ol>}
    <footer className='ob-start-foot'>
      <button type='button' className='ob-link' onClick={openSampleWorkspace}><PlayCircle size={14} />Explore a sample workspace</button>
      {done && <button type='button' className='ws-button ws-button-sm' onClick={hide}>Hide</button>}
    </footer>
  </section>;
}

function StepRow({ step, current, onGuide }: { step: ChecklistStep; current: boolean; onGuide: () => void }) {
  const Icon = ICONS[step.id];
  const target = parseRoute(`${routeBase()}/${step.destination}`);
  return <li className='ob-step' data-state={step.complete ? 'done' : current ? 'current' : undefined}>
    <span className='ob-step-check' aria-hidden='true'>{step.complete ? <Check size={13} strokeWidth={3} /> : <Icon size={15} strokeWidth={1.8} />}</span>
    <span className='ob-step-copy'><strong>{step.label}</strong><small>{step.description}</small></span>
    <span className='ob-step-actions'>
      {step.complete ? <span className='ob-step-done'>Done</span> : <>
        <button type='button' className='ws-button ws-button-sm ws-button-quiet' onClick={onGuide}>Show me</button>
        <a className={`ws-button ws-button-sm${current ? ' ws-button-primary' : ''}`} aria-label={`${step.label}: open`} {...linkProps(target)}>{current ? 'Start' : 'Open'}<ArrowRight size={13} /></a>
      </>}
    </span>
  </li>;
}
