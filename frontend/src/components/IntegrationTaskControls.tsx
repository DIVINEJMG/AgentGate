import { useState } from 'react';
import { prepareIntegrationTask, resumeIntegrationWork, settleIntegrationOutcome } from '../lib/integrationFoundationApi';
import type { ApiVersion } from '../lib/systemApi';

export function IntegrationTaskControls({ organizationId, version, commandId, clarify }: {
  organizationId: string; version: ApiVersion; commandId: string; clarify: boolean;
}) {
  const [answer, setAnswer] = useState(''), [busy, setBusy] = useState(false), [status, setStatus] = useState('');
  async function submit() {
    setBusy(true); setStatus('');
    try { await prepareIntegrationTask(version, organizationId, commandId, answer); setStatus('Preparation queued. Updates will arrive here.'); }
    catch { setStatus('Preparation could not resume. Check connection sharing and the requested resource.'); }
    finally { setBusy(false); }
  }
  return <div className="cf-live-result-actions">
    {clarify && <input aria-label="Account or resource clarification" placeholder="Clarify the account or resource" value={answer} onChange={e => setAnswer(e.target.value)} disabled={busy}/>}
    <button type="button" disabled={busy} onClick={() => void submit()}>{busy ? 'Preparing…' : 'Continue task setup'}</button>
    {status && <span role="status">{status}</span>}
  </div>;
}

export function IntegrationResumeControl({ organizationId, version, workItemId, onDone }: {
  organizationId: string; version: ApiVersion; workItemId: string; onDone: () => void;
}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function resume() {
    setBusy(true); setError('');
    try { await resumeIntegrationWork(version, organizationId, workItemId); onDone(); }
    catch { setError('Reconnect the account and review access before resuming.'); }
    finally { setBusy(false); }
  }
  return <><button type="button" disabled={busy} onClick={() => void resume()}>Resume after reconnecting</button>{error && <span role="alert">{error}</span>}</>;
}

/** For a write AgentGate couldn't confirm: the person checks the account and says what happened. */
export function IntegrationOutcomeControl({ organizationId, version, workItemId, onDone }: {
  organizationId: string; version: ApiVersion; workItemId: string; onDone: () => void;
}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function settle(outcome: 'check' | 'not_done' | 'done') {
    setBusy(true); setError('');
    try { await settleIntegrationOutcome(version, organizationId, workItemId, outcome); onDone(); }
    catch (caught) {
      const detail = (caught as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'This could not be settled. Try again.');
    }
    finally { setBusy(false); }
  }
  return <>
    <button type="button" className="ws-button ws-button-sm" disabled={busy} title="Look for the write again" onClick={() => void settle('check')}>Check again</button>
    <button type="button" className="ws-button ws-button-sm" disabled={busy} title="It didn't happen, so run this step again" onClick={() => void settle('not_done')}>It didn't happen</button>
    <button type="button" className="ws-button ws-button-sm" disabled={busy} title="It happened, so carry on from the next step" onClick={() => void settle('done')}>It happened</button>
    {error && <span role="alert">{error}</span>}
  </>;
}
