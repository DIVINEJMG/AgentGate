import { useEffect, useState } from 'react';
import { auth } from '../platform/authClient';
import { authorizeGitHub, connectGitHubInstallation, githubInstallations, githubReadiness, type GitHubReadiness } from '../lib/githubApi';
import type { ApiVersion } from '../lib/systemApi';
import type { IntegrationConnection } from '../lib/integrationApi';

export default function GitHubConnection({ organizationId, version, onConnected, connections, onInspect, onRemove, onLegacyConnect, working }: {
  organizationId: string; version: ApiVersion; onConnected: () => void;
  connections: IntegrationConnection[]; onInspect: (id: string) => void;
  onRemove: (id: string) => Promise<void>; onLegacyConnect: () => void; working: boolean;
}) {
  const [readiness, setReadiness] = useState<GitHubReadiness | null>(null);
  const [installations, setInstallations] = useState<{ id: string; account: string; suspended: boolean }[]>([]);
  const [installationsLoaded, setInstallationsLoaded] = useState(false);
  const [choice, setChoice] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const session = new URLSearchParams(window.location.search).get('github_setup');
  useEffect(() => {
    let active = true;
    setInstallationsLoaded(false); setInstallations([]); setChoice(''); setError('');
    void githubReadiness(version, organizationId).then(async r => {
      if (!active) return;
      setReadiness(r);
      if (r.enabled && session) {
        const response = await githubInstallations(version, organizationId, session);
        if (active) {setInstallations(response.installations);setInstallationsLoaded(true);}
      }
    }).catch(() => { if (active) setError('GitHub setup could not be loaded. Check backend availability and sign in with the account that started it.'); });
    return () => { active = false; };
  }, [organizationId, version, session]);
  async function connect() {
    setBusy(true); setError('');
    try {
      if (session) {
        await connectGitHubInstallation(version, organizationId, session, choice);
        const url = new URL(window.location.href); url.searchParams.delete('github_setup');
        window.history.replaceState({}, '', url); setInstallations([]); onConnected();
      } else {
        const { authorizationUrl } = await authorizeGitHub(version, organizationId);
        const url = new URL(authorizationUrl);
        if (url.origin !== 'https://github.com') throw new Error('Unexpected authorization destination.');
        window.location.assign(url.href);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'GitHub setup could not complete. Check App configuration, installation access and account ownership.');
    }
    finally { setBusy(false); }
  }
  return <div className="cx-operations">
    {connections.map(connection => <div className="cx-file-actions" key={connection.id}>
      <strong>{connection.displayName}</strong>
      <button type="button" disabled={working || busy} onClick={() => onInspect(connection.id)}>Edit access & resources</button>
      <button type="button" className="danger" disabled={working || busy} onClick={() => void onRemove(connection.id)}>Remove</button>
    </div>)}
    {readiness?.enabled ? <>
    {!readiness.authenticationConfigured && <p role="status">{readiness.credentialVaultConfigured === false ? 'Secure credential storage is unavailable. Ask your administrator to configure it before connecting.' : 'GitHub App credentials and its HTTPS callback must be configured before connecting.'}</p>}
    {session && installations.length > 0 && <label>Choose your installation <select value={choice} disabled={busy} onChange={e => setChoice(e.target.value)}>
      <option value="">Select account</option>{installations.map(i => <option key={i.id} value={i.id} disabled={i.suspended}>{i.account}{i.suspended ? ' · suspended' : ''}</option>)}
    </select></label>}
    {session && !installationsLoaded && !error && <p role="status">Checking repository installations...</p>}
    {session && installationsLoaded && installations.length === 0 && <p role="status">No installation of this GitHub App is available to your GitHub account. Install the App for the repositories you want to connect, then refresh installations. You are already signed in.</p>}
    {session && installationsLoaded && installations.length > 0 && installations.every(i => i.suspended) && <p role="status">All available installations are suspended. Restore the installation in GitHub, then refresh.</p>}
    {session && installationsLoaded && installations.length === 0 && !readiness.installationUrl && <p role="status">The App installation link is unavailable. Ask your workspace administrator for the GitHub App's installation page.</p>}
    {(!session || installations.length > 0) && <button type="button" className="secondary-button" disabled={busy || !readiness.authenticationConfigured || (!!session && !choice)} onClick={() => void connect()}>{busy ? 'Connecting…' : session ? 'Connect installation' : 'Connect GitHub App'}</button>}
    {session && <button type="button" disabled={busy} onClick={async () => {
      setBusy(true);setError('');
      try {const result=await githubInstallations(version, organizationId, session);setInstallations(result.installations);setInstallationsLoaded(true);setChoice(current => result.installations.some(i => i.id === current && !i.suspended) ? current : '');}
      catch(cause) {setError(cause instanceof Error ? cause.message : 'Installations could not be refreshed.');}
      finally {setBusy(false);}
    }}>Refresh installations</button>}
    {readiness.installationUrl && <a href={readiness.installationUrl} target="_blank" rel="noreferrer">{session && installationsLoaded && installations.length === 0 ? 'Install GitHub App' : 'Manage App installation'}</a>}
    </> : readiness ? <button type="button" onClick={onLegacyConnect}>Connect GitHub</button> : <p role="status">Checking GitHub connection setup…</p>}
    {readiness?.signInLinked && <p role="status">GitHub is linked for sign-in. Repository access is managed separately through an App installation.</p>}
    {readiness?.loginEnabled && !readiness.signInLinked && <><p>Link GitHub to sign in, then choose where to connect its repositories.</p><button type="button" className="secondary-button" disabled={busy || working} onClick={async () => {
      setBusy(true);setError('');
      try {await auth.startGitHub('link');} catch (cause) {setError(cause instanceof Error ? cause.message : 'GitHub linking could not start.');setBusy(false);}
    }}>Link GitHub for sign-in</button></>}
    {error && <p className="inline-error" role="alert">{error}</p>}
  </div>;
}
