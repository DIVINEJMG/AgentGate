import type { ReactNode } from 'react';
import type { ApiVersion } from '../../lib/systemApi';
import { linkProps } from '../routes';
import { PageHeader } from '../ui';

/** Header shared by the Settings sections. */
export function SettingsHeader({ active, actions }: { active: 'workspace' | 'billing' | 'developer'; actions?: ReactNode }) {
  const tabs = [{ id: 'workspace' as const, label: 'Workspace' }, { id: 'billing' as const, label: 'Usage & billing' }, { id: 'developer' as const, label: 'Developer' }];
  return <>
    <PageHeader title='Settings' description='Your workspace profile, the people in it, your plan, and developer preferences.' actions={actions} />
    <nav className='ws-tabs' aria-label='Settings sections'>{tabs.map((tab) => <a key={tab.id} aria-current={active === tab.id ? 'page' : undefined} {...linkProps({ page: 'settings', section: tab.id })}>{tab.label}</a>)}</nav>
  </>;
}

export function DeveloperSettings({ apiVersion, onApiVersionChange }: { apiVersion: ApiVersion; onApiVersionChange: (version: ApiVersion) => void }) {
  return <div className='ws-page'>
    <SettingsHeader active='developer' />
    <section className='ws-section ws-narrow'>
      <div className='ws-section-head'><h2>API version</h2><p>Which version of the Audoryn API this workspace reads and writes through.</p></div>
      <div className='ws-rows'>
        {(['v1', 'v2'] as const).map((version) => <label key={version} className='ws-row' style={{ gridTemplateColumns: 'auto 1fr', cursor: 'pointer' }}>
          <input type='radio' name='api-version' checked={apiVersion === version} onChange={() => onApiVersionChange(version)} />
          <span className='ws-row-main'><strong>{version === 'v1' ? 'Version 1' : 'Version 2'}{version === 'v2' && <span className='ws-tag' style={{ marginLeft: 8 }}>current</span>}</strong><small>{version === 'v1' ? 'The original contract. Stable for existing integrations.' : 'The current contract with richer, nested records.'}</small></span>
        </label>)}
      </div>
      <p className='ws-muted' style={{ marginTop: 10, fontSize: 13 }}>Both versions apply the same authorization, approvals and audit. The choice is remembered on this device.</p>
    </section>
  </div>;
}
