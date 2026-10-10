import { useEffect, useState } from 'react';
import { ArrowRight, Clock3, LockKeyhole, Plus, Search, ShieldCheck, Users } from 'lucide-react';
import type { OrganizationAccess } from '../../lib/identityApi';
import type { ApiVersion } from '../../lib/systemApi';
import { auth } from '../../platform/authClient';
import { notify } from '../feedback';
import { linkProps } from '../routes';
import { Ago, EmptyState, Notice, Section, SkeletonLines, sentence } from '../ui';
import { ConnectionsHeader, Pulse, ToolMark } from './Chrome';
import { ConnectFlow } from './ConnectFlow';
import { HEALTH_TONE, RISK_TONE, SIGN_IN_LABEL, toolBySlug, TOOLS, useConnections, type ConnectionsData, type Tool } from './data';

const needsVault = (tool: Tool, data: ConnectionsData | null) => tool.credential === 'required' && data?.security?.credentialVaultConfigured === false;
const signInFor = (tool: Tool, data: ConnectionsData | null) => tool.provider === 'github' && data?.github && !data.github.enabled ? 'token' : tool.signIn;

export function DirectoryPage({ organization, apiVersion }: { organization: OrganizationAccess; apiVersion: ApiVersion }) {
  const { data, error } = useConnections(organization, apiVersion);
  const [query, setQuery] = useState('');
  const term = query.trim().toLowerCase();
  const available = TOOLS.filter((tool) => tool.available && (!term || `${tool.name} ${tool.category} ${tool.summary}`.toLowerCase().includes(term)));
  const later = TOOLS.filter((tool) => !tool.available);
  const connectedCount = (tool: Tool) => data?.integrations.filter((item) => item.provider === tool.provider && item.status !== 'disconnected').length ?? 0;

  return <div className='ws-page ws-page-wide'>
    <ConnectionsHeader active='directory' count={data?.integrations.filter((item) => item.status !== 'disconnected').length} />
    {error && <Notice tone='danger'>{error}</Notice>}
    <div className='ws-toolbar ws-toolbar-tight'>
      <p className='ws-muted'>Every tool connects through the gateway. Pick one to see exactly what workers could do with it before you connect.</p>
      <label className='ws-search-field'><Search size={15} aria-hidden='true' /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder='Find a tool' aria-label='Find a tool' /></label>
    </div>
    {available.length ? <ul className='ws-tool-grid'>{available.map((tool) => {
      const count = connectedCount(tool);
      return <li key={tool.slug}><a className='ws-conn-card ws-tool-card' {...linkProps({ page: 'connections', view: 'tool', id: tool.slug })}>
        <div className='ws-conn-card-head'><ToolMark provider={tool.provider} /><span><strong>{tool.name}</strong><small>{tool.category}</small></span>{count > 0 && <Pulse tone='ok' label={`${count} connected`} />}</div>
        <p>{tool.summary}</p>
        <div className='ws-tool-meta'>
          <span className='ws-tag'>{SIGN_IN_LABEL[signInFor(tool, data)]}</span>
          {needsVault(tool, data) && <span className='ws-tag' data-tone='warn'><LockKeyhole size={11} />Needs vault</span>}
          <span className='ws-tool-risk'>{tool.abilities.some((ability) => ability.risk === 'high') ? 'Includes high-risk actions' : 'Read and low-risk actions'}</span>
        </div>
      </a></li>;
    })}</ul> : <EmptyState icon={<Search size={18} />} title='No tools match'>Try another name.</EmptyState>}
    {later.length > 0 && <p className='ws-later'><Clock3 size={14} /><span><strong>Not available yet:</strong> {later.map((tool) => `${tool.name}. ${tool.unavailableReason ?? ''}`).join(' ')}</span></p>}
  </div>;
}

export function ToolPage({ organization, apiVersion, slug }: { organization: OrganizationAccess; apiVersion: ApiVersion; slug?: string }) {
  const { data, error, refresh } = useConnections(organization, apiVersion);
  const tool = toolBySlug(slug);
  const canManage = organization.permissions.includes('integrations.manage');
  const session = new URLSearchParams(window.location.search).get('github_setup');
  const [open, setOpen] = useState(false);
  const [linking, setLinking] = useState(false);
  // Coming back from GitHub with a setup session continues the connect flow where it left off.
  useEffect(() => { if (session && tool?.provider === 'github' && data) setOpen(true); }, [session, tool?.provider, Boolean(data)]);

  if (!tool) return <div className='ws-page'><ConnectionsHeader active='directory' /><EmptyState title='This tool is not in the directory'><a className='ws-link' {...linkProps({ page: 'connections', view: 'directory' })}>Back to the directory</a></EmptyState></div>;
  const accounts = data?.integrations.filter((item) => item.provider === tool.provider) ?? [];
  const vaultMissing = needsVault(tool, data);
  const githubApp = tool.provider === 'github' && Boolean(data?.github?.enabled);
  const blocked = !tool.available || vaultMissing || (githubApp && !data?.github?.authenticationConfigured);

  return <div className='ws-page'>

    <header className='ws-tool-hero'>
      <ToolMark provider={tool.provider} size='lg' />
      <div><div className='ws-muted ws-small'>{tool.category}</div><h1>{tool.name}</h1><p>{tool.about || tool.summary}</p></div>
      <div className='ws-page-actions'>{tool.available && canManage && <button type='button' className='ws-button ws-button-primary' disabled={!data || blocked} onClick={() => setOpen(true)}><Plus size={15} />{accounts.some((item) => item.status !== 'disconnected') ? 'Connect another' : 'Connect'}</button>}</div>
    </header>
    {error && <Notice tone='danger'>{error}</Notice>}
    {!tool.available && <Notice tone='info'><Clock3 size={14} /> Not available yet. {tool.unavailableReason}</Notice>}
    {vaultMissing && <Notice tone='warn'><LockKeyhole size={14} /> {tool.name} needs the credential vault, which isn’t set up in this workspace. Ask an administrator to configure it.</Notice>}
    {githubApp && data?.github && !data.github.authenticationConfigured && <Notice tone='warn'>The GitHub App isn’t fully configured on the server yet, so GitHub can’t be connected.</Notice>}
    {!canManage && tool.available && <Notice tone='info'><Users size={14} /> Your role can see connections but not add them. Ask someone with connection access.</Notice>}

    {!data ? <SkeletonLines rows={5} /> : <>
      <dl className='ws-tool-facts'>
        <div><dt>Signs in with</dt><dd>{SIGN_IN_LABEL[signInFor(tool, data)]}</dd></div>
        <div><dt>Credential</dt><dd>{tool.credential === 'required' ? 'Required, encrypted in the vault' : tool.credential === 'optional' ? 'Optional' : 'Not available'}</dd></div>
        <div><dt>Reaches</dt><dd>{tool.reaches || '—'}</dd></div>
        <div><dt>Who can connect</dt><dd>People with connection access in this workspace</dd></div>
      </dl>

      {tool.abilities.length > 0 && <Section title='What workers can do' description='Risk is fixed by the tool. Your policies decide what is allowed, held or blocked.'>
        <ul className='ws-ability-list ws-ability-rows'>{tool.abilities.map((ability) => <li key={ability.label}>
          <span className='ws-state' data-tone={RISK_TONE[ability.risk]}>{sentence(ability.risk)}</span>
          <span><strong>{ability.label}</strong><small>{ability.detail}</small></span>
          {ability.approval && <span className='ws-tag' data-tone='warn'>Always needs approval</span>}
        </li>)}</ul>
      </Section>}

      {accounts.length > 0 && <Section title='Your accounts' count={accounts.length}>
        <ul className='ws-rows'>{accounts.map((account) => <li key={account.id} className='ws-row'>
          <ToolMark provider={account.provider} size='sm' />
          <a className='ws-row-main ws-row-link' {...linkProps({ page: 'connections', view: 'account', id: account.id })}><strong>{account.displayName}</strong><small><code>{account.resourceKey}</code></small></a>
          <div className='ws-row-meta'><Pulse tone={HEALTH_TONE[account.status] ?? 'neutral'} label={sentence(account.status)} /><span className='ws-hide-sm'>connected <Ago value={account.createdAt} /></span><ArrowRight size={14} /></div>
        </li>)}</ul>
      </Section>}

      {tool.provider === 'github' && data.github?.loginEnabled && <Section title='Sign in with GitHub' description='Separate from repository access.'>
        <p className='ws-muted'>{data.github.signInLinked ? 'Your GitHub account is linked for signing in to Audoryn. Repository access is still granted only through the App installation.' : 'You can also link GitHub to sign in to Audoryn. That never gives workers repository access.'}</p>
        {!data.github.signInLinked && <div className='ws-form-actions ws-form-actions-start'><button type='button' className='ws-button ws-button-sm' disabled={linking} onClick={() => { setLinking(true); void auth.startGitHub('link').catch((caught) => { setLinking(false); notify({ key: 'github-link', tone: 'danger', title: 'GitHub linking could not start', body: caught instanceof Error ? caught.message : undefined }); }); }}><ToolMark provider='github' size='sm' />Link GitHub for sign-in</button></div>}
      </Section>}

      <p className='ws-policy-note'><ShieldCheck size={15} /> Connecting a tool doesn’t give any worker access. A worker also needs the scope declared on its identity, and every request is checked against your policies, risk and approvals.</p>
    </>}

    {data && tool.available && <ConnectFlow tool={tool} organization={organization} apiVersion={apiVersion} data={data} open={open} onClose={() => setOpen(false)} onConnected={refresh} />}
  </div>;
}
