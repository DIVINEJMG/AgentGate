import {useEffect, useRef, useState} from 'react';
import {ArrowRight, Check, ExternalLink, LoaderCircle, ShieldCheck} from 'lucide-react';
import {api} from '../platform/apiClient';
import {continueGitHubSetup, installationReturn, type GitHubSetupResult} from '../lib/githubSetup';
import type {OrganizationAccess} from '../lib/identityApi';

export default function GitHubSignupConnection({flowId, organizations, onFinished}: {
  flowId: string; organizations: OrganizationAccess[];
  onFinished: (organization: OrganizationAccess | null, onboardingId?: string) => void;
}) {
  const [selected, setSelected] = useState(organizations.length === 1 ? organizations[0].id : '');
  const [result, setResult] = useState<GitHubSetupResult | null>(null);
  const [choice, setChoice] = useState('');
  const [busy, setBusy] = useState(true);
  const [skipping, setSkipping] = useState(false);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const abandoned = useRef(false);

  function finish(organizationId: string | null) {
    window.sessionStorage.removeItem('audoryn.github.installation');
    onFinished(organizations.find(item => item.id === organizationId) || null);
  }

  async function check(organizationId: string, installationId?: string) {
    if (abandoned.current) return;
    const current = ++generation.current;
    setBusy(true);setError('');
    try {
      const data = await continueGitHubSetup(organizationId, flowId, installationId);
      if (current !== generation.current) return;
      if (data.status === 'connected') finish(organizationId);
      else {setResult(data);setChoice('');}
    } catch (cause) {
      if (current === generation.current) setError(cause instanceof Error ? cause.message : 'We could not finish connecting GitHub. Retry or skip for now.');
    } finally {if (current === generation.current) setBusy(false);}
  }

  useEffect(() => {
    let active = true;
    void (async () => {
      // StrictMode's discarded effect never dispatches onboarding.
      await Promise.resolve();if (!active || abandoned.current) return;
      try {
        const {data} = await api.get<{status: string; organizationId: string | null}>(`/api/v2/auth/github/setup/${flowId}`);
        if (!active || abandoned.current) return;
        const organizationId = data.organizationId || (organizations.length === 1 ? organizations[0].id : '');
        if (organizationId && !organizations.some(item => item.id === organizationId)) throw new Error('This setup belongs to a workspace you cannot access. You can skip for now.');
        setSelected(organizationId);
        if (data.status === 'connected') finish(organizationId);
        else if (organizationId) await check(organizationId, installationReturn(flowId));
        else setBusy(false);
      } catch (cause) {
        if (active && !abandoned.current) {setError(cause instanceof Error ? cause.message : 'GitHub setup is unavailable. You remain signed in.');setBusy(false);}
      }
    })();
    return () => {active = false;generation.current++;};
  }, [flowId]);

  async function install() {
    if (abandoned.current) return;
    const current = ++generation.current;
    setBusy(true);setError('');
    try {
      const {data} = await api.post<{installationUrl: string}>(`/api/v2/organizations/${selected}/github/setup/${flowId}/install`, {});
      if (current !== generation.current) return;
      const destination = new URL(data.installationUrl);
      if (destination.origin !== 'https://github.com' || !destination.pathname.startsWith('/apps/')) throw new Error('Unexpected GitHub installation destination.');
      window.location.assign(destination.href);
    } catch (cause) {if (current === generation.current) setError(cause instanceof Error ? cause.message : 'GitHub installation could not start.');}
    finally {if (current === generation.current) setBusy(false);}
  }

  function skip() {
    abandoned.current = true;generation.current++;setSkipping(true);
    // Workspace access does not depend on a provider or dismissal request.
    // The backend clears pending credentials; completed connections stay intact.
    void api.post(`/api/v2/auth/github/setup/${flowId}/dismiss`, {}).catch(() => {
      console.warn('GitHub setup was skipped locally; saved setup dismissal could not be confirmed.');
    });
    finish(null);
  }

  const waitingForInstall = result?.status === 'installation_required';
  return <main className='github-setup-page'>
    <header className='github-setup-header'><div className='github-setup-brand'><img src='/audoryn-mark.png' alt=''/>Audoryn</div><span>ACCOUNT SETUP</span></header>
    <section className='github-setup-card' aria-labelledby='github-setup-title'>
      <div className='github-setup-provider'><img src='/provider-marks/github.svg' alt='GitHub'/><span className='github-setup-connected'><Check size={13} aria-hidden='true'/>Signed in</span></div>
      <p className='github-setup-eyebrow'>OPTIONAL CONNECTION</p>
      <h1 id='github-setup-title'>Bring your repositories<br/>into your workspace.</h1>
      <p className='github-setup-intro'>Your Audoryn account is ready. Connect GitHub to let your workers use the repositories you approve.</p>
      <div className='github-setup-permissions'><ShieldCheck size={19} aria-hidden='true'/><div><strong>You choose the access.</strong><p>Approve selected repositories on GitHub. You can change access later, or skip this step entirely.</p></div></div>
      {organizations.length > 1 && <label className='github-setup-field'>Workspace<select value={selected} disabled={busy || !!result || skipping} onChange={event => setSelected(event.target.value)}><option value=''>Choose a workspace</option>{organizations.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
      {result?.status === 'select_installation' && <label className='github-setup-field'>GitHub account<select value={choice} disabled={busy || skipping} onChange={event => setChoice(event.target.value)}><option value=''>Choose an installation</option>{result.installations?.map(item => <option value={item.id} key={item.id} disabled={item.suspended}>{item.account}{item.suspended ? ' · suspended' : ''}</option>)}</select></label>}
      {busy && <p className='github-setup-progress' role='status'><LoaderCircle size={16} aria-hidden='true'/>Checking your GitHub access...</p>}
      {!busy && waitingForInstall && <p className='github-setup-note'>Approve the App on GitHub. We’ll finish connecting when you return.</p>}
      {!busy && waitingForInstall && !result.installationUrl && <p className='github-setup-error' role='alert'>The App installation link is unavailable. Your administrator needs to configure it. You can skip for now.</p>}
      {error && <p className='github-setup-error' role='alert'>{error}</p>}
      <div className='github-setup-actions'>
        {waitingForInstall && result.installationUrl ? <button type='button' className='github-setup-primary' disabled={busy || skipping} onClick={() => void install()}><img src='/provider-marks/github.svg' alt=''/>Continue to GitHub<ExternalLink size={15} aria-hidden='true'/></button>
          : <button type='button' className='github-setup-primary' disabled={busy || skipping || !selected || (result?.status === 'select_installation' && !choice)} onClick={() => void check(selected, choice || installationReturn(flowId))}>{error ? 'Retry connection' : result?.status === 'select_installation' ? 'Connect selected account' : 'Connect GitHub'}<ArrowRight size={16} aria-hidden='true'/></button>}
        {result?.status === 'select_installation' && result.installationUrl && <button type='button' className='github-setup-manage' disabled={busy || skipping} onClick={() => void install()}>Manage installation on GitHub<ExternalLink size={13} aria-hidden='true'/></button>}
        <button type='button' className='github-setup-skip' disabled={skipping} onClick={() => void skip()}>{skipping ? 'Continuing...' : 'Skip for now'}</button>
      </div>
      <p className='github-setup-footer'>Skipping won’t affect your sign-in. Connect GitHub later from Integrations.</p>
    </section>
    <footer className='github-setup-page-footer'>AUDORYN <span>Secure workspace access</span></footer>
  </main>;
}
