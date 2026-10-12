// Motion helpers. Every animation on the site goes through these, so it stops when the visitor
// prefers reduced motion, and pauses while off screen, hidden or being interacted with.
import {useEffect, useRef, useState, type RefObject} from 'react';

export function prefersReducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Calls `tick` every `ms` while the element is on screen, the tab is visible and `active` is true. */
export function useTicker(element: RefObject<Element | null>, ms: number, tick: () => void, active = true) {
  const callback = useRef(tick);
  callback.current = tick;
  useEffect(() => {
    const target = element.current;
    if (!target || !active || prefersReducedMotion()) return;
    let timer: ReturnType<typeof setInterval> | undefined;
    let visible = false;
    const start = () => {if (!timer && visible && !document.hidden) timer = setInterval(() => callback.current(), ms);};
    const stop = () => {if (timer) clearInterval(timer); timer = undefined;};
    const observer = new IntersectionObserver(([entry]) => {visible = entry.isIntersecting; if (visible) start(); else stop();}, {threshold: .2});
    const onVisibility = () => (document.hidden ? stop() : start());
    observer.observe(target);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {stop(); observer.disconnect(); document.removeEventListener('visibilitychange', onVisibility);};
  }, [element, ms, active]);
}

/** Types each word in turn into a slot. Returns the visible text; the full first word without motion. */
export function useTypedWords(element: RefObject<Element | null>, words: string[]) {
  const [state, setState] = useState({index: 0, length: words[0]?.length ?? 0, deleting: false, hold: 18});
  useTicker(element, 70, () => setState(current => {
    const word = words[current.index];
    if (current.hold > 0) return {...current, hold: current.hold - 1};
    if (!current.deleting) return current.length < word.length ? {...current, length: current.length + 1} : {...current, deleting: true, hold: 22};
    if (current.length > 0) return {...current, length: current.length - 1};
    return {index: (current.index + 1) % words.length, length: 0, deleting: false, hold: 3};
  }));
  return words[state.index].slice(0, state.length);
}
