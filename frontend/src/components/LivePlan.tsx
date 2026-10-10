import { useRef } from 'react';
import { Check, CircleDashed, Hand, Loader2, OctagonX } from 'lucide-react';
import type { TaskActivity } from '../lib/taskActivityApi';
import { TaskActivityPanel } from './TaskActivityPanel';
import { activityElapsed, phaseLabel } from './taskActivityModel';

export type PlanStepState = 'waiting' | 'running' | 'done' | 'held' | 'blocked';

/** Runtime step statuses (pending, running, waiting_approval, completed, failed) in plain words. */
export function planStepState(state: string): PlanStepState {
  if (state === 'completed' || state === 'executed' || state === 'success') return 'done';
  if (state === 'running' || state === 'executing') return 'running';
  if (state === 'waiting_approval' || state === 'held') return 'held';
  if (state === 'failed' || state === 'blocked' || state === 'policy_denied' || state === 'cancelled') return 'blocked';
  return 'waiting';
}

const STEP_LABEL: Record<PlanStepState, string> = { waiting: 'Waiting', running: 'Running', done: 'Done', held: 'Held for approval', blocked: 'Blocked' };

function duration(fromIso: string, toMs: number) {
  const seconds = Math.max(0, Math.round((toMs - Date.parse(fromIso)) / 1000));
  if (!Number.isFinite(seconds)) return '';
  return seconds >= 3600 ? `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m` : seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
}

function StepIcon({ state }: { state: PlanStepState }) {
  if (state === 'done') return <Check size={13} strokeWidth={2.4} />;
  if (state === 'running') return <Loader2 size={13} strokeWidth={2.2} className='cf-spin' />;
  if (state === 'held') return <Hand size={12} strokeWidth={2.2} />;
  if (state === 'blocked') return <OctagonX size={13} strokeWidth={2.2} />;
  return <CircleDashed size={13} strokeWidth={2} />;
}

/**
 * The worker's live plan, shown inline in the conversation while it works.
 * Steps come from the runtime's observed actions; the current phase is always the last line.
 */
export function LivePlan({ task, observedAt, disconnected, now, workerName, index, total }: {
  task: TaskActivity; observedAt: string; disconnected: boolean; now: number; workerName: string; index: number; total: number;
}) {
  const details = useRef<HTMLDialogElement>(null);
  const clock = disconnected ? Date.parse(observedAt) : now;
  const steps = task.events.map((event, position) => {
    const state = planStepState(event.state);
    const started = position === 0 ? task.startedAt : task.events[position - 1].at;
    const end = state === 'running' || state === 'waiting' ? clock : Date.parse(event.at);
    return { ...event, state, took: duration(started, end) };
  });
  const held = task.status === 'waiting_approval' || steps.some((step) => step.state === 'held');
  const working = !disconnected && !held && !steps.some((step) => step.state === 'running');
  return <section className='cf-plan' data-state={disconnected ? 'stale' : held ? 'held' : 'live'} aria-label={`${workerName} live plan`}>
    <header className='cf-plan-head'>
      <span className='cf-plan-pulse' aria-hidden='true' />
      <strong>{disconnected ? 'Last observed' : held ? 'Waiting for you' : 'Working'}</strong>
      <span className='cf-plan-meta'>{total > 1 ? `Task ${index + 1} of ${total} · ` : ''}{phaseLabel(task.phase)}</span>
      <time className='cf-plan-time'>{activityElapsed(task, observedAt, clock)}</time>
      <button type='button' className='cf-plan-more' onClick={() => details.current?.showModal()}>Details</button>
    </header>
    <ol className='cf-plan-steps'>
      {steps.map((step) => <li key={step.id} data-state={step.state}>
        <span className='cf-plan-icon' aria-hidden='true'><StepIcon state={step.state} /></span>
        <span className='cf-plan-label'>{step.label}{step.state === 'done' && step.verified === false ? <em> · outcome unverified</em> : null}</span>
        <span className='cf-plan-status'>{STEP_LABEL[step.state]}</span>
        <time>{step.took}</time>
      </li>)}
      {working && <li data-state='running' className='cf-plan-current'>
        <span className='cf-plan-icon' aria-hidden='true'><StepIcon state='running' /></span>
        <span className='cf-plan-label' role='status'>{task.detail || phaseLabel(task.phase)}</span>
        <span className='cf-plan-status'>{task.executionState === 'retry_scheduled' ? 'Retry scheduled' : 'Running'}</span>
        <time>{task.phaseStartedAt ? duration(task.phaseStartedAt, clock) : ''}</time>
      </li>}
    </ol>
    {disconnected && <p className='cf-plan-note'>Updates interrupted. Reconnecting; the work shown is what was last observed.</p>}
    {task.retryAt && <p className='cf-plan-note'>Next retry at {new Date(task.retryAt).toLocaleTimeString()}.</p>}
    <dialog ref={details} className='cf-process-dialog' aria-label='Task process details' onClick={(event) => { if (event.target === details.current) details.current?.close(); }}>
      <div className='cf-process-dialog-content'>
        <header><strong>Task process</strong><button type='button' onClick={() => details.current?.close()} aria-label='Close process details'>Close</button></header>
        <TaskActivityPanel tasks={[task]} observedAt={observedAt} disconnected={disconnected} now={now} historical />
      </div>
    </dialog>
  </section>;
}

export type GovernanceOutcome = 'allowed' | 'held' | 'blocked';

/** The policy outcome of a command a worker attempted, when the status says so. */
export function governanceOutcome(status: unknown): GovernanceOutcome | null {
  const value = String(status ?? '');
  if (['policy_denied', 'denied', 'blocked', 'rejected'].includes(value)) return 'blocked';
  if (['waiting_approval', 'held', 'approval_required', 'require_approval'].includes(value)) return 'held';
  if (['completed', 'executed', 'allowed', 'succeeded'].includes(value)) return 'allowed';
  return null;
}
