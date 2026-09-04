import { useEffect, useState } from 'react';
import { Bot, Check, Clipboard, KeyRound, PauseCircle, Plus, RefreshCw, ShieldOff, X } from 'lucide-react';
import type { AuthUser } from '@appdeploy/client';
import type { OrganizationAccess } from '../lib/identityApi';
import { listAgents, registerAgent, revokeCredential, rotateCredential, setAgentLifecycle, type AgentIdentity, type AgentStatus, type CredentialReveal } from '../lib/agentApi';
import type { ApiVersion } from '../lib/systemApi';

interface Props { organization: OrganizationAccess; user: AuthUser; apiVersion: ApiVersion; onApiVersionChange: (version: ApiVersion) => void; onCountChange: (count: number) => void; }

export default function AgentsPanel({ organization, user, apiVersion, onApiVersionChange, onCountChange }: Props) {
    const [agents, setAgents] = useState<AgentIdentity[]>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [registerOpen, setRegisterOpen] = useState(false);
    const [name, setName] = useState('');
    const [description, setDescription] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [credentialReveal, setCredentialReveal] = useState<{ agent: AgentIdentity; credential: CredentialReveal } | null>(null);
    const [copied, setCopied] = useState(false);
    const [pendingLifecycle, setPendingLifecycle] = useState<AgentStatus | null>(null);
    const [pendingCredentialAction, setPendingCredentialAction] = useState<'rotate' | 'revoke' | null>(null);

    async function refreshAgents() {
        setLoading(true); setError(null);
        try { const items = await listAgents(apiVersion, organization.id); setAgents(items); onCountChange(items.length); if (selectedId && !items.some((item) => item.id === selectedId)) setSelectedId(null); }
        catch { setError('AgentGate could not load agent identities for this organization.'); }
        finally { setLoading(false); }
    }

    useEffect(() => { void refreshAgents(); }, [apiVersion, organization.id]);

    const selected = agents.find((agent) => agent.id === selectedId) ?? null;
    const canManage = organization.permissions.includes('agents.manage');

    function replaceAgent(agent: AgentIdentity) { setAgents((current) => current.map((item) => item.id === agent.id ? agent : item)); }

    async function handleRegister(event: React.FormEvent) {
        event.preventDefault(); setError(null);
        if (name.trim().length < 2) { setError('Agent name must contain at least 2 characters.'); return; }
        setSubmitting(true);
        try {
            const result = await registerAgent(apiVersion, organization.id, name, description);
            setAgents((current) => [result.agent, ...current]); onCountChange(agents.length + 1); setSelectedId(result.agent.id);
            setRegisterOpen(false); setName(''); setDescription(''); setCredentialReveal(result); setCopied(false);
        } catch (caught) { setError(apiMessage(caught, 'Agent registration failed.')); }
        finally { setSubmitting(false); }
    }

    async function confirmLifecycle() {
        if (!selected || !pendingLifecycle) return;
        setSubmitting(true); setError(null);
        try { const agent = await setAgentLifecycle(apiVersion, organization.id, selected.id, pendingLifecycle); replaceAgent(agent); setPendingLifecycle(null); }
        catch (caught) { setError(apiMessage(caught, 'Agent lifecycle update failed.')); }
        finally { setSubmitting(false); }
    }

    async function confirmCredentialAction() {
        if (!selected || !pendingCredentialAction) return;
        setSubmitting(true); setError(null);
        try {
            if (pendingCredentialAction === 'rotate') { const result = await rotateCredential(apiVersion, organization.id, selected.id); replaceAgent(result.agent); setCredentialReveal(result); setCopied(false); }
            else { replaceAgent(await revokeCredential(apiVersion, organization.id, selected.id)); }
            setPendingCredentialAction(null);
        } catch (caught) { setError(apiMessage(caught, 'Credential operation failed.')); }
        finally { setSubmitting(false); }
    }

    async function copyCredential() { if (!credentialReveal) return; await navigator.clipboard.writeText(credentialReveal.credential.secret); setCopied(true); }

    return <>
        <section className="page-heading agents-heading"><div><p className="eyebrow">NON-HUMAN IDENTITY</p><h1>Agent identities</h1><p className="page-subtitle">Register, own, suspend and credential AI workers without inheriting human sessions.</p></div><div className="heading-actions"><div className="api-switcher"><span>Compatibility view</span><div className="segmented"><button className={apiVersion === 'v1' ? 'active' : ''} onClick={() => onApiVersionChange('v1')}>API v1</button><button className={apiVersion === 'v2' ? 'active' : ''} onClick={() => onApiVersionChange('v2')}>API v2</button></div></div>{canManage && <button className="primary-button compact" onClick={() => setRegisterOpen(true)}><Plus size={15} /> Register agent</button>}</div></section>

        {error && <div className="inline-error" role="alert">{error}</div>}

        <section className="agents-layout">
            <article className="panel agents-list-panel"><div className="panel-heading"><div><p className="panel-kicker">REGISTRY</p><h2>{organization.name}</h2></div><span className="status-chip neutral">{agents.length} IDENTITIES</span></div>{loading ? <div className="agents-empty"><RefreshCw className="spin" size={20} /><strong>Loading agent registry</strong></div> : agents.length === 0 ? <div className="agents-empty"><Bot size={22} /><strong>No agent identities yet</strong><p>Register the first AI worker when you are ready to give it an identity separate from every human account.</p>{canManage && <button className="secondary-button" onClick={() => setRegisterOpen(true)}>Register first agent</button>}</div> : <div className="agent-list">{agents.map((agent) => <button key={agent.id} className={`agent-row ${selectedId === agent.id ? 'selected' : ''}`} onClick={() => setSelectedId(agent.id)}><div className="agent-avatar"><Bot size={16} /></div><div className="agent-row-main"><strong>{agent.name}</strong><span>{agent.description || 'No description provided'}</span></div><div className="agent-row-meta"><StatusBadge status={agent.status} /><span>{agent.credential.fingerprint}</span></div></button>)}</div>}</article>

            <article className="panel agent-detail-panel"><div className="panel-heading"><div><p className="panel-kicker">IDENTITY DETAIL</p><h2>{selected ? selected.name : 'Select an agent'}</h2></div>{selected && <StatusBadge status={selected.status} />}</div>{!selected ? <div className="agents-empty detail-empty"><KeyRound size={22} /><strong>No identity selected</strong><p>Select an agent to review ownership, lifecycle and credential metadata.</p></div> : <div className="agent-detail"><div className="identity-id-block"><span>AGENT ID</span><code>{selected.id}</code></div><dl className="detail-grid"><div><dt>Owner</dt><dd>{selected.ownerUserId === user.userId ? 'You' : selected.ownerUserId}</dd></div><div><dt>Organization</dt><dd>{organization.name}</dd></div><div><dt>Credential</dt><dd><CredentialBadge status={selected.credential.status} /></dd></div><div><dt>Version</dt><dd>v{selected.credential.version}</dd></div><div><dt>Fingerprint</dt><dd className="mono">{selected.credential.fingerprint || '—'}</dd></div><div><dt>Scope</dt><dd>{selected.credential.scopes.join(', ')}</dd></div><div><dt>Created</dt><dd>{new Date(selected.createdAt).toLocaleDateString()}</dd></div><div><dt>Last used</dt><dd>{selected.credential.lastUsedAt ? new Date(selected.credential.lastUsedAt).toLocaleString() : 'Never'}</dd></div></dl>{canManage && <div className="agent-actions"><p>Lifecycle controls</p><div className="action-row">{selected.status === 'active' && <button className="secondary-button" onClick={() => setPendingLifecycle('suspended')}><PauseCircle size={14} /> Suspend</button>}{selected.status === 'suspended' && selected.credential.status === 'active' && <button className="secondary-button" onClick={() => setPendingLifecycle('active')}><Check size={14} /> Activate</button>}{selected.status !== 'disabled' && <button className="danger-button" onClick={() => setPendingLifecycle('disabled')}><ShieldOff size={14} /> Disable</button>}</div><p>Credential controls</p><div className="action-row">{selected.status !== 'disabled' && <button className="secondary-button" onClick={() => setPendingCredentialAction('rotate')}><RefreshCw size={14} /> Rotate credential</button>}{selected.credential.status === 'active' && <button className="danger-button subtle" onClick={() => setPendingCredentialAction('revoke')}><KeyRound size={14} /> Revoke credential</button>}</div></div>}</div>}</article>
        </section>

        {registerOpen && <div className="modal-backdrop"><section className="modal-card" role="dialog" aria-modal="true" aria-label="Register agent"><div className="modal-title"><div><p className="panel-kicker">FOUNDATION 3</p><h2>Register agent identity</h2></div><button className="icon-button" aria-label="Close registration" onClick={() => setRegisterOpen(false)}><X size={16} /></button></div><form className="agent-form" onSubmit={handleRegister}><label htmlFor="agent-name">Agent name</label><input id="agent-name" value={name} onChange={(event) => setName(event.target.value)} maxLength={80} placeholder="Support Agent" autoFocus /><label htmlFor="agent-description">Purpose</label><textarea id="agent-description" value={description} onChange={(event) => setDescription(event.target.value)} maxLength={240} placeholder="Handles customer support triage and drafts responses." /><div className="credential-note"><KeyRound size={17} /><div><strong>A credential will be issued once.</strong><p>AgentGate stores only its SHA-256 hash. The plaintext credential cannot be recovered later.</p></div></div><button className="primary-button" disabled={submitting}>{submitting ? 'Registering…' : 'Register agent'}</button></form></section></div>}

        {credentialReveal && <div className="modal-backdrop"><section className="modal-card credential-modal" role="dialog" aria-modal="true" aria-label="Agent credential"><div className="modal-title"><div><p className="panel-kicker">COPY ONCE</p><h2>Store this agent credential</h2></div></div><p className="modal-copy">This secret is shown only for this issuance. After you close this window, AgentGate will keep the fingerprint and hash — not the secret.</p><div className="credential-secret"><code>{credentialReveal.credential.secret}</code><button className="icon-button" aria-label="Copy credential" onClick={copyCredential}>{copied ? <Check size={16} /> : <Clipboard size={16} />}</button></div><div className="credential-meta"><span>Fingerprint {credentialReveal.credential.fingerprint}</span><span>Version {credentialReveal.credential.version}</span></div><button className="primary-button" onClick={() => { setCredentialReveal(null); setCopied(false); }}>{copied ? 'Credential stored' : 'I have stored this credential'}</button></section></div>}

        {pendingLifecycle && selected && <ConfirmModal title={`${pendingLifecycle === 'disabled' ? 'Disable' : pendingLifecycle === 'suspended' ? 'Suspend' : 'Activate'} ${selected.name}?`} description={pendingLifecycle === 'disabled' ? 'Disabling is terminal in Foundation 3. The active credential will be revoked and this identity cannot be reactivated.' : pendingLifecycle === 'suspended' ? 'Suspension is reversible and future execution will be denied while this identity is suspended.' : 'Activation restores the identity lifecycle state. Its credential must already be active.'} destructive={pendingLifecycle !== 'active'} busy={submitting} onCancel={() => setPendingLifecycle(null)} onConfirm={confirmLifecycle} />}
        {pendingCredentialAction && selected && <ConfirmModal title={pendingCredentialAction === 'rotate' ? `Rotate ${selected.name}'s credential?` : `Revoke ${selected.name}'s credential?`} description={pendingCredentialAction === 'rotate' ? 'The current credential will stop being authoritative and a new plaintext secret will be shown once.' : 'Revoking the credential also suspends the identity. A new credential must be rotated before it can be activated again.'} destructive={pendingCredentialAction === 'revoke'} busy={submitting} onCancel={() => setPendingCredentialAction(null)} onConfirm={confirmCredentialAction} />}
    </>;
}

function StatusBadge({ status }: { status: AgentStatus }) { return <span className={`status-chip agent-status ${status}`}>{status.toUpperCase()}</span>; }
function CredentialBadge({ status }: { status: 'active' | 'revoked' }) { return <span className={`credential-badge ${status}`}>{status}</span>; }
function ConfirmModal({ title, description, destructive, busy, onCancel, onConfirm }: { title: string; description: string; destructive: boolean; busy: boolean; onCancel: () => void; onConfirm: () => void }) { return <div className="modal-backdrop"><section className="modal-card confirm-modal" role="dialog" aria-modal="true"><div className="modal-title"><h2>{title}</h2><button className="icon-button" aria-label="Cancel action" onClick={onCancel}><X size={16} /></button></div><p className="modal-copy">{description}</p><div className="confirm-actions"><button className="secondary-button" onClick={onCancel} disabled={busy}>Cancel</button><button className={destructive ? 'danger-button solid' : 'primary-button compact'} onClick={onConfirm} disabled={busy}>{busy ? 'Applying…' : 'Confirm'}</button></div></section></div>; }
function apiMessage(value: unknown, fallback: string) { const candidate = value as { response?: { data?: { error?: string } }; message?: string }; return candidate.response?.data?.error || candidate.message || fallback; }
