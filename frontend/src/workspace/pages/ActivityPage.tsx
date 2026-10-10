import { useEffect, useMemo, useState } from 'react';
import { Activity, Check, Copy, Hand, KeyRound, OctagonX, Play, RefreshCw, Search, TriangleAlert } from 'lucide-react';
import { listActions, runGatewayTest, type ActionRecord } from '../../lib/actionApi';
import { listAgents, type AgentIdentity } from '../../lib/agentApi';
import { listCapabilityCatalog, type CapabilityCatalog } from '../../lib/capabilityApi';
import type { OrganizationAccess } from '../../lib/identityApi';
import type { ApiVersion } from '../../lib/systemApi';
import { notify } from '../feedback';
import { usePageTitle } from '../history';
import { linkProps, navigate, queryParam, setQuery } from '../routes';
import { Ago, Avatar, EmptyState, Notice, PageHeader, Pills, Sheet, SkeletonLines, humanize } from '../ui';

type Filter = 'all' | 'executed' | 'held' | 'blocked' | 'failed';
const OUTCOME: Record<string, { label: string; tone: 'ok' | 'warn' | 'danger' | 'neutral' }> = {
  executed: { label: 'Allowed', tone: 'ok' }, held: { label: 'Held for approval', tone: 'warn' }, blocked: { label: 'Blocked', tone: 'danger' }, failed: { label: 'Failed', tone: 'danger' },
};
const newKey = () => `gate_${crypto.randomUUID()}`;
function errorText(value: unknown) {
  const data = value as { response?: { data?: { error?: string } }; message?: string };
  return data.response?.data?.error || data.message || 'Action Gateway request failed.';
}
const operation = (item: ActionRecord) => humanize(item.request.providerOperation || item.request.action || item.request.scope);

export default function ActivityPage({ organization, apiVersion, actionId }: { organization: OrganizationAccess; apiVersion: ApiVersion; actionId?: string }) {
  const [actions, setActions] = useState<ActionRecord[]>([]);
  const [agents, setAgents] = useState<AgentIdentity[]>([]);
  const [catalog, setCatalog] = useState<CapabilityCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [testOpen, setTestOpen] = useState(false);
  const [search, setSearch] = useState('');
  const canManage = organization.permissions.includes('actions.manage');
  const filter = (['executed', 'held', 'blocked', 'failed'].includes(queryParam('filter')) ? queryParam('filter') : 'all') as Filter;

  async function hydrate() {
    setError(null);
    try {
      const [nextActions, nextAgents, nextCatalog] = await Promise.all([listActions(apiVersion, organization.id), listAgents(apiVersion, organization.id), listCapabilityCatalog(apiVersion, organization.id)]);
      setActions(nextActions); setAgents(nextAgents); setCatalog(nextCatalog);
    } catch (caught) { setError(errorText(caught)); } finally { setLoading(false); }
  }
  useEffect(() => { setLoading(true); void hydrate(); }, [apiVersion, organization.id]);

  const counts = useMemo(() => {
    const tally = { executed: 0, held: 0, blocked: 0, failed: 0 };
    for (const item of actions) if (item.status in tally) tally[item.status as keyof typeof tally] += 1;
    return tally;
  }, [actions]);
  const term = search.trim().toLowerCase();
  const visible = actions
    .filter((item) => filter === 'all' || item.status === filter)
    .filter((item) => !term || `${item.agent.name} ${item.request.resourceName ?? ''} ${item.request.scope} ${item.request.providerOperation ?? ''}`.toLowerCase().includes(term))
    .sort((a, b) => Date.parse(b.requestedAt) - Date.parse(a.requestedAt));
  const selected = actions.find((item) => item.id === actionId) ?? null;
  usePageTitle(selected ? operation(selected) : null);
  const at = (id?: string) => ({ page: 'activity' as const, actionId: id });

  // On wide screens, open the newest action so the detail pane is never empty.
  useEffect(() => {
    if (loading || actionId || !visible[0] || window.matchMedia('(max-width: 860px)').matches) return;
    navigate(`${linkProps(at(visible[0].id)).href}${window.location.search}`, { replace: true, keepScroll: true });
  }, [loading, actionId, visible[0]?.id]);

  return <div className='ws-page ws-page-wide'>
    <PageHeader title='Activity' description='Every action your workers attempted, and what the gateway decided before anything reached a tool.'
      actions={<>
        <button type='button' className='ws-button' onClick={() => { setLoading(true); void hydrate(); }} disabled={loading}><RefreshCw size={15} />Refresh</button>
        {canManage && <button type='button' className='ws-button' onClick={() => setTestOpen(true)}><Play size={15} />Test the gateway</button>}
      </>} />
    {error && <Notice tone='danger' action={<button type='button' className='ws-button ws-button-sm' onClick={() => void hydrate()}>Retry</button>}>{error}</Notice>}

    <div className='ws-inbox' data-detail={selected ? 'true' : undefined}>
      <div className='ws-inbox-list'>
        <Pills label='Outcome' value={filter} onChange={(next) => setQuery({ filter: next === 'all' ? undefined : next })} options={[
          { value: 'all', label: 'All', count: actions.length },
          { value: 'executed', label: 'Allowed', count: counts.executed },
          { value: 'held', label: 'Held', count: counts.held },
          { value: 'blocked', label: 'Blocked', count: counts.blocked },
          { value: 'failed', label: 'Failed', count: counts.failed },
        ]} />
        <label className='ws-search-field ws-search-full'><Search size={15} aria-hidden='true' /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder='Search worker, tool or operation' aria-label='Search activity' /></label>
        {loading ? <SkeletonLines rows={6} /> : visible.length === 0
          ? <EmptyState icon={<Activity size={18} />} title={actions.length ? 'Nothing in this view' : 'No actions yet'}>{actions.length ? 'Try another outcome or search.' : 'When a worker reaches for a tool, the attempt and the decision appear here.'}</EmptyState>
          : <ul className='ws-rows'>{visible.map((item) => {
            const outcome = OUTCOME[item.status] ?? { label: humanize(item.status), tone: 'neutral' as const };
            return <li key={item.id} className='ws-row ws-inbox-row' aria-current={item.id === actionId ? 'true' : undefined}>
              <span className='ws-outcome-icon' data-tone={outcome.tone} aria-hidden='true'><OutcomeIcon status={item.status} /></span>
              <a className='ws-row-main ws-row-link' {...linkProps(at(item.id))} onClick={(event) => { if (event.metaKey || event.ctrlKey || event.shiftKey) return; event.preventDefault(); navigate(linkProps(at(item.id)).href + window.location.search, { keepScroll: true }); }}>
                <strong>{operation(item)}</strong><small>{item.agent.name} · {item.request.resourceName ?? item.request.resourceId}</small>
              </a>
              <div className='ws-inbox-row-meta'><span className='ws-state' data-tone={outcome.tone}>{outcome.label}</span><Ago value={item.requestedAt} /></div>
            </li>;
          })}</ul>}
      </div>
      <div className='ws-inbox-detail'>
        {selected ? <>
          <a className='ws-back ws-inbox-back' {...linkProps(at())}>← Activity</a>
          <ActionDetail item={selected} />
        </> : <div className='ws-inbox-placeholder'><Activity size={20} /><span>{loading ? 'Loading activity…' : 'Select an action to see how the gateway decided.'}</span></div>}
      </div>
    </div>

    {canManage && <GatewayTest open={testOpen} onClose={() => setTestOpen(false)} organization={organization} apiVersion={apiVersion} agents={agents} catalog={catalog} onResult={(result) => { setActions((current) => [result, ...current.filter((item) => item.id !== result.id)]); setTestOpen(false); navigate(linkProps(at(result.id)).href); }} />}
  </div>;
}

function OutcomeIcon({ status }: { status: string }) {
  if (status === 'executed') return <Check size={14} strokeWidth={2.4} />;
  if (status === 'held') return <Hand size={13} strokeWidth={2.2} />;
  if (status === 'blocked') return <OctagonX size={14} strokeWidth={2.2} />;
  return <TriangleAlert size={14} strokeWidth={2.2} />;
}

function ActionDetail({ item }: { item: ActionRecord }) {
  const outcome = OUTCOME[item.status] ?? { label: humanize(item.status), tone: 'neutral' as const };
  const policyStep = item.policy.outcome === 'ALLOW' ? 'ok' : item.policy.outcome === 'DENY' ? 'danger' : 'warn';
  const steps: Array<{ label: string; value: string; tone: string }> = [
    { label: 'Identity', value: item.agent.name, tone: 'ok' },
    { label: 'Capability', value: item.request.scope, tone: item.status === 'blocked' && !item.policy.winningPolicy ? 'danger' : 'ok' },
    { label: 'Policy', value: humanize(item.policy.outcome.toLowerCase()), tone: policyStep },
    { label: 'Tool', value: item.status === 'executed' ? 'Reached and executed' : item.status === 'held' ? 'Waiting for approval' : item.status === 'failed' ? 'Reached, then failed' : 'Not reached', tone: item.status === 'executed' ? 'ok' : item.status === 'held' ? 'warn' : 'danger' },
  ];
  const copy = () => { void navigator.clipboard.writeText(JSON.stringify(item.result?.data ?? null, null, 2)).then(() => notify({ key: 'copy', tone: 'ok', title: 'Result copied' })); };
  return <div className='ws-detail' key={item.id}>
    <div className='ws-detail-head'>
      <Avatar name={item.agent.name} seed={item.agent.id} size={40} />
      <div>
        <div className='ws-muted'>{item.agent.name} · <Ago value={item.requestedAt} /></div>
        <h2>{operation(item)}</h2>
        <div className='ws-detail-sub'><span className='ws-state' data-tone={outcome.tone}>{outcome.label}</span>{item.request.risk && <span className='ws-muted'>{humanize(item.request.risk)} risk</span>}</div>
      </div>
    </div>
    <p className='ws-detail-why'>{item.agent.name} asked to <strong>{humanize(item.request.action || item.request.scope)}</strong> on <strong>{item.request.resourceName ?? item.request.resourceId}</strong>{item.request.target ? <> ({item.request.target})</> : null}. {item.error || item.policy.reason}</p>
    <ol className='ws-path' aria-label='Gateway path'>{steps.map((step, index) => <li key={step.label} data-tone={step.tone}><span className='ws-path-dot'>{index + 1}</span><strong>{step.label}</strong><small>{step.value}</small></li>)}</ol>
    <dl className='ws-facts'>
      <div><dt>Deciding policy</dt><dd>{item.policy.winningPolicy ? <>{item.policy.winningPolicy.name}<small>{humanize(item.policy.winningPolicy.effect)} · priority {item.policy.winningPolicy.priority}</small></> : 'Default deny or precondition'}</dd></div>
      <div><dt>Policies matched</dt><dd>{item.policy.matchedPolicies.length || 'None'}</dd></div>
      <div><dt>Completed</dt><dd>{item.completedAt ? new Date(item.completedAt).toLocaleString() : '—'}</dd></div>
    </dl>
    {item.result && <section className='ws-section'>
      <div className='ws-section-head'><h2>What the tool returned</h2><div className='ws-section-action'><button type='button' className='ws-button ws-button-sm' onClick={copy}><Copy size={13} />Copy JSON</button></div></div>
      <p className='ws-muted'>{item.result.summary}</p>
      <pre className='ws-code'>{JSON.stringify(item.result.data, null, 2)}</pre>
    </section>}
    <details className='ws-technical'><summary>Technical detail</summary><dl className='ws-facts'>
      <div><dt>Correlation</dt><dd><code>{item.correlationId}</code></dd></div>
      <div><dt>Idempotency</dt><dd><code>{item.idempotencyHash}</code></dd></div>
      <div><dt>Credential</dt><dd><code>{item.agent.credentialFingerprint}</code></dd></div>
      {item.result?.providerRequestId && <div><dt>Provider request</dt><dd><code>{item.result.providerRequestId}</code></dd></div>}
    </dl></details>
  </div>;
}

function GatewayTest({ open, onClose, organization, apiVersion, agents, catalog, onResult }: { open: boolean; onClose: () => void; organization: OrganizationAccess; apiVersion: ApiVersion; agents: AgentIdentity[]; catalog: CapabilityCatalog | null; onResult: (result: ActionRecord) => void }) {
  const [agentId, setAgentId] = useState('');
  const [resourceId, setResourceId] = useState('');
  const [scope, setScope] = useState('');
  const [credential, setCredential] = useState('');
  const [idempotencyKey, setIdempotencyKey] = useState(newKey);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const resource = catalog?.resources.find((item) => item.id === resourceId) ?? null;
  useEffect(() => { setAgentId((current) => current && agents.some((item) => item.id === current) ? current : agents[0]?.id ?? ''); }, [agents]);
  useEffect(() => { setResourceId((current) => current && catalog?.resources.some((item) => item.id === current) ? current : catalog?.resources[0]?.id ?? ''); }, [catalog]);
  useEffect(() => { if (!resource) { setScope(''); return; } if (!resource.actions.some((item) => item.scope === scope)) setScope(resource.actions[0]?.scope ?? ''); }, [resourceId, catalog]);
  useEffect(() => { if (!open) setCredential(''); }, [open]);

  async function run() {
    if (!agentId || !resourceId || !scope || !credential) return;
    setRunning(true); setError(null);
    try { const result = await runGatewayTest(apiVersion, organization.id, { agentId, credential, resourceId, scope, idempotencyKey }); setCredential(''); setIdempotencyKey(newKey()); onResult(result); }
    catch (caught) { setError(errorText(caught)); } finally { setRunning(false); }
  }
  return <Sheet open={open} onClose={onClose} title='Test the gateway' subtitle='Send one controlled action through identity, capability, policy and approval checks.'
    footer={<><button type='button' className='ws-button' onClick={onClose}>Cancel</button><button type='button' className='ws-button ws-button-primary' disabled={running || !agentId || !resourceId || !scope || !credential} onClick={() => void run()}><Play size={15} />{running ? 'Running…' : 'Run through gateway'}</button></>}>
    <div className='ws-form'>
      <Notice tone='info'><KeyRound size={15} /> The agent secret is used for this one request and is never saved.</Notice>
      {error && <Notice tone='danger'>{error}</Notice>}
      <label className='ws-field'>Agent identity<select value={agentId} onChange={(event) => setAgentId(event.target.value)}><option value=''>Choose an agent</option>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name} · {agent.status}</option>)}</select></label>
      <label className='ws-field'>Resource<select value={resourceId} onChange={(event) => setResourceId(event.target.value)}><option value=''>Choose a resource</option>{catalog?.resources.map((item) => <option key={item.id} value={item.id}>{item.displayName}</option>)}</select></label>
      <label className='ws-field'>Scope<select value={scope} onChange={(event) => setScope(event.target.value)}><option value=''>Choose a scope</option>{resource?.actions.map((item) => <option key={item.scope} value={item.scope}>{item.scope}</option>)}</select></label>
      <label className='ws-field'>Agent secret<input type='password' autoComplete='off' value={credential} onChange={(event) => setCredential(event.target.value)} placeholder='Paste the agent secret' /></label>
      <label className='ws-field'>Idempotency key<span className='ws-input-row'><input value={idempotencyKey} onChange={(event) => setIdempotencyKey(event.target.value)} /><button type='button' className='ws-icon-button' aria-label='Generate a new key' onClick={() => setIdempotencyKey(newKey())}><RefreshCw size={14} /></button></span><small>Reusing a key replays the earlier decision instead of acting twice.</small></label>
    </div>
  </Sheet>;
}
