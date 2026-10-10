import { useEffect, useState } from 'react';
import { cancelCoding, codingDiff, codingStatus, type CodingSession } from '../lib/githubApi';
import type { ApiVersion } from '../lib/systemApi';
import { startActivityFeed } from './taskActivityFeed';

export default function CodingProgress({ organizationId, version, workItemId, canCancel }: {
  organizationId: string; version: ApiVersion; workItemId: string; canCancel: boolean;
}) {
  const [sessions, setSessions] = useState<CodingSession[]>([]), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [diffs, setDiffs] = useState<Record<string, string>>({});
  const [refresh, setRefresh] = useState<(() => void) | null>(null);
  useEffect(() => {
    let active = true;
    setSessions([]); setDiffs({}); setError('');
    // Workspace evidence and cleanup must not depend on integration-management readiness.
    const feed = startActivityFeed({
        load: signal => codingStatus(version, organizationId, workItemId, signal),
        nextIntervalMs: snapshot => snapshot.sessions.some(s => ['creating', 'creating_uncertain', 'running'].includes(s.status)
          || s.commands.some(c => ['starting', 'running'].includes(c.status))) ? 15000 : 60000,
        onSnapshot: snapshot => { if (active) { setSessions(snapshot.sessions); setError(''); } },
        onConnectivity: connected => { if (active && !connected) setError('Workspace status could not load. Check your connection and account access, then retry. Previously saved evidence is retained.'); },
    });
    setRefresh(() => () => { void feed.refresh(); });
    return () => { active = false; feed.stop(); };
  }, [organizationId, version, workItemId]);
  async function cancel() {
    setBusy(true); setError('');
    try { await cancelCoding(version, organizationId, workItemId); setSessions((await codingStatus(version, organizationId, workItemId)).sessions); }
    catch { setError('Cancellation could not complete. Saved work is retained; try again shortly.'); }
    finally { setBusy(false); }
  }
  if (!sessions.length && !error) return null;
  return <details className="jobs-queue-management"><summary>Coding workspace · {sessions.length ? sessions.map(s => s.status).join(', ') : 'status unavailable'}</summary>
    {sessions.map(s => <section key={s.id}><strong>{s.resource}</strong><p>{s.status} · base {s.baseSha.slice(0, 12)} · {Math.ceil(s.activeSeconds / 60)} active min</p>
      {['creating', 'creating_uncertain'].includes(s.status) && s.initialization?.stage && <p>
        {({ snapshot: 'Downloading repository source', validation: 'Validating source', sandbox_creation: 'Preparing workspace',
          bundle_transfer: 'Uploading source', verification: 'Verifying source' } as Record<string, string>)[s.initialization.stage] ?? 'Preparing workspace'}
        {s.initialization.stage === 'bundle_transfer' && ` · ${s.initialization.confirmedBundles}/${s.initialization.totalBundles} bundles`}
      </p>}
      <small>Commands below identify the source revision and actual exit status. Publication and PR outcomes are separate.</small>
      {s.commands.map(c => <details key={c.id}><summary>{c.command} · {c.status}{c.output.exitCode !== undefined ? ` · exit ${c.output.exitCode}` : ''}</summary>
        {c.output.sourceFingerprint && <small>Workspace fingerprint {c.output.sourceFingerprint.slice(0, 12)}</small>}
        <pre>{c.output.stdout}{c.output.stderr}</pre></details>)}
      {s.artifactId && <details onToggle={e => {
        if (e.currentTarget.open && diffs[s.id] === undefined) void codingDiff(version, organizationId, workItemId, s.id)
          .then(d => setDiffs(old => ({ ...old, [s.id]: d.diff + (d.truncated ? '\n… Preview truncated; full patch is retained in the artifact.' : '') })))
          .catch(() => setError('Diff evidence could not be loaded. Check connection sharing.'));
      }}><summary>Inspect saved diff</summary><pre>{diffs[s.id] ?? 'Loading saved diff…'}</pre></details>}
    </section>)}
    {canCancel && sessions.some(s => ['creating', 'creating_uncertain', 'running', 'paused'].includes(s.status)) && <button type="button" disabled={busy} onClick={() => void cancel()}>Cancel coding workspace</button>}
    {error && <><p role="alert">{error}</p><button type="button" onClick={() => refresh?.()}>Retry workspace status</button></>}
  </details>;
}
