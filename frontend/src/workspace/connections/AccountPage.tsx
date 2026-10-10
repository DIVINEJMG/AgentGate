import { useEffect, useMemo, useState } from 'react';
import { Activity, Check, ExternalLink, Hand, KeyRound, Loader2, OctagonX, RefreshCw, Scale, Share2, ShieldCheck, TriangleAlert, Unplug } from 'lucide-react';
import { saveAgentCapabilityProfile } from '../../lib/capabilityApi';
import { authorizeGitHub } from '../../lib/githubApi';
import type { OrganizationAccess } from '../../lib/identityApi';
import { discoverConnectionResources, reconnectConnection, shareConnection, sharingSubjects, type ShareSubject } from '../../lib/integrationFoundationApi';
import { checkIntegration, disconnectIntegration } from '../../lib/integrationApi';
import type { ApiVersion } from '../../lib/systemApi';
import { notify } from '../feedback';
import { usePageTitle } from '../history';
import { useLive } from '../live';
import { linkProps, navigate, queryParam } from '../routes';
import { Ago, Avatar, Confirm, EmptyState, Notice, Section, SkeletonLines, humanize, sentence } from '../ui';
import { Pulse, ToolMark } from './Chrome';
import { actionsFor, activityFor, errorText, foundationFor, HEALTH_TONE, identitiesFor, policiesFor, resourcesFor, RISK_TONE, ruleFor, scopesFor, toolFor, useConnections, type ConnectionsData } from './data';

type Tab = 'overview' | 'resources' | 'access' | 'settings';
const TABS: Array<{ id: Tab; label: string }> = [{ id: 'overview', label: 'Overview' }, { id: 'resources', label: 'Resources' }, { id: 'access', label: 'Access' }, { id: 'settings', label: 'Settings' }];
const RULE: Record<string, { label: string; tone: 'ok' | 'warn' | 'danger' }> = { allow: { label: 'Allowed', tone: 'ok' }, require_approval: { label: 'Needs approval', tone: 'warn' }, deny: { label: 'Blocked', tone: 'danger' } };
const OUTCOME: Record<string, { label: string; tone: 'ok' | 'warn' | 'danger' }> = { executed: { label: 'Allowed', tone: 'ok' }, held: { label: 'Held', tone: 'warn' }, blocked: { label: 'Blocked', tone: 'danger' }, failed: { label: 'Failed', tone: 'danger' } };

export default function AccountPage({ organization, apiVersion, id }: { organization: OrganizationAccess; apiVersion: ApiVersion; id: string }) {
  const { data, error, refresh, patch } = useConnections(organization, apiVersion);
  const tab = (TABS.some((item) => item.id === queryParam('tab')) ? queryParam('tab') : 'overview') as Tab;
  const [checking, setChecking] = useState(false);
  const connection = data?.integrations.find((item) => item.id === id) ?? null;
  usePageTitle(connection?.displayName);
  const base = linkProps({ page: 'connections', view: 'account', id }).href;

  if (!data) return <div className='ws-page'>{error ? <Notice tone='danger'>{error}</Notice> : <SkeletonLines rows={6} avatar />}</div>;
  if (!connection) return <div className='ws-page'><EmptyState title='This connection doesn’t exist here'>It may have been removed, or it belongs to another workspace.</EmptyState></div>;

  const tool = toolFor(connection.provider);
  const tone = HEALTH_TONE[connection.status] ?? 'neutral';
  const foundation = foundationFor(data, connection.id);
  const needsReconnect = foundation?.authorizationState === 'reconnect_required';

  async function checkNow() {
    setChecking(true);
    try {
      const checked = await checkIntegration(apiVersion, organization.id, connection!.id);
      patch((current) => ({ ...current, integrations: current.integrations.map((item) => item.id === checked.id ? checked : item) }));
      notify({ key: 'health', tone: checked.status === 'connected' ? 'ok' : 'warn', title: checked.status === 'connected' ? 'Connection is healthy' : `Connection is ${checked.status}`, body: checked.health.message });
    } catch (caught) { notify({ key: 'health', tone: 'danger', title: 'Health check failed', body: errorText(caught) }); }
    finally { setChecking(false); }
  }

  return <div className='ws-page'>

    <header className='ws-tool-hero ws-account-hero'>
      <ToolMark provider={connection.provider} size='lg' />
      <div>
        <div className='ws-muted ws-small'>{tool?.name ?? sentence(connection.provider)}</div>
        <h1>{connection.displayName}</h1>
        <div className='ws-detail-sub'><Pulse tone={tone} label={connection.status === 'connected' ? 'Healthy' : sentence(connection.status)} /><code className='ws-muted'>{connection.resourceKey}</code><span className='ws-muted'>checked <Ago value={connection.health.lastCheckedAt} /></span></div>
      </div>
      <div className='ws-page-actions'>
        {/^https?:\/\//i.test(connection.webUrl) && <a className='ws-button' href={connection.webUrl} target='_blank' rel='noreferrer'>Open in {tool?.name ?? 'tool'}<ExternalLink size={13} /></a>}
        {connection.status !== 'disconnected' && <button type='button' className='ws-button' disabled={checking} onClick={() => void checkNow()}>{checking ? <Loader2 size={15} className='cf-spin' /> : <RefreshCw size={15} />}Check now</button>}
      </div>
    </header>
    {error && <Notice tone='danger'>{error}</Notice>}
    {connection.status === 'disconnected' && <Notice tone='info'>This connection is disconnected. Its stored credential was removed and workers can no longer use it. It stays here for history.</Notice>}
    {needsReconnect && <Notice tone='warn' action={<a className='ws-button ws-button-sm' href={`${base}?tab=settings`} onClick={(event) => { event.preventDefault(); navigate(`${base}?tab=settings`, { keepScroll: true }); }}>Reconnect</a>}>{tool?.name ?? 'The tool'} needs to be reauthorised{foundation?.reason ? `: ${foundation.reason}` : '.'}</Notice>}
    <nav className='ws-tabs' aria-label='Connection sections'>{TABS.map((item) => <a key={item.id} href={item.id === 'overview' ? base : `${base}?tab=${item.id}`} aria-current={tab === item.id ? 'page' : undefined} onClick={(event) => { if (event.metaKey || event.ctrlKey) return; event.preventDefault(); navigate(item.id === 'overview' ? base : `${base}?tab=${item.id}`, { keepScroll: true }); }}>{item.label}</a>)}</nav>

    {tab === 'overview' && <Overview data={data} id={connection.id} />}
    {tab === 'resources' && <Resources data={data} id={connection.id} organization={organization} apiVersion={apiVersion} onRefresh={refresh} />}
    {tab === 'access' && <Access data={data} id={connection.id} organization={organization} apiVersion={apiVersion} patch={patch} />}
    {tab === 'settings' && <Settings data={data} id={connection.id} organization={organization} apiVersion={apiVersion} onChanged={refresh} />}
  </div>;
}

/* ---------- Overview ---------- */

function Overview({ data, id }: { data: ConnectionsData; id: string }) {
  const live = useLive();
  const connection = data.integrations.find((item) => item.id === id)!;
  const activity = activityFor(data, id);
  const identities = identitiesFor(data, id);
  const workers = live.workers.filter((worker) => worker.status !== 'archived' && identities.some((entry) => entry.agent.id === worker.agentIdentityId));
  const scopes = scopesFor(data, id);
  const policies = policiesFor(data, id);
  const credential = connection.credential.mode === 'encrypted_secret';
  const githubApp = foundationFor(data, id)?.authenticationStrategy === 'github_app';
  const chain: Array<{ label: string; value: string; tone: 'ok' | 'warn' | 'danger' | 'neutral' }> = [
    { label: 'Tool', value: connection.status === 'connected' ? 'Reachable' : sentence(connection.status), tone: HEALTH_TONE[connection.status] ?? 'neutral' },
    { label: 'Credential', value: githubApp ? 'GitHub App installation' : credential ? `Encrypted · ${connection.credential.fingerprint?.slice(0, 8) ?? 'stored'}` : 'None stored', tone: credential || connection.provider === 'browser' || connection.provider === 'github' ? 'ok' : 'warn' },
    { label: 'Scopes', value: `${scopes.length} offered`, tone: scopes.length ? 'ok' : 'neutral' },
    { label: 'Identities', value: `${identities.length} declare them`, tone: identities.length ? 'ok' : 'neutral' },
    { label: 'Policies', value: `${policies.length} apply`, tone: policies.length ? 'ok' : 'warn' },
  ];
  return <>
    <ol className='ws-chain' aria-label='How a request through this connection is checked'>{chain.map((node) => <li key={node.label} data-tone={node.tone}><span className='ws-chain-dot' /><strong>{node.label}</strong><small>{node.value}</small></li>)}</ol>
    <p className='ws-detail-why'>{connection.health.message}</p>
    <dl className='ws-stats ws-stats-4'>
      <div><dt>Resources</dt><dd>{resourcesFor(data, id).length}</dd><small>{scopes.length} actions offered</small></div>
      <div><dt>Workers using it</dt><dd>{workers.length}</dd><small>through {identities.length} identit{identities.length === 1 ? 'y' : 'ies'}</small></div>
      <div><dt>Requests</dt><dd>{activity.length}</dd><small>{activity.filter((item) => item.status === 'blocked').length} blocked · {activity.filter((item) => item.status === 'held').length} held</small></div>
      <div><dt>Last used</dt><dd className='ws-stat-small'>{activity[0] ? <Ago value={activity[0].requestedAt} /> : 'Not yet'}</dd><small>connected <Ago value={connection.createdAt} /></small></div>
    </dl>
    <div className='ws-cols'>
      <Section title='Recent gateway decisions' count={activity.length} action={<a className='ws-link' {...linkProps({ page: 'activity' })}>All activity</a>}>
        {activity.length ? <ul className='ws-rows ws-feed'>{activity.slice(0, 8).map((item) => {
          const outcome = OUTCOME[item.status] ?? { label: sentence(item.status), tone: 'ok' as const };
          return <li key={item.id} className='ws-row'>
            <span className='ws-outcome-icon' data-tone={outcome.tone} aria-hidden='true'>{item.status === 'executed' ? <Check size={13} strokeWidth={2.4} /> : item.status === 'held' ? <Hand size={12} /> : item.status === 'blocked' ? <OctagonX size={13} /> : <TriangleAlert size={13} />}</span>
            <a className='ws-row-main ws-row-link' {...linkProps({ page: 'activity', actionId: item.id })}><strong>{humanize(item.request.providerOperation || item.request.scope)}</strong><small>{item.agent.name} · {outcome.label}</small></a>
            <div className='ws-row-meta'><Ago value={item.requestedAt} /></div>
          </li>;
        })}</ul> : <p className='ws-quiet'><Activity size={14} /> No worker has used this connection yet.</p>}
      </Section>
      <Section title='Workers using it' count={workers.length}>
        {workers.length ? <ul className='ws-rows'>{workers.map((worker) => <li key={worker.id} className='ws-row ws-compact-row'>
          <Avatar name={worker.name} seed={worker.id} size={26} live={live.liveness(worker)} />
          <a className='ws-row-main ws-row-link' {...linkProps({ page: 'worker', workerId: worker.id, tab: 'overview' })}><strong>{worker.name}</strong><small>{worker.department || 'Worker'}</small></a>
        </li>)}</ul> : <p className='ws-quiet'>No worker’s identity declares this connection’s scopes yet. Grant them on the Access tab.</p>}
      </Section>
    </div>
  </>;
}

/* ---------- Resources ---------- */

function Resources({ data, id, organization, apiVersion, onRefresh }: { data: ConnectionsData; id: string; organization: OrganizationAccess; apiVersion: ApiVersion; onRefresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const resources = resourcesFor(data, id);
  const discovered = data.foundation?.resources.filter((resource) => resource.connectionId === id) ?? [];
  const actions = actionsFor(data, id);
  const resourceIds = resources.map((resource) => resource.id);
  async function rediscover() {
    setBusy(true);
    try { await discoverConnectionResources(apiVersion, organization.id, id); await onRefresh(); notify({ key: 'discover', tone: 'ok', title: 'Resources rediscovered' }); }
    catch (caught) { notify({ key: 'discover', tone: 'danger', title: 'Discovery failed', body: errorText(caught) }); }
    finally { setBusy(false); }
  }
  return <>
    <Section title={data.foundation?.enabled ? 'Discovered resources' : 'Resources'} count={data.foundation?.enabled ? discovered.length : resources.length} action={data.foundation?.enabled && <button type='button' className='ws-button ws-button-sm' disabled={busy} onClick={() => void rediscover()}>{busy ? <Loader2 size={13} className='cf-spin' /> : <RefreshCw size={13} />}Rediscover</button>}>
      {data.foundation?.enabled ? (discovered.length ? <ul className='ws-rows'>{discovered.map((resource) => <li key={resource.id} className='ws-row'>
        <Pulse tone={HEALTH_TONE[resource.health] ?? (resource.health === 'ok' ? 'ok' : 'warn')} />
        <span className='ws-row-main'><strong>{resource.name}</strong><small><code>{resource.externalId}</code> · {resource.capabilities.length} action{resource.capabilities.length === 1 ? '' : 's'}</small></span>
        <div className='ws-row-meta'>{resource.webUrl && /^https?:\/\//.test(resource.webUrl) && <a className='ws-link' href={resource.webUrl} target='_blank' rel='noreferrer'>Open<ExternalLink size={11} /></a>}<span>{sentence(resource.health)}</span></div>
      </li>)}</ul> : <p className='ws-quiet'>Nothing discovered yet. Rediscover to look again.</p>)
      : resources.length ? <ul className='ws-rows'>{resources.map((resource) => <li key={resource.id} className='ws-row'>
        <Pulse tone={HEALTH_TONE[resource.status] ?? 'neutral'} />
        <span className='ws-row-main'><strong>{resource.displayName}</strong><small><code>{resource.key}</code> · {sentence(resource.type)}</small></span>
        <div className='ws-row-meta'>{resource.actions.length} actions</div>
      </li>)}</ul> : <p className='ws-quiet'>This connection exposes no resources.</p>}
    </Section>
    <Section title='Actions it offers' count={actions.length} description='What a worker could ask for, its fixed risk, and what your current rules decide.'>
      {actions.length ? <div className='ws-table-wrap'><table className='ws-table'>
        <thead><tr><th>Action</th><th>Scope</th><th>Risk</th><th>Your rules</th></tr></thead>
        <tbody>{actions.map((action) => {
          const rule = ruleFor(data, resourceIds, action);
          const look = RULE[rule.effect] ?? RULE.deny;
          return <tr key={action.scope}>
            <td><strong>{sentence(action.action)} {humanize(action.target)}</strong><small className='ws-block'>{action.description}</small></td>
            <td><code>{action.scope}</code></td>
            <td><span className='ws-state' data-tone={RISK_TONE[action.risk]}>{sentence(action.risk)}</span></td>
            <td><span className='ws-state' data-tone={look.tone}>{look.label}</span><small className='ws-block'>{rule.policy ? rule.policy.name : 'No rule matches'}</small></td>
          </tr>;
        })}</tbody>
      </table></div> : <p className='ws-quiet'>No actions are offered.</p>}
    </Section>
  </>;
}

/* ---------- Access ---------- */

function Access({ data, id, organization, apiVersion, patch }: { data: ConnectionsData; id: string; organization: OrganizationAccess; apiVersion: ApiVersion; patch: ReturnType<typeof useConnections>['patch'] }) {
  const foundation = foundationFor(data, id);
  const scopes = scopesFor(data, id);
  const policies = policiesFor(data, id);
  const canManageScopes = organization.permissions.includes('capabilities.manage');
  return <>
    <Sharing id={id} organization={organization} apiVersion={apiVersion} canShare={Boolean(foundation?.canShare)} enabled={Boolean(data.foundation?.enabled && foundation)} />
    <Section title='Agent identities' description='Which identities expect this connection’s scopes. Declaring a scope is not permission; policies still decide.'>
      {data.agents.length === 0 ? <p className='ws-quiet'>No agent identities yet. <a className='ws-link' {...linkProps({ page: 'connections', view: 'identities' })}>Register one</a>.</p>
        : scopes.length === 0 ? <p className='ws-quiet'>This connection offers no scopes to declare.</p>
        : <ul className='ws-rows'>{data.agents.map((agent) => <IdentityScopes key={agent.id} data={data} agentId={agent.id} scopes={scopes} canManage={canManageScopes} organization={organization} apiVersion={apiVersion} patch={patch} />)}</ul>}
    </Section>
    <Section title='Policies that apply' count={policies.length} action={<a className='ws-link' {...linkProps({ page: 'policies' })}>Open policies</a>}>
      {policies.length ? <ul className='ws-rows'>{policies.map((policy) => <li key={policy.id} className='ws-row ws-compact-row'>
        <Scale size={15} className='ws-muted' />
        <span className='ws-row-main'><strong>{policy.name}</strong><small>Priority {policy.priority}{policy.selectors.resourceIds.length ? ' · this connection' : ' · any resource'}</small></span>
        <div className='ws-row-meta'><span className='ws-state' data-tone={(RULE[policy.effect] ?? RULE.deny).tone}>{(RULE[policy.effect] ?? RULE.deny).label}</span></div>
      </li>)}</ul> : <Notice tone='warn'>No enabled policy matches this connection, so every request through it is blocked.</Notice>}
    </Section>
  </>;
}

function IdentityScopes({ data, agentId, scopes, canManage, organization, apiVersion, patch }: { data: ConnectionsData; agentId: string; scopes: string[]; canManage: boolean; organization: OrganizationAccess; apiVersion: ApiVersion; patch: ReturnType<typeof useConnections>['patch'] }) {
  const agent = data.agents.find((item) => item.id === agentId)!;
  const declared = data.profiles[agentId]?.declaredScopes ?? [];
  const mine = declared.filter((scope) => scopes.includes(scope));
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string[]>(mine);
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (!open) setDraft(mine); }, [open, mine.join('|')]);
  const dirty = draft.slice().sort().join('|') !== mine.slice().sort().join('|');
  async function save() {
    setSaving(true);
    try {
      const next = [...declared.filter((scope) => !scopes.includes(scope)), ...draft].sort();
      const profile = await saveAgentCapabilityProfile(apiVersion, organization.id, agentId, next);
      patch((current) => ({ ...current, profiles: { ...current.profiles, [agentId]: profile } }));
      notify({ key: 'scopes', tone: 'ok', title: `Saved ${agent.name}’s scopes` });
      setOpen(false);
    } catch (caught) { notify({ key: 'scopes', tone: 'danger', title: 'Scopes were not saved', body: errorText(caught) }); }
    finally { setSaving(false); }
  }
  return <li className='ws-row ws-identity-scopes' data-open={open ? 'true' : undefined}>
    <span className='ws-doc-icon' aria-hidden='true'><KeyRound size={14} /></span>
    <a className='ws-row-main' {...linkProps({ page: 'connections', view: 'identities', id: agentId })}><strong>{agent.name}</strong><small>{mine.length ? `${mine.length} of ${scopes.length} scopes declared` : 'No scopes declared'} · {sentence(agent.status)}</small></a>
    <div className='ws-row-meta'>{canManage && <button type='button' className='ws-button ws-button-sm' aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'Close' : 'Edit'}</button>}</div>
    {open && <div className='ws-scope-editor'>
      <ul>{scopes.map((scope) => <li key={scope}><label><input type='checkbox' checked={draft.includes(scope)} onChange={() => setDraft((current) => current.includes(scope) ? current.filter((item) => item !== scope) : [...current, scope])} /><code>{scope}</code></label></li>)}</ul>
      <div className='ws-form-actions'><button type='button' className='ws-button ws-button-sm ws-button-quiet' onClick={() => setDraft(draft.length === scopes.length ? [] : scopes)}>{draft.length === scopes.length ? 'Clear all' : 'Select all'}</button><span className='ws-connect-spacer' /><button type='button' className='ws-button ws-button-sm ws-button-primary' disabled={!dirty || saving} onClick={() => void save()}>{saving ? 'Saving…' : 'Save'}</button></div>
    </div>}
  </li>;
}

function Sharing({ id, organization, apiVersion, canShare, enabled }: { id: string; organization: OrganizationAccess; apiVersion: ApiVersion; canShare: boolean; enabled: boolean }) {
  const [state, setState] = useState<{ subjects: ShareSubject[]; grants: Array<{ subjectId: string; subjectType: string; active: boolean }> } | null>(null);
  const [choice, setChoice] = useState('');
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  async function load() { try { setState(await sharingSubjects(apiVersion, organization.id, id)); setFailed(false); } catch { setFailed(true); } }
  useEffect(() => { if (enabled && canShare) void load(); }, [enabled, canShare, id]);
  const shared = useMemo(() => state?.subjects.filter((subject) => state.grants.some((grant) => grant.active && grant.subjectId === subject.id && grant.subjectType === subject.type)) ?? [], [state]);
  async function toggle(subject: ShareSubject, active: boolean) {
    setBusy(true);
    try { await shareConnection(apiVersion, organization.id, id, subject, active); await load(); setChoice(''); notify({ key: 'share', tone: 'ok', title: active ? `Shared with ${subject.name}` : `Stopped sharing with ${subject.name}` }); }
    catch (caught) { notify({ key: 'share', tone: 'danger', title: 'Sharing was not changed', body: errorText(caught) }); }
    finally { setBusy(false); }
  }
  if (!enabled) return <Section title='Who can use it'><p className='ws-quiet'><Share2 size={14} /> Workspace-wide. Per-account sharing isn’t enabled in this workspace, so anyone with connection access can use it, within policy.</p></Section>;
  if (!canShare) return <Section title='Who can use it'><p className='ws-quiet'><Share2 size={14} /> This account was shared with you. Only its owner can change who else can use it.</p></Section>;
  const options = state?.subjects.filter((subject) => !shared.includes(subject)) ?? [];
  const pick = options.find((subject) => `${subject.type}:${subject.id}` === choice);
  return <Section title='Who can use it' count={shared.length} description='Private to you until you share it. Sharing never skips task grants or policy.'>
    {failed ? <Notice tone='danger' action={<button type='button' className='ws-button ws-button-sm' onClick={() => void load()}>Retry</button>}>Sharing could not be loaded.</Notice> : !state ? <SkeletonLines rows={2} /> : <>
      <ul className='ws-rows'>{shared.length ? shared.map((subject) => <li key={`${subject.type}:${subject.id}`} className='ws-row ws-compact-row'>
        <Avatar name={subject.name} seed={subject.id} size={24} />
        <span className='ws-row-main'><strong>{subject.name}</strong><small>{subject.type === 'worker' ? 'Worker' : 'Person'}</small></span>
        <div className='ws-row-meta'><button type='button' className='ws-button ws-button-sm ws-button-quiet' disabled={busy} onClick={() => void toggle(subject, false)}>Stop sharing</button></div>
      </li>) : <li className='ws-quiet'>Only you can use this account.</li>}</ul>
      {options.length > 0 && <div className='ws-share-add'>
        <select aria-label='Share with' value={choice} disabled={busy} onChange={(event) => setChoice(event.target.value)}><option value=''>Share with a person or worker…</option>{options.map((subject) => <option key={`${subject.type}:${subject.id}`} value={`${subject.type}:${subject.id}`}>{subject.name} · {subject.type === 'worker' ? 'worker' : 'person'}</option>)}</select>
        <button type='button' className='ws-button ws-button-sm ws-button-primary' disabled={!pick || busy} onClick={() => pick && void toggle(pick, true)}><Share2 size={13} />Share</button>
      </div>}
    </>}
  </Section>;
}

/* ---------- Settings ---------- */

function Settings({ data, id, organization, apiVersion, onChanged }: { data: ConnectionsData; id: string; organization: OrganizationAccess; apiVersion: ApiVersion; onChanged: () => Promise<void> }) {
  const connection = data.integrations.find((item) => item.id === id)!;
  const foundation = foundationFor(data, id);
  const canManage = organization.permissions.includes('integrations.manage');
  const tool = toolFor(connection.provider);
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const githubApp = foundation?.authenticationStrategy === 'github_app';
  const ownsCredential = Boolean(foundation?.canShare);

  async function reauthorise() {
    setBusy(true);
    try {
      const { authorizationUrl } = await authorizeGitHub(apiVersion, organization.id, id);
      const destination = new URL(authorizationUrl);
      if (destination.origin !== 'https://github.com') throw new Error('Unexpected authorization destination.');
      window.location.assign(destination.href);
    } catch (caught) { notify({ key: 'reconnect', tone: 'danger', title: 'Reconnecting could not start', body: errorText(caught) }); setBusy(false); }
  }
  async function replace() {
    setBusy(true);
    try { await reconnectConnection(apiVersion, organization.id, id, secret); setSecret(''); await onChanged(); notify({ key: 'reconnect', tone: 'ok', title: 'Credential replaced and verified' }); }
    catch (caught) { notify({ key: 'reconnect', tone: 'danger', title: 'The credential was not accepted', body: errorText(caught) }); }
    finally { setBusy(false); }
  }
  async function disconnect() {
    setConfirming(false); setBusy(true);
    try { await disconnectIntegration(apiVersion, organization.id, id); await onChanged(); notify({ key: 'disconnect', tone: 'ok', title: `${connection.displayName} disconnected` }); navigate({ page: 'connections', view: 'home' }); }
    catch (caught) { notify({ key: 'disconnect', tone: 'danger', title: 'Disconnect failed', body: errorText(caught) }); }
    finally { setBusy(false); }
  }

  return <>
    <Section title='Credential' description={connection.credential.mode === 'encrypted_secret' ? `Encrypted · fingerprint ${connection.credential.fingerprint ?? 'unknown'}` : 'No credential is stored.'}>
      {connection.status === 'disconnected' ? <p className='ws-quiet'>Disconnected. Connect again from the directory to restore access.</p>
        : !data.foundation?.enabled || !foundation ? <p className='ws-quiet'>Changing the credential in place isn’t enabled in this workspace. To change it, disconnect and connect {tool?.name ?? 'the tool'} again.</p>
        : !ownsCredential ? <p className='ws-quiet'>This account was shared with you. Only its owner can change the credential.</p>
        : githubApp ? <div className='ws-setting-row'><span><strong>Reauthorise the GitHub App</strong><small>Sign in to GitHub again to restore or refresh access{foundation.authorizationState === 'reconnect_required' ? '. Needed now.' : '.'}</small></span><button type='button' className='ws-button' disabled={busy || !canManage} onClick={() => void reauthorise()}><ToolMark provider='github' size='sm' />Reconnect GitHub</button></div>
        : <form className='ws-setting-row' onSubmit={(event) => { event.preventDefault(); void replace(); }}>
          <span><strong>{foundation.authorizationState === 'reconnect_required' ? 'Reconnect with a new credential' : 'Rotate the credential'}</strong><small>The new credential is verified with {tool?.name ?? 'the tool'} before the old one is replaced.</small></span>
          <span className='ws-input-row'><input type='password' autoComplete='off' value={secret} disabled={busy || !canManage} onChange={(event) => setSecret(event.target.value)} placeholder='New token' aria-label='New credential' /><button type='submit' className='ws-button' disabled={busy || !secret || !canManage}>{busy ? <Loader2 size={14} className='cf-spin' /> : <ShieldCheck size={14} />}Verify & replace</button></span>
        </form>}
    </Section>
    {connection.provider === 'github' && data.github?.installationUrl && <Section title='Repository access'><div className='ws-setting-row'><span><strong>Change which repositories are included</strong><small>Repository selection lives in the GitHub App installation.</small></span><a className='ws-button' href={data.github.installationUrl} target='_blank' rel='noreferrer'>Manage on GitHub<ExternalLink size={13} /></a></div></Section>}
    <section className='ws-danger-zone ws-danger-block'>
      <div><strong>Disconnect {connection.displayName}</strong><p>Workers lose access straight away and the stored credential is removed.{connection.provider === 'github' && githubApp ? ' The GitHub App installation itself stays on GitHub.' : ''} History stays in the audit trail.</p></div>
      <button type='button' className='ws-button ws-button-danger' disabled={busy || !canManage || connection.status === 'disconnected'} onClick={() => setConfirming(true)}><Unplug size={14} />Disconnect</button>
    </section>
    <Confirm open={confirming} tone='danger' title={`Disconnect ${connection.displayName}?`} confirmLabel='Disconnect' onCancel={() => setConfirming(false)} onConfirm={() => void disconnect()}
      body={<><p>{identitiesFor(data, id).length ? `${identitiesFor(data, id).length} identit${identitiesFor(data, id).length === 1 ? 'y declares' : 'ies declare'} scopes on this connection; their requests will start failing.` : 'No identity declares scopes on this connection.'}</p><p>The stored credential is deleted. You can connect again later.</p></>} />
    <p className='ws-muted ws-small'>Connection <code>{connection.id}</code> · created <Ago value={connection.createdAt} /> · updated <Ago value={connection.updatedAt} /></p>
  </>;
}
