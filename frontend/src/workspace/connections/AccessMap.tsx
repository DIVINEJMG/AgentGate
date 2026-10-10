import { useEffect, useState } from 'react';
import { Grid3x3 } from 'lucide-react';
import { saveAgentCapabilityProfile } from '../../lib/capabilityApi';
import type { OrganizationAccess } from '../../lib/identityApi';
import type { ApiVersion } from '../../lib/systemApi';
import { notify } from '../feedback';
import { linkProps } from '../routes';
import { EmptyState, Notice, Sheet, SkeletonLines, sentence } from '../ui';
import { ConnectionsHeader, ToolMark } from './Chrome';
import { actionsFor, errorText, RISK_TONE, scopesFor, useConnections } from './data';

/** Identities against connections: how many of each connection's scopes every identity declares. */
export default function AccessMap({ organization, apiVersion }: { organization: OrganizationAccess; apiVersion: ApiVersion }) {
  const { data, error, patch } = useConnections(organization, apiVersion);
  const [cell, setCell] = useState<{ agentId: string; connectionId: string } | null>(null);
  const [draft, setDraft] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const canManage = organization.permissions.includes('capabilities.manage');
  const connections = data?.integrations.filter((item) => item.status !== 'disconnected') ?? [];

  const scopes = data && cell ? scopesFor(data, cell.connectionId) : [];
  const declared = data && cell ? data.profiles[cell.agentId]?.declaredScopes ?? [] : [];
  useEffect(() => { if (cell) setDraft(declared.filter((scope) => scopes.includes(scope))); }, [cell?.agentId, cell?.connectionId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    if (!cell || !data) return;
    setSaving(true);
    try {
      const next = [...declared.filter((scope) => !scopes.includes(scope)), ...draft].sort();
      const profile = await saveAgentCapabilityProfile(apiVersion, organization.id, cell.agentId, next);
      patch((current) => ({ ...current, profiles: { ...current.profiles, [cell.agentId]: profile } }));
      notify({ key: 'access-map', tone: 'ok', title: 'Access saved' });
      setCell(null);
    } catch (caught) { notify({ key: 'access-map', tone: 'danger', title: 'Access was not saved', body: errorText(caught) }); }
    finally { setSaving(false); }
  }

  const agent = data?.agents.find((item) => item.id === cell?.agentId);
  const connection = connections.find((item) => item.id === cell?.connectionId);
  const stale = data ? Object.values(data.profiles).reduce((sum, profile) => sum + profile.staleScopes.length, 0) : 0;

  return <div className='ws-page ws-page-wide'>
    <ConnectionsHeader active='access' count={connections.length} />
    {error && <Notice tone='danger'>{error}</Notice>}
    <p className='ws-summary-line'>Who can reach what, at a glance. Each cell counts the scopes an identity declares on a connection. Declaring a scope is <strong>not</strong> permission: policies, risk and approvals still decide every request.</p>
    {stale > 0 && <Notice tone='warn'>{stale} declared scope{stale === 1 ? ' no longer matches' : 's no longer match'} a connected tool. Open the identity to review.</Notice>}
    {!data ? <SkeletonLines rows={5} /> : !data.agents.length || !connections.length ? <EmptyState icon={<Grid3x3 size={18} />} title={!connections.length ? 'No connections yet' : 'No agent identities yet'}>{!connections.length ? <>Connect a tool from the <a className='ws-link' {...linkProps({ page: 'connections', view: 'directory' })}>directory</a> first.</> : <>Register an identity under <a className='ws-link' {...linkProps({ page: 'connections', view: 'identities' })}>Agent identities</a>.</>}</EmptyState> : <div className='ws-table-wrap'>
      <table className='ws-matrix'>
        <thead><tr><th scope='col'>Identity</th>{connections.map((item) => <th key={item.id} scope='col'><a {...linkProps({ page: 'connections', view: 'account', id: item.id })}><ToolMark provider={item.provider} size='sm' /><span>{item.displayName}</span></a></th>)}</tr></thead>
        <tbody>{data.agents.map((row) => <tr key={row.id}>
          <th scope='row'><a {...linkProps({ page: 'connections', view: 'identities', id: row.id })}><strong>{row.name}</strong><small>{sentence(row.status)}</small></a></th>
          {connections.map((item) => {
            const total = scopesFor(data, item.id);
            const count = (data.profiles[row.id]?.declaredScopes ?? []).filter((scope) => total.includes(scope)).length;
            const level = !total.length ? 'none' : count === 0 ? 'empty' : count === total.length ? 'full' : 'part';
            return <td key={item.id}><button type='button' className='ws-matrix-cell' data-level={level} disabled={!total.length} onClick={() => setCell({ agentId: row.id, connectionId: item.id })} aria-label={`${row.name} on ${item.displayName}: ${count} of ${total.length} scopes. ${canManage ? 'Edit' : 'View'}.`}>
              <span className='ws-matrix-bar'><i style={{ width: total.length ? `${count / total.length * 100}%` : 0 }} /></span><b>{total.length ? `${count}/${total.length}` : '—'}</b>
            </button></td>;
          })}
        </tr>)}</tbody>
      </table>
    </div>}

    <Sheet open={Boolean(cell && agent && connection)} onClose={() => setCell(null)} title={agent && connection ? `${agent.name} on ${connection.displayName}` : ''} subtitle='Scopes this identity expects to use on this connection.'
      footer={canManage ? <><button type='button' className='ws-button' onClick={() => setCell(null)}>Cancel</button><button type='button' className='ws-button ws-button-primary' disabled={saving} onClick={() => void save()}>{saving ? 'Saving…' : 'Save'}</button></> : <button type='button' className='ws-button' onClick={() => setCell(null)}>Close</button>}>
      {data && cell && <ul className='ws-rows ws-scope-list'>{actionsFor(data, cell.connectionId).map((action) => {
        const checked = draft.includes(action.scope);
        return <li key={action.scope} className='ws-row ws-scope-row' data-checked={checked ? 'true' : undefined}>
          <input type='checkbox' id={`map-${action.scope}`} checked={checked} disabled={!canManage} onChange={() => setDraft((current) => current.includes(action.scope) ? current.filter((item) => item !== action.scope) : [...current, action.scope])} />
          <label className='ws-row-main' htmlFor={`map-${action.scope}`}><strong><code>{action.scope}</code></strong><small>{action.description}</small></label>
          <div className='ws-row-meta'><span className='ws-state' data-tone={RISK_TONE[action.risk]}>{sentence(action.risk)}</span></div>
        </li>;
      })}</ul>}
    </Sheet>
  </div>;
}
