import { useEffect, useRef, useState } from 'react';

const HIDE_AFTER_MS = 1200;
const EDGE_ZONE_PX = 28;
type Tick = { y: number; label: string; target: Element };

const sectionHeadings = () => [...document.querySelectorAll('.ws-main .ws-section-head h2, .ws-main .ws-doc-body h2')]
  .filter((element) => (element as HTMLElement).offsetParent !== null && !element.closest('dialog'));
const headingLabel = (element: Element) => (element.textContent ?? '').replace(/\s*\d+\s*$/, '').trim();
const offsetTop = () => {
  const bar = document.querySelector('.ws-topbar') as HTMLElement | null;
  const preview = document.querySelector('.ws-preview-bar') as HTMLElement | null;
  return (bar?.offsetHeight ?? 0) + (preview?.offsetHeight ?? 0) + 16;
};

/**
 * The page's own scrollbar: a thin track on the right with a pill thumb and a tick for
 * every section heading. Native scrolling (wheel, touch, keys) is untouched; this only
 * reflects it and adds dragging, jumping to sections and a "you are here" label.
 * Off for touch, narrow screens and forced colours, where the system bar stays.
 */
export function PageScrollbar({ routeKey }: { routeKey: string }) {
  const rail = useRef<HTMLDivElement>(null);
  const thumb = useRef<HTMLSpanElement>(null);
  const hideTimer = useRef<number | null>(null);
  const drag = useRef<{ pointerId: number; offset: number } | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [scrollable, setScrollable] = useState(false);
  const [visible, setVisible] = useState(false);
  const [near, setNear] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [ticks, setTicks] = useState<Tick[]>([]);
  const [hoverTick, setHoverTick] = useState<number | null>(null);
  const [here, setHere] = useState('');
  const ticksReady = useRef(false);
  const collectRef = useRef<() => void>(() => {});

  // Decide whether the custom bar replaces the native one.
  useEffect(() => {
    const query = window.matchMedia('(min-width: 861px) and (pointer: fine) and (forced-colors: none)');
    const update = () => setEnabled(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    const root = document.documentElement;
    if (enabled) root.dataset.wsScrollbar = 'custom'; else delete root.dataset.wsScrollbar;
    return () => { delete root.dataset.wsScrollbar; };
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    let frame = 0;
    const measure = () => {
      frame = 0;
      const doc = document.documentElement;
      const max = doc.scrollHeight - window.innerHeight;
      const track = rail.current?.clientHeight ?? 0;
      setScrollable(max > 8);
      if (max > 8 && !ticksReady.current) { ticksReady.current = true; window.setTimeout(collectRef.current, 0); }
      if (max <= 8 || !track || !thumb.current) return;
      const size = Math.max(36, track * window.innerHeight / doc.scrollHeight);
      const top = (track - size) * Math.min(1, Math.max(0, window.scrollY / max));
      thumb.current.style.height = `${size}px`;
      thumb.current.style.transform = `translateY(${top}px)`;
      const headings = sectionHeadings();
      const marker = window.scrollY + offsetTop() + 8;
      const current = [...headings].reverse().find((element) => element.getBoundingClientRect().top + window.scrollY <= marker);
      setHere(current ? headingLabel(current) : '');
    };
    const schedule = () => { if (!frame) frame = window.requestAnimationFrame(measure); };
    const collect = () => {
      const track = rail.current?.clientHeight ?? 0;
      const doc = document.documentElement;
      const max = doc.scrollHeight - window.innerHeight;
      if (!track || max <= 8) { setTicks([]); return; }
      // Like a minimap: each tick sits at its heading's share of the whole page.
      const next = sectionHeadings().map((element) => {
        const target = element.getBoundingClientRect().top + window.scrollY;
        return { y: Math.min(track - 4, Math.max(4, track * target / doc.scrollHeight)), label: headingLabel(element), target: element };
      }).filter((tick, index, all) => tick.label && (index === 0 || Math.abs(tick.y - all[index - 1].y) > 6));
      setTicks(next);
      schedule();
    };
    collectRef.current = collect;
    ticksReady.current = false;
    const onScroll = () => {
      schedule();
      setVisible(true);
      if (hideTimer.current) window.clearTimeout(hideTimer.current);
      hideTimer.current = window.setTimeout(() => setVisible(false), HIDE_AFTER_MS);
    };
    const onMove = (event: PointerEvent) => setNear(event.pointerType === 'mouse' && window.innerWidth - event.clientX <= EDGE_ZONE_PX);
    let collectTimer = 0;
    const later = () => { window.clearTimeout(collectTimer); collectTimer = window.setTimeout(collect, 150); };
    const resize = new ResizeObserver(later);
    resize.observe(document.body);
    const main = document.querySelector('.ws-main');
    const mutations = new MutationObserver(later);
    if (main) mutations.observe(main, { childList: true, subtree: true });
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', later);
    window.addEventListener('pointermove', onMove, { passive: true });
    collect();
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      window.clearTimeout(collectTimer);
      resize.disconnect(); mutations.disconnect();
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', later);
      window.removeEventListener('pointermove', onMove);
    };
  }, [enabled, routeKey]);

  if (!enabled) return null;

  const scrollToTrackY = (y: number, size: number) => {
    const track = rail.current?.clientHeight ?? 1;
    const max = document.documentElement.scrollHeight - window.innerHeight;
    window.scrollTo({ top: Math.max(0, Math.min(max, (y / Math.max(1, track - size)) * max)) });
  };
  const show = visible || near || dragging || hoverTick !== null;

  return <div ref={rail} className='ws-scrollbar' data-scrollable={scrollable ? 'true' : 'false'} data-visible={show && scrollable ? 'true' : undefined} data-expanded={near || dragging ? 'true' : undefined} aria-hidden='true'
    onPointerDown={(event) => {
      if ((event.target as Element).closest('.ws-scrollbar-tick')) return;
      const size = thumb.current?.offsetHeight ?? 36;
      const top = rail.current!.getBoundingClientRect().top;
      if (event.target === thumb.current) drag.current = { pointerId: event.pointerId, offset: event.clientY - thumb.current!.getBoundingClientRect().top };
      else { drag.current = { pointerId: event.pointerId, offset: size / 2 }; scrollToTrackY(event.clientY - top - size / 2, size); }
      event.currentTarget.setPointerCapture(event.pointerId);
      setDragging(true);
      event.preventDefault();
    }}
    onPointerMove={(event) => {
      if (!drag.current || drag.current.pointerId !== event.pointerId) return;
      const size = thumb.current?.offsetHeight ?? 36;
      scrollToTrackY(event.clientY - rail.current!.getBoundingClientRect().top - drag.current.offset, size);
    }}
    onPointerUp={(event) => { if (drag.current?.pointerId === event.pointerId) { drag.current = null; setDragging(false); } }}
    onPointerCancel={() => { drag.current = null; setDragging(false); }}>
    <span className='ws-scrollbar-track' />
    {ticks.map((tick, index) => <button key={`${tick.label}-${index}`} type='button' tabIndex={-1} className='ws-scrollbar-tick' style={{ top: tick.y }}
      onPointerEnter={() => setHoverTick(index)} onPointerLeave={() => setHoverTick(null)}
      onClick={() => window.scrollTo({ top: tick.target.getBoundingClientRect().top + window.scrollY - offsetTop(), behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })}>
      {hoverTick === index && <span className='ws-scrollbar-label'>{tick.label}</span>}
    </button>)}
    <span ref={thumb} className='ws-scrollbar-thumb'>{dragging && here && <span className='ws-scrollbar-label ws-scrollbar-here'>{here}</span>}</span>
  </div>;
}

/**
 * Scroll areas whose bar is hidden (sidebar, tab rows, steppers) get soft fades at the
 * edges that have more content, so overflow stays discoverable. The active tab is kept
 * in view on narrow screens.
 */
export function useEdgeFades(routeKey: string) {
  useEffect(() => {
    const selector = '.ws-nav, .ws-tabs, .ws-stepper, .ws-crumbs ol';
    const mark = (element: Element) => {
      const el = element as HTMLElement;
      const vertical = el.classList.contains('ws-nav');
      const start = vertical ? el.scrollTop > 2 : el.scrollLeft > 2;
      const end = vertical ? el.scrollHeight - el.clientHeight - el.scrollTop > 2 : el.scrollWidth - el.clientWidth - el.scrollLeft > 2;
      el.dataset.fade = start && end ? 'both' : start ? 'start' : end ? 'end' : '';
      if (!el.dataset.fade) delete el.dataset.fade;
    };
    const all = () => document.querySelectorAll(selector).forEach(mark);
    const onScroll = (event: Event) => { const target = event.target as Element; if (target instanceof Element && target.matches(selector)) mark(target); };
    const frame = window.requestAnimationFrame(() => {
      all();
      // Bring the active tab into view horizontally only, so page scroll positions are untouched.
      document.querySelectorAll('.ws-tabs [aria-current="page"], .ws-crumbs [aria-current="page"]').forEach((element) => {
        const row = element.closest('.ws-tabs, .ws-crumbs ol') as HTMLElement | null;
        if (!row || row.scrollWidth <= row.clientWidth) return;
        const item = element as HTMLElement;
        const left = item.offsetLeft - row.offsetLeft;
        if (left < row.scrollLeft || left + item.offsetWidth > row.scrollLeft + row.clientWidth) row.scrollLeft = Math.max(0, left - 24);
      });
    });
    const resize = new ResizeObserver(all);
    document.querySelectorAll(selector).forEach((element) => resize.observe(element));
    document.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', all);
    const timer = window.setTimeout(all, 400);
    return () => { window.cancelAnimationFrame(frame); window.clearTimeout(timer); resize.disconnect(); document.removeEventListener('scroll', onScroll, true); window.removeEventListener('resize', all); };
  }, [routeKey]);
}
