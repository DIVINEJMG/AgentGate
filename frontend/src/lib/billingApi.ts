import { api } from '../platform/client';
import type { ApiVersion } from './systemApi';

/** Plans and prices come from the Console; the page only chooses a plan, a cycle and a currency. */
export interface CatalogPrice { currency: string; monthly: number; annual: number; provider: string }
export interface CatalogPlan { code: string; name: string; description: string; free: boolean; prices: CatalogPrice[]; limits: Record<string, number | boolean>; tagline?: string | null; highlights?: string[]; badge?: string | null }
/** One row of the plan comparison; names come from AgentGate's plan service. */
export interface ComparisonRow { key: string; name: string; kind: 'count' | 'period' | 'feature' | 'setting' }
export interface CatalogProvider { name: string; currencies: string[]; checkoutHosts: string[]; clientToken: string | null }
export interface BillingCatalog {
  plans: CatalogPlan[];
  comparison: ComparisonRow[];
  providers: CatalogProvider[];
  current: { planCode: string; planName: string; status: string; billingCycle: string; provider: string | null; periodEnd: string | null; cancelAtPeriodEnd: boolean; manageable: boolean } | null;
}
export interface CheckoutState { id: string; status: 'open' | 'completed' | 'expired' | 'failed'; provider: string; url?: string | null; failureReason?: string | null }

/** A limit at or near its end, as the plan service reports it. */
export interface PlanLimit { key: string; name: string; used: number; limit: number; period: boolean; state: 'ok' | 'near' | 'reached' }
export interface UpgradeRequest { id: string; limit: string; name: string; requestedBy: string | null; requestedAt: string }
/** What the app may suggest now; the server applies the frequency rules. */
export interface PlanPrompts {
  plan: { code: string | null; name: string | null; status: string; periodEnd: string | null; enforced: boolean } | null;
  canUpgrade: boolean;
  limits: PlanLimit[];
  banner: string | null;
  features: Record<string, boolean>;
  requests: UpgradeRequest[];
}

const unwrap = <T>(version: ApiVersion, data: any): T => (version === 'v1' ? data : data.data) as T;

export async function loadCatalog(v: ApiVersion, o: string) { return unwrap<BillingCatalog>(v, (await api.get(`/api/${v}/organizations/${o}/billing/catalog`)).data); }
export async function startCheckout(v: ApiVersion, o: string, input: { planCode: string; billingCycle: 'monthly' | 'annual'; currency: string; requestKey: string }) {
  return unwrap<CheckoutState>(v, (await api.post(`/api/${v}/organizations/${o}/billing/checkout`, input)).data);
}
export async function checkoutStatus(v: ApiVersion, o: string, id: string) { return unwrap<CheckoutState>(v, (await api.get(`/api/${v}/organizations/${o}/billing/checkouts/${encodeURIComponent(id)}`)).data); }
export async function manageBilling(v: ApiVersion, o: string) { return unwrap<{ url: string }>(v, (await api.post(`/api/${v}/organizations/${o}/billing/manage`)).data); }

export async function loadPrompts(v: ApiVersion, o: string) { return unwrap<PlanPrompts>(v, (await api.get(`/api/${v}/organizations/${o}/plan/prompts`)).data); }
export async function dismissPrompt(v: ApiVersion, o: string, key: string) { await api.post(`/api/${v}/organizations/${o}/plan/prompts/dismiss`, { key }); }
export async function requestUpgrade(v: ApiVersion, o: string, key: string) { return unwrap<{ sent: boolean; requestedAt: string }>(v, (await api.post(`/api/${v}/organizations/${o}/plan/upgrade-requests`, { key })).data); }

/** Leave for a provider's page only when its address is one the catalogue named (or Paystack/Paddle's own). */
export function safeRedirect(url: string, hosts: string[]): string | null {
  try {
    const target = new URL(url);
    const local = target.hostname === 'localhost' || target.hostname === '127.0.0.1';
    return (target.protocol === 'https:' || (target.protocol === 'http:' && local)) && hosts.includes(target.hostname) ? target.toString() : null;
  } catch { return null; }
}

const PADDLE_SCRIPT = 'https://cdn.paddle.com/paddle/v2/paddle.js';

/** Paddle's checkout runs in Paddle.js on this page; it opens the transaction in `?_ptxn=`. */
export function openPaddleCheckout(token: string, successUrl: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const start = () => {
      const paddle = (window as any).Paddle;
      if (!paddle) { reject(new Error('Paddle could not be loaded.')); return; }
      if (token.startsWith('test_')) paddle.Environment.set('sandbox');
      paddle.Initialize({ token, checkout: { settings: { displayMode: 'overlay', successUrl } } });
      resolve();
    };
    if ((window as any).Paddle) { start(); return; }
    const script = document.createElement('script');
    script.src = PADDLE_SCRIPT;
    script.async = true;
    script.onload = start;
    script.onerror = () => reject(new Error('Paddle could not be loaded.'));
    document.head.appendChild(script);
  });
}
