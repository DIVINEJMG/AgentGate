import { useEffect, useState } from 'react';
import { Check, ChevronRight, LockKeyhole, Mail, RotateCcw, Send, ShieldCheck, Users } from 'lucide-react';
import AIOperationsPanel from './AIOperationsPanel';
import type { OrganizationAccess } from '../lib/identityApi';
import { loadProductization, updateWorkspaceSettings, type Productization } from '../lib/productApi';
import { createOrganizationInvite, loadCommercial, type CommercialSnapshot, type InvitationRole } from '../lib/commercialApi';
import type { ApiVersion } from '../lib/systemApi';

export default function SettingsPanel({ organization, apiVersion, onApiVersionChange, onWorkspaceUpdated }: { organization: OrganizationAccess; apiVersion: ApiVersion; onApiVersionChange: (version: ApiVersion) => void; onWorkspaceUpdated: (name: string) => void }) {
  const [product, setProduct] = useState<Productization | null>(null);
  const [commercial, setCommercial] = useState<CommercialSnapshot | null>(null);
  const [form, setForm] = useState({ name: organization.name, securityContactEmail: '', companyUrl: '' });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [inviting, setInviting] = useState(false);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<InvitationRole>('operator');
  const [inviteUrl, setInviteUrl] = useState('');
  const [invitationsUnavailable, setInvitationsUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const canManage = organization.permissions.includes('organizations.manage');
  const dirty = !!product && (form.name !== product.workspace.name || form.securityContactEmail !== product.workspace.securityContactEmail || form.companyUrl !== product.workspace.companyUrl);
  async function hydrate() {
    setLoading(true); setError(null);
    try {
      const [next, billing] = await Promise.allSettled([loadProductization(apiVersion, organization.id), loadCommercial(apiVersion, organization.id)]);
      if (next.status === 'rejected') throw next.reason;
      setProduct(next.value); setCommercial(billing.status === 'fulfilled' ? billing.value : null);
      setInvitationsUnavailable(billing.status === 'rejected');
      setForm({ name: next.value.workspace.name, securityContactEmail: next.value.workspace.securityContactEmail, companyUrl: next.value.workspace.companyUrl });
    } catch (caught) { setError(messageFor(caught)); }
    finally { setLoading(false); }
  }
  useEffect(() => { void hydrate(); }, [apiVersion, organization.id]);
  async function save(event: React.FormEvent) {
    event.preventDefault(); if (!canManage || !dirty) return;
    setSaving(true); setError(null); setNotice(null);
    try { const next = await updateWorkspaceSettings(apiVersion, organization.id, form); setProduct(next); onWorkspaceUpdated(next.workspace.name); setNotice('Workspace profile saved.'); }
    catch (caught) { setError(messageFor(caught)); }
    finally { setSaving(false); }
  }
  async function invite(event: React.FormEvent) {
    event.preventDefault(); if (!commercial?.invitations.canInvite) return;
    setInviting(true); setError(null); setNotice(null);
    try {
      const next = await createOrganizationInvite(apiVersion, organization.id, email, role);
      setInviteUrl(next.url); setEmail('');
      setNotice('Invite created. Share its link with the intended teammate.');
      try { setCommercial(await loadCommercial(apiVersion, organization.id)); }
      catch { setInvitationsUnavailable(true); }
    } catch (caught) { setError(messageFor(caught)); }
    finally { setInviting(false); }
  }
  return <section className="org-stage org-settings">
    <header className="org-intro org-enter"><div><p className="org-kicker">ORGANIZATION / SETTINGS</p><h1>Set the workspace.</h1><p>Keep its identity, security contact, and human access clear.</p></div><div className="org-intro-actions"><span className="org-api-label">API</span><select aria-label="API version" value={apiVersion} onChange={e => onApiVersionChange(e.target.value as ApiVersion)}><option value="v1">v1</option><option value="v2">v2</option></select></div></header>
    {error && <div className="org-error" role="alert">{error}<button onClick={() => void hydrate()}>Reload</button></div>}
    {notice && <div className="org-notice" role="status"><Check size={16}/>{notice}</div>}
    {!product && loading && <div className="org-loading">Loading workspace settings…</div>}
    {product && <div className="org-settings-grid">
      <nav className="org-settings-index" aria-label="Settings sections"><a href="#org-profile"><span>01</span> Workspace profile <ChevronRight size={14}/></a><a href="#org-people"><span>02</span> People & access <ChevronRight size={14}/></a><a href="#org-readiness"><span>03</span> Readiness <ChevronRight size={14}/></a><a href="#org-diagnostics"><span>04</span> Diagnostics <ChevronRight size={14}/></a></nav>
      <div className="org-settings-main">
        <section className="org-settings-section org-enter" id="org-profile"><div className="org-settings-heading"><div><span className="org-kicker">01 / IDENTITY</span><h2>Workspace profile</h2><p>The details people use to identify and contact this organization.</p></div><ShieldCheck size={20}/></div><form onSubmit={save} className="org-profile-form"><label>Workspace name<input required minLength={2} maxLength={80} value={form.name} disabled={!canManage} onChange={e => setForm(current => ({...current,name:e.target.value}))}/><small>Shown across the workspace.</small></label><div className="org-field-pair"><label>Security contact<input type="email" placeholder="security@company.com" value={form.securityContactEmail} disabled={!canManage} onChange={e => setForm(current => ({...current,securityContactEmail:e.target.value}))}/><small>For security correspondence.</small></label><label>Company URL<input type="url" placeholder="https://company.com" value={form.companyUrl} disabled={!canManage} onChange={e => setForm(current => ({...current,companyUrl:e.target.value}))}/><small>Public organization website.</small></label></div><div className="org-form-actions">{canManage ? <><button type="button" className="org-secondary" disabled={!dirty || saving} onClick={() => setForm({name:product.workspace.name,securityContactEmail:product.workspace.securityContactEmail,companyUrl:product.workspace.companyUrl})}><RotateCcw size={14}/> Reset</button><button className="org-primary" disabled={!dirty || saving}>{saving ? 'Saving…' : 'Save changes'}</button></> : <p><LockKeyhole size={14}/> Your role can view these settings but cannot edit them.</p>}</div></form></section>
        <section className="org-settings-section org-enter" id="org-people"><div className="org-settings-heading"><div><span className="org-kicker">02 / ACCESS</span><h2>People & invitations</h2><p>Bring a human teammate into this organization with a defined role.</p></div><Users size={20}/></div>{invitationsUnavailable && <p className="org-caveat">Invitation data is temporarily unavailable. Workspace profile editing remains available.</p>}{commercial?.invitations.canInvite ? <form className="org-invite-form" onSubmit={invite}><label>Email address<input type="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="teammate@company.com"/></label><label>Organization role<select value={role} onChange={e => setRole(e.target.value as InvitationRole)}><option value="admin">Admin</option><option value="security_manager">Security manager</option><option value="operator">Operator</option><option value="approver">Approver</option><option value="viewer">Viewer</option></select></label><button className="org-primary" disabled={inviting || !email}><Send size={14}/>{inviting ? 'Creating…' : 'Create invite'}</button></form> : !invitationsUnavailable && <p className="org-permission"><LockKeyhole size={15}/> Your role can view invitations but cannot create them.</p>}{inviteUrl && <div className="org-invite-result"><strong>One-week authenticated link</strong><input readOnly value={inviteUrl} onFocus={e => e.currentTarget.select()} aria-label="Invitation link"/><small>Share only with the email address entered. The link is allow-listed to that person.</small></div>}<div className="org-invite-list"><div className="org-list-label"><span>RECENT INVITATIONS</span><span>{commercial?.invitations.recent.length ?? 0}</span></div>{commercial?.invitations.recent.length ? commercial.invitations.recent.map(item => <div className="org-person-row" key={item.id}><Mail size={16}/><span><strong>{item.email}</strong><small>{item.role.replace('_',' ')} · expires {new Date(item.expiresAt).toLocaleDateString()}</small></span><em>{item.status}</em></div>) : <p className="org-empty">{invitationsUnavailable ? 'Invitation history could not be loaded.' : 'No invitations have been created yet.'}</p>}{commercial?.invitations.windowTruncated && <p className="org-caveat">Only a bounded invitation window is shown.</p>}</div></section>
        <section className="org-settings-section org-enter" id="org-readiness"><div className="org-settings-heading"><div><span className="org-kicker">03 / SETUP</span><h2>Readiness</h2><p>{product.onboarding.complete} of {product.onboarding.total} steps complete.</p></div><strong className="org-readiness-number">{product.onboarding.percent}%</strong></div><div className="org-readiness-track"><span style={{width:`${product.onboarding.percent}%`}}/></div><div className="org-steps">{product.onboarding.steps.map(step => <div key={step.id}><span className={step.complete ? 'complete' : ''}>{step.complete ? <Check size={13}/> : null}</span><strong>{step.label}</strong><small>{step.complete ? 'Complete' : 'Next step'}</small></div>)}</div></section>
        <details className="org-settings-section org-diagnostics org-enter" id="org-diagnostics"><summary><span><span className="org-kicker">04 / TECHNICAL</span><strong>Provider diagnostics</strong><small>Configuration, model routes, and recent AI operations.</small></span><ChevronRight size={18}/></summary><AIOperationsPanel organizationId={organization.id} apiVersion={apiVersion}/></details>
      </div>
    </div>}
  </section>;
}
function messageFor(value:unknown){const data=value as {response?:{data?:{error?:string}};message?:string};return data.response?.data?.error||data.message||'Workspace settings request failed.'}
