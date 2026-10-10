import { IdentitiesStage } from './ConnectionsStages';
import { useEffect, useState } from 'react';
import { Check, Clipboard, KeyRound, X } from 'lucide-react';
import type { AuthUser } from '../platform/client';
import type { OrganizationAccess } from '../lib/identityApi';
import { listAgents, registerAgent, revokeCredential, rotateCredential, setAgentLifecycle, type AgentIdentity, type AgentStatus, type CredentialReveal } from '../lib/agentApi';
import type { ApiVersion } from '../lib/systemApi';
import { navigate } from '../workspace/routes';

interface Props { organization: OrganizationAccess; user: AuthUser; apiVersion: ApiVersion; onApiVersionChange?: (version: ApiVersion) => void; onCountChange: (count: number) => void; identityId?: string; }

export default function AgentsPanel({ organization, user, apiVersion, onApiVersionChange = () => {}, onCountChange, identityId }: Props) {
    const [agents, setAgents] = useState<AgentIdentity[]>([]);
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
        try { const items = await listAgents(apiVersion, organization.id); setAgents(items); onCountChange(items.length); }
        catch { setError('Audoryn could not load agent identities for this organization.'); }
        finally { setLoading(false); }
    }

    useEffect(() => { void refreshAgents(); }, [apiVersion, organization.id]);

    const selected = agents.find((agent) => agent.id === identityId) ?? null;
    const canManage = organization.permissions.includes('agents.manage');

    function replaceAgent(agent: AgentIdentity) { setAgents((current) => current.map((item) => item.id === agent.id ? agent : item)); }

    async function handleRegister(event: React.FormEvent) {
        event.preventDefault(); setError(null);
        if (name.trim().length < 2) { setError('Agent name must contain at least 2 characters.'); return; }
        setSubmitting(true);
        try {
            const result = await registerAgent(apiVersion, organization.id, name, description);
            setAgents((current) => [result.agent, ...current]); onCountChange(agents.length + 1); navigate({ page: 'connections', view: 'identities', id: result.agent.id });
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
        <IdentitiesStage organization={organization} identityId={identityId} organizationName={organization.name} userId={user.userId} apiVersion={apiVersion} onApiVersionChange={onApiVersionChange} agents={agents} selected={selected} select={(id) => navigate({ page: 'connections', view: 'identities', id: id || undefined })} canManage={canManage} loading={loading} submitting={submitting} error={error} register={()=>setRegisterOpen(true)} lifecycle={setPendingLifecycle} credentialAction={setPendingCredentialAction}/>
        {registerOpen && <div className="modal-backdrop"><section className="modal-card cx-modal" role="dialog" aria-modal="true" aria-label="Register agent"><div className="modal-title"><div><p className="panel-kicker">AGENT IDENTITY</p><h2>Register agent identity</h2></div><button className="icon-button" aria-label="Close registration" onClick={() => setRegisterOpen(false)}><X size={16} /></button></div><form className="agent-form" onSubmit={handleRegister}><label htmlFor="agent-name">Agent name</label><input id="agent-name" value={name} onChange={(event) => setName(event.target.value)} maxLength={80} placeholder="Support Agent" autoFocus /><label htmlFor="agent-description">Purpose</label><textarea id="agent-description" value={description} onChange={(event) => setDescription(event.target.value)} maxLength={240} placeholder="Handles customer support triage and drafts responses." /><div className="credential-note"><KeyRound size={17} /><div><strong>A credential will be issued once.</strong><p>Audoryn stores only its SHA-256 hash. The plaintext credential cannot be recovered later.</p></div></div><button className="primary-button" disabled={submitting}>{submitting ? 'Registering…' : 'Register agent'}</button></form></section></div>}

        {credentialReveal && <div className="modal-backdrop"><section className="modal-card credential-modal cx-modal" role="dialog" aria-modal="true" aria-label="Agent credential"><div className="modal-title"><div><p className="panel-kicker">COPY ONCE</p><h2>Store this agent credential</h2></div></div><p className="modal-copy">This secret is shown only for this issuance. After you close this window, Audoryn will keep the fingerprint and hash — not the secret.</p><div className="credential-secret"><code>{credentialReveal.credential.secret}</code><button className="icon-button" aria-label="Copy credential" onClick={copyCredential}>{copied ? <Check size={16} /> : <Clipboard size={16} />}</button></div><div className="credential-meta"><span>Fingerprint {credentialReveal.credential.fingerprint}</span><span>Version {credentialReveal.credential.version}</span></div><button className="primary-button" onClick={() => { setCredentialReveal(null); setCopied(false); }}>{copied ? 'Credential stored' : 'I have stored this credential'}</button></section></div>}

        {pendingLifecycle && selected && <ConfirmModal title={`${pendingLifecycle === 'disabled' ? 'Disable' : pendingLifecycle === 'suspended' ? 'Suspend' : 'Activate'} ${selected.name}?`} description={pendingLifecycle === 'disabled' ? 'Disabling is terminal for this identity. The active credential will be revoked and this identity cannot be reactivated.' : pendingLifecycle === 'suspended' ? 'Suspension is reversible and future execution will be denied while this identity is suspended.' : 'Activation restores the identity lifecycle state. Its credential must already be active.'} destructive={pendingLifecycle !== 'active'} busy={submitting} onCancel={() => setPendingLifecycle(null)} onConfirm={confirmLifecycle} />}
        {pendingCredentialAction && selected && <ConfirmModal title={pendingCredentialAction === 'rotate' ? `Rotate ${selected.name}'s credential?` : `Revoke ${selected.name}'s credential?`} description={pendingCredentialAction === 'rotate' ? 'The current credential will stop being authoritative and a new plaintext secret will be shown once.' : 'Revoking the credential also suspends the identity. A new credential must be rotated before it can be activated again.'} destructive={pendingCredentialAction === 'revoke'} busy={submitting} onCancel={() => setPendingCredentialAction(null)} onConfirm={confirmCredentialAction} />}
    </>;
}

function ConfirmModal({ title, description, destructive, busy, onCancel, onConfirm }: { title: string; description: string; destructive: boolean; busy: boolean; onCancel: () => void; onConfirm: () => void }) { return <div className="modal-backdrop"><section className="modal-card confirm-modal cx-modal" role="dialog" aria-modal="true"><div className="modal-title"><h2>{title}</h2><button className="icon-button" aria-label="Cancel action" onClick={onCancel}><X size={16} /></button></div><p className="modal-copy">{description}</p><div className="confirm-actions"><button className="secondary-button" onClick={onCancel} disabled={busy}>Cancel</button><button className={destructive ? 'danger-button solid' : 'primary-button compact'} onClick={onConfirm} disabled={busy}>{busy ? 'Applying…' : 'Confirm'}</button></div></section></div>; }
function apiMessage(value: unknown, fallback: string) { const candidate = value as { response?: { data?: { error?: string } }; message?: string }; return candidate.response?.data?.error || candidate.message || fallback; }
