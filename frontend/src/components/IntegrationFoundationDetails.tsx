import { useEffect, useState } from 'react';
import type { ApiVersion } from '../lib/systemApi';
import { authorizeGitHub } from '../lib/githubApi';
import {
  discoverConnectionResources, foundationConnections, foundationReadiness, foundationResources,
  reconnectConnection, shareConnection, sharingSubjects,
  type FoundationConnection, type FoundationResource, type ShareSubject,
} from '../lib/integrationFoundationApi';

export default function IntegrationFoundationDetails({ organizationId, version, connectionId }: {
  organizationId: string; version: ApiVersion; connectionId: string;
}) {
  const [connection, setConnection] = useState<FoundationConnection | null>(null);
  const [resources, setResources] = useState<FoundationResource[]>([]);
  const [subjects, setSubjects] = useState<ShareSubject[]>([]);
  const [grants, setGrants] = useState<{ subjectId: string; subjectType: string; active: boolean }[]>([]);
  const [selected, setSelected] = useState('');
  const [credential, setCredential] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function load() {
    if (!(await foundationReadiness(version, organizationId)).enabled) return;
    const [connections, catalog] = await Promise.all([
      foundationConnections(version, organizationId), foundationResources(version, organizationId),
    ]);
    const current = connections.find(c => c.id === connectionId) ?? null;
    setConnection(current); setResources(catalog.filter(r => r.connectionId === connectionId));
    if (current?.canShare) {
      const sharing = await sharingSubjects(version, organizationId, connectionId);
      setSubjects(sharing.subjects); setGrants(sharing.grants);
    }
  }
  useEffect(() => { setConnection(null); setError(''); void load().catch(() => setError('Connection readiness could not be loaded.')); }, [organizationId, version, connectionId]);
  async function perform(operation: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await operation(); setCredential(''); await load(); }
    catch { setError('The connection action could not be completed. Check access and provider readiness.'); }
    finally { setBusy(false); }
  }
  if (!connection) return error ? <p className="inline-error" role="alert">{error}</p> : null;
  const recipient = subjects.find(s => `${s.type}:${s.id}` === selected);
  const isShared = !!recipient && grants.some(g => g.subjectId === recipient.id && g.subjectType === recipient.type && g.active);
  return <div className="cx-operations">
    <span>ACCOUNT ACCESS & RESOURCES</span>
    <p>{connection.authorizationState.replaceAll('_', ' ')}{connection.reason ? ` · ${connection.reason}` : ''}</p>
    <small>{connection.canShare ? 'Private to you until explicitly shared. Task grants still limit each action.' : 'Shared account. Only its owner can change credentials or sharing.'}</small>
    <ul>{resources.map(r => <li key={r.id}>{r.name} · {r.health} <small>{r.capabilities.length} available capabilities</small></li>)}</ul>
    <button type="button" className="secondary-button" disabled={busy} onClick={() => void perform(() => discoverConnectionResources(version, organizationId, connectionId))}>Refresh resources</button>
    {connection.canShare && <>
      <label htmlFor="integration-share-recipient">Share with a member or worker</label>
      <select id="integration-share-recipient" value={selected} onChange={e => setSelected(e.target.value)} disabled={busy}>
        <option value="">Choose recipient</option>{subjects.map(s => <option key={`${s.type}:${s.id}`} value={`${s.type}:${s.id}`}>{s.name} · {s.type}</option>)}
      </select>
      <button type="button" className="secondary-button" disabled={busy || !recipient} onClick={() => recipient && void perform(() => shareConnection(version, organizationId, connectionId, recipient, !isShared))}>{isShared ? 'Revoke sharing' : 'Share account'}</button>
      {connection.authorizationState === 'reconnect_required' && connection.authenticationStrategy === 'github_app' && <button type="button" className="secondary-button" disabled={busy} onClick={() => void perform(async () => {
        const { authorizationUrl } = await authorizeGitHub(version, organizationId, connectionId);
        const destination = new URL(authorizationUrl);
        if (destination.origin !== 'https://github.com') throw new Error('Unexpected authorization destination.');
        window.location.assign(destination.href);
      })}>Reconnect GitHub App</button>}
      {connection.authorizationState === 'reconnect_required' && connection.authenticationStrategy !== 'github_app' && <form onSubmit={e => { e.preventDefault(); void perform(() => reconnectConnection(version, organizationId, connectionId, credential)); }}>
        <label htmlFor="integration-reconnect-secret">Replacement provider credential</label><input id="integration-reconnect-secret" type="password" autoComplete="off" value={credential} onChange={e => setCredential(e.target.value)} />
        <button type="submit" className="secondary-button" disabled={busy || !credential}>Validate & reconnect</button>
      </form>}
    </>}
    {error && <p className="inline-error" role="alert">{error}</p>}
  </div>;
}
