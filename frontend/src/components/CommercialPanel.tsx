import { useEffect, useState } from 'react';
import { ArrowRight, RefreshCw } from 'lucide-react';
import { SettingsHeader } from '../workspace/pages/LegacyPage';
import { Confirm, Notice, Section, SkeletonLines, State, sentence } from '../workspace/ui';
import type { OrganizationAccess } from '../lib/identityApi';
import type { ApiVersion } from '../lib/systemApi';
import { loadCommercial, type CommercialSnapshot, type PlanLimit } from '../lib/commercialApi';
import { redeemUsageReset } from '../lib/billingApi';
import { navigate } from '../workspace/routes';
import { capitalise, formatAmount, formatDate, shortName } from '../lib/planFormat';

export default function CommercialPanel({ organization, apiVersion }: { organization: OrganizationAccess; apiVersion: ApiVersion; onApiVersionChange?: (v: ApiVersion) => void }) {
  const [data, setData] = useState<CommercialSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState<'ask' | 'busy' | null>(null);
  const [resetDone, setResetDone] = useState(false);
  async function refresh() {
    setLoading(true);
    setError(null);
    try { setData(await loadCommercial(apiVersion, organization.id)); }
    catch (caught) { setError(messageFor(caught)); }
    finally { setLoading(false); }
  }
  useEffect(() => { void refresh(); }, [apiVersion, organization.id]);
  const plan = data?.billing.plan ?? null;
  const reached = plan?.limits.filter(item => item.state === 'reached') ?? [];
  const near = plan?.limits.filter(item => item.state === 'near') ?? [];
  const resets = plan?.resets?.available ?? 0;
  const canReset = organization.permissions.includes('organizations.manage');
  async function useReset() {
    setResetting('busy'); setError(null);
    try { await redeemUsageReset(apiVersion, organization.id); setResetDone(true); await refresh(); }
    catch (caught) { setError(messageFor(caught)); }
    finally { setResetting(null); }
  }
  return <div className='ws-page'>
    <SettingsHeader active='billing' actions={<button type='button' className='ws-button' onClick={() => void refresh()} disabled={loading}><RefreshCw size={15} className={loading ? 'cf-spin' : ''} />Refresh</button>} />
    {error && <Notice tone='danger' action={<button type='button' className='ws-button ws-button-sm' onClick={() => void refresh()}>Try again</button>}>{error}</Notice>}
    {!data && loading && <SkeletonLines rows={6} />}
    {data && !plan && <Notice tone='warn'>This workspace has no plan yet. Ask your administrator to choose one.</Notice>}
    {data && plan && <>
      {!plan.allowsNewWork && <Notice tone='danger'>This workspace's plan is {plan.status === 'active' || plan.status === 'trialing' || plan.status === 'past_due' ? 'over' : humanize(plan.status)}, so new work can't start. Running tasks finish and your results stay available. Ask your administrator to renew the plan.</Notice>}
      {plan.allowsNewWork && reached.length > 0 && <Notice tone='danger'>You've reached your plan's limit for {list(reached)}. New work that needs more waits{reached.every(item => item.period) && plan.periodEnd ? ` until ${formatDate(plan.periodEnd)}` : ''}; ask your administrator to upgrade the plan.</Notice>}
      {plan.allowsNewWork && near.length > 0 && <Notice tone='warn'>You've used over 80% of your plan's {list(near)}.</Notice>}
      {resetDone && <Notice tone='ok'>This period's usage starts again from zero. Your plan and its renewal date are unchanged.</Notice>}
      {resets > 0 && <Notice tone='info' action={canReset ? <button type='button' className='ws-button ws-button-sm' disabled={resetting === 'busy'} onClick={() => setResetting('ask')}>Use a reset</button> : undefined}>
        You have {resets === 1 ? 'a usage reset' : `${resets} usage resets`}{plan.resets?.nextExpiresAt ? ` (${resets === 1 ? 'use it' : 'the next one expires'} by ${formatDate(plan.resets.nextExpiresAt)})` : ''}. Using one starts this period's usage again from zero; your plan stays the same.{canReset ? '' : ' Ask your administrator to use it.'}
      </Notice>}
      <Confirm open={resetting === 'ask'} title='Use a usage reset?' confirmLabel='Reset usage' onCancel={() => setResetting(null)} onConfirm={() => void useReset()}
        body={<p>Tasks, AI tokens, coding time and web searches used this period go back to zero. Your plan, workers and renewal date don't change, and the reset can't be undone.</p>} />
      <section className='ws-plan'>
        <div className='ws-plan-main'>
          <div className='ws-muted ws-small'>Your plan</div>
          <h2>{plan.name ?? sentence(plan.code ?? 'Current plan')}</h2>
          <p><State value={plan.status} /> {plan.validUntil ? `New work can start until ${formatDate(plan.validUntil)}.` : plan.periodEnd ? '' : 'No end date.'}</p>
        </div>
        <dl className='ws-facts'>
          <div><dt>Usage period</dt><dd>{formatDate(plan.periodStart)}{plan.periodEnd ? ` – ${formatDate(plan.periodEnd)}` : ''}</dd><small>{plan.periodEnd ? `Period allowances renew on ${formatDate(plan.periodEnd)}.` : 'Period allowances do not renew.'}</small></div>
          <div><dt>Limits</dt><dd>{plan.enforced ? 'Applied' : 'Shown for information'}</dd><small>{plan.enforced ? 'At a limit running tasks finish; only new work waits.' : 'They are not applied yet.'}</small></div>
        </dl>
      </section>
      <Section title='This period'>
        <div className='ws-limits'>{plan.limits.filter(item => item.period).map(item => <Limit key={item.key} item={item} />)}</div>
      </Section>
      <Section title='Your workspace'>
        <div className='ws-limits'>{plan.limits.filter(item => !item.period).map(item => <Limit key={item.key} item={item} />)}</div>
      </Section>
      <Section title='Included'>
        <dl className='ws-facts'>
          {plan.included.map(item => <div key={item.key}><dt>{sentence(item.name)}</dt><dd>{item.included ? 'Included' : 'Not included'}</dd></div>)}
          {plan.settings.map(item => <div key={item.key}><dt>{sentence(item.name)}</dt><dd>{item.value.toLocaleString()}</dd></div>)}
        </dl>
      </Section>
      <section className='ws-plan-cta'>
        <div><strong>Need more room?</strong><p className='ws-muted ws-small'>Compare plans, see prices in your currency, and upgrade or manage billing.</p></div>
        <button type='button' className='ws-button ws-button-primary' onClick={() => navigate({ page: 'plans' })}>See plans<ArrowRight size={14} /></button>
      </section>
    </>}
    {data && <details className='ws-technical'>
      <summary>Operational health · {sentence(data.production.state)}{data.production.openEscalations ? `, ${data.production.openEscalations} open escalation${data.production.openEscalations === 1 ? '' : 's'}` : ''}</summary>
      <dl className='ws-facts'>
        <Metric label={`Failed runs since ${formatDate(data.production.since)}`} value={data.production.failedRuns} />
        <Metric label='Failed tool actions' value={data.production.failedActions} />
        <Metric label='Open escalations' value={data.production.openEscalations} />
        <Metric label='Critical incidents' value={data.production.criticalIncidents} />
      </dl>
    </details>}
  </div>;
}
function Limit({ item }: { item: PlanLimit }) {
  const share = item.limit ? Math.min(100, Math.round(item.used / item.limit * 100)) : 100;
  return <div className='ws-limit'>
    <div className='ws-limit-head'><strong>{label(item)}</strong><span>{formatAmount(item.key, item.used)} of {formatAmount(item.key, item.limit)}</span></div>
    <div className='ws-meter' role='progressbar' aria-label={`${label(item)} used`} aria-valuenow={item.used} aria-valuemin={0} aria-valuemax={item.limit} data-tone={item.state === 'reached' ? 'danger' : item.state === 'near' ? 'warn' : undefined}><span style={{ width: `${share}%` }} /></div>
  </div>;
}
function Metric({label,value}:{label:string;value:number}){return <div><dt>{label}</dt><dd>{value}</dd></div>}
function label(item:PlanLimit){return capitalise(shortName(item.name))}
function list(items:PlanLimit[]){const names=items.map(item=>shortName(item.name));return names.length<2?names.join(''):`${names.slice(0,-1).join(', ')} and ${names[names.length-1]}`}
function humanize(value:string){return value.replace(/_/g,' ')}
function messageFor(value:unknown){const data=value as {response?:{data?:{error?:string}};message?:string};return data.response?.data?.error||data.message||'Usage and billing could not be loaded.'}
