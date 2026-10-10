import { useMemo, useState, type ReactNode } from 'react';
import IntegrationFoundationDetails from './IntegrationFoundationDetails';
import { Bot, Check, CirclePause, ExternalLink, Globe2, KeyRound, LockKeyhole, Network, Plus, RefreshCw, Search, ShieldOff, Unplug } from 'lucide-react';
import type { AgentIdentity, AgentStatus } from '../lib/agentApi';
import type { IntegrationConnection, IntegrationProvider, IntegrationSecurityStatus } from '../lib/integrationApi';
import type { ApiVersion } from '../lib/systemApi';
import { visibleIdentities, visibleProviders, type ConnectionFilter, type IdentityFilter, type ProviderOption } from './connectionsModel';
import { linkProps } from '../workspace/routes';
import type { OrganizationAccess } from '../lib/identityApi';
import { ConnectionsHeader as NewHeader, Pulse, ToolMark } from '../workspace/connections/Chrome';
import { scopesFor, useConnections, type ConnectionsData } from '../workspace/connections/data';
import { useLive } from '../workspace/live';
import { usePageTitle } from '../workspace/history';
import { Ago, Avatar, EmptyState, Notice, Pills, Section, SkeletonLines, State, sentence } from '../workspace/ui';

const providerMarks: Partial<Record<IntegrationProvider, string>> = {
  github: '/provider-marks/github.svg',
  gmail: '/provider-marks/gmail.svg',
  google_drive: '/provider-marks/google-drive.svg',
  slack: '/provider-marks/slack.svg',
  google_calendar: '/provider-marks/google-calendar.svg',
};
function providerIcon(provider: IntegrationProvider, size = 19) {
  const mark = providerMarks[provider];
  if (mark) return <img src={mark} width={size} height={size} alt='' aria-hidden='true' />;
  const Icon = provider === 'browser' ? Globe2 : Network;
  return <Icon size={size} strokeWidth={1.8} />;
}
const HEALTH_TONE: Record<string, 'ok' | 'warn' | 'danger' | 'neutral'> = { connected: 'ok', degraded: 'warn', error: 'danger', disconnected: 'neutral' };

/** Header shared by the three Connections sections. */
function ConnectionsHeader({ active, actions }: { active: 'integrations' | 'identities' | 'capabilities'; actions?: ReactNode }) {
  return <NewHeader active={active === 'integrations' ? 'home' : active === 'capabilities' ? 'access' : 'identities'} actions={actions} />;
}

/* ---------- Integrations ---------- */

export function IntegrationsStage(p: { organizationId: string; organizationName: string; apiVersion: ApiVersion; onApiVersionChange: (version: ApiVersion) => void; providers: ProviderOption[]; items: IntegrationConnection[]; security: IntegrationSecurityStatus | null; selected: IntegrationConnection | null; select: (id: string) => void; canManage: boolean; loading: boolean; working: boolean; error: string | null; openProvider: (provider: ProviderOption) => void; checkHealth: () => void; disconnect: () => void; githubControls?: ReactNode }) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<ConnectionFilter>('all');
  const visible = useMemo(() => visibleProviders(p.providers, p.items, query, filter), [p.providers, p.items, query, filter]);
  const active = p.items.filter((item) => item.status !== 'disconnected');
  const degraded = active.filter((item) => item.status === 'degraded').length;
  const vault = Boolean(p.security?.credentialVaultConfigured);
  const locked = (option: ProviderOption) => option.state === 'guarded' || (option.credential === 'required' && !vault);

  return <div className='ws-page'>
    <ConnectionsHeader active='integrations' />
    {p.error && <Notice tone='danger'>{p.error}</Notice>}
    <p className='ws-summary-line'><strong>{active.length}</strong> connected tool{active.length === 1 ? '' : 's'}{degraded ? <>, <span data-tone='warn'>{degraded} need attention</span></> : null}. {vault ? <span data-tone='ok'>The credential vault is ready.</span> : <span data-tone='warn'>The credential vault is not set up, so only tools without a stored credential can connect.</span>} Every tool runs through the gateway.</p>

    <Section title='Your connections' count={p.items.length}>
      {p.loading ? <SkeletonLines rows={3} avatar /> : p.items.length ? <div className='ws-split-inline'>
        <ul className='ws-rows'>{p.items.map((item) => <li key={item.id} className='ws-row ws-inbox-row' aria-current={p.selected?.id === item.id ? 'true' : undefined}>
          <span className='ws-provider-mark'>{providerIcon(item.provider, 18)}</span>
          <button type='button' className='ws-row-main ws-row-link ws-row-button' onClick={() => p.select(item.id)}><strong>{item.displayName}</strong><small>{sentence(item.provider)} · {item.credential.mode === 'encrypted_secret' ? 'credential stored' : 'no credential'}</small></button>
          <div className='ws-inbox-row-meta'><State value={item.status} tone={HEALTH_TONE[item.status]} /></div>
        </li>)}</ul>
        {p.selected && <div className='ws-detail ws-connection-detail' key={p.selected.id}>
          <div className='ws-detail-head'><span className='ws-provider-mark ws-provider-mark-lg'>{providerIcon(p.selected.provider, 22)}</span><div><div className='ws-muted ws-small'>{sentence(p.selected.provider)}</div><h2>{p.selected.displayName}</h2><div className='ws-detail-sub'><State value={p.selected.status} tone={HEALTH_TONE[p.selected.status]} /><span className='ws-muted'>checked {p.selected.health.lastCheckedAt ? <Ago value={p.selected.health.lastCheckedAt} /> : 'never'}</span></div></div></div>
          <p className='ws-detail-why'>{p.selected.health.message}</p>
          <dl className='ws-facts'>
            <div><dt>Resource</dt><dd><code>{p.selected.resourceKey}</code></dd></div>
            <div><dt>Credential</dt><dd>{p.selected.credential.mode === 'encrypted_secret' ? <>Encrypted<small>{p.selected.credential.fingerprint ?? 'fingerprinted'}</small></> : 'None'}</dd></div>
          </dl>
          <div><div className='ws-muted ws-small'>What this tool can do. These are tool operations, not permissions; policy decides each request.</div><div className='ws-chip-list' style={{ marginTop: 8 }}>{p.selected.supportedOperations.map((operation) => <code key={operation}>{operation}</code>)}</div></div>
          <div className='ws-legacy-embed'><IntegrationFoundationDetails organizationId={p.organizationId} version={p.apiVersion} connectionId={p.selected.id} /></div>
          <div className='ws-form-actions ws-form-actions-start'>
            {/^https?:\/\//i.test(p.selected.webUrl) && <a className='ws-button' href={p.selected.webUrl} target='_blank' rel='noreferrer'>Open in {sentence(p.selected.provider)}<ExternalLink size={13} /></a>}
            {p.selected.status !== 'disconnected' && <button type='button' className='ws-button' disabled={p.working} onClick={p.checkHealth}><RefreshCw size={14} />Check health</button>}
            {p.canManage && p.selected.status !== 'disconnected' && <button type='button' className='ws-button ws-button-danger' disabled={p.working} onClick={p.disconnect}><Unplug size={14} />Disconnect</button>}
          </div>
        </div>}
      </div> : <EmptyState icon={<Network size={18} />} title='No tools connected yet'>Pick a tool below. Workers can only reach what is connected here, and only within policy.</EmptyState>}
    </Section>

    <Section title='Add a tool' count={visible.length}>
      <div className='ws-toolbar ws-toolbar-tight'><Pills label='Tool filter' value={filter} onChange={setFilter} options={[{ value: 'all', label: 'All' }, { value: 'connected', label: 'Connected' }, { value: 'not_connected', label: 'Not connected' }]} /><label className='ws-search-field'><Search size={15} aria-hidden='true' /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder='Find a tool' aria-label='Search tools' /></label></div>
      {visible.length ? <ul className='ws-provider-grid'>{visible.map((option) => {
        const connected = active.filter((item) => item.provider === option.provider).length;
        const unavailable = locked(option);
        return <li key={option.provider} className='ws-provider'>
          <div className='ws-provider-top'><span className='ws-provider-mark'>{providerIcon(option.provider, 20)}</span><strong>{option.name}</strong>{connected > 0 ? <span className='ws-tag' data-tone='ok'>{connected} connected</span> : option.state === 'guarded' ? <span className='ws-tag'>Not available</span> : unavailable ? <span className='ws-tag' data-tone='warn'><LockKeyhole size={11} />Needs vault</span> : null}</div>
          <p>{option.description}</p>
          {option.provider === 'github' && p.githubControls ? <div className='ws-legacy-embed'>{p.githubControls}</div> : <div className='ws-provider-foot'>
            <span>{option.credential === 'required' ? 'Needs a credential' : option.credential === 'disabled' ? 'Connection closed' : 'Credential optional'}</span>
            {p.canManage && option.state !== 'guarded' && <button type='button' className='ws-button ws-button-sm' disabled={unavailable} onClick={() => p.openProvider(option)}><Plus size={13} />Connect</button>}
          </div>}
        </li>;
      })}</ul> : <EmptyState icon={<Search size={18} />} title='No matching tools'>Try another name or filter.</EmptyState>}
    </Section>
  </div>;
}

/* ---------- Agent identities ---------- */

export function IdentitiesStage(p: { organization: OrganizationAccess; identityId?: string; organizationName: string; userId: string; apiVersion: ApiVersion; onApiVersionChange: (version: ApiVersion) => void; agents: AgentIdentity[]; selected: AgentIdentity | null; select: (id: string) => void; canManage: boolean; loading: boolean; submitting: boolean; error: string | null; register: () => void; lifecycle: (status: AgentStatus) => void; credentialAction: (action: 'rotate' | 'revoke') => void }) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<IdentityFilter>('all');
  const visible = useMemo(() => visibleIdentities(p.agents, query, filter), [p.agents, query, filter]);
  usePageTitle(p.identityId ? p.selected?.name : null);
  const connections = useConnections(p.organization, p.apiVersion);
  const live = useLive();
  const count = (status: string) => p.agents.filter((agent) => agent.status === status).length;
  const owner = (id: string) => id === p.userId ? 'You' : id;
  const workersOf = (agentId: string) => live.workers.filter((worker) => worker.agentIdentityId === agentId && worker.status !== 'archived');

  if (p.identityId) {
    const agent = p.selected;
    return <div className='ws-page'>

      {p.error && <Notice tone='danger'>{p.error}</Notice>}
      {!agent ? (p.loading ? <SkeletonLines rows={5} /> : <EmptyState icon={<Bot size={18} />} title='This identity doesn’t exist here'>It may have been removed.</EmptyState>) : <>
        <header className='ws-tool-hero'>
          <span className='ws-tool-mark' data-size='lg'><Bot size={24} strokeWidth={1.7} /></span>
          <div><div className='ws-muted ws-small'>Agent identity · owned by {owner(agent.ownerUserId)}</div><h1>{agent.name}</h1><div className='ws-detail-sub'><Pulse tone={agent.status === 'active' ? 'ok' : agent.status === 'suspended' ? 'warn' : 'neutral'} label={sentence(agent.status)} /><span className='ws-muted'>credential {agent.credential.status} · v{agent.credential.version}</span></div></div>
        </header>
        <p className='ws-detail-why'>{agent.description || 'No purpose recorded.'}</p>
        <ol className='ws-chain' aria-label='How this identity is checked'>
          <li data-tone={agent.status === 'active' ? 'ok' : 'warn'}><span className='ws-chain-dot' /><strong>Identity</strong><small>{sentence(agent.status)}</small></li>
          <li data-tone={agent.credential.status === 'active' ? 'ok' : 'danger'}><span className='ws-chain-dot' /><strong>Credential</strong><small>{sentence(agent.credential.status)} · <code>{agent.credential.fingerprint?.slice(0, 8) || '—'}</code></small></li>
          <li data-tone={(connections.data?.profiles[agent.id]?.declaredScopes.length ?? 0) ? 'ok' : 'neutral'}><span className='ws-chain-dot' /><strong>Scopes</strong><small>{connections.data?.profiles[agent.id]?.declaredScopes.length ?? '…'} declared</small></li>
          <li data-tone='neutral'><span className='ws-chain-dot' /><strong>Policy</strong><small>Decides each request</small></li>
        </ol>
        <dl className='ws-stats ws-stats-4'>
          <div><dt>Workers</dt><dd>{workersOf(agent.id).length}</dd><small>act as this identity</small></div>
          <div><dt>Last used</dt><dd className='ws-stat-small'>{agent.credential.lastUsedAt ? <Ago value={agent.credential.lastUsedAt} /> : 'Never'}</dd><small>by its credential</small></div>
          <div><dt>Expires</dt><dd className='ws-stat-small'>{agent.credential.expiresAt ? new Date(agent.credential.expiresAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : 'Never'}</dd><small>credential expiry</small></div>
          <div><dt>Created</dt><dd className='ws-stat-small'>{new Date(agent.createdAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}</dd><small>{p.organizationName}</small></div>
        </dl>
        <IdentityAccess agentId={agent.id} connections={connections.data} />
        {workersOf(agent.id).length > 0 && <Section title='Workers acting as this identity' count={workersOf(agent.id).length}>
          <ul className='ws-rows'>{workersOf(agent.id).map((worker) => <li key={worker.id} className='ws-row ws-compact-row'><Avatar name={worker.name} seed={worker.id} size={26} live={live.liveness(worker)} /><a className='ws-row-main ws-row-link' {...linkProps({ page: 'worker', workerId: worker.id, tab: 'overview' })}><strong>{worker.name}</strong><small>{worker.department || 'Worker'}</small></a></li>)}</ul>
        </Section>}
        {p.canManage && <Section title='Controls'>
          <div className='ws-control-rows'>
            <div><span>Lifecycle<small className='ws-block'>Suspending is reversible; disabling is permanent.</small></span><div className='ws-form-actions ws-form-actions-start'>
              {agent.status === 'active' && <button type='button' className='ws-button ws-button-sm' disabled={p.submitting} onClick={() => p.lifecycle('suspended')}><CirclePause size={14} />Suspend</button>}
              {agent.status === 'suspended' && agent.credential.status === 'active' && <button type='button' className='ws-button ws-button-sm' disabled={p.submitting} onClick={() => p.lifecycle('active')}><Check size={14} />Activate</button>}
              {agent.status !== 'disabled' && <button type='button' className='ws-button ws-button-sm ws-button-danger' disabled={p.submitting} onClick={() => p.lifecycle('disabled')}><ShieldOff size={14} />Disable</button>}
            </div></div>
            <div><span>Credential<small className='ws-block'>A rotated secret is shown once.</small></span><div className='ws-form-actions ws-form-actions-start'>
              {agent.status !== 'disabled' && <button type='button' className='ws-button ws-button-sm' disabled={p.submitting} onClick={() => p.credentialAction('rotate')}><RefreshCw size={14} />Rotate</button>}
              {agent.credential.status === 'active' && <button type='button' className='ws-button ws-button-sm ws-button-danger' disabled={p.submitting} onClick={() => p.credentialAction('revoke')}><KeyRound size={14} />Revoke</button>}
            </div></div>
          </div>
        </Section>}
        <details className='ws-technical'><summary>Technical detail</summary><dl className='ws-facts'><div><dt>Identity</dt><dd><code>{agent.id}</code></dd></div><div><dt>Fingerprint</dt><dd><code>{agent.credential.fingerprint || '—'}</code></dd></div><div><dt>Credential scopes</dt><dd>{agent.credential.scopes.length ? <span className='ws-chip-list'>{agent.credential.scopes.map((scope) => <code key={scope}>{scope}</code>)}</span> : '—'}</dd></div></dl></details>
      </>}
    </div>;
  }

  return <div className='ws-page ws-page-wide'>
    <ConnectionsHeader active='identities' actions={p.canManage && <button type='button' className='ws-button ws-button-primary' onClick={p.register}><Plus size={15} />Register identity</button>} />
    {p.error && <Notice tone='danger'>{p.error}</Notice>}
    <p className='ws-summary-line'>Each worker acts as an agent identity: separate from people, with its own owner, credential and lifecycle. <strong>{count('active')}</strong> active{count('suspended') ? <>, <span data-tone='warn'>{count('suspended')} suspended</span></> : null}.</p>
    <div className='ws-toolbar ws-toolbar-tight'>
      <Pills label='Identity state' value={filter} onChange={setFilter} options={[{ value: 'all', label: 'All', count: p.agents.length }, { value: 'active', label: 'Active', count: count('active') }, { value: 'suspended', label: 'Suspended', count: count('suspended') }, { value: 'disabled', label: 'Disabled', count: count('disabled') }]} />
      <label className='ws-search-field'><Search size={15} aria-hidden='true' /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder='Find identity, purpose or ID' aria-label='Search agent identities' /></label>
    </div>
    {p.loading && !p.agents.length ? <SkeletonLines rows={4} /> : visible.length ? <ul className='ws-rows'>{visible.map((agent) => {
      const declared = connections.data?.profiles[agent.id]?.declaredScopes.length;
      return <li key={agent.id} className='ws-row ws-identity-row'>
        <span className='ws-doc-icon' aria-hidden='true'><Bot size={15} /></span>
        <a className='ws-row-main ws-row-link' {...linkProps({ page: 'connections', view: 'identities', id: agent.id })}><strong>{agent.name}</strong><small>{agent.description || 'No purpose recorded'}</small></a>
        <div className='ws-row-meta'>
          <span className='ws-hide-sm'>{workersOf(agent.id).length} worker{workersOf(agent.id).length === 1 ? '' : 's'}</span>
          <span className='ws-hide-sm'>{declared ?? '…'} scope{declared === 1 ? '' : 's'}</span>
          {agent.credential.status !== 'active' && <span className='ws-raised'>credential {agent.credential.status}</span>}
          <Pulse tone={agent.status === 'active' ? 'ok' : agent.status === 'suspended' ? 'warn' : 'neutral'} label={sentence(agent.status)} />
        </div>
      </li>;
    })}</ul> : <EmptyState icon={<Bot size={18} />} title={p.agents.length ? 'No matching identities' : 'No identities yet'}>{p.agents.length ? 'Change the search or filter.' : 'Register an identity to give a worker its own credential and lifecycle.'}</EmptyState>}
  </div>;
}

/** The connections and scopes an identity expects, grouped by connection. */
function IdentityAccess({ agentId, connections }: { agentId: string; connections: ConnectionsData | null }) {
  if (!connections) return <SkeletonLines rows={3} />;
  const declared = connections.profiles[agentId]?.declaredScopes ?? [];
  const stale = connections.profiles[agentId]?.staleScopes ?? [];
  const groups = connections.integrations.filter((item) => item.status !== 'disconnected').map((item) => ({ item, scopes: scopesFor(connections, item.id).filter((scope) => declared.includes(scope)) })).filter((group) => group.scopes.length);
  return <Section title='Access' count={declared.length} description='Scopes this identity expects, by connection. Policies still decide every request.' action={<a className='ws-link' {...linkProps({ page: 'connections', view: 'access' })}>Access map</a>}>
    {groups.length ? <ul className='ws-rows'>{groups.map(({ item, scopes }) => <li key={item.id} className='ws-row ws-access-row'>
      <ToolMark provider={item.provider} size='sm' />
      <a className='ws-row-main ws-row-link' {...linkProps({ page: 'connections', view: 'account', id: item.id })}><strong>{item.displayName}</strong><small className='ws-chip-list'>{scopes.map((scope) => <code key={scope}>{scope}</code>)}</small></a>
    </li>)}</ul> : <p className='ws-quiet'>This identity doesn’t declare any scopes on a connected tool yet. Grant them from a connection’s Access tab or the Access map.</p>}
    {stale.length > 0 && <Notice tone='warn'>{stale.length} declared scope{stale.length === 1 ? '' : 's'} no longer match a connected tool: <code>{stale.join(', ')}</code></Notice>}
  </Section>;
}
