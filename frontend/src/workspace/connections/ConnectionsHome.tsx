import { ArrowRight, Plus, RefreshCw } from 'lucide-react';
import type { OrganizationAccess } from '../../lib/identityApi';
import type { ApiVersion } from '../../lib/systemApi';
import { useLive } from '../live';
import { linkProps } from '../routes';
import { Ago, Notice, Section, SkeletonLines, sentence } from '../ui';
import { ConnectionsHeader, Pulse, ToolMark } from './Chrome';
import { activityFor, HEALTH_TONE, resourcesFor, toolFor, TOOLS, useConnections, workerCountFor, type ConnectionsData } from './data';

export default function ConnectionsHome({ organization, apiVersion }: { organization: OrganizationAccess; apiVersion: ApiVersion }) {
  const { data, error, refreshing, refresh } = useConnections(organization, apiVersion);
  const live = useLive();
  const canManage = organization.permissions.includes('integrations.manage');
  const active = data?.integrations.filter((item) => item.status !== 'disconnected') ?? [];
  const closed = data?.integrations.filter((item) => item.status === 'disconnected') ?? [];

  return <div className='ws-page ws-page-wide'>
    <ConnectionsHeader active='home' count={data ? active.length : undefined} actions={<>
      <button type='button' className='ws-button' onClick={() => void refresh()} disabled={refreshing}><RefreshCw size={15} className={refreshing ? 'cf-spin' : ''} />Refresh</button>
      {canManage && <a className='ws-button ws-button-primary' {...linkProps({ page: 'connections', view: 'directory' })}><Plus size={15} />Add connection</a>}
    </>} />
    {error && <Notice tone='danger' action={<button type='button' className='ws-button ws-button-sm' onClick={() => void refresh()}>Retry</button>}>{error}</Notice>}
    {!data ? <><div className='ws-status-strip ws-status-loading'>{[0, 1, 2, 3].map((index) => <div key={index}><i /></div>)}</div><SkeletonLines rows={4} avatar /></> : <>
      <StatusStrip data={data} />
      {active.length === 0 ? <section className='ws-conn-empty'>
        <div><h2>Connect your first tool</h2><p>Workers can only reach tools you connect here, and only within your policies. Start with the directory.</p></div>
        <ul className='ws-conn-suggest'>{TOOLS.filter((tool) => tool.available).slice(0, 4).map((tool) => <li key={tool.slug}><a {...linkProps({ page: 'connections', view: 'tool', id: tool.slug })}><ToolMark provider={tool.provider} /><span><strong>{tool.name}</strong><small>{tool.summary}</small></span><ArrowRight size={14} /></a></li>)}</ul>
        <a className='ws-button ws-button-primary' {...linkProps({ page: 'connections', view: 'directory' })}>Open the directory<ArrowRight size={14} /></a>
      </section> : <ul className='ws-conn-grid' aria-label='Connected accounts'>
        {active.map((connection) => {
          const tool = toolFor(connection.provider);
          const recent = activityFor(data, connection.id)[0];
          const workers = workerCountFor(data, connection.id, live.workers);
          const resources = resourcesFor(data, connection.id).length;
          const tone = HEALTH_TONE[connection.status] ?? 'neutral';
          return <li key={connection.id}>
            <a className='ws-conn-card' {...linkProps({ page: 'connections', view: 'account', id: connection.id })}>
              <div className='ws-conn-card-head'><ToolMark provider={connection.provider} /><span><strong>{connection.displayName}</strong><small>{tool?.name ?? sentence(connection.provider)}</small></span><Pulse tone={tone} label={connection.status === 'connected' ? 'Healthy' : sentence(connection.status)} /></div>
              <code className='ws-conn-key'>{connection.resourceKey}</code>
              <dl className='ws-conn-stats'>
                <div><dt>Resources</dt><dd>{resources}</dd></div>
                <div><dt>Workers</dt><dd>{workers}</dd></div>
                <div><dt>Last used</dt><dd>{recent ? <Ago value={recent.requestedAt} /> : 'Not yet'}</dd></div>
              </dl>
            </a>
          </li>;
        })}
      </ul>}
      {closed.length > 0 && <Section title='Disconnected' count={closed.length} description='Kept for history. Workers can no longer use them.'>
        <ul className='ws-rows'>{closed.map((connection) => <li key={connection.id} className='ws-row'>
          <ToolMark provider={connection.provider} size='sm' />
          <a className='ws-row-main ws-row-link' {...linkProps({ page: 'connections', view: 'account', id: connection.id })}><strong>{connection.displayName}</strong><small>{toolFor(connection.provider)?.name} · disconnected <Ago value={connection.updatedAt} /></small></a>
        </li>)}</ul>
      </Section>}
    </>}
  </div>;
}

/** Live platform checks that decide what can connect. Each item comes from a real status read. */
function StatusStrip({ data }: { data: ConnectionsData }) {
  const vault = data.security?.credentialVaultConfigured;
  const github = data.github;
  const items: Array<{ label: string; value: string; detail: string; tone: 'ok' | 'warn' | 'danger' | 'neutral' }> = [
    { label: 'Gateway', value: data.gateway === 'operational' ? 'Operational' : 'Unreachable', detail: data.gateway === 'operational' ? 'Every tool request is checked here' : 'Tool requests cannot be checked', tone: data.gateway === 'operational' ? 'ok' : 'danger' },
    { label: 'Credential vault', value: vault ? 'Ready' : vault === false ? 'Not set up' : 'Unknown', detail: vault ? `Secrets are ${data.security?.encryption || 'encrypted'} at rest` : 'Token-based tools cannot connect yet', tone: vault ? 'ok' : 'warn' },
    { label: 'GitHub App', value: !github ? 'Unknown' : github.enabled && github.authenticationConfigured ? 'Ready' : github.enabled ? 'Needs setup' : 'Token mode', detail: !github ? 'Status could not be read' : github.enabled && github.authenticationConfigured ? 'Install to grant repository access' : github.enabled ? 'App credentials or callback missing' : 'Single repositories connect with a token', tone: github?.enabled && github.authenticationConfigured ? 'ok' : github ? 'warn' : 'neutral' },
    { label: 'Discovery & sharing', value: data.foundation?.enabled ? 'On' : 'Off', detail: data.foundation?.enabled ? 'Resources are discovered and shared per account' : 'Accounts are workspace-wide', tone: data.foundation?.enabled ? 'ok' : 'neutral' },
  ];
  return <dl className='ws-status-strip' aria-label='Connection services'>{items.map((item) => <div key={item.label}>
    <dt>{item.label}</dt><dd><Pulse tone={item.tone} label={item.value} /></dd><small>{item.detail}</small>
  </div>)}</dl>;
}
