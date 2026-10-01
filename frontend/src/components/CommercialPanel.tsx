import { useEffect, useState } from 'react';
import { Check, CircleAlert, RefreshCw, ShieldCheck } from 'lucide-react';
import type { OrganizationAccess } from '../lib/identityApi';
import type { ApiVersion } from '../lib/systemApi';
import { loadCommercial, type CommercialSnapshot } from '../lib/commercialApi';

export default function CommercialPanel({ organization, apiVersion, onApiVersionChange }: { organization: OrganizationAccess; apiVersion: ApiVersion; onApiVersionChange: (v: ApiVersion) => void }) {
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
  return <section className="org-stage org-billing">
    <header className="org-intro org-enter"><div><p className="org-kicker">ORGANIZATION / USAGE & BILLING</p><h1>Capacity, in plain view.</h1><p>See what this workspace uses, what the plan allows, and how billing is connected.</p></div><div className="org-intro-actions"><button className="org-refresh" onClick={() => void refresh()} disabled={loading}><RefreshCw size={15} className={loading ? 'org-spin' : ''}/> Refresh</button><span className="org-api-label">API</span><select aria-label="API version" value={apiVersion} onChange={e => onApiVersionChange(e.target.value as ApiVersion)}><option value="v1">v1</option><option value="v2">v2</option></select></div></header>
    {error && <div className="org-error" role="alert">{error}<button onClick={() => void refresh()}>Try again</button></div>}
    {data && <>
      <div className="org-billing-lead org-enter"><article className="org-capacity"><div className="org-capacity-top"><span className="org-kicker">ACTIVE PLAN</span><span className="org-status"><Check size={13}/> Enforced</span></div><div className="org-capacity-title"><h2>{data.billing.plan.name}</h2><p>{data.billing.plan.positioning}</p></div><div className="org-capacity-reading"><strong>{used}</strong><span>of {limit} workers in use</span></div><div className="org-capacity-track" role="progressbar" aria-label="Worker capacity used" aria-valuenow={used} aria-valuemin={0} aria-valuemax={limit}><span style={{ width: `${share}%` }}/></div><div className="org-capacity-foot"><span>{share}% used</span><span>{data.entitlements.remainingWorkers} available</span></div></article><article className="org-billing-rail"><div><span className="org-kicker">CATALOG PRICE</span><strong>{data.billing.plan.priceMonthlyUsd === 0 ? '$0' : `$${data.billing.plan.priceMonthlyUsd}`}<small> / month</small></strong><p>Plan catalog amount. This is not an invoice or a charge.</p></div><div><span className="org-kicker">PAYMENT CONNECTION</span><strong className="org-rail-state">{data.billing.adapter.providerConnected ? 'Connected' : 'Not connected'}</strong><p>{data.billing.adapter.providerConnected ? 'A billing provider is connected.' : 'No checkout is available. The current plan remains authoritative.'}</p></div></article></div>
      <section className="org-section org-enter"><div className="org-section-head"><div><span className="org-kicker">CURRENT PERIOD</span><h2>Usage ledger</h2><p>Counts recorded since {date(data.usage.periodStart)}.</p></div><span className="org-asof">Updated {date(data.generatedAt)}</span></div><div className="org-usage-ledger"><div className="org-usage-primary"><span>Runs this month</span><strong>{data.usage.runsThisMonth}</strong><div className="org-run-track" aria-hidden="true"><span style={{width:`${Math.min(100,data.usage.runsCompletedThisMonth / Math.max(1,data.usage.runsThisMonth) * 100)}%`}}/><i style={{width:`${Math.min(100,data.usage.runsFailedThisMonth / Math.max(1,data.usage.runsThisMonth) * 100)}%`}}/></div><div className="org-run-key"><span><i className="done"/> {data.usage.runsCompletedThisMonth} completed</span><span><i className="failed"/> {data.usage.runsFailedThisMonth} failed</span></div></div><dl className="org-usage-facts"><div><dt>Workers</dt><dd>{data.usage.workers}</dd></div><div><dt>Jobs created this month</dt><dd>{data.usage.jobsCreatedThisMonth}</dd></div><div><dt>All jobs</dt><dd>{data.usage.jobsTotal}</dd></div><div><dt>Accounting events</dt><dd>{data.usage.accountingEventsThisMonth}</dd></div></dl></div>{data.usage.windowTruncated && <p className="org-caveat"><CircleAlert size={15}/> A source window was truncated. These totals are conservative.</p>}</section>
      <section className="org-section org-enter"><div className="org-section-head"><div><span className="org-kicker">BILLING MODEL</span><h2>What your plan controls</h2></div></div><div className="org-truth-grid"><div><ShieldCheck size={20}/><strong>Worker capacity</strong><p>The server enforces the worker limit shown above.</p></div><div><Check size={20}/><strong>Execution authority</strong><p>Capacity does not grant actions. Identity, capabilities, policy, and approvals still govern work.</p></div><div><Check size={20}/><strong>Payment status</strong><p>{data.billing.adapter.providerConnected ? `Adapter: ${data.billing.adapter.key}.` : 'No payment provider is connected. Billing actions are unavailable.'}</p></div></div></section>
      <details className="org-operational org-enter"><summary>Operational signals <span>{data.production.state} · {data.production.openEscalations} open escalations</span></summary><div className="org-operational-grid"><Metric label="Dead-letter work" value={data.production.deadLetterWorkItems}/><Metric label="Failed runs this month" value={data.production.failedRunsThisMonth}/><Metric label="Failed provider actions" value={data.production.failedActionsThisMonth}/><Metric label="Critical escalations" value={data.production.criticalEscalations}/><Metric label="Critical incidents" value={data.production.criticalIncidents}/></div>{data.production.windowTruncated && <p className="org-caveat">Monitoring source window truncated.</p>}</details>
    </>}
    {!data && loading && <div className="org-loading">Loading usage and billing…</div>}
  </section>;
}
function Metric({label,value}:{label:string;value:number}){return <div><span>{label}</span><strong>{value}</strong></div>}
function date(value:string){const parsed=new Date(value);return Number.isNaN(parsed.getTime())?'—':parsed.toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'})}
function messageFor(value:unknown){const data=value as {response?:{data?:{error?:string}};message?:string};return data.response?.data?.error||data.message||'Usage and billing could not be loaded.'}
