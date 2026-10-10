import { useEffect, useState } from 'react';

/**
 * In-app history. Every workspace navigation is numbered inside a "session segment",
 * so Back and Forward only move between workspace pages and never leave the app.
 * Each entry also remembers its scroll position and a readable title.
 */
type Entry = { idx: number; url: string; title: string; trail: string };
type Stored = { segment: string; entries: Entry[] };
type State = { wsSeg?: string; wsIdx?: number; wsScroll?: number } | null;

const KEY = 'audoryn.ws.history';
const CHANGE = 'audoryn:history';
const MAX = 60;
let segment = '';
let entries: Entry[] = [];
let current = 0;
let started = false;

const here = () => window.location.pathname + window.location.search;
const emit = () => window.dispatchEvent(new Event(CHANGE));
function persist() { try { window.sessionStorage.setItem(KEY, JSON.stringify({ segment, entries } satisfies Stored)); } catch { /* history menu is a convenience */ } }
function newSegment() { return Math.random().toString(36).slice(2, 10); }

/** Called once when the workspace mounts. */
export function startHistory() {
  if (started) return;
  started = true;
  try { window.history.scrollRestoration = 'manual'; } catch { /* older browsers */ }
  let stored: Stored | null = null;
  try { stored = JSON.parse(window.sessionStorage.getItem(KEY) ?? 'null'); } catch { stored = null; }
  const state = window.history.state as State;
  if (state?.wsSeg && typeof state.wsIdx === 'number' && stored?.segment === state.wsSeg) {
    segment = stored.segment; entries = stored.entries; current = state.wsIdx;
    if (!entries.some((entry) => entry.idx === current)) entries.push({ idx: current, url: here(), title: '', trail: '' });
  } else {
    // A fresh arrival (new tab, or coming from outside the workspace) starts a new segment.
    segment = newSegment(); current = 0; entries = [{ idx: 0, url: here(), title: '', trail: '' }];
    window.history.replaceState({ ...(state ?? {}), wsSeg: segment, wsIdx: 0 }, '', window.location.href);
  }
  persist();
  window.addEventListener('popstate', onPop);
}

function onPop(event: PopStateEvent) {
  const state = event.state as State;
  if (state?.wsSeg === segment && typeof state.wsIdx === 'number') {
    current = state.wsIdx;
    const entry = entries.find((item) => item.idx === current);
    if (entry) entry.url = here(); else entries.push({ idx: current, url: here(), title: '', trail: '' });
    restoreScroll(state.wsScroll ?? 0);
  } else {
    // An entry from before this segment: start again from here.
    segment = newSegment(); current = 0; entries = [{ idx: 0, url: here(), title: '', trail: '' }];
    window.history.replaceState({ ...(state ?? {}), wsSeg: segment, wsIdx: 0 }, '', window.location.href);
    window.scrollTo({ top: 0 });
  }
  persist(); emit();
}

/** Restore after the page has had a chance to render its data; keeps trying briefly while content loads. */
function restoreScroll(top: number) {
  const until = Date.now() + 2000;
  const attempt = () => {
    const room = document.documentElement.scrollHeight - window.innerHeight;
    window.scrollTo({ top: Math.min(top, Math.max(0, room)) });
    if (room < top && Date.now() < until) window.setTimeout(attempt, 80);
  };
  window.requestAnimationFrame(attempt);
}

/** Used by navigate(): remembers where we were, then records the new entry. */
export function pushEntry(url: string) {
  window.history.replaceState({ ...(window.history.state ?? {}), wsSeg: segment, wsIdx: current, wsScroll: window.scrollY }, '', window.location.href);
  current += 1;
  entries = [...entries.filter((entry) => entry.idx < current), { idx: current, url, title: '', trail: '' }].slice(-MAX);
  window.history.pushState({ wsSeg: segment, wsIdx: current, wsScroll: 0 }, '', url);
  persist(); emit();
}
export function replaceEntry(url: string) {
  window.history.replaceState({ ...(window.history.state ?? {}), wsSeg: segment, wsIdx: current }, '', url);
  const entry = entries.find((item) => item.idx === current);
  if (entry) entry.url = url;
  persist(); emit();
}

/** The breadcrumb names the current entry, so tooltips and the history menu read naturally. */
export function nameEntry(title: string, trail: string) {
  const entry = entries.find((item) => item.idx === current);
  if (!entry || (entry.title === title && entry.trail === trail)) return;
  entry.title = title; entry.trail = trail;
  persist(); emit();
}

export function goTo(idx: number) { if (entries.some((entry) => entry.idx === idx) && idx !== current) window.history.go(idx - current); }

export function useHistoryState() {
  const [, setTick] = useState(0);
  useEffect(() => {
    const update = () => setTick((tick) => tick + 1);
    window.addEventListener(CHANGE, update);
    return () => window.removeEventListener(CHANGE, update);
  }, []);
  const back = entries.filter((entry) => entry.idx < current).sort((a, b) => b.idx - a.idx);
  const forward = entries.filter((entry) => entry.idx > current).sort((a, b) => a.idx - b.idx);
  return {
    canBack: back[0]?.idx === current - 1,
    canForward: forward[0]?.idx === current + 1,
    back: back.filter((entry, index) => entry.idx === current - 1 - index).slice(0, 10),
    forward: forward.filter((entry, index) => entry.idx === current + 1 + index).slice(0, 10),
  };
}

/* ---------- Page titles for the breadcrumb ---------- */

const TITLE = 'audoryn:page-title';
const titles = new Map<string, string>();

/** A page names the record it shows (a connection, a result…) for the breadcrumb. */
export function usePageTitle(title: string | null | undefined) {
  useEffect(() => {
    if (!title) return;
    const path = window.location.pathname;
    titles.set(path, title);
    window.dispatchEvent(new Event(TITLE));
  }, [title]);
}
export function usePageTitles() {
  const [, setTick] = useState(0);
  useEffect(() => {
    const update = () => setTick((tick) => tick + 1);
    window.addEventListener(TITLE, update);
    return () => window.removeEventListener(TITLE, update);
  }, []);
  return (path: string) => titles.get(path);
}

/** True when stepping by delta stays inside this workspace session. */
export function canStep(delta: -1 | 1) { return entries.some((entry) => entry.idx === current + delta); }
