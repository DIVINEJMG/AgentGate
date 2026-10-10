import { useEffect, useState } from 'react';
import { Check, LockKeyhole, Mail, RotateCcw, Send } from 'lucide-react';
import { SettingsHeader } from '../workspace/pages/LegacyPage';
import { Notice, Section, SkeletonLines, State, sentence } from '../workspace/ui';
import AIOperationsPanel from './AIOperationsPanel';
import type { OrganizationAccess } from '../lib/identityApi';
import { loadProductization, updateWorkspaceSettings, type Productization } from '../lib/productApi';
import { createOrganizationInvite, loadCommercial, type CommercialSnapshot, type InvitationRole } from '../lib/commercialApi';
import type { ApiVersion } from '../lib/systemApi';

export default function SettingsPanel({ organization, apiVersion, onWorkspaceUpdated }: { organization: OrganizationAccess; apiVersion: ApiVersion; onApiVersionChange?: (version: ApiVersion) => void; onWorkspaceUpdated: (name: string) => void }) {
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
  const percent = product?.onboarding.percent ?? 0;
  return <div className='ws-page'>
    <SettingsHeader active='workspace' />
    {error && <Notice tone='danger' action={<button type='button' className='ws-button ws-button-sm' onClick={() => void hydrate()}>Reload</button>}>{error}</Notice>}
    {notice && <Notice tone='ok'>{notice}</Notice>}
    {!product && loading && <SkeletonLines rows={6} />}
    {product && <>
      <Section title='Workspace profile' description='How people identify and reach this organization.'>
        <form onSubmit={save} className='ws-form ws-narrow'>
          <label className='ws-field'>Workspace name<input required minLength={2} maxLength={80} value={form.name} disabled={!canManage} onChange={e => setForm(current => ({ ...current, name: e.target.value }))} /><small>Shown across the workspace.</small></label>
          <div className='ws-field-pair'>
            <label className='ws-field'>Security contact<input type='email' placeholder='security@company.com' value={form.securityContactEmail} disabled={!canManage} onChange={e => setForm(current => ({ ...current, securityContactEmail: e.target.value }))} /><small>Where security correspondence goes.</small></label>
            <label className='ws-field'>Company website<input type='url' placeholder='https://company.com' value={form.companyUrl} disabled={!canManage} onChange={e => setForm(current => ({ ...current, companyUrl: e.target.value }))} /><small>Your public website.</small></label>
          </div>
          {canManage ? <div className='ws-form-actions ws-form-actions-start'>
            <button type='submit' className='ws-button ws-button-primary' disabled={!dirty || saving}>{saving ? 'Saving…' : 'Save changes'}</button>
            <button type='button' className='ws-button ws-button-quiet' disabled={!dirty || saving} onClick={() => setForm({ name: product.workspace.name, securityContactEmail: product.workspace.securityContactEmail, companyUrl: product.workspace.companyUrl })}><RotateCcw size={14} />Reset</button>
          </div> : <p className='ws-muted ws-small'><LockKeyhole size={13} /> Your role can view these settings but cannot change them.</p>}
        </form>
      </Section>

      <Section title='People & invitations' description='Invite a teammate with a defined role. Each link works only for the email it was created for.'>
        {invitationsUnavailable && <Notice tone='warn'>Invitation data is unavailable right now. You can still edit the workspace profile.</Notice>}
        {commercial?.invitations.canInvite ? <form className='ws-invite' onSubmit={invite}>
          <label className='ws-field'>Email<input type='email' required value={email} onChange={e => setEmail(e.target.value)} placeholder='teammate@company.com' /></label>
          <label className='ws-field'>Role<select value={role} onChange={e => setRole(e.target.value as InvitationRole)}><option value='admin'>Admin</option><option value='security_manager'>Security manager</option><option value='operator'>Operator</option><option value='approver'>Approver</option><option value='viewer'>Viewer</option></select></label>
          <button type='submit' className='ws-button ws-button-primary' disabled={inviting || !email}><Send size={14} />{inviting ? 'Creating…' : 'Create invite'}</button>
        </form> : !invitationsUnavailable && <p className='ws-muted ws-small'><LockKeyhole size={13} /> Your role can see invitations but cannot create them.</p>}
        {inviteUrl && <div className='ws-invite-link'><strong>Invite link, valid for one week</strong><input readOnly value={inviteUrl} onFocus={e => e.currentTarget.select()} aria-label='Invitation link' /><small>Share it only with the person you entered.</small></div>}
        <ul className='ws-rows'>
          {commercial?.invitations.recent.length ? commercial.invitations.recent.map(item => <li className='ws-row' key={item.id}>
            <span className='ws-doc-icon' aria-hidden='true'><Mail size={15} /></span>
            <span className='ws-row-main'><strong>{item.email}</strong><small>{sentence(item.role)} · expires {new Date(item.expiresAt).toLocaleDateString()}</small></span>
            <div className='ws-row-meta'><State value={item.status} /></div>
          </li>) : <li className='ws-quiet'>{invitationsUnavailable ? 'Invitation history could not be loaded.' : 'No invitations yet.'}</li>}
        </ul>
        {commercial?.invitations.windowTruncated && <p className='ws-muted ws-small'>Showing recent invitations only.</p>}
      </Section>

      <Section title='Setup' count={`${product.onboarding.complete}/${product.onboarding.total}`} description={percent === 100 ? 'Everything is set up.' : 'What is left before work can run on its own.'}>
        <div className='ws-meter' role='progressbar' aria-label='Setup progress' aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${percent}%` }} /></div>
        <ul className='ws-rows ws-checklist'>{product.onboarding.steps.map(step => <li key={step.id} className='ws-row ws-compact-row' data-done={step.complete ? 'true' : undefined}>
          <span className='ws-check-dot' aria-hidden='true'>{step.complete ? <Check size={12} strokeWidth={3} /> : null}</span>
          <span className='ws-row-main'><strong>{step.label}</strong></span>
          <div className='ws-row-meta'>{step.complete ? 'Done' : 'To do'}</div>
        </li>)}</ul>
      </Section>

      <details className='ws-technical'>
        <summary>AI provider diagnostics</summary>
        <p className='ws-muted ws-small' style={{ margin: '0 0 12px' }}>Configuration, model routes and recent AI operations.</p>
        <div className='ws-legacy-embed'><AIOperationsPanel organizationId={organization.id} apiVersion={apiVersion} /></div>
      </details>
    </>}
  </div>;
}
function messageFor(value:unknown){const data=value as {response?:{data?:{error?:string}};message?:string};return data.response?.data?.error||data.message||'Workspace settings request failed.'}
