import { useEffect, useMemo, useState } from 'react';
import { CalendarClock, FileText, MessageSquare, MoreHorizontal, Pause, Pencil, Play } from 'lucide-react';
import type { AuthUser } from '../../platform/client';
import { listConversations, type ConversationThread } from '../../lib/conversationApi';
import type { OrganizationAccess } from '../../lib/identityApi';
import { loadJobsWorkspace, type Job, type WorkItem } from '../../lib/jobsApi';
import { loadMemory } from '../../lib/memoryApi';
import type { MemoryRecord } from '../../lib/memoryApi';
import { loadResults, type ResultSummary } from '../../lib/resultsApi';
import { loadTriggerWorkspace, type TriggerConfig } from '../../lib/schedulerApi';
import type { ApiVersion } from '../../lib/systemApi';
import type { ManagedWorker, SecurityIdentityOption, WorkforceRole, WorkerStatus } from '../../lib/workforceApi';
import { useLive } from '../live';
import { linkProps, type WorkerTab } from '../routes';
import { Ago, Avatar, EmptyState, PageSkeleton, Section, SkeletonLines, State, humanize } from '../ui';

const TABS: Array<{ id: WorkerTab; label: string }> = [
  { id: 'overview', label: 'Overview' }, { id: 'conversations', label: 'Conversations' }, { id: 'jobs', label: 'Jobs' },
  { id: 'results', label: 'Results' }, { id: 'memory', label: 'Memory' }, { id: 'settings', label: 'Settings' },
];

type Loaded = { jobs: Job[]; workItems: WorkItem[]; configs: TriggerConfig[]; results: ResultSummary[]; threads: ConversationThread[]; memories: MemoryRecord[] };

export default function WorkerProfilePage({ worker, loading, tab, role, identity, user, organization, apiVersion, canManage, saving, onEdit, onStatus, onDelete }: {
  worker: ManagedWorker | undefined;
  loading: boolean;
  tab: WorkerTab;
  role?: WorkforceRole;
  identity?: SecurityIdentityOption;
  user: AuthUser;
  organization: OrganizationAccess;
  apiVersion: ApiVersion;
  canManage: boolean;
  saving: boolean;
  onEdit: () => void;
  onStatus: (status: WorkerStatus) => void;
  onDelete: () => void;
}) {
  const live = useLive();
  const [data, setData] = useState<Loaded | null>(null);
  const [menu, setMenu] = useState(false);
  const workerId = worker?.id;

  useEffect(() => {
    if (!workerId) return;
    let active = true;
    setData(null);
    Promise.all([
      loadJobsWorkspace(apiVersion, organization.id).catch(() => null),
      loadTriggerWorkspace(apiVersion, organization.id).catch(() => null),
      loadResults(apiVersion, organization.id).catch(() => null),
      listConversations(apiVersion, organization.id).catch(() => []),
      loadMemory(apiVersion, organization.id).catch(() => null),
    ]).then(([jobs, triggers, results, threads, memory]) => {
      if (!active) return;
      setData({
        jobs: (jobs?.jobs ?? []).filter((job) => job.workerId === workerId),
        workItems: (jobs?.workItems ?? []).filter((item) => item.workerId === workerId),
        configs: (triggers?.configs ?? []).filter((config) => config.workerId === workerId),
        results: (results?.items ?? []).filter((result) => result.workerId === workerId),
        threads: threads.filter((thread) => thread.workerId === workerId),
        memories: ((memory?.memories ?? []) as MemoryRecord[]).filter((record) => record.workerId === workerId),
      });
    });
    return () => { active = false; };
  }, [apiVersion, organization.id, workerId]);

  useEffect(() => {
    if (!menu) return;
    const close = (event: PointerEvent) => { if (!(event.target as Element).closest('[data-ws-menu]')) setMenu(false); };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [menu]);

  const nextRun = useMemo(() => data?.configs.filter((config) => config.schedule.enabled && config.schedule.nextDueAt).sort((a, b) => new Date(a.schedule.nextDueAt ?? 0).getTime() - new Date(b.schedule.nextDueAt ?? 0).getTime())[0], [data]);

  if (!worker) return loading ? <PageSkeleton /> : <div className='ws-page'><EmptyState title='Worker not found' action={<a className='ws-button' {...linkProps({ page: 'workers' })}>Back to workers</a>}>It may have been deleted, or you may not have access to it.</EmptyState></div>;

  const state = live.liveness(worker);
  const run = live.activeRun(worker.id);
  const pendingApprovals = live.approvals.filter((item) => item.agentId === worker.agentIdentityId && item.status === 'pending');
  const jobName = (id: string) => data?.jobs.find((job) => job.id === id)?.name;
  const enabledDays = Object.entries(worker.workingHours.days).filter(([, value]) => value.enabled);
  const stateLine = state === 'working' ? (jobName(run?.jobId ?? '') ? `Working on ${jobName(run?.jobId ?? '')}` : 'Working') : state === 'waiting' ? `Waiting · ${humanize(run?.waitingReason || run?.status)}` : state === 'ready' ? 'Ready for work' : humanize(worker.status);

  return <div className='ws-page'>
    <header className='ws-profile-head'>

      <div className='ws-profile-identity'>
        <Avatar name={worker.name} seed={worker.id} size={56} live={state} />
        <div>
          <h1>{worker.name}</h1>
          <p>{role?.name ?? 'No role'} · {worker.department || 'No department'}</p>
          <p className='ws-profile-state' data-state={state}>{stateLine}</p>
        </div>
        <div className='ws-page-actions'>
          <a className='ws-button ws-button-primary' {...linkProps({ page: 'conversations', workerId: worker.id })}><MessageSquare size={15} />Message</a>
          {canManage && worker.status !== 'archived' && <button type='button' className='ws-button' onClick={onEdit}><Pencil size={14} />Edit</button>}
          {canManage && worker.status === 'active' && <button type='button' className='ws-button' disabled={saving} onClick={() => onStatus('paused')}><Pause size={14} />Pause</button>}
          {canManage && worker.status !== 'active' && worker.status !== 'archived' && <button type='button' className='ws-button' disabled={saving} onClick={() => onStatus('active')}><Play size={14} />Activate</button>}
          {canManage && <div className='ws-menu-anchor' data-ws-menu><button type='button' className='ws-icon-button' aria-label='More actions' aria-expanded={menu} onClick={() => setMenu(!menu)}><MoreHorizontal size={17} /></button>
            {menu && <div className='ws-menu ws-menu-right' role='menu'>
              {worker.status !== 'suspended' && worker.status !== 'archived' && <button type='button' role='menuitem' disabled={saving} onClick={() => { setMenu(false); onStatus('suspended'); }}>Suspend worker</button>}
              {worker.status !== 'archived' && <button type='button' role='menuitem' disabled={saving} onClick={() => { setMenu(false); onStatus('archived'); }}>Archive</button>}
              <hr /><button type='button' role='menuitem' className='ws-danger-text' disabled={saving} onClick={() => { setMenu(false); onDelete(); }}>Delete worker</button>
            </div>}
          </div>}
        </div>
      </div>
      <nav className='ws-tabs' aria-label={`${worker.name} sections`}>{TABS.map((entry) => <a key={entry.id} aria-current={tab === entry.id ? 'page' : undefined} {...linkProps({ page: 'worker', workerId: worker.id, tab: entry.id })}>{entry.label}{entry.id === 'conversations' && data ? <span className='ws-tab-count'>{data.threads.length}</span> : entry.id === 'jobs' && data ? <span className='ws-tab-count'>{data.jobs.length}</span> : entry.id === 'results' && data ? <span className='ws-tab-count'>{data.results.length}</span> : null}</a>)}</nav>
    </header>

    {tab === 'overview' && <div className='ws-cols'>
      <div style={{ display: 'grid', gap: 36, alignContent: 'start' }}>
        {pendingApprovals.length > 0 && <a className='ws-callout' {...linkProps({ page: 'inbox', filter: 'approvals' })}><strong>{pendingApprovals.length} {pendingApprovals.length === 1 ? 'action is' : 'actions are'} waiting for your approval</strong><span>Review in Inbox →</span></a>}
        <Section title='Right now'>
          {run ? <ul className='ws-rows'><li className='ws-row'>
            <Avatar name={worker.name} seed={worker.id} size={32} live={state} />
            <a className='ws-row-main ws-row-link' {...linkProps({ page: 'runs' })}><strong>{jobName(run.jobId) ?? run.planSummary ?? 'Run in progress'}</strong><small>Step {Math.min(run.currentStep + 1, Math.max(run.stepIds.length, 1))} of {Math.max(run.stepIds.length, 1)} · {humanize(run.status)}</small>
              <span className='ws-steps'>{Array.from({ length: Math.max(run.stepIds.length, 1) }, (_, index) => <i key={index} data-state={index < run.currentStep ? 'done' : index === run.currentStep ? (state === 'waiting' ? 'waiting' : 'active') : undefined} />)}</span></a>
            <div className='ws-row-meta'><Ago value={run.updatedAt} /></div>
          </li></ul> : <p className='ws-quiet'>{worker.status === 'active' ? 'Not working on anything right now. Send a message or run a job to give it work.' : `This worker is ${humanize(worker.status)}.`}</p>}
        </Section>
        <Section title='Recent results' action={<a className='ws-link' {...linkProps({ page: 'worker', workerId: worker.id, tab: 'results' })}>All</a>}>
          {!data ? <SkeletonLines rows={2} /> : data.results.length ? <ul className='ws-rows'>{data.results.slice(0, 3).map((result) => resultRow(result))}</ul> : <p className='ws-quiet'>No results yet.</p>}
        </Section>
        <Section title='Responsibilities'>
          {worker.responsibilities.length ? <ul className='ws-bullets'>{worker.responsibilities.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p className='ws-quiet'>No worker-specific responsibilities.</p>}
        </Section>
      </div>
      <aside className='ws-aside'>
        <dl className='ws-facts ws-facts-stacked'>
          <div><dt>Next scheduled work</dt><dd>{!data ? '…' : nextRun ? <><CalendarClock size={14} /> <Ago value={nextRun.schedule.nextDueAt} /><small>{nextRun.schedule.cadence}</small></> : 'No schedule'}</dd></div>
          <div><dt>Supervisor</dt><dd>{worker.supervisorUserId === user.userId ? (user.name || user.email || 'You') : worker.supervisorUserId}</dd></div>
          <div><dt>Working hours</dt><dd>{enabledDays.length ? enabledDays.map(([day, value]) => <span key={day} className='ws-hours'>{day.slice(0, 3)} {value.start}–{value.end}</span>) : 'None'}<small>{worker.workingHours.timezone}</small></dd></div>
          <div><dt>Joined</dt><dd><Ago value={worker.createdAt} /></dd></div>
        </dl>
      </aside>
    </div>}

    {tab === 'conversations' && <Section title='Conversations' action={<a className='ws-button ws-button-sm' {...linkProps({ page: 'conversations', workerId: worker.id })}>New message</a>}>
      {!data ? <SkeletonLines rows={3} /> : data.threads.length ? <ul className='ws-rows'>{data.threads.map((thread) => <li key={thread.id} className='ws-row' style={{ gridTemplateColumns: 'auto minmax(0,1fr) auto' }}>
        <span className='ws-doc-icon' aria-hidden='true'><MessageSquare size={15} /></span>
        <a className='ws-row-main ws-row-link' {...linkProps({ page: 'conversations', workerId: worker.id, threadId: thread.id })}><strong>{thread.title}</strong><small>{humanize(thread.status)}</small></a>
        <div className='ws-row-meta'><Ago value={thread.updatedAt} /></div>
      </li>)}</ul> : <EmptyState title='No conversations yet' action={<a className='ws-button ws-button-primary' {...linkProps({ page: 'conversations', workerId: worker.id })}>Start a conversation</a>}>Ask {worker.name} to do something, or to explain what it’s working on.</EmptyState>}
    </Section>}

    {tab === 'jobs' && <Section title='Jobs' action={<a className='ws-link' {...linkProps({ page: 'jobs' })}>Manage jobs</a>}>
      {!data ? <SkeletonLines rows={3} /> : data.jobs.length ? <ul className='ws-rows'>{data.jobs.map((job) => {
        const config = data.configs.find((item) => item.jobId === job.id);
        return <li key={job.id} className='ws-row' style={{ gridTemplateColumns: 'minmax(0,1fr) auto' }}>
          <a className='ws-row-main ws-row-link' {...linkProps({ page: 'jobs' })}><strong>{job.name}</strong><small>{job.objective || job.description}</small></a>
          <div className='ws-row-meta'><span className='ws-hide-sm'>{config?.schedule.enabled ? config.schedule.cadence : 'On demand'}</span><State value={job.status} /></div>
        </li>;
      })}</ul> : <EmptyState title='No jobs yet'>Jobs describe recurring or on-demand work this worker owns.</EmptyState>}
    </Section>}

    {tab === 'results' && <Section title='Results'>
      {!data ? <SkeletonLines rows={3} /> : data.results.length ? <ul className='ws-rows'>{data.results.map((result) => resultRow(result))}</ul> : <EmptyState icon={<FileText size={18} />} title='No results yet'>Finished work from {worker.name} appears here.</EmptyState>}
    </Section>}

    {tab === 'memory' && <Section title='Memory' description='What this worker remembers. Memory is context, never permission.' action={<a className='ws-link' {...linkProps({ page: 'memory' })}>Manage memory</a>}>
      {!data ? <SkeletonLines rows={3} /> : data.memories.length ? <ul className='ws-rows'>{data.memories.map((record) => <li key={record.id} className='ws-row' style={{ gridTemplateColumns: 'minmax(0,1fr) auto' }}>
        <span className='ws-row-main'><strong>{record.title}</strong><small>{record.content}</small></span>
        <div className='ws-row-meta'><span className='ws-tag'>{record.source}</span><Ago value={record.updatedAt} /></div>
      </li>)}</ul> : <EmptyState title='Nothing remembered yet'>Run memory is created automatically. Long-term memory is added by people.</EmptyState>}
    </Section>}

    {tab === 'settings' && <div style={{ display: 'grid', gap: 36, maxWidth: 760 }}>
      <Section title='Profile' action={canManage && worker.status !== 'archived' ? <button type='button' className='ws-button ws-button-sm' onClick={onEdit}><Pencil size={13} />Edit</button> : undefined}>
        <dl className='ws-facts ws-facts-stacked'>
          <div><dt>Description</dt><dd>{worker.description || '—'}</dd></div>
          <div><dt>Instructions</dt><dd className='ws-pre'>{worker.instructions || 'No worker-specific instructions.'}</dd></div>
          <div><dt>Department</dt><dd>{worker.department || '—'}</dd></div>
          <div><dt>Timezone</dt><dd>{worker.workingHours.timezone}</dd></div>
        </dl>
      </Section>
      <Section title='Security identity' description='The identity this worker acts with. Capabilities, policy and approvals stay separate decisions.'>
        <dl className='ws-facts ws-facts-stacked'>
          <div><dt>Identity</dt><dd>{identity?.name ?? <code>{worker.agentIdentityId}</code>}{worker.agentIdentityProvisioning === 'automatic' && <small>Managed automatically</small>}</dd></div>
          <div><dt>State</dt><dd>{identity ? <><State value={identity.status} /> <small>Credential {identity.credentialStatus}</small></> : 'Unavailable'}</dd></div>
          <div><dt>Worker ID</dt><dd><code>{worker.id}</code></dd></div>
        </dl>
      </Section>
      {canManage && <Section title='Lifecycle'>
        <div className='ws-danger-zone'>
          <div><strong>Suspend or archive</strong><p>Suspending stops work immediately. Archived workers can’t be restored.</p></div>
          <div className='ws-page-actions'>
            {worker.status !== 'suspended' && worker.status !== 'archived' && <button type='button' className='ws-button' disabled={saving} onClick={() => onStatus('suspended')}>Suspend</button>}
            {worker.status !== 'archived' && <button type='button' className='ws-button ws-button-danger' disabled={saving} onClick={() => onStatus('archived')}>Archive</button>}
          </div>
          <div><strong>Delete worker</strong><p>Ends active runs, removes its jobs and work items, and revokes its managed credential. Run and audit history is kept.</p></div>
          <div className='ws-page-actions'><button type='button' className='ws-button ws-button-danger' disabled={saving} onClick={onDelete}>Delete</button></div>
        </div>
      </Section>}
    </div>}
  </div>;
}

function resultRow(result: ResultSummary) {
  return <li key={result.id} className='ws-row' style={{ gridTemplateColumns: 'auto minmax(0,1fr) auto' }}>
    <span className='ws-doc-icon' aria-hidden='true'><FileText size={15} /></span>
    <a className='ws-row-main ws-row-link' {...linkProps({ page: 'results', resultId: result.id })}><strong>{result.title}</strong><small>{result.jobName} · {result.summary}</small></a>
    <div className='ws-row-meta'><State value={result.status} /><span className='ws-hide-sm'><Ago value={result.completedAt} /></span></div>
  </li>;
}
