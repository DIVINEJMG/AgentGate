import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react';
import { loadSystemStatus } from '../lib/systemApi';
import { isWorkspacePreview } from '../preview/previewMode';

/* ---------- Toasts ---------- */

export type Toast = { key: string; tone: 'danger' | 'warn' | 'info' | 'ok'; title: string; body?: string; sticky?: boolean };
const TOAST = 'audoryn:toast';
const OUTAGE = 'outage';

export function notify(toast: Toast) {
  window.dispatchEvent(new CustomEvent<Toast>(TOAST, { detail: toast }));
}

/** Names what is unavailable. Returns null when Audoryn reports itself operational. */
export async function diagnoseOutage(): Promise<Toast | null> {
  if (!navigator.onLine) return { key: 'offline', tone: 'warn', title: 'You’re offline', body: 'Check your connection. Work continues on Audoryn and this page catches up when you reconnect.', sticky: true };
  try {
    const status = await loadSystemStatus('v2');
    if (status.state === 'operational') return null;
    return { key: OUTAGE, tone: 'danger', title: 'Audoryn is degraded', body: 'Some services are not fully available. Actions stay blocked until they can be verified.', sticky: true };
  } catch {
    return { key: OUTAGE, tone: 'danger', title: 'Audoryn API unreachable', body: 'The service didn’t respond. What you see may be out of date, and changes can’t be saved right now.', sticky: true };
  }
}

let lastDiagnosis = 0;
async function reportOutage() {
  if (isWorkspacePreview() || Date.now() - lastDiagnosis < 10_000) return;
  lastDiagnosis = Date.now();
  const outage = await diagnoseOutage();
  if (outage) notify(outage);
}

export function Toaster() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  useEffect(() => {
    const add = (event: Event) => { const toast = (event as CustomEvent<Toast>).detail; setToasts((current) => [...current.filter((item) => item.key !== toast.key), toast].slice(-3)); };
    const request = (event: Event) => { const detail = (event as CustomEvent<{ phase: string; failure?: string }>).detail; if (detail.phase === 'end' && detail.failure) void reportOutage(); };
    const online = () => { setToasts((current) => current.filter((item) => item.key !== 'offline')); notify({ key: 'online', tone: 'ok', title: 'Back online' }); };
    const offline = () => notify({ key: 'offline', tone: 'warn', title: 'You’re offline', body: 'Check your connection. This page catches up when you reconnect.', sticky: true });
    window.addEventListener(TOAST, add);
    window.addEventListener('audoryn:request', request);
    window.addEventListener('online', online);
    window.addEventListener('offline', offline);
    return () => { window.removeEventListener(TOAST, add); window.removeEventListener('audoryn:request', request); window.removeEventListener('online', online); window.removeEventListener('offline', offline); };
  }, []);

  // While an outage is reported, re-check and clear it once Audoryn recovers.
  const outageShown = toasts.some((toast) => toast.key === OUTAGE);
  useEffect(() => {
    if (!outageShown) return;
    const timer = window.setInterval(() => {
      void diagnoseOutage().then((outage) => {
        if (outage) { setToasts((current) => current.map((item) => item.key === OUTAGE ? outage : item)); return; }
        setToasts((current) => current.filter((item) => item.key !== OUTAGE));
        notify({ key: 'recovered', tone: 'ok', title: 'Audoryn is available again' });
      });
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [outageShown]);

  useEffect(() => {
    const timers = toasts.filter((toast) => !toast.sticky).map((toast) => window.setTimeout(() => setToasts((current) => current.filter((item) => item.key !== toast.key)), 5000));
    return () => timers.forEach(window.clearTimeout);
  }, [toasts]);

  return <div className='ws-toasts' aria-live='polite'>
    {toasts.map((toast) => {
      const Icon = toast.tone === 'ok' ? CheckCircle2 : toast.tone === 'info' ? Info : AlertTriangle;
      return <div key={toast.key} className='ws-toast' data-tone={toast.tone} role={toast.tone === 'danger' ? 'alert' : 'status'}>
        <Icon size={16} aria-hidden='true' />
        <div><strong>{toast.title}</strong>{toast.body && <p>{toast.body}</p>}</div>
        <button type='button' className='ws-icon-button' aria-label={`Dismiss ${toast.title}`} onClick={() => setToasts((current) => current.filter((item) => item.key !== toast.key))}><X size={14} /></button>
      </div>;
    })}
  </div>;
}

/* ---------- Progress ---------- */

const SPINNER_DELAY_MS = 600;
const SLOW_NAVIGATION_MS = 12_000;
const ROUTE_EVENT = 'audoryn:route';

/**
 * Feedback between a click and the next page being ready, as in the Console:
 * a progress bar at the top edge, a busy mark on the link that was clicked,
 * repeat clicks on that link ignored, a centred spinner only when loading is
 * slow, and an outage check if the page still has not loaded after 12 seconds.
 * Background reads (refreshes, realtime catch-up) only show the bar.
 */
export function RequestProgress() {
  const [phase, setPhase] = useState<'idle' | 'loading' | 'done'>('idle');
  const [pending, setPending] = useState<string | null>(null);
  const [slow, setSlow] = useState(false);
  const inflight = useRef(0);
  const barTimer = useRef<number | null>(null);
  const nav = useRef<{ destination: string; routed: boolean } | null>(null);
  const marked = useRef<HTMLElement[]>([]);

  useEffect(() => {
    const clearMarks = () => { marked.current.forEach((element) => element.removeAttribute('data-pending')); marked.current = []; };
    const settle = () => {
      if (barTimer.current !== null) { window.clearTimeout(barTimer.current); barTimer.current = null; }
      setPhase((current) => current === 'idle' ? 'idle' : 'done');
      window.setTimeout(() => setPhase((current) => current === 'done' ? 'idle' : current), 320);
    };
    const finish = () => { if (!nav.current) return; nav.current = null; clearMarks(); setPending(null); setSlow(false); settle(); };
    const checkDone = () => { if (nav.current?.routed && inflight.current === 0) finish(); };

    function onRequest(event: Event) {
      const detail = (event as CustomEvent<{ phase: string }>).detail;
      inflight.current = Math.max(0, inflight.current + (detail.phase === 'start' ? 1 : -1));
      if (nav.current) { if (inflight.current === 0) window.setTimeout(checkDone, 60); return; }
      if (inflight.current > 0) {
        if (barTimer.current === null) barTimer.current = window.setTimeout(() => { barTimer.current = null; if (inflight.current > 0) setPhase('loading'); }, 150);
      } else settle();
    }
    function onClick(event: MouseEvent) {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const anchor = (event.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
      if (!anchor || !anchor.closest('.ws-app') || anchor.hasAttribute('download') || (anchor.target && anchor.target !== '_self')) return;
      const url = new URL(anchor.href, window.location.href);
      if (url.origin !== window.location.origin || !/^\/(app|workspace-preview)(\/|$)/.test(url.pathname)) return;
      const destination = url.pathname + url.search;
      if (destination === window.location.pathname + window.location.search) return;
      if (nav.current?.destination === destination) { event.preventDefault(); event.stopPropagation(); return; }
      clearMarks();
      anchor.setAttribute('data-pending', 'true');
      marked.current.push(anchor);
      nav.current = { destination, routed: false };
      setPending(destination);
      setSlow(false);
      setPhase('loading');
    }
    function onRoute() {
      if (!nav.current) return;
      nav.current.routed = true;
      // Give the new page a moment to start its reads before deciding it is ready.
      window.setTimeout(checkDone, 120);
    }
    window.addEventListener('audoryn:request', onRequest);
    window.addEventListener(ROUTE_EVENT, onRoute);
    window.addEventListener('popstate', onRoute);
    document.addEventListener('click', onClick, true);
    return () => {
      window.removeEventListener('audoryn:request', onRequest);
      window.removeEventListener(ROUTE_EVENT, onRoute);
      window.removeEventListener('popstate', onRoute);
      document.removeEventListener('click', onClick, true);
    };
  }, []);

  useEffect(() => {
    if (!pending) return;
    const spinner = window.setTimeout(() => setSlow(true), SPINNER_DELAY_MS);
    const timeout = window.setTimeout(() => {
      marked.current.forEach((element) => element.removeAttribute('data-pending')); marked.current = [];
      nav.current = null; setPending(null); setSlow(false); setPhase('idle');
      notify({ key: 'slow-navigation', tone: 'warn', title: 'This page is taking longer than expected', body: 'You can keep working here and try again in a moment.' });
      void reportOutage();
    }, SLOW_NAVIGATION_MS);
    return () => { window.clearTimeout(spinner); window.clearTimeout(timeout); };
  }, [pending]);

  return <>
    <div className='ws-progress' data-phase={phase} aria-hidden='true'><i /></div>
    {pending && slow && <div className='ws-nav-spinner' role='status'><span className='ws-spinner' aria-hidden='true' />Loading page…</div>}
  </>;
}
