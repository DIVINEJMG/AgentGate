/** How plan limits, usage and prices read across "Usage & billing" and the Plans page. */

/** A limit or its use, in the unit its key implies (bytes, seconds, or a count). */
export function formatAmount(key: string, value: number): string {
  if (key.endsWith('.bytes')) { const gb = value / 1024 ** 3; return gb >= 1 ? `${+gb.toFixed(1)} GB` : `${Math.round(value / 1024 ** 2)} MB`; }
  if (key.includes('.seconds.')) { const hours = value / 3600; return hours >= 1 ? `${+hours.toFixed(1)} h` : `${Math.round(value / 60)} min`; }
  return Math.round(value).toLocaleString(undefined, value >= 1_000_000 ? { notation: 'compact', maximumFractionDigits: 1 } : undefined);
}

/** A price in minor units (kobo, cents) as money. */
export function formatMoney(minor: number, currency: string): string {
  try { return new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: minor % 100 ? 2 : 0 }).format(minor / 100); }
  catch { return `${(minor / 100).toLocaleString()} ${currency}`; }
}

/** A limit's name without its unit words ("seconds of coding time" -> "coding time"). */
export function shortName(name: string): string { return name.replace(/^(seconds|bytes) of /, ''); }

export function capitalise(value: string): string { return value.charAt(0).toUpperCase() + value.slice(1); }

export function formatDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}
