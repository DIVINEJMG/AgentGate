import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, Bot, BriefcaseBusiness, MessageCircle, Plus, Search, ShieldCheck, SlidersHorizontal, Users, X } from 'lucide-react';
import type { AuthUser } from '../platform/client';
import type { OrganizationAccess } from '../lib/identityApi';
import type { ApiVersion } from '../lib/systemApi';
import type { ManagedWorker, WorkforceRole, WorkforceSnapshot, WorkerStatus } from '../lib/workforceApi';
import type { AppView } from '../navigation';
import WorkerExperienceProfile from './WorkerExperienceProfile';
import WorkforceTemplates from './WorkforceTemplates';

type StatusFilter = 'all' | WorkerStatus;

export default function WorkerDirectoryPage({ data, selected, loading, saving, canManage, organization, user, apiVersion, onApiVersionChange, onSelect, onCreate, onCreateRole, onEditRole, onEditWorker, onStatus, onDelete, onChat, onNavigate, onRefresh }: {
  data: WorkforceSnapshot;
  selected: ManagedWorker | null;
  loading: boolean;
  saving: boolean;
  canManage: boolean;
  organization: OrganizationAccess;
  user: AuthUser;
  apiVersion: ApiVersion;
  onApiVersionChange: (version: ApiVersion) => void;
  onSelect: (worker: ManagedWorker | null) => void;
  onCreate: () => void;
  onCreateRole: () => void;
  onEditRole: (role: WorkforceRole) => void;
  onEditWorker: (worker: ManagedWorker) => void;
  onStatus: (worker: ManagedWorker, status: WorkerStatus) => void;
  onDelete: (worker: ManagedWorker) => void;
  onChat: (worker: ManagedWorker) => void;
  onNavigate: (view: AppView) => void;
  onRefresh: () => void;
}) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<StatusFilter>('all');
  const [showSetup, setShowSetup] = useState(false);
  const detailRef = useRef<HTMLElement>(null);
  const roles = useMemo(() => new Map(data.roles.map((role) => [role.id, role])), [data.roles]);
  const visible = useMemo(() => data.workers.filter((worker) => {
    if (filter !== 'all' && worker.status !== filter) return false;
    const haystack = `${worker.name} ${worker.department} ${roles.get(worker.roleId)?.name ?? ''}`.toLowerCase();
    return haystack.includes(query.trim().toLowerCase());
  }), [data.workers, filter, query, roles]);

  useEffect(() => {
    if (!selected) return;
    const frame = requestAnimationFrame(() => detailRef.current?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' }));
    return () => cancelAnimationFrame(frame);
  }, [selected?.id]);

  return <div className='worker-directory-page'>
    <header className='worker-directory-header'>
      <div><p className='eyebrow'>WORKFORCE / WORKERS</p><h1>Workers</h1><p>Build your team, see its state, and open a dedicated conversation when work needs direction.</p></div>
      <div className='worker-directory-header-actions'>
        <button type='button' className='worker-text-action' onClick={() => setShowSetup((value) => !value)} aria-expanded={showSetup}><SlidersHorizontal size={15} /> Setup tools</button>
        {canManage && <button type='button' className='primary-button' onClick={onCreate}><Plus size={16} /> Create worker</button>}
      </div>
    </header>

    <div className='worker-directory-overview' aria-label='Workforce summary'>
      <div><span>Workers</span><strong>{data.summary.workers}</strong></div>
      <div><span>Active</span><strong>{data.summary.active}</strong></div>
      <div><span>Paused</span><strong>{data.summary.paused}</strong></div>
      <div><span>Departments</span><strong>{data.summary.departments}</strong></div>
    </div>

    <section className='worker-directory-list' aria-label='Managed workers'>
      <div className='worker-directory-toolbar'>
        <div><span className='panel-kicker'>DIRECTORY</span><h2>Managed workers <small>{visible.length}</small></h2></div>
        <div className='worker-directory-controls'>
          <label className='worker-search'><Search size={16} aria-hidden='true' /><span className='sr-only'>Search workers</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder='Search name, role, department' /></label>
          <label className='worker-status-filter'><span className='sr-only'>Filter worker status</span><select value={filter} onChange={(event) => setFilter(event.target.value as StatusFilter)}><option value='all'>All states</option><option value='active'>Active</option><option value='paused'>Paused</option><option value='draft'>Draft</option><option value='suspended'>Suspended</option><option value='archived'>Archived</option></select></label>
        </div>
      </div>
      {loading ? <div className='worker-directory-loading'>Loading workers…</div> : visible.length === 0 ? <div className='worker-directory-empty'><Users size={23} /><strong>{data.workers.length ? 'No workers match this search' : 'Your team starts here'}</strong><p>{data.workers.length ? 'Try another name or state.' : 'Create a worker by describing the outcome it should own.'}</p>{data.workers.length ? <button type='button' onClick={() => { setQuery(''); setFilter('all'); }}>Clear filters</button> : canManage && <button type='button' className='primary-button' onClick={onCreate}>Create first worker</button>}</div> : <div className='worker-table-wrap'><table className='worker-table'><thead><tr><th scope='col'>Worker</th><th scope='col'>Role</th><th scope='col'>Department</th><th scope='col'>State</th><th scope='col'><span className='sr-only'>Actions</span></th></tr></thead><tbody>{visible.map((worker) => <tr key={worker.id} className={selected?.id === worker.id ? 'is-selected' : ''}><td><button type='button' className='worker-name-button' onClick={() => onSelect(worker)}><span className={`worker-directory-avatar ${worker.status}`}><Bot size={18} /></span><span><strong>{worker.name}</strong><small>{worker.description || 'Managed AI worker'}</small></span></button></td><td>{roles.get(worker.roleId)?.name ?? 'Unassigned role'}</td><td>{worker.department || '—'}</td><td><span className={`worker-directory-state ${worker.status}`}><i />{worker.status}</span></td><td><div className='worker-row-actions'><button type='button' onClick={() => onChat(worker)} aria-label={`Chat with ${worker.name}`}><MessageCircle size={15} /><span>Chat</span></button><button type='button' onClick={() => onSelect(worker)} aria-label={`View ${worker.name}`}><ArrowRight size={15} /><span>View</span></button></div></td></tr>)}</tbody></table></div>}
    </section>

    {selected && <section ref={detailRef} className='worker-detail-sheet' aria-label={`${selected.name} details`} key={selected.id}><div className='worker-detail-sheet-top'><span>WORKER DETAILS</span><button type='button' onClick={() => onSelect(null)} aria-label='Close worker details'><X size={17} /></button></div><WorkerExperienceProfile worker={selected} role={roles.get(selected.roleId)} identity={data.securityIdentities.find((item) => item.id === selected.agentIdentityId)} currentUser={user} organizationId={organization.id} apiVersion={apiVersion} canManage={canManage} saving={saving} onEdit={() => onEditWorker(selected)} onStatus={(next) => onStatus(selected, next)} onDelete={() => onDelete(selected)} onChat={() => onChat(selected)} onNavigate={onNavigate} /></section>}

    {showSetup && <section className='worker-setup-area'><div className='worker-setup-intro'><ShieldCheck size={17} /><div><strong>Set up work with explicit controls</strong><p>Templates and roles help define a worker. Capabilities, policies, and approvals remain separate decisions.</p></div><div className='api-switcher'><span>API</span><div className='segmented'><button type='button' className={apiVersion === 'v1' ? 'active' : ''} onClick={() => onApiVersionChange('v1')}>v1</button><button type='button' className={apiVersion === 'v2' ? 'active' : ''} onClick={() => onApiVersionChange('v2')}>v2</button></div></div></div><WorkforceTemplates organization={organization} user={user} apiVersion={apiVersion} identities={data.securityIdentities.filter((identity) => !identity.boundWorkerId)} onApplied={onRefresh} /><section className='worker-roles'><div className='worker-roles-title'><div><BriefcaseBusiness size={16} /><h2>Role catalog</h2></div>{canManage && <button type='button' onClick={onCreateRole}><Plus size={14} /> Add role</button>}</div>{data.roles.length ? <div className='worker-roles-list'>{data.roles.map((role) => <div key={role.id}><div><strong>{role.name}</strong><p>{role.purpose}</p></div><span>{role.defaultResponsibilities.length} responsibilities</span>{canManage && <button type='button' onClick={() => onEditRole(role)}>Edit</button>}</div>)}</div> : <p className='worker-role-empty'>No reusable roles yet.</p>}</section></section>}
    {(data.window.workersTruncated || data.window.rolesTruncated) && <p className='audit-window-note'>The directory exceeded its 100-record read window. Counts may be conservative.</p>}
  </div>;
}
