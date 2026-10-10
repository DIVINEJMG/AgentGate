import { useMemo, useState } from 'react';
import { BriefcaseBusiness, MessageSquare, Plus, Search, Shapes, Users } from 'lucide-react';
import type { AuthUser } from '../../platform/client';
import type { OrganizationAccess } from '../../lib/identityApi';
import type { ApiVersion } from '../../lib/systemApi';
import type { ManagedWorker, WorkforceRole, WorkforceSnapshot, WorkerStatus } from '../../lib/workforceApi';
import WorkforceTemplates from '../../components/WorkforceTemplates';
import { useLive } from '../live';
import { linkProps } from '../routes';
import { Avatar, EmptyState, PageHeader, Pills, Sheet, SkeletonLines, State, humanize } from '../ui';

type Filter = 'all' | WorkerStatus;

export default function WorkersPage({ data, loading, canManage, organization, user, apiVersion, onCreate, onCreateRole, onEditRole, onRefresh }: {
  data: WorkforceSnapshot;
  loading: boolean;
  canManage: boolean;
  organization: OrganizationAccess;
  user: AuthUser;
  apiVersion: ApiVersion;
  onCreate: () => void;
  onCreateRole: () => void;
  onEditRole: (role: WorkforceRole) => void;
  onRefresh: () => void;
}) {
  const live = useLive();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [setupOpen, setSetupOpen] = useState(false);
  const roles = useMemo(() => new Map(data.roles.map((role) => [role.id, role])), [data.roles]);
  const visible = data.workers.filter((worker) => {
    if (filter !== 'all' && worker.status !== filter) return false;
    return `${worker.name} ${worker.department} ${roles.get(worker.roleId)?.name ?? ''}`.toLowerCase().includes(query.trim().toLowerCase());
  });
  const count = (status: WorkerStatus) => data.workers.filter((worker) => worker.status === status).length;

  return <div className='ws-page'>
    <PageHeader title='Workers' description='Your AI team. Each worker has a role, a supervisor and its own conversation.'
      actions={<>
        <button type='button' className='ws-button' onClick={() => setSetupOpen(true)}><Shapes size={15} />Templates & roles</button>
        {canManage && <button type='button' className='ws-button ws-button-primary' onClick={onCreate}><Plus size={15} />New worker</button>}
      </>} />
    <div className='ws-toolbar'>
      <Pills label='Worker state' value={filter} onChange={setFilter} options={[
        { value: 'all', label: 'All', count: data.workers.length },
        { value: 'active', label: 'Active', count: count('active') },
        { value: 'paused', label: 'Paused', count: count('paused') },
        { value: 'draft', label: 'Draft', count: count('draft') },
        { value: 'suspended', label: 'Suspended', count: count('suspended') },
      ]} />
      <label className='ws-search-field'><Search size={15} aria-hidden='true' /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder='Search name, role or department' aria-label='Search workers' /></label>
    </div>
    {loading ? <SkeletonLines rows={4} avatar /> : visible.length === 0 ? <EmptyState icon={<Users size={18} />} title={data.workers.length ? 'No workers match' : 'Your team starts here'}
      action={data.workers.length ? <button type='button' className='ws-button' onClick={() => { setQuery(''); setFilter('all'); }}>Clear filters</button> : canManage ? <button type='button' className='ws-button ws-button-primary' onClick={onCreate}><Plus size={15} />Create your first worker</button> : undefined}>
      {data.workers.length ? 'Try another name or state.' : 'Describe the outcome you want, and Audoryn sets up a worker with the right role and guardrails.'}
    </EmptyState> : <ul className='ws-rows'>
      {visible.map((worker) => row(worker, roles.get(worker.roleId)?.name))}
    </ul>}
    {(data.window.workersTruncated || data.window.rolesTruncated) && <p className='ws-muted'>Showing the first 100 records; counts may be conservative.</p>}

    <Sheet open={setupOpen} onClose={() => setSetupOpen(false)} title='Templates & roles' subtitle='Reusable starting points. They define work; they never grant access.' wide>
      <section className='ws-section'>
        <div className='ws-section-head'><h2><BriefcaseBusiness size={16} />Role catalog</h2>{canManage && <div className='ws-section-action'><button type='button' className='ws-button ws-button-sm' onClick={onCreateRole}><Plus size={14} />Add role</button></div>}</div>
        {data.roles.length ? <ul className='ws-rows'>{data.roles.map((role) => <li key={role.id} className='ws-row' style={{ gridTemplateColumns: 'minmax(0,1fr) auto' }}>
          <span className='ws-row-main'><strong>{role.name}</strong><small>{role.purpose}</small></span>
          <div className='ws-row-meta'><span className='ws-hide-sm'>{role.defaultResponsibilities.length} responsibilities</span>{canManage && <button type='button' className='ws-button ws-button-sm' onClick={() => { setSetupOpen(false); onEditRole(role); }}>Edit</button>}</div>
        </li>)}</ul> : <p className='ws-muted'>No reusable roles yet.</p>}
      </section>
      <section className='ws-section ws-legacy-embed'>
        <div className='ws-section-head'><h2>Templates</h2><p>Apply a blueprint to create draft workers and jobs.</p></div>
        <WorkforceTemplates organization={organization} user={user} apiVersion={apiVersion} identities={data.securityIdentities.filter((identity) => !identity.boundWorkerId)} onApplied={() => { setSetupOpen(false); onRefresh(); }} />
      </section>
    </Sheet>
  </div>;

  function row(worker: ManagedWorker, role?: string) {
    const state = live.liveness(worker);
    const run = live.activeRun(worker.id);
    const activity = state === 'working' ? (run?.planSummary || 'Working on a job') : state === 'waiting' ? `Waiting · ${humanize(run?.waitingReason || run?.status)}` : state === 'ready' ? 'Ready for work' : humanize(worker.status);
    return <li key={worker.id} className='ws-row ws-worker-row'>
      <Avatar name={worker.name} seed={worker.id} size={36} live={state} />
      <a className='ws-row-main ws-row-link' {...linkProps({ page: 'worker', workerId: worker.id, tab: 'overview' })}><strong>{worker.name}</strong><small>{role ?? 'No role'} · {worker.department || 'No department'}</small></a>
      <span className='ws-worker-activity ws-hide-sm' data-state={state}>{activity}</span>
      <div className='ws-row-meta'><span className='ws-hide-sm'><State value={worker.status} /></span><a className='ws-button ws-button-sm' {...linkProps({ page: 'conversations', workerId: worker.id })}><MessageSquare size={14} />Message</a></div>
    </li>;
  }
}
