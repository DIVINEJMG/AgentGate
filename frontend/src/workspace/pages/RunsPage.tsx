import { useEffect, useRef, useState } from 'react';
import { Check, CircleDashed, Hand, Loader2, OctagonX, RotateCcw, Waypoints } from 'lucide-react';
import type { OrganizationAccess } from '../../lib/identityApi';
import type { ApiVersion } from '../../lib/systemApi';
import { continueRun, getRun, loadRuntime, type RuntimeRun, type RuntimeStep, type RuntimeWorkspace } from '../../lib/runtimeApi';
import { subscribeOrganizationRealtime } from '../../platform/realtimeClient';
import { planStepState, type PlanStepState } from '../../components/LivePlan';
import { notify } from '../feedback';
import { usePageTitle } from '../history';
import { useLive } from '../live';
import { linkProps, navigate, queryParam, setQuery } from '../routes';
import { Ago, Avatar, EmptyState, Notice, PageHeader, Pills, SkeletonLines, State, humanize, sentence } from '../ui';

const EMPTY: RuntimeWorkspace = { runs: [], summary: { total: 0, planning: 0, running: 0, waitingApproval: 0, completed: 0, failed: 0 }, window: { limit: 100, truncated: false }, runtime: { autonomousCadence: 'every 5 minutes', maxAiRunsPerCron: 5, maxPlanSteps: 6, maxRetries: 2 } };
type Filter = 'all' | 'active' | 'waiting' | 'completed' | 'failed';
const ACTIVE = ['planning', 'running'];
const DONE = ['completed', 'failed', 'cancelled', 'policy_denied', 'partial_completion'];
const matches = (run: RuntimeRun, filter: Filter) => filter === 'all' ? true : filter === 'active' ? ACTIVE.includes(run.status) : filter === 'waiting' ? run.status.startsWith('waiting') || run.status === 'uncertain_outcome' : filter === 'completed' ? run.status === 'completed' || run.status === 'partial_completion' : ['failed', 'cancelled', 'policy_denied'].includes(run.status);
function errorText(value: unknown) {
  const data = value as { response?: { data?: { error?: string } }; message?: string };
  return data.response?.data?.error || data.message || 'Managed Runtime request failed.';
}
function took(from: string | null, to: string | null) {
  if (!from) return '';
  const seconds = Math.max(0, Math.round(((to ? Date.parse(to) : Date.now()) - Date.parse(from)) / 1000));
  return seconds >= 3600 ? `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m` : seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
}

export default function RunsPage({ organization, apiVersion, runId, onWorkChanged }: { organization: OrganizationAccess; apiVersion: ApiVersion; runId?: string; onWorkChanged: () => void }) {
  const live = useLive();
  const [data, setData] = useState<RuntimeWorkspace>(EMPTY);
  const [steps, setSteps] = useState<RuntimeStep[]>([]);
  const [stepsFor, setStepsFor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canRun = organization.permissions.includes('jobs.run');
  const filter = (['active', 'waiting', 'completed', 'failed'].includes(queryParam('filter')) ? queryParam('filter') : 'all') as Filter;
  const selectedRef = useRef(runId);
  selectedRef.current = runId;

  async function loadSteps(id: string) {
    try { const detail = await getRun(apiVersion, organization.id, id); if (selectedRef.current === id) { setSteps(detail.steps); setStepsFor(id); } }
    catch (caught) { if (selectedRef.current === id) setError(errorText(caught)); }
  }
  async function refresh() {
    setError(null);
    try {
      setData(await loadRuntime(apiVersion, organization.id));
      if (selectedRef.current) await loadSteps(selectedRef.current);
    } catch (caught) { setError(errorText(caught)); } finally { setLoading(false); }
  }
  useEffect(() => { setLoading(true); void refresh(); }, [apiVersion, organization.id]);
  useEffect(() => { if (runId) void loadSteps(runId); }, [runId]);
  useEffect(() => subscribeOrganizationRealtime({ organizationId: organization.id, eventTypes: ['run.created', 'run.started', 'run.progress', 'run.step.started', 'run.step.completed', 'run.waiting_approval', 'run.failed', 'run.completed', 'action.proposed', 'action.approved', 'action.blocked', 'action.executed', 'approval.created', 'approval.decided'], onEvent: () => void refresh(), poll: () => refresh() }), [apiVersion, organization.id]);

  const visible = data.runs.filter((run) => matches(run, filter)).sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt));
  const selected = data.runs.find((run) => run.id === runId) ?? null;
  usePageTitle(selected ? selected.planSummary || 'Run' : null);
  const at = (id?: string) => ({ page: 'runs' as const, runId: id });
  const count = (value: Filter) => data.runs.filter((run) => matches(run, value)).length;
  const workerName = (run: RuntimeRun) => live.workers.find((worker) => worker.id === run.workerId)?.name ?? 'Worker';

  useEffect(() => {
    if (loading || runId || !visible[0] || window.matchMedia('(max-width: 860px)').matches) return;
    navigate(linkProps(at(visible[0].id)).href + window.location.search, { replace: true, keepScroll: true });
  }, [loading, runId, visible[0]?.id]);

  async function resume() {
    if (!selected) return;
    setSaving(true);
    try { await continueRun(apiVersion, organization.id, selected.id); notify({ key: 'run-continue', tone: 'ok', title: selected.status === 'waiting_ai' ? 'Planning retried' : 'Run continued' }); await refresh(); onWorkChanged(); }
    catch (caught) { notify({ key: 'run-continue', tone: 'danger', title: 'Could not continue the run', body: errorText(caught) }); } finally { setSaving(false); }
  }

  return <div className='ws-page ws-page-wide'>
    <PageHeader title='Runs' description={<>Each time a worker picks up work: its plan, every step, and how it ended. Autonomous work is checked {data.runtime.autonomousCadence}.</>} />
    {error && <Notice tone='danger' action={<button type='button' className='ws-button ws-button-sm' onClick={() => void refresh()}>Retry</button>}>{error}</Notice>}
    <div className='ws-inbox' data-detail={selected ? 'true' : undefined}>
      <div className='ws-inbox-list'>
        <Pills label='Run state' value={filter} onChange={(next) => setQuery({ filter: next === 'all' ? undefined : next })} options={[
          { value: 'all', label: 'All', count: data.runs.length }, { value: 'active', label: 'Working', count: count('active') }, { value: 'waiting', label: 'Waiting', count: count('waiting') }, { value: 'completed', label: 'Done', count: count('completed') }, { value: 'failed', label: 'Stopped', count: count('failed') },
        ]} />
        {loading ? <SkeletonLines rows={6} avatar /> : visible.length === 0
          ? <EmptyState icon={<Waypoints size={18} />} title={data.runs.length ? 'No runs in this view' : 'No runs yet'}>{data.runs.length ? 'Try another state.' : 'A run starts when a worker picks up queued work.'}</EmptyState>
          : <ul className='ws-rows'>{visible.map((run) => {
            const total = run.stepIds.length;
            const working = ACTIVE.includes(run.status);
            return <li key={run.id} className='ws-row ws-inbox-row' aria-current={run.id === runId ? 'true' : undefined}>
              <Avatar name={workerName(run)} seed={run.workerId} size={30} live={working ? 'working' : run.status.startsWith('waiting') ? 'waiting' : undefined} />
              <a className='ws-row-main ws-row-link' {...linkProps(at(run.id))} onClick={(event) => { if (event.metaKey || event.ctrlKey || event.shiftKey) return; event.preventDefault(); navigate(linkProps(at(run.id)).href + window.location.search, { keepScroll: true }); }}>
                <strong>{run.planSummary || 'Planning the work'}</strong>
                <small>{workerName(run)}{total ? ` · step ${Math.min(run.currentStep + 1, total)} of ${total}` : ''}{run.attempt > 1 ? ` · attempt ${run.attempt}` : ''}</small>
              </a>
              <div className='ws-inbox-row-meta'><State value={run.status} /><Ago value={run.updatedAt} /></div>
            </li>;
          })}</ul>}
        {data.window.truncated && <p className='ws-muted'>Showing the latest {data.window.limit} runs.</p>}
      </div>
      <div className='ws-inbox-detail'>
        {selected ? <>
          <a className='ws-back ws-inbox-back' {...linkProps(at())}>← Runs</a>
          <RunDetail run={selected} workerName={workerName(selected)} steps={stepsFor === selected.id ? steps : null} canRun={canRun} saving={saving} onResume={() => void resume()} />
        </> : <div className='ws-inbox-placeholder'><Waypoints size={20} /><span>{loading ? 'Loading runs…' : 'Select a run to follow its plan.'}</span></div>}
      </div>
    </div>
  </div>;
}

function StepIcon({ state }: { state: PlanStepState }) {
  if (state === 'done') return <Check size={13} strokeWidth={2.4} />;
  if (state === 'running') return <Loader2 size={13} strokeWidth={2.2} className='cf-spin' />;
  if (state === 'held') return <Hand size={12} strokeWidth={2.2} />;
  if (state === 'blocked') return <OctagonX size={13} strokeWidth={2.2} />;
  return <CircleDashed size={13} strokeWidth={2} />;
}

function RunDetail({ run, workerName, steps, canRun, saving, onResume }: { run: RuntimeRun; workerName: string; steps: RuntimeStep[] | null; canRun: boolean; saving: boolean; onResume: () => void }) {
  const finished = DONE.includes(run.status);
  return <div className='ws-detail' key={run.id}>
    <div className='ws-detail-head'>
      <Avatar name={workerName} seed={run.workerId} size={40} live={ACTIVE.includes(run.status) ? 'working' : undefined} />
      <div>
        <div className='ws-muted'><a className='ws-link' {...linkProps({ page: 'worker', workerId: run.workerId, tab: 'overview' })}>{workerName}</a> · started <Ago value={run.startedAt ?? run.createdAt} /></div>
        <h2>{run.planSummary || 'Planning the work'}</h2>
        <div className='ws-detail-sub'><State value={run.status} /><span className='ws-muted ws-mono'>{took(run.startedAt ?? run.createdAt, finished ? run.completedAt ?? run.updatedAt : null)}</span>{run.attempt > 1 && <span className='ws-muted'>Attempt {run.attempt}</span>}</div>
      </div>
    </div>
    {run.waitingReason && !finished && <Notice tone='warn'>{run.queueState === 'replanning' ? 'Choosing a corrected next step. ' : ''}{run.waitingReason}{run.retryAt ? ` Next retry ${new Date(run.retryAt).toLocaleTimeString()}.` : ''}</Notice>}
    {run.failure && <Notice tone='danger'>{run.failure}</Notice>}
    {run.resultSummary && <Notice tone='ok'>{run.resultSummary}</Notice>}
    {canRun && ['waiting_approval', 'waiting_ai'].includes(run.status) && <div className='ws-decision-actions ws-run-resume'><span className='ws-muted'>{run.status === 'waiting_ai' ? 'Planning stalled. You can ask the worker to plan again.' : 'Once the approval is decided, the run can continue.'}</span><button type='button' className='ws-button ws-button-primary' disabled={saving} onClick={onResume}><RotateCcw size={15} />{run.status === 'waiting_ai' ? 'Retry planning' : 'Continue after approval'}</button></div>}

    <section className='ws-section'>
      <div className='ws-section-head'><h2>Plan</h2>{steps && <span className='ws-count'>{steps.length}</span>}</div>
      {!steps ? <SkeletonLines rows={3} /> : steps.length === 0 ? <p className='ws-quiet'>The ordered plan appears once the run starts.</p> : <ol className='ws-timeline'>{steps.map((step) => {
        const state = planStepState(step.status);
        const browser = ['browser.element.press_key', 'browser.element.click'].includes(step.scope);
        const noChange = step.actionOutcome === 'no_observable_change';
        const label = noChange ? 'Ran, no page change' : step.status === 'completed' && browser ? (step.actionOutcome === 'observed_change' ? 'Ran, page changed' : 'Ran, outcome unconfirmed') : sentence(step.status === 'pending' ? 'waiting' : step.status);
        return <li key={step.id} data-state={noChange ? 'held' : state}>
          <span className='ws-timeline-icon' aria-hidden='true'><StepIcon state={noChange ? 'held' : state} /></span>
          <div className='ws-timeline-body'>
            <div className='ws-timeline-head'><strong>{step.title}</strong><span className='ws-timeline-state'>{label}</span><time className='ws-mono'>{took(step.startedAt, step.completedAt)}</time></div>
            <p>{step.instruction}</p>
            {noChange && <small>The action ran but its intended result was not seen. The worker decides the next step from what it observed.</small>}
            {!noChange && step.status === 'completed' && browser && <small>The interaction ran. The worker checks the page before treating the task as done.</small>}
            <small className='ws-timeline-meta'>{humanize(step.kind)}{step.scope ? <> · <code>{step.scope}</code></> : null}{step.actionId && <> · <a className='ws-link' {...linkProps({ page: 'activity', actionId: step.actionId })}>Gateway decision</a></>}{step.approvalId && <> · <a className='ws-link' {...linkProps({ page: 'inbox', kind: 'approval', id: step.approvalId })}>Approval</a></>}</small>
            {step.error && <p className='ws-timeline-error'>{step.error}</p>}
            {step.output && <details className='ws-timeline-output'><summary>Step output</summary><pre className='ws-code'>{step.output}</pre></details>}
          </div>
        </li>;
      })}</ol>}
    </section>
    <details className='ws-technical'><summary>Technical detail</summary><dl className='ws-facts'>
      <div><dt>Run</dt><dd><code>{run.id}</code></dd></div>
      <div><dt>Work item</dt><dd><code>{run.workItemId}</code></dd></div>
      <div><dt>Correlation</dt><dd><code>{run.correlationId}</code></dd></div>
      <div><dt>Context</dt><dd>{run.contextMemoryIds?.length ?? 0} memories · {run.artifactIds?.length ?? 0} artifacts</dd></div>
    </dl></details>
    <p className='ws-muted ws-small'>Planning never grants access. Every tool action still passes capability, policy, risk, approval and audit checks.</p>
  </div>;
}
