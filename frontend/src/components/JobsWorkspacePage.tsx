import CodingProgress from './CodingProgress';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Archive, ArrowRight, BriefcaseBusiness, CheckCircle2, ListTodo, MoreHorizontal, PauseCircle, Pencil, Play, Plus, Search, Trash2, TriangleAlert } from 'lucide-react';
import type { AgentCapabilityProfile } from '../lib/capabilityApi';
import { approvalExplanation, decideApproval, listApprovals, type ApprovalRecord } from '../lib/approvalApi';
import type { OrganizationAccess } from '../lib/identityApi';
import type { JobsWorkspace, Job, WorkItem, JobStatus } from '../lib/jobsApi';
import type { ApiVersion } from '../lib/systemApi';
import type { ManagedWorker } from '../lib/workforceApi';
import { subscribeOrganizationRealtime } from '../platform/realtimeClient';
import { filterJobs } from './workspaceListModel';
import { IntegrationResumeControl } from './IntegrationTaskControls';
import { linkProps, navigate, queryParam, useRoute } from '../workspace/routes';
import { Ago, Avatar, EmptyState, Notice, PageHeader, Pills, Sheet, SkeletonLines, State, sentence } from '../workspace/ui';
import { notify } from '../workspace/feedback';
import { usePageTitle } from '../workspace/history';

export type JobsScene = 'jobs' | 'queue' | 'triggers' | 'runtime';

function queueLabel(item: WorkItem) {
  if (item.status === 'waiting_reconnect') return 'Waiting for reconnection';
  if (item.status === 'uncertain_outcome') return 'Outcome uncertain';
  if (item.status === 'partial_completion') return 'Partially completed';
  if (item.status === 'waiting_approval') return 'Waiting for approval';
  if (item.status === 'queued' && item.queueState === 'waiting_browser_capacity') return 'Waiting for browser capacity';
  if (item.status === 'queued' && item.queueState === 'retrying') return 'Retrying';
  if (item.status === 'queued' || item.status === 'admitted') return 'Queued';
  if (item.status === 'running' || item.status === 'claimed') return 'Running';
  return sentence(item.status);
}
const queueTone = (item: WorkItem) => item.status === 'completed' ? 'ok' : ['failed', 'policy_denied'].includes(item.status) ? 'danger' : item.status.startsWith('waiting') || item.status === 'uncertain_outcome' ? 'warn' : ['running', 'claimed'].includes(item.status) ? 'info' : 'neutral';
const PRIORITY_LABEL: Record<string, string> = { low: 'Low', normal: 'Normal', high: 'High', urgent: 'Urgent' };

export default function JobsWorkspacePage({ organization, apiVersion, data, workers, selected, profile, missing, ready, loading, saving, canManage, canRun, onSceneChange, onSelect, onCreate, onEdit, onStatus, onRun, onDelete, onCancel, onRetry, onProcess, onClearQueue, onApprovalDecided, automation }: {
  organization: OrganizationAccess;
  apiVersion: ApiVersion;
  data: JobsWorkspace;
  workers: ManagedWorker[];
  selected: Job | null;
  profile: AgentCapabilityProfile | null;
  missing: string[];
  ready: boolean;
  loading: boolean;
  saving: boolean;
  canManage: boolean;
  canRun: boolean;
  scene: JobsScene;
  onSceneChange: (scene: JobsScene) => void;
  onSelect: (job: Job | null) => void;
  onCreate: () => void;
  onEdit: (job: Job) => void;
  onStatus: (job: Job, status: 'active' | 'paused' | 'archived') => void;
  onRun: (job: Job) => void;
  onDelete: (job: Job) => void;
  onCancel: (item: WorkItem) => void;
  onRetry: (item: WorkItem) => void;
  onProcess: (item: WorkItem) => void;
  onClearQueue: () => void;
  onApprovalDecided: () => void;
  automation: ReactNode;
  runs?: ReactNode;
}) {
  const route = useRoute();
  usePageTitle(selected?.name);
  const routeJobId = route.page === 'jobs' ? route.jobId : undefined;
  const view = (['queue', 'automation'].includes(queryParam('view')) ? queryParam('view') : 'jobs') as 'jobs' | 'queue' | 'automation';
  const [search, setSearch] = useState('');
  const [stateFilter, setStateFilter] = useState<'all' | JobStatus>('all');
  const [queueFilter, setQueueFilter] = useState<'all' | 'waiting' | 'running' | 'finished'>('all');
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [approvalBusy, setApprovalBusy] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const workerMap = useMemo(() => new Map(workers.map((worker) => [worker.id, worker])), [workers]);
  const activeWorkers = workers.some((worker) => worker.status !== 'archived');
  const visibleJobs = filterJobs(data.jobs, new Map(workers.map((worker) => [worker.id, worker.name])), search, stateFilter);
  const groupedJobs = workers.map((worker) => ({ worker, jobs: visibleJobs.filter((job) => job.workerId === worker.id) })).filter((group) => group.jobs.length);
  const unassignedJobs = visibleJobs.filter((job) => !workerMap.has(job.workerId));
  const selectedWorker = selected ? workerMap.get(selected.workerId) : null;
  const history = selected ? data.workItems.filter((item) => item.jobId === selected.id).slice(0, 6) : [];
  const dispatchBlocked = !['ok', 'queued'].includes(data.dispatch.state);
  const queued = data.workItems.filter((item) => item.status === 'queued' || item.status === 'uncertain_outcome' || item.status.startsWith('waiting_'));
  const inMotion = data.workItems.filter((item) => item.status === 'running' || item.status === 'claimed' || item.status === 'admitted');
  const finished = data.workItems.filter((item) => ['completed', 'failed', 'cancelled', 'partial_completion', 'policy_denied'].includes(item.status));
  const queueRows = queueFilter === 'waiting' ? queued : queueFilter === 'running' ? inMotion : queueFilter === 'finished' ? finished : data.workItems;
  const count = (status: JobStatus) => data.jobs.filter((job) => job.status === status).length;

  // The URL owns the selected job and the section, so both survive refresh and can be shared.
  useEffect(() => { onSceneChange(view === 'automation' ? 'triggers' : view); }, [view]);
  useEffect(() => {
    if (loading) return;
    const job = routeJobId ? data.jobs.find((item) => item.id === routeJobId) ?? null : null;
    if ((job?.id ?? null) !== (selected?.id ?? null)) onSelect(job);
  }, [loading, routeJobId, data.jobs]);
  useEffect(() => { setMenu(false); }, [selected?.id]);
  useEffect(() => {
    if (!menu) return;
    const close = (event: PointerEvent) => { if (!(event.target as Element).closest('[data-ws-menu]')) setMenu(false); };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [menu]);

  useEffect(() => {
    if (!organization.permissions.includes('approvals.review')) return;
    let active = true;
    const refresh = () => { void listApprovals(apiVersion, organization.id).then((items) => { if (active) setApprovals(items.filter((item) => item.status === 'pending')); }).catch(() => { if (active) setApprovals([]); }); };
    refresh();
    const unsubscribe = subscribeOrganizationRealtime({ organizationId: organization.id, eventTypes: ['approval.created', 'approval.decided', 'run.waiting_approval'], onEvent: refresh, poll: refresh });
    return () => { active = false; unsubscribe(); };
  }, [apiVersion, organization.id, organization.permissions]);

  async function decide(record: ApprovalRecord, decision: 'approve' | 'reject') {
    setApprovalBusy(record.id);
    try {
      await decideApproval(apiVersion, organization.id, record.id, decision, 'Decided in the work queue.');
      setApprovals((current) => current.filter((item) => item.id !== record.id));
      notify({ key: 'queue-approval', tone: 'ok', title: decision === 'approve' ? 'Approved. The run continues.' : 'Declined. The action will not run.' });
      onApprovalDecided();
    } catch (error) {
      notify({ key: 'queue-approval', tone: 'danger', title: 'The decision was not saved', body: error instanceof Error ? error.message : undefined });
    } finally { setApprovalBusy(null); }
  }

  const openJob = (job: Job | null) => navigate((job ? linkProps({ page: 'jobs', jobId: job.id }).href : linkProps({ page: 'jobs' }).href) + window.location.search, { keepScroll: true });
  const jobRow = (job: Job) => <li key={job.id} className='ws-row ws-job-row' aria-current={selected?.id === job.id ? 'true' : undefined}>
    <span className='ws-priority' data-priority={job.priority} title={`${PRIORITY_LABEL[job.priority]} priority`} aria-label={`${PRIORITY_LABEL[job.priority]} priority`} />
    <a className='ws-row-main ws-row-link' {...linkProps({ page: 'jobs', jobId: job.id })} onClick={(event) => { if (event.metaKey || event.ctrlKey || event.shiftKey) return; event.preventDefault(); openJob(job); }}>
      <strong>{job.name}</strong><small>{job.description || job.objective}</small>
    </a>
    <div className='ws-row-meta'><State value={job.status} /><span className='ws-hide-sm ws-mono'>r{job.revision}</span>
      {canRun && job.status === 'active' && <button type='button' className='ws-button ws-button-sm' disabled={saving} onClick={() => onRun(job)}><Play size={13} />Run</button>}
    </div>
  </li>;

  return <div className='ws-page'>
    <PageHeader title='Jobs' description='What each worker is responsible for, the work waiting in the queue, and when it starts on its own.'
      actions={canManage && <button type='button' className='ws-button ws-button-primary' disabled={!activeWorkers} onClick={onCreate}><Plus size={15} />New job</button>} />

    <nav className='ws-tabs' aria-label='Jobs sections'>
      {([['jobs', 'Jobs', data.jobs.length], ['queue', 'Work queue', queued.length + inMotion.length], ['automation', 'Automation', null]] as const).map(([id, label, value]) =>
        <a key={id} aria-current={view === id ? 'page' : undefined} href={linkProps({ page: 'jobs' }).href + (id === 'jobs' ? '' : `?view=${id}`)} onClick={(event) => { if (event.metaKey || event.ctrlKey) return; event.preventDefault(); navigate(linkProps({ page: 'jobs' }).href + (id === 'jobs' ? '' : `?view=${id}`)); }}>{label}{value !== null && <span className='ws-tab-count'>{value}</span>}</a>)}
      <a className='ws-tabs-aside' {...linkProps({ page: 'runs' })}>Run history <ArrowRight size={13} /></a>
    </nav>

    {view === 'jobs' && <>
      <div className='ws-toolbar'>
        <Pills label='Job state' value={stateFilter} onChange={setStateFilter} options={[{ value: 'all', label: 'All', count: data.jobs.length }, { value: 'active', label: 'Active', count: count('active') }, { value: 'draft', label: 'Draft', count: count('draft') }, { value: 'paused', label: 'Paused', count: count('paused') }, { value: 'archived', label: 'Archived', count: count('archived') }]} />
        <label className='ws-search-field'><Search size={15} aria-hidden='true' /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder='Search jobs or workers' aria-label='Search jobs' /></label>
      </div>
      {loading ? <SkeletonLines rows={5} /> : visibleJobs.length === 0
        ? <EmptyState icon={<BriefcaseBusiness size={18} />} title={data.jobs.length ? 'Nothing matches these filters' : 'Give a worker its first job'} action={!data.jobs.length && canManage ? <button type='button' className='ws-button ws-button-primary' disabled={!activeWorkers} onClick={onCreate}><Plus size={15} />Create a job</button> : undefined}>{data.jobs.length ? 'Try another search or state.' : 'A job is a clear objective a worker owns. Audoryn prepares how it runs and what it may touch.'}</EmptyState>
        : <div className='ws-day-groups'>
          {groupedJobs.map(({ worker, jobs }) => <section key={worker.id} className='ws-section' aria-label={`${worker.name} jobs`}>
            <div className='ws-section-head'><h2><Avatar name={worker.name} seed={worker.id} size={22} />{worker.name}</h2><span className='ws-count'>{jobs.length}</span><p>{worker.department || 'Worker'}</p></div>
            <ul className='ws-rows'>{jobs.map(jobRow)}</ul>
          </section>)}
          {unassignedJobs.length > 0 && <section className='ws-section'><div className='ws-section-head'><h2>Worker unavailable</h2><span className='ws-count'>{unassignedJobs.length}</span></div><ul className='ws-rows'>{unassignedJobs.map(jobRow)}</ul></section>}
        </div>}
    </>}

    {view === 'queue' && <>
      {dispatchBlocked && <Notice tone='warn'>Starting work is delayed. {data.dispatch.detail || 'Queued work stays safe and starts once dispatch recovers.'}</Notice>}
      <div className='ws-toolbar'>
        <Pills label='Queue state' value={queueFilter} onChange={setQueueFilter} options={[{ value: 'all', label: 'All', count: data.workItems.length }, { value: 'waiting', label: 'Waiting', count: queued.length }, { value: 'running', label: 'Running', count: inMotion.length }, { value: 'finished', label: 'Finished', count: finished.length }]} />
        {canRun && data.workItems.length > 0 && <button type='button' className='ws-button ws-button-sm ws-button-quiet ws-danger-text' disabled={saving} onClick={onClearQueue}><Trash2 size={14} />Clear queue</button>}
      </div>
      {loading ? <SkeletonLines rows={5} /> : queueRows.length === 0
        ? <EmptyState icon={<ListTodo size={18} />} title='Nothing in this view'>Scheduled and event-driven jobs add work here automatically.</EmptyState>
        : <ul className='ws-rows'>{queueRows.map((item) => {
          const job = data.jobs.find((entry) => entry.id === item.jobId);
          const worker = workerMap.get(item.workerId);
          const approval = approvals.find((entry) => entry.runId === item.runId && entry.status === 'pending');
          const note = item.status === 'waiting_approval' ? (approval ? approvalExplanation(approval) : item.waitingReason || 'Needs an approval before this run can continue.') : item.waitingReason ?? (item.status === 'queued' ? dispatchBlocked ? 'Starting is delayed' : 'Waiting to start' : null);
          return <li key={item.id} className='ws-row ws-queue-row' data-tone={queueTone(item)}>
            <Avatar name={worker?.name ?? 'Worker'} seed={item.workerId} size={30} live={['running', 'claimed'].includes(item.status) ? 'working' : item.status.startsWith('waiting') ? 'waiting' : undefined} />
            <div className='ws-row-main'>
              <strong>{job?.name ?? item.snapshot.jobName}</strong>
              <small>{worker?.name ?? 'Unknown worker'} · {PRIORITY_LABEL[item.priority] ?? item.priority} priority · revision {item.jobRevision}{note ? ` · ${note}` : ''}</small>
              <CodingProgress organizationId={organization.id} version={apiVersion} workItemId={item.id} canCancel={canRun} />
            </div>
            <div className='ws-row-meta ws-queue-meta'>
              <span className='ws-state' data-tone={queueTone(item)}>{queueLabel(item)}</span>
              <Ago value={item.createdAt} />
              <div className='ws-queue-actions'>
                {approval && <><button type='button' className='ws-button ws-button-sm' disabled={approvalBusy === approval.id} onClick={() => void decide(approval, 'reject')}>Decline</button><button type='button' className='ws-button ws-button-sm ws-button-primary' disabled={approvalBusy === approval.id} onClick={() => void decide(approval, 'approve')}>Approve</button></>}
                {canRun && item.status === 'waiting_reconnect' && <IntegrationResumeControl organizationId={organization.id} version={apiVersion} workItemId={item.id} onDone={onApprovalDecided} />}
                {item.runId && <a className='ws-button ws-button-sm ws-button-quiet' {...linkProps({ page: 'runs', runId: item.runId })}>Run</a>}
                {canManage && item.status === 'queued' && <button type='button' className='ws-button ws-button-sm' disabled={saving} onClick={() => onCancel(item)}>Cancel</button>}
                {canRun && item.status === 'failed' && item.retryCount < 2 && <button type='button' className='ws-button ws-button-sm' disabled={saving} onClick={() => onRetry(item)}>Retry</button>}
                {canRun && item.status === 'queued' && new Date(item.scheduledAt).getTime() <= Date.now() && <button type='button' className='ws-button ws-button-sm ws-button-quiet' title='Start now instead of waiting for the dispatcher' disabled={saving} onClick={() => onProcess(item)}>Start now</button>}
              </div>
            </div>
          </li>;
        })}</ul>}
    </>}

    {view === 'automation' && <section className='ws-section ws-legacy-embed' aria-label='Automation'>
      <div className='ws-section-head'><h2>When work starts</h2><p>Schedules, events and dependencies that queue work without anyone asking.</p></div>
      {automation}
    </section>}

    {(data.window.jobsTruncated || data.window.workItemsTruncated) && <p className='ws-muted'>This view shows the most recent records only.</p>}

    <Sheet open={Boolean(selected)} onClose={() => openJob(null)} wide title={selected?.name ?? ''} subtitle={selected ? <>{selectedWorker?.name ?? 'Worker unavailable'} · {PRIORITY_LABEL[selected.priority]} priority · revision {selected.revision}</> : undefined}
      footer={selected && <>
        {canManage && <div className='ws-menu-anchor' data-ws-menu style={{ marginRight: 'auto' }}>
          <button type='button' className='ws-icon-button' aria-label='More job actions' aria-expanded={menu} onClick={() => setMenu(!menu)}><MoreHorizontal size={16} /></button>
          {menu && <div className='ws-menu ws-menu-up' role='menu'>
            {selected.status !== 'archived' && <button type='button' role='menuitem' disabled={saving} onClick={() => onStatus(selected, 'archived')}><Archive size={14} />Archive job</button>}
            <button type='button' role='menuitem' className='ws-danger-text' disabled={saving} onClick={() => onDelete(selected)}><Trash2 size={14} />Delete job</button>
          </div>}
        </div>}
        {canManage && selected.status !== 'archived' && <button type='button' className='ws-button' onClick={() => onEdit(selected)}><Pencil size={14} />Edit</button>}
        {canManage && (selected.status === 'draft' || selected.status === 'paused') && <button type='button' className='ws-button' disabled={saving} onClick={() => onStatus(selected, 'active')}><Play size={14} />{selected.status === 'paused' ? 'Resume' : 'Activate'}</button>}
        {canManage && selected.status === 'active' && <button type='button' className='ws-button' disabled={saving} onClick={() => onStatus(selected, 'paused')}><PauseCircle size={14} />Pause</button>}
        {canRun && selected.status === 'active' && <button type='button' className='ws-button ws-button-primary' disabled={saving} onClick={() => onRun(selected)}><Play size={14} />Run now</button>}
      </>}>
      {selected && <div className='ws-job-detail'>
        <div className='ws-readiness' data-ready={ready ? 'true' : undefined}>{ready ? <CheckCircle2 size={16} /> : <TriangleAlert size={16} />}<div><strong>{ready ? 'Ready to run' : 'Needs setup before it can run'}</strong><span>{selectedWorker?.status !== 'active' ? 'The worker must be active.' : profile?.agent.status !== 'active' ? 'The worker’s agent identity must be active.' : missing.length ? `Missing capability: ${missing[0]}` : 'Requirements are checked again before every run.'}</span></div><State value={selected.status} /></div>
        <dl className='ws-job-facts'>
          <div><dt>Objective</dt><dd>{selected.objective}</dd></div>
          <div><dt>Instructions</dt><dd>{selected.instructions || <span className='ws-muted'>No job-specific instructions.</span>}</dd></div>
          <div><dt>Done when</dt><dd>{selected.completionCriteria.length ? <ul>{selected.completionCriteria.map((item) => <li key={item}>{item}</li>)}</ul> : <span className='ws-muted'>No criteria set.</span>}</dd></div>
          <div><dt>Needs access to</dt><dd>{selected.requiredCapabilities.length ? <span className='ws-chip-list'>{selected.requiredCapabilities.map((item) => <code key={item}>{item}</code>)}</span> : <span className='ws-muted'>No external tools.</span>}</dd></div>
          <div><dt>Responsibilities</dt><dd>{selected.responsibilityLinks.length ? selected.responsibilityLinks.join(', ') : <span className='ws-muted'>None linked.</span>}</dd></div>
        </dl>
        <section className='ws-section'>
          <div className='ws-section-head'><h2>Recent work</h2><span className='ws-count'>{history.length}</span></div>
          {history.length ? <ul className='ws-rows'>{history.map((item) => <li key={item.id} className='ws-row ws-compact-row'>
            <span className='ws-state' data-tone={queueTone(item)}>{queueLabel(item)}</span>
            <span className='ws-row-main'><small>{sentence(item.trigger.type)} · revision {item.jobRevision}</small></span>
            <div className='ws-row-meta'>{item.runId && <a className='ws-link' {...linkProps({ page: 'runs', runId: item.runId })}>Run</a>}<Ago value={item.createdAt} /></div>
          </li>)}</ul> : <p className='ws-quiet'>No work has been queued for this job yet.</p>}
        </section>
      </div>}
    </Sheet>
  </div>;
}
