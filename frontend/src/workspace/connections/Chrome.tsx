import type { ReactNode } from 'react';
import { Globe2, Network } from 'lucide-react';
import type { IntegrationProvider } from '../../lib/integrationApi';
import { linkProps, type ConnectionsView } from '../routes';
import { PageHeader } from '../ui';

const MARKS: Partial<Record<IntegrationProvider, string>> = {
  github: '/provider-marks/github.svg',
  gmail: '/provider-marks/gmail.svg',
  google_drive: '/provider-marks/google-drive.svg',
  slack: '/provider-marks/slack.svg',
  google_calendar: '/provider-marks/google-calendar.svg',
};

export function ToolMark({ provider, size = 'md' }: { provider: IntegrationProvider | string; size?: 'sm' | 'md' | 'lg' }) {
  const mark = MARKS[provider as IntegrationProvider];
  const px = size === 'lg' ? 26 : size === 'sm' ? 16 : 20;
  const Icon = provider === 'browser' ? Globe2 : Network;
  return <span className='ws-tool-mark' data-size={size} data-provider={provider}>{mark ? <img src={mark} width={px} height={px} alt='' /> : <Icon size={px} strokeWidth={1.7} />}</span>;
}

/** A health dot that pulses while the connection is healthy. */
export function Pulse({ tone, label }: { tone: 'ok' | 'warn' | 'danger' | 'neutral' | 'info'; label?: string }) {
  return <span className='ws-pulse' data-tone={tone}><i aria-hidden='true' />{label}</span>;
}

const TABS: Array<{ view: ConnectionsView; label: string }> = [
  { view: 'home', label: 'Connected' },
  { view: 'directory', label: 'Directory' },
  { view: 'identities', label: 'Agent identities' },
  { view: 'access', label: 'Access map' },
];

export function ConnectionsHeader({ active, count, actions, description }: { active: ConnectionsView; count?: number; actions?: ReactNode; description?: ReactNode }) {
  const current = active === 'tool' ? 'directory' : active === 'account' ? 'home' : active;
  return <>
    <PageHeader title='Connections' description={description ?? 'The tools your workers can reach, the identities they act as, and who can use what. Every request still passes your policies.'} actions={actions} />
    <nav className='ws-tabs' aria-label='Connections sections'>{TABS.map((tab) => <a key={tab.view} aria-current={current === tab.view ? 'page' : undefined} {...linkProps({ page: 'connections', view: tab.view })}>{tab.label}{tab.view === 'home' && count !== undefined && <span className='ws-tab-count'>{count}</span>}</a>)}</nav>
  </>;
}
