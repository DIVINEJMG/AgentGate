import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { SettingsHeader } from '../workspace/pages/LegacyPage';
import { Notice, Section, SkeletonLines, sentence } from '../workspace/ui';
import type { OrganizationAccess } from '../lib/identityApi';
import type { ApiVersion } from '../lib/systemApi';
import { loadCommercial, type CommercialSnapshot } from '../lib/commercialApi';

export default function CommercialPanel({ organization, apiVersion }: { organization: OrganizationAccess; apiVersion: ApiVersion; onApiVersionChange?: (v: ApiVersion) => void }) {
  const [data, setData] = useState<CommercialSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  async function refresh() {
    setLoading(true);
    setError(null);
    try { setData(await loadCommercial(apiVersion, organization.id)); }
    catch (caught) { setError(messageFor(caught)); }
    finally { setLoading(false); }
  }
  useEffect(() => { void refresh(); }, [apiVersion, organization.id]);
  const limit = data?.entitlements.workerLimit ?? 0;
  const used = data?.entitlements.currentWorkers ?? 0;
  const share = Math.min(100, Math.round(used / Math.max(limit, 1) * 100));
  const runs = data?.usage.runsThisMonth ?? 0;
  return <div className='ws-page'>
    <SettingsHeader active='billing' actions={<button type='button' className='ws-button' onClick={() => void refresh()} disabled={loading}><RefreshCw size={15} className={loading ? 'cf-spin' : ''} />Refresh</button>} />
    {error && <Notice tone='danger' action={<button type='button' className='ws-button ws-button-sm' onClick={() => void refresh()}>Try again</button>}>{error}</Notice>}
    {!data && loading && <SkeletonLines rows={6} />}
    {data && <>
      <section className='ws-plan'>
        <div className='ws-plan-main'>
          <div className='ws-muted ws-small'>Your plan</div>
          <h2>{data.billing.plan.name}</h2>
          <p>{data.billing.plan.positioning}</p>
        </div>
        <div className='ws-plan-capacity'>
          <div className='ws-plan-reading'><strong>{used}</strong><span>of {limit} workers</span></div>
          <div className='ws-meter' role='progressbar' aria-label='Worker capacity used' aria-valuenow={used} aria-valuemin={0} aria-valuemax={limit} data-tone={share >= 90 ? 'warn' : undefined}><span style={{ width: `${share}%` }} /></div>
          <div className='ws-muted ws-small'>{data.entitlements.remainingWorkers} more can be added · the limit is enforced by the server</div>
        </div>
      </section>
      <dl className='ws-stats ws-stats-4'>
        <div><dt>Runs this month</dt><dd>{runs}</dd><small>{data.usage.runsCompletedThisMonth} completed · {data.usage.runsFailedThisMonth} failed</small></div>
        <div><dt>Jobs</dt><dd>{data.usage.jobsTotal}</dd><small>{data.usage.jobsCreatedThisMonth} created this month</small></div>
        <div><dt>Workers</dt><dd>{data.usage.workers}</dd><small>in this workspace</small></div>
        <div><dt>Catalog price</dt><dd>{data.billing.plan.priceMonthlyUsd === 0 ? '$0' : `$${data.billing.plan.priceMonthlyUsd}`}</dd><small>per month, not an invoice</small></div>
      </dl>
      <p className='ws-muted ws-small'>Counted since {date(data.usage.periodStart)} · updated {date(data.generatedAt)}{data.usage.windowTruncated ? ' · some sources were truncated, so totals may be low' : ''}.</p>
      <Section title='How billing works here'>
        <dl className='ws-facts ws-facts-stacked'>
          <div><dt>Payments</dt><dd>{data.billing.adapter.providerConnected ? `Connected through ${data.billing.adapter.key}.` : 'No payment provider is connected, so there is no checkout. Your current plan stays in effect.'}</dd></div>
          <div><dt>Worker limit</dt><dd>Enforced by the server. Adding a worker beyond it is refused.</dd></div>
          <div><dt>What capacity does not do</dt><dd>It never grants actions. Identity, capabilities, policy and approvals still govern every request.</dd></div>
        </dl>
      </Section>
      <details className='ws-technical'>
        <summary>Operational health · {sentence(data.production.state)}{data.production.openEscalations ? `, ${data.production.openEscalations} open escalation${data.production.openEscalations === 1 ? '' : 's'}` : ''}</summary>
        <dl className='ws-facts'>
          <Metric label='Dead-letter work' value={data.production.deadLetterWorkItems} />
          <Metric label='Failed runs this month' value={data.production.failedRunsThisMonth} />
          <Metric label='Failed tool actions' value={data.production.failedActionsThisMonth} />
          <Metric label='Critical escalations' value={data.production.criticalEscalations} />
          <Metric label='Critical incidents' value={data.production.criticalIncidents} />
        </dl>
        {data.production.windowTruncated && <p className='ws-muted ws-small'>The monitoring window was truncated.</p>}
      </details>
    </>}
  </div>;
}
function Metric({label,value}:{label:string;value:number}){return <div><dt>{label}</dt><dd>{value}</dd></div>}
function date(value:string){const parsed=new Date(value);return Number.isNaN(parsed.getTime())?'—':parsed.toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'})}
function messageFor(value:unknown){const data=value as {response?:{data?:{error?:string}};message?:string};return data.response?.data?.error||data.message||'Usage and billing could not be loaded.'}
