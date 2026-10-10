import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { listApprovals, type ApprovalRecord } from '../lib/approvalApi';
import type { OrganizationAccess } from '../lib/identityApi';
import { loadRuntime, type RuntimeRun } from '../lib/runtimeApi';
import { listSupervision, type Escalation } from '../lib/supervisionApi';
import type { ApiVersion } from '../lib/systemApi';
import { loadWorkforce, type ManagedWorker, type WorkforceRole } from '../lib/workforceApi';
import { subscribeOrganizationRealtime } from '../platform/realtimeClient';
import type { Liveness } from './ui';

const WORKING = new Set(['planning', 'running']);
const WAITING = new Set(['waiting_approval', 'waiting_ai', 'waiting_configuration', 'waiting_reconnect', 'uncertain_outcome']);

export type WorkspaceLive = {
  loaded: boolean;
  workers: ManagedWorker[];
  roles: WorkforceRole[];
  runs: RuntimeRun[];
  approvals: ApprovalRecord[];
  escalations: Escalation[];
  canReview: boolean;
  /** Pending approvals plus open or acknowledged escalations. */
  inboxCount: number;
  liveness: (worker: ManagedWorker) => Liveness;
  activeRun: (workerId: string) => RuntimeRun | undefined;
  refresh: () => void;
  replaceApproval: (approval: ApprovalRecord) => void;
  replaceEscalation: (escalation: Escalation) => void;
};

const Context = createContext<WorkspaceLive | null>(null);

export function useLive() {
  const value = useContext(Context);
  if (!value) throw new Error('useLive must be used inside LiveProvider.');
  return value;
}

export function LiveProvider({ organization, apiVersion, children }: { organization: OrganizationAccess; apiVersion: ApiVersion; children: ReactNode }) {
  const canReview = organization.permissions.includes('approvals.review');
  const [workers, setWorkers] = useState<ManagedWorker[]>([]);
  const [roles, setRoles] = useState<WorkforceRole[]>([]);
  const [runs, setRuns] = useState<RuntimeRun[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [escalations, setEscalations] = useState<Escalation[]>([]);
  const [loaded, setLoaded] = useState(false);
  const pending = useRef<number | null>(null);

  const load = useCallback(async () => {
    const [workforce, runtime, approvalRows, supervision] = await Promise.all([
      loadWorkforce(apiVersion, organization.id).catch(() => null),
      loadRuntime(apiVersion, organization.id).catch(() => null),
      canReview ? listApprovals(apiVersion, organization.id).catch(() => null) : Promise.resolve([] as ApprovalRecord[]),
      listSupervision(apiVersion, organization.id).catch(() => null),
    ]);
    if (workforce) { setWorkers(workforce.workers); setRoles(workforce.roles); }
    if (runtime) setRuns(runtime.runs);
    if (approvalRows) setApprovals(approvalRows);
    if (supervision) setEscalations(supervision.escalations);
    setLoaded(true);
  }, [apiVersion, organization.id, canReview]);

  // Realtime bursts (several step events per second) collapse into one refresh.
  const refresh = useCallback(() => {
    if (pending.current !== null) return;
    pending.current = window.setTimeout(() => { pending.current = null; void load(); }, 400);
  }, [load]);

  useEffect(() => { setLoaded(false); void load(); }, [load]);
  useEffect(() => subscribeOrganizationRealtime({
    organizationId: organization.id,
    eventTypes: ['run.created', 'run.started', 'run.progress', 'run.step.started', 'run.step.completed', 'run.waiting_approval', 'run.failed', 'run.completed', 'approval.created', 'approval.decided', 'worker.status.changed', 'incident.created', 'result.created'],
    onEvent: refresh,
    poll: load,
  }), [organization.id, refresh, load]);
  useEffect(() => () => { if (pending.current !== null) window.clearTimeout(pending.current); }, []);

  const value = useMemo<WorkspaceLive>(() => {
    const activeRun = (workerId: string) => runs
      .filter((run) => run.workerId === workerId && (WORKING.has(run.status) || WAITING.has(run.status)))
      .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())[0];
    return {
      loaded, workers, roles, runs, approvals, escalations, canReview,
      inboxCount: approvals.filter((item) => item.status === 'pending').length + escalations.filter((item) => item.status !== 'resolved').length,
      activeRun,
      liveness: (worker) => {
        if (worker.status === 'paused' || worker.status === 'suspended') return 'paused';
        if (worker.status !== 'active') return 'off';
        const run = activeRun(worker.id);
        if (run && WORKING.has(run.status)) return 'working';
        if (run) return 'waiting';
        return 'ready';
      },
      refresh,
      replaceApproval: (approval) => setApprovals((current) => current.map((item) => item.id === approval.id ? approval : item)),
      replaceEscalation: (escalation) => setEscalations((current) => current.map((item) => item.id === escalation.id ? escalation : item)),
    };
  }, [loaded, workers, roles, runs, approvals, escalations, canReview, refresh]);

  return <Context.Provider value={value}>{children}</Context.Provider>;
}
