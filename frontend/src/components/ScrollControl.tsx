import { useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

export default function ScrollControl() {
  const railRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLSpanElement>(null);
  const percentRef = useRef<HTMLSpanElement>(null);
  const thumbSizeRef = useRef(48);
  const maxScrollRef = useRef(0);
  const dragRef = useRef<{ pointerId: number; offset: number } | null>(null);
  const availableRef = useRef(false);
  const hideTimerRef = useRef<number | null>(null);
  const revealRef = useRef<() => void>(() => {});
  const [enabled, setEnabled] = useState(false);
  const [visible, setVisible] = useState(false);
  const [surface, setSurface] = useState<'public' | 'workspace'>('public');

  useLayoutEffect(() => {
    const root = document.documentElement;
    const content = document.getElementById('root');
    const finePointer = window.matchMedia('(min-width: 900px) and (pointer: fine)');
    const forcedColors = window.matchMedia('(forced-colors: active)');
    let frame = 0;

    function armHide() {
      if (hideTimerRef.current !== null) window.clearTimeout(hideTimerRef.current);
      hideTimerRef.current = window.setTimeout(() => {
        if (dragRef.current || document.activeElement === railRef.current) {
          armHide();
          return;
        }
        setVisible(false);
        hideTimerRef.current = null;
      }, 5000);
    }

    function reveal() {
      if (!availableRef.current) return;
      setVisible(true);
      armHide();
    }
    revealRef.current = reveal;

    function sync() {
      frame = 0;
      const scroller = document.scrollingElement || root;
      const maxScroll = Math.max(0, scroller.scrollHeight - window.innerHeight);
      const active = finePointer.matches && !forcedColors.matches && maxScroll > 80;
      availableRef.current = active;
      maxScrollRef.current = maxScroll;
      root.classList.toggle('audoryn-rail-active', active);
      setEnabled((previous) => previous === active ? previous : active);
      if (!active) {
        setVisible(false);
        if (hideTimerRef.current !== null) window.clearTimeout(hideTimerRef.current);
        hideTimerRef.current = null;
      }
      const nextSurface = document.querySelector('.workspace-shell') ? 'workspace' : 'public';
      setSurface((previous) => previous === nextSurface ? previous : nextSurface);

      const rail = railRef.current;
      const track = trackRef.current;
      if (!rail || !track || !active) return;
      const trackHeight = track.clientHeight;
      const thumbSize = clamp(trackHeight * window.innerHeight / scroller.scrollHeight, 46, 86);
      const progress = clamp(scroller.scrollTop / maxScroll, 0, 1);
      const percentage = Math.round(progress * 100);
      thumbSizeRef.current = thumbSize;
      rail.style.setProperty('--scroll-thumb-size', `${thumbSize}px`);
      rail.style.setProperty('--scroll-thumb-offset', `${progress * (trackHeight - thumbSize)}px`);
      rail.style.setProperty('--scroll-progress', `${progress * 100}%`);
      rail.setAttribute('aria-valuenow', String(percentage));
      rail.setAttribute('aria-valuetext', `${percentage}% through page`);
      const percentText = `${String(percentage).padStart(2, '0')}%`;
      if (percentRef.current && percentRef.current.textContent !== percentText) percentRef.current.textContent = percentText;
    }

    function schedule() {
      if (!frame) frame = window.requestAnimationFrame(sync);
    }

    function handleScroll() {
      schedule();
      reveal();
    }

    function handleEdgePointer(event: globalThis.PointerEvent) {
      if (event.pointerType !== 'touch' && event.clientX >= window.innerWidth - 2) reveal();
    }

    const resizeObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule);
    resizeObserver?.observe(root);
    if (document.body) resizeObserver?.observe(document.body);
    const mutationObserver = content && typeof MutationObserver !== 'undefined' ? new MutationObserver(schedule) : null;
    if (content) mutationObserver?.observe(content, { childList: true, subtree: true });
    window.addEventListener('scroll', handleScroll, { passive: true });
    window.addEventListener('pointermove', handleEdgePointer, { passive: true });
    window.addEventListener('resize', schedule);
    finePointer.addEventListener('change', schedule);
    forcedColors.addEventListener('change', schedule);
    sync();

    return () => {
      window.cancelAnimationFrame(frame);
      if (hideTimerRef.current !== null) window.clearTimeout(hideTimerRef.current);
      resizeObserver?.disconnect();
      mutationObserver?.disconnect();
      window.removeEventListener('scroll', handleScroll);
      window.removeEventListener('pointermove', handleEdgePointer);
      window.removeEventListener('resize', schedule);
      finePointer.removeEventListener('change', schedule);
      forcedColors.removeEventListener('change', schedule);
      root.classList.remove('audoryn-rail-active');
      revealRef.current = () => {};
    };
  }, []);

  function scrollFromPointer(clientY: number, offset: number) {
    const track = trackRef.current;
    if (!track) return;
    const bounds = track.getBoundingClientRect();
    const travel = Math.max(1, bounds.height - thumbSizeRef.current);
    const progress = clamp((clientY - bounds.top - offset) / travel, 0, 1);
    window.scrollTo(0, progress * maxScrollRef.current);
  }

  function handlePointerDown(event: PointerEvent<HTMLDivElement>) {
    if (!enabled || (event.pointerType === 'mouse' && event.button !== 0)) return;
    const thumb = railRef.current?.querySelector('.audoryn-scroll-thumb');
    const onThumb = thumb instanceof Element && thumb.contains(event.target as Node);
    const offset = onThumb && thumb ? event.clientY - thumb.getBoundingClientRect().top : thumbSizeRef.current / 2;
    dragRef.current = { pointerId: event.pointerId, offset };
    if (hideTimerRef.current !== null) window.clearTimeout(hideTimerRef.current);
    hideTimerRef.current = null;
    event.currentTarget.setPointerCapture(event.pointerId);
    scrollFromPointer(event.clientY, offset);
    event.preventDefault();
  }

  function handlePointerMove(event: PointerEvent<HTMLDivElement>) {
    if (dragRef.current?.pointerId === event.pointerId) scrollFromPointer(event.clientY, dragRef.current.offset);
  }

  function handlePointerEnd(event: PointerEvent<HTMLDivElement>) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    revealRef.current();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    let next: number;
    const current = window.scrollY;
    switch (event.key) {
      case 'ArrowUp': next = current - 48; break;
      case 'ArrowDown': next = current + 48; break;
      case 'PageUp': next = current - window.innerHeight * .85; break;
      case 'PageDown': next = current + window.innerHeight * .85; break;
      case ' ': next = current + window.innerHeight * (event.shiftKey ? -.85 : .85); break;
      case 'Home': next = 0; break;
      case 'End': next = maxScrollRef.current; break;
      default: return;
    }
    event.preventDefault();
    window.scrollTo(0, clamp(next, 0, maxScrollRef.current));
  }

  return <div
    ref={railRef}
    className={`audoryn-scroll-control is-${surface}${enabled && visible ? ' is-visible' : ''}`}
    role='scrollbar'
    aria-label='Page position'
    aria-controls='audoryn-document'
    aria-orientation='vertical'
    aria-valuemin={0}
    aria-valuemax={100}
    aria-valuenow={0}
    tabIndex={enabled && visible ? 0 : -1}
    aria-hidden={!enabled || !visible}
    onPointerDown={handlePointerDown}
    onPointerMove={handlePointerMove}
    onPointerUp={handlePointerEnd}
    onPointerCancel={handlePointerEnd}
    onKeyDown={handleKeyDown}
  >
    <span className='audoryn-scroll-label' aria-hidden='true'>POSITION</span>
    <span ref={trackRef} className='audoryn-scroll-track' aria-hidden='true'>
      <span className='audoryn-scroll-progress' />
      <span className='audoryn-scroll-tick is-first' />
      <span className='audoryn-scroll-tick is-quarter' />
      <span className='audoryn-scroll-tick is-middle' />
      <span className='audoryn-scroll-tick is-three-quarter' />
      <span className='audoryn-scroll-tick is-last' />
      <span className='audoryn-scroll-thumb'><img src='/audoryn-mark.png' alt='' /></span>
    </span>
    <span ref={percentRef} className='audoryn-scroll-percent' aria-hidden='true'>00%</span>
  </div>;
}
