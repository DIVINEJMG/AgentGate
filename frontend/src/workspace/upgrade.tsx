import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ArrowRight, Gauge, Lock, Sparkles, Users, X } from 'lucide-react';
import type { OrganizationAccess } from '../lib/identityApi';
import type { ApiVersion } from '../lib/systemApi';
import { dismissPrompt, loadCatalog, loadPrompts, requestUpgrade, type BillingCatalog, type PlanLimit, type PlanPrompts } from '../lib/billingApi';
import { formatAmount, formatMoney, shortName } from '../lib/planFormat';
import { notify } from './feedback';
import { navigate, useRoute } from './routes';
import { Ago } from './ui';
import './pages/plans.css';

/** Upgrade prompts (plans, news and upgrade prompts plan, part 2). The server decides what may be
 *  shown and how often; the app shows it quietly, and only a refused request opens the modal. */
type Refusal = { message: string; limit: string | null };
type Upgrade = {
  prompts: PlanPrompts | null;
  catalog: BillingCatalog | null;
  needCatalog: () => void;
  dismiss: (key: string) => void;
  ask: (key: string) => Promise<void>;
};

const REFRESH_MS = 10 * 60_000;
const UpgradeContext = createContext<Upgrade | null>(null);
const useUpgrade = () => useContext(UpgradeContext);

export function UpgradeProvider({ organization, apiVersion, children }: { organization: OrganizationAccess; apiVersion: ApiVersion; children: ReactNode }) {
  const [prompts, setPrompts] = useState<PlanPrompts | null>(null);
  const [catalog, setCatalog] = useState<BillingCatalog | null>(null);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const catalogAsked = useRef(false);

  const refresh = useCallback(() => { void loadPrompts(apiVersion, organization.id).then(setPrompts).catch(() => {}); }, [apiVersion, organization.id]);
  useEffect(() => {
    setPrompts(null); setCatalog(null); catalogAsked.current = false;
    refresh();
    const timer = window.setInterval(refresh, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);
  useEffect(() => {
    // Only one refusal at a time; the newest one replaces nothing that is already open.
    const refused = (event: Event) => { setRefusal((open) => open ?? (event as CustomEvent<Refusal>).detail); refresh(); };
    window.addEventListener('audoryn:plan-limit', refused);
    return () => window.removeEventListener('audoryn:plan-limit', refused);
  }, [refresh]);

  const needCatalog = useCallback(() => {
    if (catalogAsked.current) return;
    catalogAsked.current = true;
    void loadCatalog(apiVersion, organization.id).then(setCatalog).catch(() => {});
  }, [apiVersion, organization.id]);
  const dismiss = useCallback((key: string) => {
    setPrompts((current) => current && {
      ...current, banner: current.banner === key ? null : current.banner,
      requests: current.requests.filter((request) => `request:${request.id}` !== key),
    });
    void dismissPrompt(apiVersion, organization.id, key).catch(() => {});
  }, [apiVersion, organization.id]);
  const ask = useCallback(async (key: string) => {
    try {
      const answer = await requestUpgrade(apiVersion, organization.id, key);
      notify({ key: 'upgrade-request', tone: 'ok', title: answer.sent ? 'Your administrators were asked' : 'Your administrators already know',
        body: answer.sent ? 'They’ll see your request in the app and can choose a bigger plan.' : 'Someone asked about this today. They’ll see it the next time they open the app.' });
    } catch (cause) {
      notify({ key: 'upgrade-request', tone: 'danger', title: 'The request could not be sent', body: cause instanceof Error ? cause.message : undefined });
    }
  }, [apiVersion, organization.id]);

  const value = useMemo(() => ({ prompts, catalog, needCatalog, dismiss, ask }), [prompts, catalog, needCatalog, dismiss, ask]);
  return <UpgradeContext.Provider value={value}>
    {children}
    <UpgradeModal refusal={refusal} onClose={() => setRefusal(null)} />
  </UpgradeContext.Provider>;
}

const usedShare = (limit: PlanLimit) => (limit.limit ? Math.min(1, limit.used / limit.limit) : 1);

function limitSentence(limit: PlanLimit, planName: string | null | undefined) {
  const name = shortName(limit.name);
  const plan = planName ? ` on the ${planName} plan` : '';
  return limit.state === 'reached'
    ? `You’ve used all ${formatAmount(limit.key, limit.limit)} ${name}${limit.period ? ' for this period' : ''}${plan}.`
    : `You’ve used ${Math.round(usedShare(limit) * 100)}% of ${limit.period ? 'this period’s' : 'your'} ${name} (${formatAmount(limit.key, limit.used)} of ${formatAmount(limit.key, limit.limit)}).`;
}

/** A quiet line above the page: the fullest limit, and (for administrators) a member's request. */
export function UpgradeBanner() {
  const upgrade = useUpgrade();
  const route = useRoute();
  const prompts = upgrade?.prompts;
  if (!upgrade || !prompts || route.page === 'plans') return null;
  const limit = prompts.limits.find((item) => item.key === prompts.banner);
  const request = prompts.requests[0];
  if (!limit && !request) return null;
  const seePlans = <button type='button' className='up-banner-cta' onClick={() => navigate({ page: 'plans' })}>See plans<ArrowRight size={14} /></button>;
  return <div className='up-banners'>
    {limit && <div className='up-banner' role='status' data-state={limit.state}>
      <span className='up-banner-icon' aria-hidden='true'><Gauge size={15} /></span>
      <p>{limitSentence(limit, prompts.plan?.name)}</p>
      <span className='up-meter' aria-hidden='true'><i style={{ width: `${usedShare(limit) * 100}%` }} /></span>
      {prompts.canUpgrade ? seePlans : <button type='button' className='up-banner-cta' onClick={() => void upgrade.ask(limit.key)}>Ask an admin to upgrade</button>}
      <button type='button' className='up-banner-close' aria-label='Dismiss for a week' title='Dismiss for a week' onClick={() => upgrade.dismiss(limit.key)}><X size={14} /></button>
    </div>}
    {request && <div className='up-banner' role='status' data-state='request'>
      <span className='up-banner-icon' aria-hidden='true'><Users size={15} /></span>
      <p><strong>{request.requestedBy || 'A member'}</strong> asked for a bigger plan ({shortName(request.name)}) <Ago value={request.requestedAt} />{prompts.requests.length > 1 ? `, and ${prompts.requests.length - 1} more` : ''}.</p>
      {seePlans}
      <button type='button' className='up-banner-close' aria-label='Dismiss' onClick={() => upgrade.dismiss(`request:${request.id}`)}><X size={14} /></button>
    </div>}
  </div>;
}

/** The plans above the current one that give more of `key`, cheapest first, with a starting price. */
function plansWithMore(catalog: BillingCatalog | null, key: string, current: string | null | undefined) {
  if (!catalog) return [];
  const value = (code: string | null | undefined) => catalog.plans.find((plan) => plan.code === code)?.limits[key];
  const now = value(current);
  return catalog.plans.filter((plan) => plan.code !== current && !plan.free).filter((plan) => {
    const offer = plan.limits[key];
    return typeof offer === 'boolean' ? offer && !now : typeof offer === 'number' && offer > (typeof now === 'number' ? now : -1);
  }).slice(0, 3).map((plan) => ({ plan, offer: plan.limits[key], price: plan.prices[0] }));
}

function UpgradeModal({ refusal, onClose }: { refusal: Refusal | null; onClose: () => void }) {
  const upgrade = useUpgrade();
  const ref = useRef<HTMLDialogElement>(null);
  const prompts = upgrade?.prompts;
  const key = refusal?.limit ?? null;
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (refusal && !dialog.open) { dialog.showModal(); upgrade?.needCatalog(); }
    if (!refusal && dialog.open) dialog.close();
  }, [refusal]); // eslint-disable-line react-hooks/exhaustive-deps
  const meter = prompts?.limits.find((item) => item.key === key);
  const feature = Boolean(key?.endsWith('.enabled'));
  const better = key && key !== 'plan.status' ? plansWithMore(upgrade?.catalog ?? null, key, prompts?.plan?.code) : [];
  const name = upgrade?.catalog?.comparison.find((row) => row.key === key)?.name;
  const title = key === 'plan.status' ? 'Your plan needs renewing' : feature ? 'Not included in your plan' : 'You’ve reached a plan limit';
  const choose = () => { onClose(); navigate({ page: 'plans' }); };
  return <dialog ref={ref} className='up-modal' aria-labelledby='up-modal-title' onCancel={(event) => { event.preventDefault(); onClose(); }} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    {refusal && <>
      <header className='up-modal-head'>
        <span className='up-modal-mark' aria-hidden='true'>{feature ? <Lock size={18} /> : <Gauge size={18} />}</span>
        <div><p className='pl-eyebrow'>{prompts?.plan?.name ? `${prompts.plan.name} plan` : 'Your plan'}</p><h2 id='up-modal-title'>{title}</h2></div>
        <button type='button' className='up-banner-close' aria-label='Close' onClick={onClose}><X size={16} /></button>
      </header>
      <div className='up-modal-body'>
        <p>{refusal.message}</p>
        {meter && <div className='up-modal-meter'>
          <div><span>{shortName(meter.name)}</span><strong>{formatAmount(meter.key, meter.used)} / {formatAmount(meter.key, meter.limit)}</strong></div>
          <span className='up-meter' data-state={meter.state}><i style={{ width: `${usedShare(meter) * 100}%` }} /></span>
        </div>}
        {better.length > 0 && <div className='up-modal-plans'>
          <p>Plans with more {name ? shortName(name) : 'room'}</p>
          <ul>{better.map(({ plan, offer, price }) => <li key={plan.code}>
            <strong>{plan.name}{plan.badge && <span className='pl-badge'>{plan.badge}</span>}</strong>
            <span>{typeof offer === 'number' ? `${formatAmount(key!, offer)} ${name ? shortName(name) : ''}` : 'Included'}</span>
            {price && <small>from {formatMoney(price.monthly, price.currency)}/mo</small>}
          </li>)}</ul>
        </div>}
        <p className='up-modal-note'><Sparkles size={14} aria-hidden='true' /> Work that is already running carries on. Only new work waits for room.</p>
      </div>
      <footer className='up-modal-foot'>
        <button type='button' className='pl-cta pl-cta-quiet' onClick={onClose}>Not now</button>
        {prompts?.canUpgrade !== false
          ? <button type='button' className='pl-cta' autoFocus onClick={choose}>See plans<ArrowRight size={15} /></button>
          : <button type='button' className='pl-cta' autoFocus onClick={() => { onClose(); void upgrade?.ask(key ?? 'plan.status'); }}>Ask an admin to upgrade</button>}
      </footer>
    </>}
  </dialog>;
}

/** Where a feature would be, when the plan leaves it out: what it is and which plan has it. */
export function PlanFeatureNotice({ feature, children }: { feature: string; children: ReactNode }) {
  const upgrade = useUpgrade();
  const excluded = upgrade?.prompts?.features[feature] === false;
  useEffect(() => { if (excluded) upgrade?.needCatalog(); }, [excluded]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!upgrade?.prompts || !excluded) return null;
  const { prompts } = upgrade;
  const first = plansWithMore(upgrade.catalog, feature, prompts.plan?.code)[0]?.plan;
  return <div className='up-feature'>
    <span className='up-modal-mark' aria-hidden='true'><Lock size={16} /></span>
    <div>
      <p className='pl-eyebrow'>{first ? `Available on ${first.name}` : 'Not in your plan'}</p>
      <p>{children} {prompts.plan?.name ? `The ${prompts.plan.name} plan doesn’t include it.` : 'Your plan doesn’t include it.'}</p>
    </div>
    {prompts.canUpgrade
      ? <button type='button' className='pl-cta' onClick={() => navigate({ page: 'plans' })}>See plans<ArrowRight size={15} /></button>
      : <button type='button' className='pl-cta pl-cta-quiet' onClick={() => void upgrade.ask(feature)}>Ask an admin to upgrade</button>}
  </div>;
}
