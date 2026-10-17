import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, CreditCard, ExternalLink, Minus, ShieldCheck, Sparkles } from 'lucide-react';
import { Notice, SkeletonLines } from '../ui';
import { routeBase } from '../routes';
import type { OrganizationAccess } from '../../lib/identityApi';
import type { ApiVersion } from '../../lib/systemApi';
import { checkoutStatus, loadCatalog, manageBilling, openPaddleCheckout, safeRedirect, startCheckout, type BillingCatalog, type CatalogPlan, type ComparisonRow } from '../../lib/billingApi';
import { capitalise, formatAmount, formatDate, formatMoney, shortName } from '../../lib/planFormat';
import './plans.css';

type Cycle = 'monthly' | 'annual';
type Message = { tone: 'info' | 'ok' | 'warn' | 'danger'; text: string };
const POLL_MS = 3000;
const POLL_TRIES = 40;
// The headline limits each card lists; the full comparison follows below the cards.
const CARD_LIMITS = ['workers.max', 'tasks.monthly', 'ai.tokens.monthly', 'coding.seconds.monthly', 'storage.bytes'];

/** Choosing a plan. Prices, plans and limits come from the Console; the plan changes only when the
 *  provider's confirmed payment reaches it, so returning from a checkout is only a reason to check. */
export default function PlansPage({ organization, apiVersion }: { organization: OrganizationAccess; apiVersion: ApiVersion }) {
  const [catalog, setCatalog] = useState<BillingCatalog | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [cycle, setCycle] = useState<Cycle>('monthly');
  const [currency, setCurrency] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<Message | null>(null);
  const polled = useRef(false);
  const canManage = organization.permissions.includes('organizations.manage');
  const plansUrl = `${window.location.origin}${routeBase()}/plans`;

  async function refresh() {
    try {
      const next = await loadCatalog(apiVersion, organization.id);
      setCatalog(next);
      setCurrency((current) => current || next.providers.flatMap((item) => item.currencies)[0] || '');
    } catch { setUnavailable(true); }
  }
  useEffect(() => { void refresh(); }, [apiVersion, organization.id]);

  // Paddle's checkout opens here: /plans/pay/<checkout>?_ptxn=<transaction>.
  useEffect(() => {
    const match = window.location.pathname.match(/\/plans\/pay\/([0-9a-f-]{36})$/);
    const token = catalog?.providers.find((item) => item.name === 'paddle')?.clientToken;
    if (!match || !token || !new URLSearchParams(window.location.search).get('_ptxn')) return;
    openPaddleCheckout(token, `${plansUrl}?checkout=${match[1]}`).catch(() => setMessage({ tone: 'danger', text: 'The checkout could not be opened. Try again shortly.' }));
  }, [catalog]);

  // Back from a checkout: wait until the provider's confirmation has reached the Console.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get('checkout');
    if (!id || polled.current || !/^[0-9a-f-]{36}$/.test(id)) return;
    polled.current = true;
    setMessage({ tone: 'info', text: 'Confirming your payment with the payment provider…' });
    let tries = 0;
    const timer = window.setInterval(async () => {
      tries += 1;
      try {
        const state = await checkoutStatus(apiVersion, organization.id, id);
        const done = state.status === 'completed' ? { tone: 'ok', text: 'Payment confirmed. Your new plan is active.' }
          : state.status === 'failed' ? { tone: 'danger', text: 'The payment could not be applied to a plan. Contact support; you will not be charged twice.' }
          : state.status === 'expired' ? { tone: 'warn', text: 'The checkout expired before a payment arrived. Choose a plan again when you are ready.' } : null;
        if (done) { window.clearInterval(timer); setMessage(done as Message); if (state.status === 'completed') void refresh(); return; }
      } catch { /* keep waiting */ }
      if (tries >= POLL_TRIES) { window.clearInterval(timer); setMessage({ tone: 'warn', text: 'Still waiting for the payment provider. Refresh this page in a few minutes.' }); }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [apiVersion, organization.id]);

  async function choose(plan: CatalogPlan) {
    setBusy(plan.code); setMessage(null);
    try {
      const checkout = await startCheckout(apiVersion, organization.id, { planCode: plan.code, billingCycle: cycle, currency, requestKey: crypto.randomUUID().replace(/-/g, '') });
      const target = checkout.url ? safeRedirect(checkout.url, (catalog?.providers ?? []).flatMap((item) => item.checkoutHosts)) : null;
      if (!target) throw new Error('The payment provider returned an unexpected address.');
      window.location.assign(target);
    } catch (caught) { setMessage({ tone: 'danger', text: messageFor(caught) }); setBusy(null); }
  }

  async function manage() {
    setBusy('manage'); setMessage(null);
    try {
      const { url } = await manageBilling(apiVersion, organization.id);
      const host = new URL(url).hostname;
      if (host !== 'paystack.com' && !host.endsWith('.paystack.com') && !host.endsWith('.paddle.com')) throw new Error('Billing returned an unexpected address.');
      window.location.assign(url);
    } catch (caught) { setMessage({ tone: 'danger', text: messageFor(caught) }); setBusy(null); }
  }

  const currencies = useMemo(() => [...new Set((catalog?.providers ?? []).flatMap((item) => item.currencies))], [catalog]);
  const plans = (catalog?.plans ?? []).filter((plan) => plan.free || plan.prices.some((price) => price.currency === currency && price[cycle] > 0));
  const current = catalog?.current ?? null;
  const paying = Boolean(current?.provider && current.status !== 'cancelled');
  const saving = bestSaving(catalog?.plans ?? [], currency);
  const featured = plans.find((plan) => plan.badge)?.code ?? null;

  return <div className='ws-page pl-page'>
    <header className='pl-hero'>
      <div className='pl-hero-copy'>
        <span className='pl-eyebrow'><Sparkles size={13} aria-hidden='true' />Plans</span>
        <h1>Capacity for your AI workforce</h1>
        <p>Every plan runs the same models with the same governance and approvals. Choose the capacity your team needs; change it any time.</p>
      </div>
      {catalog && plans.length > 1 && <div className='pl-controls'>
        <div className='pl-cycle' role='radiogroup' aria-label='Billing period'>
          {(['monthly', 'annual'] as const).map((value) => <button key={value} type='button' role='radio' aria-checked={cycle === value} onClick={() => setCycle(value)}>
            {value === 'monthly' ? 'Monthly' : 'Annual'}{value === 'annual' && saving > 0 && <span className='pl-save'>Save {saving}%</span>}
          </button>)}
        </div>
        {currencies.length > 1 && <label className='pl-currency'><span className='sr-only'>Currency</span>
          <select value={currency} onChange={(event) => setCurrency(event.target.value)}>{currencies.map((code) => <option key={code} value={code}>{code}</option>)}</select>
        </label>}
      </div>}
    </header>

    {message && <Notice tone={message.tone}>{message.text}</Notice>}
    {current?.cancelAtPeriodEnd && current.periodEnd && <Notice tone='warn'>Your {current.planName} plan ends on {formatDate(current.periodEnd)}. The workspace then returns to the free plan.</Notice>}
    {!catalog && !unavailable && <SkeletonLines rows={8} />}
    {unavailable && <Notice tone='info'>Plans can't be shown right now. Ask your administrator to change the plan, or try again shortly.</Notice>}

    {catalog && <>
      <div className='pl-grid' data-count={plans.length}>
        {plans.map((plan) => {
          const price = plan.prices.find((item) => item.currency === currency);
          const isCurrent = current?.planCode === plan.code;
          return <article key={plan.code} className='pl-card' data-featured={plan.code === featured ? 'true' : undefined} data-current={isCurrent ? 'true' : undefined} aria-label={`${plan.name} plan`}>
            {plan.code === featured && <div className='pl-glow' aria-hidden='true' />}
            <div className='pl-card-head'>
              <h2>{plan.name}</h2>
              {isCurrent ? <span className='pl-badge pl-badge-current'>Current plan</span> : plan.badge && <span className='pl-badge'>{plan.badge}</span>}
            </div>
            <p className='pl-tagline'>{plan.tagline || plan.description || (plan.free ? 'Start with every capability, in small amounts.' : '')}</p>
            <div className='pl-price'>
              {plan.free || !price ? <><strong>Free</strong><span>forever</span></>
                : <><strong>{formatMoney(cycle === 'monthly' ? price.monthly : Math.round(price.annual / 1200) * 100, currency)}</strong><span>/ month</span></>}
            </div>
            <p className='pl-billed'>{!plan.free && price ? (cycle === 'annual' ? `${formatMoney(price.annual, currency)} billed yearly` : 'Billed monthly') : 'No card needed'}</p>
            <Action plan={plan} isCurrent={isCurrent} paying={paying} canManage={canManage} busy={busy} onChoose={choose} onManage={manage} />
            <ul className='pl-points'>
              {CARD_LIMITS.filter((key) => key in plan.limits).map((key) => <li key={key}><Check size={14} aria-hidden='true' />
                <span><strong>{formatAmount(key, Number(plan.limits[key]))}</strong> {shortName(rowName(catalog.comparison, key))}{key.endsWith('.monthly') ? ' a month' : ''}</span></li>)}
              {(plan.highlights ?? []).map((text) => <li key={text}><Sparkles size={14} aria-hidden='true' /><span>{text}</span></li>)}
            </ul>
          </article>;
        })}
      </div>

      {plans.length > 0 && <section className='pl-compare' aria-labelledby='pl-compare-title'>
        <h2 id='pl-compare-title'>Compare plans</h2>
        <div className='pl-table-wrap' role='region' aria-label='Plan comparison' tabIndex={0}>
          <table className='pl-table'>
            <thead><tr><th scope='col'><span className='sr-only'>Limit</span></th>{plans.map((plan) => <th key={plan.code} scope='col' data-featured={plan.code === featured ? 'true' : undefined}>{plan.name}</th>)}</tr></thead>
            <tbody>{catalog.comparison.map((row) => <tr key={row.key}>
              <th scope='row'>{capitalise(shortName(row.name))}{row.kind === 'period' && <small>per month</small>}</th>
              {plans.map((plan) => <td key={plan.code} data-featured={plan.code === featured ? 'true' : undefined}>{cell(row, plan.limits[row.key])}</td>)}
            </tr>)}</tbody>
          </table>
        </div>
      </section>}

      <footer className='pl-foot'>
        <ShieldCheck size={16} aria-hidden='true' />
        <p>Payments are handled on the provider's secure page ({(catalog.providers ?? []).map((item) => `${capitalise(item.name)} for ${item.currencies.join(', ')}`).join('; ') || 'none yet'}). Card details never reach Audoryn. At a limit, running tasks always finish; only new work waits.</p>
      </footer>
    </>}
  </div>;
}

function Action({ plan, isCurrent, paying, canManage, busy, onChoose, onManage }: { plan: CatalogPlan; isCurrent: boolean; paying: boolean; canManage: boolean; busy: string | null; onChoose: (plan: CatalogPlan) => void; onManage: () => void }) {
  if (isCurrent && paying) return <button type='button' className='pl-cta pl-cta-quiet' disabled={!canManage || busy !== null} onClick={onManage}><ExternalLink size={15} />{busy === 'manage' ? 'Opening…' : 'Manage billing'}</button>;
  if (isCurrent) return <button type='button' className='pl-cta pl-cta-quiet' disabled>Your current plan</button>;
  if (plan.free) return <button type='button' className='pl-cta pl-cta-quiet' disabled>{paying ? 'Cancel in Manage billing' : 'Included'}</button>;
  if (!canManage) return <button type='button' className='pl-cta pl-cta-quiet' disabled>Admins can change the plan</button>;
  if (paying) return <button type='button' className='pl-cta pl-cta-quiet' disabled={busy !== null} onClick={onManage}>Switch in Manage billing</button>;
  return <button type='button' className='pl-cta' disabled={busy !== null} onClick={() => onChoose(plan)}><CreditCard size={15} />{busy === plan.code ? 'Opening checkout…' : `Choose ${plan.name}`}</button>;
}

function rowName(rows: ComparisonRow[], key: string) { return rows.find((row) => row.key === key)?.name ?? key; }

function cell(row: ComparisonRow, value: number | boolean | undefined) {
  if (row.kind === 'feature') return value === true ? <Check size={16} className='pl-yes' aria-label='Included' /> : <Minus size={16} className='pl-no' aria-label='Not included' />;
  if (value === undefined || value === false) return <Minus size={16} className='pl-no' aria-label='Not included' />;
  return <span className='pl-num'>{row.kind === 'setting' ? Number(value).toLocaleString() : formatAmount(row.key, Number(value))}</span>;
}

/** The largest annual saving among plans priced in this currency, as a whole percent. */
function bestSaving(plans: CatalogPlan[], currency: string) {
  return Math.max(0, ...plans.flatMap((plan) => plan.prices.filter((price) => price.currency === currency && price.monthly > 0 && price.annual > 0)
    .map((price) => Math.floor((1 - price.annual / (price.monthly * 12)) * 100))));
}

function messageFor(value: unknown) { const data = value as { response?: { data?: { error?: string; detail?: string } }; message?: string }; return data.response?.data?.detail || data.response?.data?.error || data.message || 'Billing is unavailable right now.'; }
