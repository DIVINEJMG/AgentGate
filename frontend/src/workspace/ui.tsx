import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react';
import { linkProps, type WorkspaceRoute } from './routes';

/* ---------- Identity ---------- */

const HUES = [152, 205, 262, 28, 340, 188, 96, 230];

/** A stable, calm colour per worker so people recognise workers at a glance. */
export function workerHue(seed: string) {
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) hash = (hash * 31 + seed.charCodeAt(index)) >>> 0;
  return HUES[hash % HUES.length];
}

export function initials(name: string) {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return ((words[0]?.[0] ?? '') + (words[1]?.[0] ?? words[0]?.[1] ?? '')).toUpperCase() || 'A';
}

export type Liveness = 'working' | 'waiting' | 'ready' | 'paused' | 'off';

export function Avatar({ name, seed, size = 32, live }: { name: string; seed?: string; size?: number; live?: Liveness }) {
  const hue = workerHue(seed ?? name);
  return <span className='ws-avatar' data-live={live} style={{ '--hue': hue, width: size, height: size, fontSize: Math.max(10, Math.round(size * 0.38)) } as React.CSSProperties} aria-hidden='true'>
    {initials(name)}
    {live && live !== 'off' && <i className='ws-avatar-dot' />}
  </span>;
}

/* ---------- Time ---------- */

export function relativeTime(value: string | null | undefined, now = Date.now()) {
  if (!value) return '—';
  const date = new Date(value).getTime();
  if (Number.isNaN(date)) return '—';
  const seconds = Math.round((now - date) / 1000);
  const future = seconds < 0;
  const abs = Math.abs(seconds);
  const unit = abs < 45 ? null : abs < 3600 ? `${Math.round(abs / 60)} min` : abs < 86400 ? `${Math.round(abs / 3600)} h` : abs < 604800 ? `${Math.round(abs / 86400)} d` : null;
  if (!unit) return abs < 45 ? (future ? 'in a moment' : 'just now') : new Date(date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  return future ? `in ${unit}` : `${unit} ago`;
}

/** Relative time that keeps itself current, with the exact time on hover. */
export function Ago({ value }: { value: string | null | undefined }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 30000); return () => window.clearInterval(timer); }, []);
  if (!value) return <span className='ws-muted'>—</span>;
  const date = new Date(value);
  return <time className='ws-time' dateTime={date.toISOString()} title={date.toLocaleString()}>{relativeTime(value, now)}</time>;
}

/* ---------- Status ---------- */

export type Tone = 'ok' | 'live' | 'warn' | 'danger' | 'info' | 'muted' | 'neutral';

const TONES: Record<string, Tone> = {
  active: 'ok', completed: 'ok', approved: 'ok', executed: 'ok', resolved: 'ok', ready: 'ok', healthy: 'ok', connected: 'ok', succeeded: 'ok',
  running: 'live', planning: 'live', working: 'live', claimed: 'live', processing: 'live',
  pending: 'warn', waiting_approval: 'warn', held: 'warn', open: 'warn', attention: 'warn', paused: 'warn', acknowledged: 'info', queued: 'info', scheduled: 'info', draft: 'neutral',
  failed: 'danger', blocked: 'danger', rejected: 'danger', policy_denied: 'danger', critical: 'danger', high: 'danger', suspended: 'danger',
  cancelled: 'muted', archived: 'muted', expired: 'muted', disconnected: 'muted',
};

export function toneFor(value: string | null | undefined): Tone {
  if (!value) return 'muted';
  const key = value.toLowerCase();
  if (TONES[key]) return TONES[key];
  if (key.startsWith('waiting')) return 'warn';
  return 'neutral';
}

export const humanize = (value: string | null | undefined) => (value ? value.replaceAll('_', ' ') : '—');
/** Sentence case for a status: `waiting_approval` → `Waiting approval`. */
export const sentence = (value: string | null | undefined) => { const text = humanize(value); return text.charAt(0).toUpperCase() + text.slice(1); };

export function State({ value, label, tone }: { value?: string | null; label?: string; tone?: Tone }) {
  return <span className='ws-state' data-tone={tone ?? toneFor(value)}>{label ?? sentence(value)}</span>;
}

/* ---------- Page structure ---------- */

export function PageHeader({ title, description, actions, back, eyebrow, children }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; back?: { label: string; to: WorkspaceRoute }; eyebrow?: ReactNode; children?: ReactNode }) {
  return <header className='ws-page-header'>
    <div className='ws-page-heading'>
      {back && <a className='ws-back' {...linkProps(back.to)}>← {back.label}</a>}
      {eyebrow && <div className='ws-eyebrow'>{eyebrow}</div>}
      <h1>{title}</h1>
      {description && <p>{description}</p>}
      {children}
    </div>
    {actions && <div className='ws-page-actions'>{actions}</div>}
  </header>;
}

export function Section({ title, count, action, children, id, description }: { title: ReactNode; count?: number | string; action?: ReactNode; children: ReactNode; id?: string; description?: ReactNode }) {
  const generated = useId();
  const headingId = id ?? generated;
  return <section className='ws-section' aria-labelledby={headingId}>
    <div className='ws-section-head'>
      <h2 id={headingId}>{title}{count !== undefined && <span className='ws-count'>{count}</span>}</h2>
      {description && <p>{description}</p>}
      {action && <div className='ws-section-action'>{action}</div>}
    </div>
    {children}
  </section>;
}

export function EmptyState({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className='ws-empty'>{icon && <span className='ws-empty-icon' aria-hidden='true'>{icon}</span>}<strong>{title}</strong>{children && <p>{children}</p>}{action && <div className='ws-empty-action'>{action}</div>}</div>;
}

export function Pills<T extends string>({ value, options, onChange, label }: { value: T; options: Array<{ value: T; label: string; count?: number }>; onChange: (value: T) => void; label: string }) {
  return <div className='ws-pills' role='group' aria-label={label}>{options.map((option) => <button key={option.value} type='button' aria-pressed={value === option.value} onClick={() => onChange(option.value)}>{option.label}{option.count !== undefined && <span>{option.count}</span>}</button>)}</div>;
}

export function Notice({ tone = 'info', children, action }: { tone?: 'info' | 'warn' | 'danger' | 'ok'; children: ReactNode; action?: ReactNode }) {
  const Icon = tone === 'ok' ? CheckCircle2 : tone === 'info' ? Info : AlertTriangle;
  return <div className='ws-notice' data-tone={tone} role={tone === 'danger' ? 'alert' : 'status'}><Icon size={16} aria-hidden='true' /><div>{children}</div>{action}</div>;
}

/* ---------- Loading ---------- */

export function SkeletonLines({ rows = 4, avatar = false }: { rows?: number; avatar?: boolean }) {
  return <div className='ws-skeleton' aria-hidden='true'>{Array.from({ length: rows }, (_, index) => <div key={index} className='ws-skeleton-row'>{avatar && <i className='ws-skeleton-avatar' />}<span><i style={{ width: `${62 - (index % 3) * 12}%` }} /><i style={{ width: `${38 - (index % 2) * 10}%` }} /></span></div>)}</div>;
}

export function PageSkeleton({ title = true }: { title?: boolean }) {
  return <div className='ws-page' aria-busy='true' aria-label='Loading'>
    {title && <div className='ws-page-header'><div className='ws-skeleton'><i style={{ width: 180, height: 22 }} /><i style={{ width: 320 }} /></div></div>}
    <SkeletonLines rows={5} avatar />
  </div>;
}

/* ---------- Overlays ---------- */

export function Sheet({ open, onClose, title, subtitle, children, footer, wide }: { open: boolean; onClose: () => void; title: ReactNode; subtitle?: ReactNode; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) { returnFocus.current = document.activeElement as HTMLElement | null; dialog.showModal(); }
    if (!open && dialog.open) { dialog.close(); returnFocus.current?.focus(); }
  }, [open]);
  return <dialog ref={ref} className='ws-sheet' data-wide={wide ? 'true' : undefined} aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); onClose(); }} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    {open && <>
      <header><div><h2 id={titleId}>{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button type='button' className='ws-icon-button' aria-label='Close' onClick={onClose}><X size={16} /></button></header>
      <div className='ws-sheet-body'>{children}</div>
      {footer && <footer>{footer}</footer>}
    </>}
  </dialog>;
}

/** A confirmation that replaces window.confirm / window.prompt, with an optional required note. */
export function Confirm({ open, title, body, confirmLabel, tone = 'default', note, onConfirm, onCancel }: {
  open: boolean; title: string; body?: ReactNode; confirmLabel: string; tone?: 'default' | 'danger';
  note?: { label: string; required?: boolean; minLength?: number; placeholder?: string };
  onConfirm: (note: string) => void; onCancel: () => void;
}) {
  const [value, setValue] = useState('');
  useEffect(() => { if (open) setValue(''); }, [open]);
  const valid = !note?.required || value.trim().length >= (note.minLength ?? 1);
  return <Sheet open={open} onClose={onCancel} title={title} footer={<><button type='button' className='ws-button' onClick={onCancel}>Cancel</button><button type='button' className={tone === 'danger' ? 'ws-button ws-button-danger' : 'ws-button ws-button-primary'} disabled={!valid} onClick={() => onConfirm(value.trim())}>{confirmLabel}</button></>}>
    {body && <div className='ws-prose'>{body}</div>}
    {note && <label className='ws-field'><span>{note.label}</span><textarea rows={3} value={value} placeholder={note.placeholder} onChange={(event) => setValue(event.target.value)} autoFocus /></label>}
  </Sheet>;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className='ws-kbd'>{children}</kbd>;
}
