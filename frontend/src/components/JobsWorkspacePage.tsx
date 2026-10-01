import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Activity, Archive, ArrowRight, BriefcaseBusiness, CheckCircle2, ChevronRight, Clock3, Filter, ListTodo, PauseCircle, PlayCircle, Plus, Search, ShieldCheck, Trash2, X } from 'lucide-react';
import type { AgentCapabilityProfile } from '../lib/capabilityApi';
import type { JobsWorkspace, Job, WorkItem, JobStatus } from '../lib/jobsApi';
import type { ManagedWorker } from '../lib/workforceApi';
import { filterJobs } from './workspaceListModel';

export type JobsScene = 'jobs' | 'queue' | 'triggers' | 'runtime';

function displayDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

const sections: Array<{ id: JobsScene; label: string; description: string; icon: typeof BriefcaseBusiness }> = [
  { id: 'jobs', label: 'Definitions', description: 'What workers own', icon: BriefcaseBusiness },
  { id: 'queue', label: 'Work queue', description: 'What is moving', icon: ListTodo },
  { id: 'triggers', label: 'Automation', description: 'When work starts', icon: Clock3 },
  { id: 'runtime', label: 'Run history', description: 'How work executed', icon: Activity },
];

export default function JobsWorkspacePage({ data, workers, selected, profile, missing, ready, loading, saving, canManage, canRun, scene, onSceneChange, onSelect, onCreate, onEdit, onStatus, onRun, onDelete, onCancel, onRetry, onProcess, onClearQueue, automation, runs }: {
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
  automation: ReactNode;
  runs: ReactNode;
}) {
  const [search, setSearch] = useState('');
  const [stateFilter, setStateFilter] = useState<'all' | JobStatus>('all');
  const [queueFilter, setQueueFilter] = useState<'all' | 'waiting' | 'running' | 'finished'>('all');
  const inspectorRef = useRef<HTMLElement>(null);
  const workerMap = useMemo(() => new Map(workers.map((worker) => [worker.id, worker])), [workers]);
  const activeWorkers = workers.some((worker) => worker.status !== 'archived');
  const visibleJobs = filterJobs(data.jobs, new Map(workers.map((worker) => [worker.id, worker.name])), search, stateFilter);
  const groupedJobs = workers.map((worker) => ({ worker, jobs: visibleJobs.filter((job) => job.workerId === worker.id) })).filter((group) => group.jobs.length);
  const unassignedJobs = visibleJobs.filter((job) => !workerMap.has(job.workerId));
  const selectedWorker = selected ? workerMap.get(selected.workerId) : null;
  const history = selected ? data.workItems.filter((item) => item.jobId === selected.id).slice(0, 5) : [];
  const dispatchBlocked = !['ok', 'queued'].includes(data.dispatch.state);
  const queued = data.workItems.filter((item) => item.status === 'queued' || item.status.startsWith('waiting_'));
  const inMotion = data.workItems.filter((item) => item.status === 'running' || item.status === 'claimed');
  const finished = data.workItems.filter((item) => ['completed', 'failed', 'cancelled'].includes(item.status));
  const queueRows = queueFilter === 'waiting' ? queued : queueFilter === 'running' ? inMotion : queueFilter === 'finished' ? finished : data.workItems;

  useEffect(() => {
    if (!selected || scene !== 'jobs') return;
    const frame = requestAnimationFrame(() => inspectorRef.current?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' }));
    return () => cancelAnimationFrame(frame);
  }, [selected?.id, scene]);

  return <div className='jobs-command-page'>
    <header className='jobs-command-heading'>
      <div><p className='eyebrow'>WORKFORCE / JOBS</p><h1>Jobs</h1><p>Define the work. Watch it enter the queue. Inspect every result of execution.</p></div>
      {canManage && <button type='button' className='jobs-create-button' disabled={!activeWorkers} onClick={onCreate}><Plus size={16} /> New job</button>}
    </header>

    <div className='jobs-flowline' aria-label='Job and queue summary'>
      <div><span>01 / DEFINED</span><strong>{data.jobSummary.total}</strong><small>Job definitions</small></div>
      <i aria-hidden='true' />
      <div><span>02 / ACTIVE</span><strong>{data.jobSummary.active}</strong><small>Ready to receive work</small></div>
      <i aria-hidden='true' />
      <div className={queued.length ? 'has-motion' : ''}><span>03 / WAITING</span><strong>{queued.length}</strong><small>Work items in queue</small></div>
      <i aria-hidden='true' />
      <div><span>04 / FINISHED</span><strong>{finished.length}</strong><small>In the current window</small></div>
    </div>

    <div className='jobs-command-layout'>
      <nav className='jobs-command-nav' aria-label='Jobs workspace views'>{sections.map((section) => { const Icon = section.icon; return <button key={section.id} type='button' className={scene === section.id ? 'is-current' : ''} aria-current={scene === section.id ? 'page' : undefined} onClick={() => onSceneChange(section.id)}><Icon size={16} /><span><strong>{section.label}</strong><small>{section.description}</small></span>{section.id === 'queue' && queued.length > 0 && <em>{queued.length}</em>}</button>; })}<div className='jobs-command-nav-note'><ShieldCheck size={15} /><span>Every action passes through capability, policy, approval, and audit controls.</span></div></nav>

      <main className='jobs-command-main' key={scene}>
        {scene === 'jobs' && <>
          <div className='jobs-scene-heading'><div><span className='jobs-scene-index'>01 / DEFINITIONS</span><h2>Work assigned to your team</h2><p>Jobs are grouped by their worker, with the next action close to each definition.</p></div><span className='jobs-scene-total'>{visibleJobs.length} shown</span></div>
          <div className='jobs-registry-tools'><label><Search size={16} /><span className='sr-only'>Search jobs</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder='Search jobs or workers' /></label><label><Filter size={15} /><span className='sr-only'>Filter job status</span><select value={stateFilter} onChange={(event) => setStateFilter(event.target.value as 'all' | JobStatus)}><option value='all'>All states</option><option value='active'>Active</option><option value='draft'>Draft</option><option value='paused'>Paused</option><option value='archived'>Archived</option></select></label></div>
          {loading ? <div className='jobs-command-empty'>Loading jobs…</div> : visibleJobs.length === 0 ? <div className='jobs-command-empty'><BriefcaseBusiness size={27} /><strong>{data.jobs.length ? 'Nothing matches these filters' : 'Define your first job'}</strong><p>{data.jobs.length ? 'Try another search or state.' : 'Give a worker a clear objective and Audoryn will prepare its execution path.'}</p>{!data.jobs.length && canManage && <button type='button' disabled={!activeWorkers} onClick={onCreate}>Create a job <ArrowRight size={15} /></button>}</div> : <div className='jobs-registry'>
            {groupedJobs.map(({ worker, jobs }) => <section key={worker.id} className='jobs-worker-group'><div className='jobs-worker-group-heading'><span className='jobs-worker-initial'>{worker.name.slice(0, 1).toUpperCase()}</span><div><strong>{worker.name}</strong><small>{worker.department || 'Worker'} · {jobs.length} job{jobs.length === 1 ? '' : 's'}</small></div><ChevronRight size={16} /></div>{jobs.map((job) => <JobLine key={job.id} job={job} selected={selected?.id === job.id} onSelect={() => onSelect(job)} />)}</section>)}
            {unassignedJobs.length > 0 && <section className='jobs-worker-group'><div className='jobs-worker-group-heading'><span className='jobs-worker-initial'>?</span><div><strong>Unavailable worker</strong><small>{unassignedJobs.length} jobs</small></div></div>{unassignedJobs.map((job) => <JobLine key={job.id} job={job} selected={selected?.id === job.id} onSelect={() => onSelect(job)} />)}</section>}
          </div>}
          {selected && <section ref={inspectorRef} className='jobs-inspector' key={selected.id} aria-label={`${selected.name} details`}><div className='jobs-inspector-top'><span>JOB DOSSIER / REVISION {selected.revision}</span><button type='button' onClick={() => onSelect(null)} aria-label='Close job details'><X size={17} /></button></div><div className='jobs-inspector-heading'><div><span className={`jobs-state ${selected.status}`}>{selected.status}</span><h2>{selected.name}</h2><p>{selectedWorker?.name ?? 'Unavailable worker'} · {selected.priority} priority</p></div><div className='jobs-inspector-actions'>{canRun && selected.status === 'active' && <button type='button' className='jobs-run-button' disabled={saving} onClick={() => onRun(selected)}><PlayCircle size={15} /> Run now</button>}{canManage && selected.status !== 'archived' && <button type='button' onClick={() => onEdit(selected)}>Edit definition</button>}{canManage && (selected.status === 'draft' || selected.status === 'paused') && <button type='button' disabled={saving} onClick={() => onStatus(selected, 'active')}><PlayCircle size={15} /> {selected.status === 'paused' ? 'Resume' : 'Activate'}</button>}{canManage && selected.status === 'active' && <button type='button' disabled={saving} onClick={() => onStatus(selected, 'paused')}><PauseCircle size={15} /> Pause</button>}</div></div><div className={`jobs-inspector-readiness ${ready ? 'is-ready' : ''}`}><CheckCircle2 size={17} /><div><strong>{ready ? 'Ready to run' : 'Needs setup'}</strong><span>{selectedWorker?.status !== 'active' ? 'Worker must be active.' : profile?.agent.status !== 'active' ? 'Agent identity must be active.' : missing.length ? `Missing active capability: ${missing[0]}` : 'Requirements are checked again before each run.'}</span></div></div><div className='jobs-inspector-body'><div><div className='jobs-inspector-block'><span>OBJECTIVE</span><p>{selected.objective}</p></div><div className='jobs-inspector-block'><span>INSTRUCTIONS</span><p>{selected.instructions || 'No job-specific instructions.'}</p></div><div className='jobs-inspector-block'><span>COMPLETION CRITERIA</span>{selected.completionCriteria.length ? <ul>{selected.completionCriteria.map((item) => <li key={item}>{item}</li>)}</ul> : <p>None specified.</p>}</div></div><div><div className='jobs-inspector-block'><span>REQUIRED CAPABILITIES</span>{selected.requiredCapabilities.length ? <ul>{selected.requiredCapabilities.map((item) => <li key={item}>{item}</li>)}</ul> : <p>No external capability required.</p>}</div><div className='jobs-inspector-block'><span>RESPONSIBILITY LINKS</span>{selected.responsibilityLinks.length ? <ul>{selected.responsibilityLinks.map((item) => <li key={item}>{item}</li>)}</ul> : <p>None linked.</p>}</div><div className='jobs-inspector-block'><span>RECENT WORK</span>{history.length ? history.map((item) => <p key={item.id}><span className={`jobs-state ${item.status}`}>{item.status.replaceAll('_', ' ')}</span> {displayDate(item.createdAt)}</p>) : <p>No work items yet.</p>}</div></div></div><details className='jobs-inspector-danger'><summary>Administrative actions</summary><div>{canManage && selected.status !== 'archived' && <button type='button' disabled={saving} onClick={() => onStatus(selected, 'archived')}><Archive size={14} /> Archive job</button>}{canManage && <button type='button' disabled={saving} onClick={() => onDelete(selected)}><Trash2 size={14} /> Delete job</button>}</div></details></section>}
        </>}
        {scene === 'queue' && <><div className='jobs-scene-heading'><div><span className='jobs-scene-index'>02 / WORK QUEUE</span><h2>Work moving through Audoryn</h2><p>Live items are grouped by their current state. Manual processing remains an operator control.</p></div>{canRun && data.workItems.length > 0 && <details className='jobs-queue-management'><summary>Queue actions</summary><button type='button' disabled={saving} onClick={onClearQueue}><Trash2 size={14} /> Clear entire queue</button></details>}</div>{dispatchBlocked && <div className='jobs-dispatch-alert'><ShieldCheck size={16} /><span>Runtime dispatch delayed. {data.dispatch.detail || 'Work remains safely queued.'}</span></div>}<div className='jobs-queue-switch' aria-label='Filter work queue'>{([['all', 'All', data.workItems.length], ['waiting', 'Waiting', queued.length], ['running', 'In motion', inMotion.length], ['finished', 'Finished', finished.length]] as const).map(([id, label, count]) => <button key={id} type='button' className={queueFilter === id ? 'is-active' : ''} onClick={() => setQueueFilter(id)}>{label}<span>{count}</span></button>)}</div>{loading ? <div className='jobs-command-empty'>Loading work queue…</div> : queueRows.length ? <div className='jobs-queue-timeline'>{queueRows.map((item, index) => { const job = data.jobs.find((entry) => entry.id === item.jobId); const worker = workerMap.get(item.workerId); return <article className='jobs-queue-item' key={item.id} style={{ animationDelay: (Math.min(index, 8) * 28) + 'ms' }}><span className={`jobs-queue-node ${item.status}`} /><div className='jobs-queue-item-head'><div><span className={`jobs-state ${item.status}`}>{item.status.replaceAll('_', ' ')}</span><h3>{job?.name ?? item.snapshot.jobName}</h3><p>{worker?.name ?? 'Unknown worker'} · {item.priority} priority · revision {item.jobRevision}</p></div><time>{displayDate(item.createdAt)}</time></div><div className='jobs-queue-item-foot'><span>{item.runId ? `Run ${item.runId.slice(0, 8)}` : item.status === 'queued' ? dispatchBlocked ? 'Dispatch delayed' : 'Waiting for runtime' : `Correlation ${item.correlationId.slice(0, 8)}`}</span><div>{canRun && item.status === 'queued' && <button type='button' disabled={saving} onClick={() => onCancel(item)}>Cancel</button>}{canRun && item.status === 'failed' && item.retryCount < 2 && <button type='button' disabled={saving} onClick={() => onRetry(item)}>Retry</button>}{canRun && item.status === 'queued' && new Date(item.scheduledAt).getTime() <= Date.now() && <details><summary>Advanced</summary><button type='button' disabled={saving} onClick={() => onProcess(item)}>Process now</button></details>}</div></div></article>; })}</div> : <div className='jobs-command-empty'><ListTodo size={26} /><strong>Nothing in this view</strong><p>Scheduled and event-driven jobs add work here automatically.</p></div>}</>}
        {scene === 'triggers' && <div className='jobs-advanced-scene'><div className='jobs-scene-heading'><div><span className='jobs-scene-index'>03 / AUTOMATION</span><h2>When work begins</h2><p>Inspect schedules, events, and dependency triggers for your jobs.</p></div></div>{automation}</div>}
        {scene === 'runtime' && <div className='jobs-advanced-scene'><div className='jobs-scene-heading'><div><span className='jobs-scene-index'>04 / RUN HISTORY</span><h2>Execution record</h2><p>Review plans, steps, and outcomes after work enters runtime.</p></div></div>{runs}</div>}
      </main>
    </div>
    {(data.window.jobsTruncated || data.window.workItemsTruncated) && <p className='audit-window-note'>This view is limited to the current bounded read window.</p>}
  </div>;
}

function JobLine({ job, selected, onSelect }: { job: Job; selected: boolean; onSelect: () => void }) {
  return <button type='button' className={`jobs-definition-line${selected ? ' is-selected' : ''}`} onClick={onSelect}><span className={`jobs-priority-mark ${job.priority}`} /><span className='jobs-definition-copy'><strong>{job.name}</strong><small>{job.description || job.objective}</small></span><span className={`jobs-state ${job.status}`}>{job.status}</span><span className='jobs-definition-revision'>R{job.revision}</span><ArrowRight size={16} /></button>;
}
