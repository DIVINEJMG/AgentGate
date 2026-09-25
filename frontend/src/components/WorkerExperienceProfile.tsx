import { useEffect, useMemo, useState } from 'react';
import {
  Bot,
  Clock3,
  PauseCircle,
  PlayCircle,
  ShieldCheck,
  Trash2,
  Wrench,
} from 'lucide-react';
import type { AuthUser } from '../platform/client';
import { listApprovals, type ApprovalRecord } from '../lib/approvalApi';
import { loadJobsWorkspace, type WorkItem } from '../lib/jobsApi';
import { loadResults, type ResultSummary } from '../lib/resultsApi';
import { loadTriggerWorkspace, type TriggerConfig } from '../lib/schedulerApi';
import type { ApiVersion } from '../lib/systemApi';
import type {
  ManagedWorker,
  WorkforceRole,
  WorkerStatus,
} from '../lib/workforceApi';
import type { AppView } from '../navigation';
import WorkerConversation from './WorkerConversation';

function dateTime(value: string | null | undefined) {
  if (!value) return 'Not scheduled';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Not scheduled';
  return date.toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function employeeStatus(worker: ManagedWorker, current?: WorkItem) {
  if (worker.status === 'paused') return 'Paused';
  if (worker.status === 'suspended') return 'Suspended';
  if (worker.status === 'archived') return 'Archived';
  if (!current) return worker.status === 'active' ? 'Ready' : 'Draft';
  const state = String(current.status);
  if (state.includes('approval')) return 'Waiting for approval';
  if (state.includes('integration') || state.includes('dependency')) return 'Waiting for setup';
  if (state === 'running' || state === 'claimed') return 'Working';
  return 'Scheduled';
}

export default function WorkerExperienceProfile({
  worker,
  role,
  identity,
  currentUser,
  organizationId,
  apiVersion,
  canManage,
  saving,
  onEdit,
  onStatus,
  onDelete,
  onNavigate,
}: {
  worker: ManagedWorker;
  role?: WorkforceRole;
  identity?: { name: string; status: string; credentialStatus: string };
  currentUser: AuthUser;
  organizationId: string;
  apiVersion: ApiVersion;
  canManage: boolean;
  saving: boolean;
  onEdit: () => void;
  onStatus: (status: WorkerStatus) => void;
  onDelete: () => void;
  onNavigate: (view: AppView) => void;
}) {
  const [workItems, setWorkItems] = useState<WorkItem[]>([]);
  const [configs, setConfigs] = useState<TriggerConfig[]>([]);
  const [results, setResults] = useState<ResultSummary[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([
      loadJobsWorkspace(apiVersion, organizationId).catch(() => null),
      loadTriggerWorkspace(apiVersion, organizationId).catch(() => null),
      loadResults(apiVersion, organizationId).catch(() => null),
      listApprovals(apiVersion, organizationId).catch(() => []),
    ]).then(([jobs, triggers, resultWorkspace, approvalRows]) => {
      if (!active) return;
      setWorkItems(jobs?.workItems ?? []);
      setConfigs(triggers?.configs ?? []);
      setResults(resultWorkspace?.items ?? []);
      setApprovals(approvalRows);
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [apiVersion, organizationId, worker.id, reloadToken]);

  const currentWork = useMemo(() => {
    const rank: Record<string, number> = {
      running: 0,
      claimed: 1,
      waiting_approval: 2,
      waiting_dependency: 3,
      waiting_ai: 3,
      waiting_configuration: 3,
      queued: 4,
    };
    return workItems
      .filter((item) => item.workerId === worker.id && rank[String(item.status)] !== undefined)
      .sort((a, b) => (rank[String(a.status)] ?? 99) - (rank[String(b.status)] ?? 99))[0];
  }, [workItems, worker.id]);

  const nextSchedule = useMemo(() => (
    configs
      .filter((item) => item.workerId === worker.id && item.schedule.enabled && item.schedule.nextDueAt)
      .sort((a, b) => (
        new Date(a.schedule.nextDueAt ?? 0).getTime() - new Date(b.schedule.nextDueAt ?? 0).getTime()
      ))[0]
  ), [configs, worker.id]);

  const recentResult = useMemo(() => (
    results
      .filter((item) => item.workerId === worker.id)
      .sort((a, b) => new Date(b.completedAt).getTime() - new Date(a.completedAt).getTime())[0]
  ), [results, worker.id]);

  const pendingApprovals = approvals.filter(
    (item) => item.agentId === worker.agentIdentityId && item.status === 'pending',
  );
  const enabledHours = Object.entries(worker.workingHours.days).filter(([, value]) => value.enabled);

  return (
    <>
      <div className="employee-profile-head">
        <span className={`worker-avatar large ${worker.status}`}><Bot size={20} /></span>
        <div>
          <p className="panel-kicker">DIGITAL EMPLOYEE</p>
          <h2>{worker.name}</h2>
          <span>{role?.name ?? 'Worker'} · {worker.department || 'Unassigned'}</span>
        </div>
        <span className={`employee-state ${worker.status === 'active' ? 'success' : ''}`}>
          {employeeStatus(worker, currentWork)}
        </span>
      </div>

      <div className="employee-work-summary">
        <article>
          <span>Current work</span>
          <strong>{currentWork?.snapshot.jobName ?? (loading ? 'Loading…' : 'Nothing active')}</strong>
          <small>{currentWork ? String(currentWork.status).replaceAll('_', ' ') : 'Ready for the next task'}</small>
        </article>
        <article>
          <span>Next scheduled work</span>
          <strong>{dateTime(nextSchedule?.schedule.nextDueAt)}</strong>
          <small>{nextSchedule ? nextSchedule.schedule.cadence : 'No active schedule'}</small>
        </article>
        <article>
          <span>Recent result</span>
          <strong>{recentResult?.title ?? 'No result yet'}</strong>
          <small>{recentResult?.summary ?? 'Completed work will appear here.'}</small>
        </article>
      </div>

      <WorkerConversation
        organizationId={organizationId}
        workerId={worker.id}
        workerName={worker.name}
        apiVersion={apiVersion}
        pendingApprovals={pendingApprovals}
        onApprovalsChanged={() => setReloadToken((value) => value + 1)}
        onNavigate={onNavigate}
      />

      <details className="worker-advanced">
        <summary><Wrench size={14} /> Advanced diagnostics & configuration</summary>
        <div className="advanced-diagnostic-links">
          <button type="button" onClick={() => onNavigate('jobs')}>Jobs & schedules</button>
          <button type="button" onClick={() => onNavigate('runtime')}>Runs & steps</button>
          <button type="button" onClick={() => onNavigate('actions')}>Actions</button>
          <button type="button" onClick={() => onNavigate('capabilities')}>Capabilities</button>
          <button type="button" onClick={() => onNavigate('policies')}>Policies</button>
          <button type="button" onClick={() => onNavigate('audit')}>Audit</button>
        </div>

        <div className="worker-profile-actions">
          {canManage && worker.status !== 'archived' && <button className="secondary-button" onClick={onEdit}>Edit profile</button>}
          {canManage && worker.status !== 'active' && worker.status !== 'archived' && (
            <button className="secondary-button" disabled={saving} onClick={() => onStatus('active')}>
              <PlayCircle size={14} /> Activate
            </button>
          )}
          {canManage && worker.status === 'active' && (
            <button className="secondary-button" disabled={saving} onClick={() => onStatus('paused')}>
              <PauseCircle size={14} /> Pause
            </button>
          )}
          {canManage && worker.status !== 'suspended' && worker.status !== 'archived' && (
            <button className="ghost-button" disabled={saving} onClick={() => onStatus('suspended')}>Suspend worker</button>
          )}
          {canManage && worker.status !== 'archived' && (
            <button className="danger-button" disabled={saving} onClick={() => onStatus('archived')}>Archive</button>
          )}
          {canManage && (
            <button className="danger-button solid" disabled={saving} onClick={onDelete}>
              <Trash2 size={14} /> Delete worker
            </button>
          )}
        </div>

        <dl className="worker-details">
          <div>
            <dt>Supervisor</dt>
            <dd>{worker.supervisorUserId === currentUser.userId ? (currentUser.name || currentUser.email || 'You') : worker.supervisorUserId}</dd>
          </div>
          <div>
            <dt>Security identity</dt>
            <dd>{identity?.name ?? worker.agentIdentityId}{worker.agentIdentityProvisioning === 'automatic' ? ' · managed automatically' : ''}</dd>
          </div>
          <div><dt>Agent state</dt><dd>{identity ? `${identity.status} · credential ${identity.credentialStatus}` : 'Unavailable'}</dd></div>
          <div><dt>Timezone</dt><dd>{worker.workingHours.timezone}</dd></div>
        </dl>

        <section className="worker-profile-section">
          <strong>Responsibilities</strong>
          {worker.responsibilities.length
            ? <ul>{worker.responsibilities.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>
            : <p>No worker-specific responsibilities.</p>}
        </section>
        <section className="worker-profile-section"><strong>Instructions</strong><p>{worker.instructions || 'No worker-specific instructions.'}</p></section>
        <section className="worker-profile-section">
          <strong>Working hours</strong>
          <div className="schedule-chips">
            {enabledHours.length
              ? enabledHours.map(([day, value]) => <span key={day}><Clock3 size={12} />{day.slice(0, 3)} {value.start}–{value.end}</span>)
              : <span>No working days enabled</span>}
          </div>
        </section>
        <section className="worker-security-note">
          <ShieldCheck size={15} />
          <p>Conversation changes canonical state through governed services. F29/F30 authority, approvals, policies and browser controls remain mandatory.</p>
        </section>
      </details>
    </>
  );
}
