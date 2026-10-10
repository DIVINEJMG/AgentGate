import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { ArrowLeft, ArrowRight, Check, CircleDashed, ExternalLink, KeyRound, Loader2, LockKeyhole, OctagonX, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { browserConfigFromDraft, browserDraftValid, EMPTY_BROWSER_DRAFT, type BrowserConnectionDraft } from '../../components/BrowserConnectionFields';
import { authorizeGitHub, connectGitHubInstallation, githubInstallations } from '../../lib/githubApi';
import type { OrganizationAccess } from '../../lib/identityApi';
import { discoverConnectionResources } from '../../lib/integrationFoundationApi';
import { checkIntegration, connectProvider, integrationSecurityStatus, listIntegrations, type IntegrationConnection } from '../../lib/integrationApi';
import type { ApiVersion } from '../../lib/systemApi';
import { navigate } from '../routes';
import { Notice, sentence } from '../ui';
import { ToolMark } from './Chrome';
import { errorText, RISK_TONE, SIGN_IN_LABEL, type ConnectionsData, type Tool } from './data';

type StepId = 'account' | 'reach' | 'review' | 'checks';
type Check = { id: string; label: string; state: 'waiting' | 'running' | 'done' | 'skipped' | 'failed'; detail?: string };

/**
 * Connecting a tool, one decision per step. The final step runs the real requests in order
 * and ticks each line only when its request returns.
 */
export function ConnectFlow({ tool, organization, apiVersion, data, open, onClose, onConnected }: {
  tool: Tool; organization: OrganizationAccess; apiVersion: ApiVersion; data: ConnectionsData;
  open: boolean; onClose: () => void; onConnected: () => Promise<void> | void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const githubApp = tool.provider === 'github' && Boolean(data.github?.enabled);
  const session = new URLSearchParams(window.location.search).get('github_setup');
  const vault = Boolean(data.security?.credentialVaultConfigured);
  const steps: StepId[] = ['account', 'reach', 'review', 'checks'];
  const [step, setStep] = useState<StepId>('account');
  const [token, setToken] = useState('');
  const [repository, setRepository] = useState('');
  const [browser, setBrowser] = useState<BrowserConnectionDraft>(EMPTY_BROWSER_DRAFT);
  const [login, setLogin] = useState({ username: '', password: '', extra: '' });
  const [installations, setInstallations] = useState<Array<{ id: string; account: string; suspended: boolean }> | null>(null);
  const [installation, setInstallation] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checks, setChecks] = useState<Check[]>([]);
  const [created, setCreated] = useState<IntegrationConnection | null>(null);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
  }, [open]);
  useEffect(() => {
    if (!open) return;
    setStep(githubApp && session ? 'reach' : 'account'); setError(null); setChecks([]); setCreated(null); setBusy(false);
    setToken(''); setRepository(''); setBrowser(EMPTY_BROWSER_DRAFT); setLogin({ username: '', password: '', extra: '' }); setInstallation('');
    if (githubApp && session) void loadInstallations();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  async function loadInstallations() {
    if (!session) return;
    setBusy(true); setError(null);
    try { const result = await githubInstallations(apiVersion, organization.id, session); setInstallations(result.installations); setInstallation((current) => result.installations.some((item) => item.id === current && !item.suspended) ? current : result.installations.find((item) => !item.suspended)?.id ?? ''); }
    catch (caught) { setError(errorText(caught)); setInstallations([]); }
    finally { setBusy(false); }
  }
  async function continueWithGitHub() {
    setBusy(true); setError(null);
    try {
      const { authorizationUrl } = await authorizeGitHub(apiVersion, organization.id);
      const destination = new URL(authorizationUrl);
      if (destination.origin !== 'https://github.com') throw new Error('Unexpected authorization destination.');
      window.location.assign(destination.href);
    } catch (caught) { setError(errorText(caught)); setBusy(false); }
  }

  const browserCredential = () => {
    if (login.extra.trim()) return login.extra.trim();
    if (!login.username && !login.password) return '';
    return JSON.stringify({ username: login.username, password: login.password });
  };
  const credential = tool.provider === 'browser' ? browserCredential() : token.trim();
  const accountValid = githubApp ? Boolean(session) : tool.credential === 'required' ? vault && token.trim().length > 0 : true;
  const reachValid = githubApp ? Boolean(installation) : tool.provider === 'github' ? /^[\w.-]+\/[\w.-]+$/.test(repository.trim()) : tool.provider === 'browser' ? browserDraftValid(browser) : true;
  const loginJsonValid = !login.extra.trim() || (() => { try { JSON.parse(login.extra); return true; } catch { return false; } })();

  async function run() {
    setStep('checks'); setError(null); setBusy(true);
    const list: Check[] = [
      ...(credential && !githubApp ? [{ id: 'vault', label: 'Credential vault is ready', state: 'waiting' as const }] : []),
      { id: 'connect', label: githubApp ? 'Linking the GitHub App installation' : credential ? `Verifying the credential with ${tool.name} and encrypting it` : `Reaching ${tool.name}`, state: 'waiting' },
      { id: 'discover', label: 'Discovering resources', state: 'waiting' },
      { id: 'health', label: 'Checking health', state: 'waiting' },
    ];
    setChecks(list);
    const mark = (id: string, state: Check['state'], detail?: string) => setChecks((current) => current.map((item) => item.id === id ? { ...item, state, detail } : item));
    let connection: IntegrationConnection | null = null;
    try {
      if (list[0].id === 'vault') {
        mark('vault', 'running');
        const security = await integrationSecurityStatus(apiVersion, organization.id);
        if (!security.credentialVaultConfigured) throw Object.assign(new Error('The credential vault is not configured, so the credential cannot be stored.'), { step: 'vault' });
        mark('vault', 'done', security.encryption || 'Encrypted at rest');
      }
      mark('connect', 'running');
      try {
        if (githubApp && session) {
          const before = new Set((await listIntegrations(apiVersion, organization.id)).map((item) => item.id));
          await connectGitHubInstallation(apiVersion, organization.id, session, installation);
          const after = await listIntegrations(apiVersion, organization.id);
          connection = after.filter((item) => item.provider === 'github' && !before.has(item.id)).sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt))[0] ?? after.filter((item) => item.provider === 'github').sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt))[0] ?? null;
          const url = new URL(window.location.href); url.searchParams.delete('github_setup'); window.history.replaceState(null, '', url);
        } else {
          // The start page's site is always allowed; extra chips are added alongside it.
          const startOrigin = tool.provider === 'browser' ? new URL(browser.startUrl.trim()).origin : '';
          const config = tool.provider === 'github' ? { repository: repository.trim() } : tool.provider === 'browser' ? browserConfigFromDraft({ ...browser, allowedOrigins: browser.allowedOrigins ? [startOrigin, browser.allowedOrigins].join(',') : '' }) : {};
          connection = await connectProvider(apiVersion, organization.id, tool.provider, config, credential);
        }
      } catch (caught) { throw Object.assign(new Error(errorText(caught)), { step: 'connect' }); }
      setCreated(connection);
      mark('connect', 'done', connection ? connection.displayName : undefined);
      if (!connection) { mark('discover', 'skipped', 'Open the connection to see its resources'); mark('health', 'skipped'); }
      else {
        if (data.foundation?.enabled) {
          mark('discover', 'running');
          try { await discoverConnectionResources(apiVersion, organization.id, connection.id); mark('discover', 'done'); }
          catch (caught) { mark('discover', 'failed', `${errorText(caught)} You can rediscover from the connection page.`); }
        } else mark('discover', 'skipped', 'Discovery is not enabled in this workspace');
        mark('health', 'running');
        try { const checked = await checkIntegration(apiVersion, organization.id, connection.id); setCreated(checked); mark('health', checked.status === 'connected' ? 'done' : 'failed', checked.health.message); }
        catch (caught) { mark('health', 'failed', errorText(caught)); }
      }
      await onConnected();
    } catch (caught) {
      const failedStep = (caught as { step?: string }).step ?? 'connect';
      mark(failedStep, 'failed', (caught as Error).message);
      setChecks((current) => current.map((item) => item.state === 'waiting' ? { ...item, state: 'skipped' } : item));
      setError('Nothing was saved. Go back, adjust and try again.');
    } finally { setBusy(false); }
  }

  const index = steps.indexOf(step);
  const finished = step === 'checks' && !busy;
  const succeeded = finished && created && !error;

  return <dialog ref={dialog} className='ws-modal ws-connect' aria-labelledby='connect-title' onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}>
    {open && <>
      <header className='ws-connect-head'>
        <ToolMark provider={tool.provider} size='lg' />
        <div><h2 id='connect-title'>Connect {tool.name}</h2><p>{SIGN_IN_LABEL[githubApp ? 'github_app' : tool.signIn]} · {tool.category}</p></div>
        <button type='button' className='ws-icon-button' aria-label='Close' disabled={busy} onClick={onClose}><X size={16} /></button>
      </header>
      <ol className='ws-stepper' aria-label='Steps'>
        {(['Account', 'What it can reach', 'Review', 'Live checks'] as const).map((label, position) => <li key={label} data-state={position < index ? 'done' : position === index ? 'current' : 'next'}><span>{position < index ? <Check size={11} strokeWidth={3} /> : position + 1}</span>{label}</li>)}
      </ol>

      <div className='ws-connect-body'>
        {error && step !== 'checks' && <Notice tone='danger'>{error}</Notice>}

        {step === 'account' && (githubApp ? <Panel title='Sign in with GitHub' lead='You’ll install the Audoryn GitHub App and choose its repositories on GitHub. You come straight back here to finish.'>
          {!data.github?.authenticationConfigured && <Notice tone='warn'>{data.github?.credentialVaultConfigured === false ? 'The credential vault must be configured before GitHub can connect.' : 'The GitHub App’s credentials and callback address are not configured yet. Ask your administrator.'}</Notice>}
          <button type='button' className='ws-button ws-button-primary ws-button-lg' disabled={busy || !data.github?.authenticationConfigured} onClick={() => void continueWithGitHub()}>{busy ? <Loader2 size={16} className='cf-spin' /> : <ToolMark provider='github' size='sm' />}Continue with GitHub</button>
          <p className='ws-muted ws-small'>Audoryn asks GitHub only for what the App declares. You can remove repositories from GitHub at any time.</p>
        </Panel> : tool.provider === 'browser' ? <Panel title='Sign-in for the website' lead='Optional. Only needed if workers must sign in. The login is encrypted and used at the browser edge; the AI never sees it.'>
          {!vault && <Notice tone='warn'><LockKeyhole size={14} /> The credential vault is not set up, so a login can’t be stored. You can still connect public pages.</Notice>}
          <div className='ws-field-pair'>
            <label className='ws-field'>Username or email<input autoComplete='off' value={login.username} disabled={!vault} onChange={(event) => setLogin({ ...login, username: event.target.value })} placeholder='user@example.com' /></label>
            <label className='ws-field'>Password<input type='password' autoComplete='new-password' value={login.password} disabled={!vault} onChange={(event) => setLogin({ ...login, password: event.target.value })} /></label>
          </div>
          <details className='ws-technical'><summary>Other login fields</summary><label className='ws-field'>Login as JSON<textarea rows={3} value={login.extra} disabled={!vault} onChange={(event) => setLogin({ ...login, extra: event.target.value })} placeholder='{"username":"…","password":"…","otpSecret":"…"}' /><small>Replaces the two fields above when filled. {loginJsonValid ? '' : 'This is not valid JSON.'}</small></label></details>
        </Panel> : <Panel title={tool.provider === 'github' ? 'GitHub access token' : `${tool.name} access token`} lead={tool.credential === 'required' ? `Audoryn checks the token with ${tool.name}, then keeps only an encrypted copy and its fingerprint.` : 'Optional for public repositories. Private repositories need a token.'}>
          {!vault && <Notice tone={tool.credential === 'required' ? 'danger' : 'warn'}><LockKeyhole size={14} /> The credential vault is not set up{tool.credential === 'required' ? `, so ${tool.name} can’t connect yet.` : '. Only public repositories can connect.'}</Notice>}
          <label className='ws-field'>Token<span className='ws-input-row'><KeyRound size={15} className='ws-input-icon' /><input type='password' autoComplete='off' spellCheck={false} value={token} disabled={!vault} onChange={(event) => setToken(event.target.value)} placeholder={vault ? 'Paste the token' : 'Vault not configured'} /></span><small>Validated once against {tool.name}. It is never shown again.</small></label>
        </Panel>)}

        {step === 'reach' && (githubApp ? <Panel title='Choose the installation' lead='Repository access was chosen on GitHub. Pick which installation this workspace should use.'>
          {busy && !installations ? <p className='ws-muted'><Loader2 size={14} className='cf-spin' /> Checking your installations…</p> : installations && installations.length ? <ul className='ws-choice-list' role='radiogroup' aria-label='GitHub installations'>{installations.map((item) => <li key={item.id}><label data-disabled={item.suspended ? 'true' : undefined}><input type='radio' name='installation' value={item.id} checked={installation === item.id} disabled={item.suspended} onChange={() => setInstallation(item.id)} /><ToolMark provider='github' size='sm' /><span><strong>{item.account}</strong><small>{item.suspended ? 'Suspended on GitHub' : 'Installation'}</small></span></label></li>)}</ul>
            : <Notice tone='warn'>No installation of the App is available to your GitHub account yet. Install it for the repositories you want, then refresh.</Notice>}
          <div className='ws-form-actions ws-form-actions-start'>
            <button type='button' className='ws-button ws-button-sm' disabled={busy} onClick={() => void loadInstallations()}><RefreshCw size={13} />Refresh installations</button>
            {data.github?.installationUrl && <a className='ws-button ws-button-sm ws-button-quiet' href={data.github.installationUrl} target='_blank' rel='noreferrer'>Manage on GitHub<ExternalLink size={12} /></a>}
          </div>
        </Panel> : tool.provider === 'github' ? <Panel title='Repository' lead='One repository per token connection. Only owner/name coordinates are accepted; arbitrary URLs are never fetched.'>
          <label className='ws-field'>Repository<input className='ws-mono-input' value={repository} onChange={(event) => setRepository(event.target.value)} placeholder='owner/repository' spellCheck={false} /><small>{repository && !/^[\w.-]+\/[\w.-]+$/.test(repository.trim()) ? 'Use the form owner/repository.' : 'For example acme/web-app.'}</small></label>
        </Panel> : tool.provider === 'browser' ? <BrowserReach value={browser} onChange={setBrowser} /> : <Panel title='What it can reach' lead={tool.reaches}>
          <p className='ws-muted'>There is nothing to choose for {tool.name}: access follows the account the token belongs to.</p>
        </Panel>)}

        {step === 'review' && <Panel title='Review' lead='This connection makes these actions available. Your policies still decide every single request.'>
          <dl className='ws-review-facts'>
            <div><dt>Tool</dt><dd>{tool.name}</dd></div>
            <div><dt>Signs in with</dt><dd>{githubApp ? `GitHub App · ${installations?.find((item) => item.id === installation)?.account ?? ''}` : credential ? 'Encrypted credential' : 'No credential'}</dd></div>
            <div><dt>Reaches</dt><dd>{tool.provider === 'github' && !githubApp ? <code>{repository}</code> : tool.provider === 'browser' ? <code>{browser.startUrl}</code> : tool.reaches}</dd></div>
          </dl>
          <ul className='ws-ability-list'>{tool.abilities.filter((ability) => tool.provider !== 'browser' || ability.label !== 'Sign in with a stored login' || credential).map((ability) => <li key={ability.label}>
            <span className='ws-state' data-tone={RISK_TONE[ability.risk]}>{sentence(ability.risk)}</span>
            <span><strong>{ability.label}</strong><small>{ability.detail}</small></span>
            {ability.approval && <span className='ws-tag' data-tone='warn'>Always needs approval</span>}
          </li>)}{tool.provider === 'browser' && browser.enableFileTransfer && <li><span className='ws-state' data-tone='warn'>High</span><span><strong>Upload and download files</strong><small>Artifact-scoped and approval-governed.</small></span></li>}</ul>
          <p className='ws-policy-note'><ShieldCheck size={15} /> Connecting doesn’t grant any worker access. Workers also need the scope declared on their identity, and every request passes your policies, risk checks and approvals.</p>
        </Panel>}

        {step === 'checks' && <Panel title={busy ? 'Connecting…' : succeeded ? 'Connected' : 'Connection stopped'} lead={busy ? 'Each line completes when Audoryn gets a real answer.' : succeeded ? `${created!.displayName} is ready.` : undefined}>
          <ol className='ws-checks' aria-live='polite'>{checks.map((item) => <li key={item.id} data-state={item.state}>
            <span className='ws-check-icon'>{item.state === 'done' ? <Check size={13} strokeWidth={3} /> : item.state === 'running' ? <Loader2 size={13} className='cf-spin' /> : item.state === 'failed' ? <OctagonX size={13} /> : <CircleDashed size={13} />}</span>
            <span><strong>{item.label}</strong>{item.detail && <small>{item.detail}</small>}</span>
          </li>)}</ol>
          {error && <Notice tone='danger'>{error}</Notice>}
        </Panel>}
      </div>

      <footer className='ws-connect-foot'>
        {step !== 'account' && step !== 'checks' && <button type='button' className='ws-button ws-button-quiet' disabled={busy} onClick={() => { setError(null); setStep(steps[index - 1]); }}><ArrowLeft size={14} />Back</button>}
        {finished && !succeeded && <button type='button' className='ws-button ws-button-quiet' onClick={() => { setError(null); setChecks([]); setStep(githubApp ? 'reach' : 'account'); }}><ArrowLeft size={14} />Back to edit</button>}
        <span className='ws-connect-spacer' />
        {step === 'account' && githubApp && <button type='button' className='ws-button' onClick={onClose}>Cancel</button>}
        {step === 'account' && !githubApp && <button type='button' className='ws-button ws-button-primary' disabled={!accountValid || !loginJsonValid} onClick={() => setStep('reach')}>Continue<ArrowRight size={14} /></button>}
        {step === 'reach' && <button type='button' className='ws-button ws-button-primary' disabled={!reachValid || busy} onClick={() => setStep('review')}>Continue<ArrowRight size={14} /></button>}
        {step === 'review' && <button type='button' className='ws-button ws-button-primary' onClick={() => void run()}><ShieldCheck size={14} />Connect {tool.name}</button>}
        {succeeded && <button type='button' className='ws-button ws-button-primary' onClick={() => { onClose(); navigate({ page: 'connections', view: 'account', id: created!.id }); }}>Open connection<ArrowRight size={14} /></button>}
        {finished && !succeeded && <button type='button' className='ws-button' onClick={onClose}>Close</button>}
      </footer>
    </>}
  </dialog>;
}

function Panel({ title, lead, children }: { title: string; lead?: string; children: ReactNode }) {
  return <section className='ws-connect-panel'><h3>{title}</h3>{lead && <p className='ws-connect-lead'>{lead}</p>}{children}</section>;
}

/** The browser's boundary: a start page, allowed sites as chips, optional path rules and two switches. */
function BrowserReach({ value, onChange }: { value: BrowserConnectionDraft; onChange: (next: BrowserConnectionDraft) => void }) {
  const set = <K extends keyof BrowserConnectionDraft>(key: K, next: BrowserConnectionDraft[K]) => onChange({ ...value, [key]: next });
  const startOrigin = (() => { try { return new URL(value.startUrl).origin; } catch { return ''; } })();
  return <Panel title='What the browser can reach' lead='Workers can only open the start page, the sites below and the paths you allow. Anything else is blocked.'>
    <div className='ws-field-pair'>
      <label className='ws-field'>Start page<input type='url' className='ws-mono-input' value={value.startUrl} onChange={(event) => set('startUrl', event.target.value)} placeholder='https://portal.example.com/dashboard' /><small>{value.startUrl && !startOrigin ? 'Use a full http or https address.' : 'Its site is allowed automatically.'}</small></label>
      <label className='ws-field'>Name<input value={value.displayName} maxLength={100} onChange={(event) => set('displayName', event.target.value)} placeholder={startOrigin ? new URL(startOrigin).hostname : 'Customer portal'} /><small>Optional. Defaults to the site’s name.</small></label>
    </div>
    <Chips label='Allowed sites' values={value.allowedOrigins} fixed={startOrigin} placeholder='https://login.example.com' kind='origin' onChange={(next) => set('allowedOrigins', next)} />
    <details className='ws-technical'>
      <summary>Path rules and blocked sites</summary>
      <div className='ws-form'>
        <Chips label='Blocked sites' values={value.deniedOrigins} placeholder='https://admin.example.com' kind='origin' onChange={(next) => set('deniedOrigins', next)} />
        <div className='ws-field-pair'>
          <Chips label='Allowed paths' values={value.allowedPaths} placeholder='/reports' kind='path' onChange={(next) => set('allowedPaths', next)} />
          <Chips label='Blocked paths' values={value.deniedPaths} placeholder='/billing/delete' kind='path' onChange={(next) => set('deniedPaths', next)} />
        </div>
      </div>
    </details>
    <ul className='ws-switch-list'>
      <li><span><strong>File upload and download</strong><small>Adds file actions. They stay tied to run artifacts and follow approvals.</small></span><Toggle label='File upload and download' checked={value.enableFileTransfer} onChange={(next) => set('enableFileTransfer', next)} /></li>
      <li><span><strong>Private network addresses</strong><small>Keep off for normal websites. When off, localhost, internal IPs and cloud metadata addresses are blocked.</small></span><Toggle label='Private network addresses' checked={value.allowPrivateNetwork} danger onChange={(next) => set('allowPrivateNetwork', next)} /></li>
    </ul>
  </Panel>;
}

function Toggle({ label, checked, danger, onChange }: { label: string; checked: boolean; danger?: boolean; onChange: (next: boolean) => void }) {
  return <button type='button' role='switch' aria-checked={checked} aria-label={label} className='ws-toggle' data-danger={danger ? 'true' : undefined} onClick={() => onChange(!checked)}><i /></button>;
}

/** Comma-separated values edited as removable chips. Enter, comma or paste adds; Backspace removes the last. */
function Chips({ label, values, fixed, placeholder, kind, onChange }: { label: string; values: string; fixed?: string; placeholder: string; kind: 'origin' | 'path'; onChange: (next: string) => void }) {
  const [draft, setDraft] = useState('');
  const [problem, setProblem] = useState('');
  const list = values.split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
  const normalize = (raw: string) => {
    const text = raw.trim();
    if (!text) return null;
    if (kind === 'path') return text.startsWith('/') ? text : `/${text}`;
    try { const url = new URL(/^https?:\/\//i.test(text) ? text : `https://${text}`); return url.origin; } catch { return null; }
  };
  const add = (raw: string) => {
    const parts = raw.split(/[\s,]+/).filter(Boolean);
    const next = [...list];
    for (const part of parts) { const value = normalize(part); if (!value) { setProblem(`“${part}” isn’t a valid ${kind === 'path' ? 'path' : 'site address'}.`); return; } if (!next.includes(value) && value !== fixed) next.push(value); }
    setProblem(''); setDraft(''); onChange(next.join(','));
  };
  const key = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); add(draft); }
    if (event.key === 'Backspace' && !draft && list.length) onChange(list.slice(0, -1).join(','));
  };
  return <div className='ws-field'>
    <span>{label}</span>
    <div className='ws-chips' onClick={(event) => (event.currentTarget.querySelector('input') as HTMLInputElement | null)?.focus()}>
      {fixed && <span className='ws-chip' data-fixed='true' title='From the start page'>{fixed}</span>}
      {list.map((item) => <span key={item} className='ws-chip'>{item}<button type='button' aria-label={`Remove ${item}`} onClick={() => onChange(list.filter((value) => value !== item).join(','))}><X size={11} /></button></span>)}
      <input value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={key} onBlur={() => draft && add(draft)} onPaste={(event) => { const text = event.clipboardData.getData('text'); if (/[\s,]/.test(text.trim())) { event.preventDefault(); add(text); } }} placeholder={list.length || fixed ? '' : placeholder} aria-label={label} />
    </div>
    {problem && <small className='ws-danger-text'>{problem}</small>}
  </div>;
}
