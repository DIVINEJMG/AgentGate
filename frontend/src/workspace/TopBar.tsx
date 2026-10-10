import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { ArrowLeft, ArrowRight, ChevronRight } from 'lucide-react';
import { toolBySlug } from './connections/data';
import { canStep, goTo, nameEntry, useHistoryState, usePageTitles } from './history';
import { useLive } from './live';
import { linkProps, queryParam, type WorkspaceRoute } from './routes';
import { sentence } from './ui';

export type Crumb = { label: string; to?: WorkspaceRoute };

const PAGE_LABEL: Partial<Record<WorkspaceRoute['page'], string>> = {
  home: 'Home', inbox: 'Inbox', workers: 'Workers', conversations: 'Conversations', jobs: 'Jobs', results: 'Results', memory: 'Memory',
  activity: 'Activity', runs: 'Runs', performance: 'Performance', policies: 'Policies', risk: 'Risk', incidents: 'Incidents', audit: 'Audit',
  connections: 'Connections', settings: 'Settings',
};
const TAB_LABEL: Record<string, string> = { conversations: 'Conversations', jobs: 'Jobs', results: 'Results', memory: 'Memory', settings: 'Settings', resources: 'Resources', access: 'Access' };

/** Where you are, as a path you can click back up. Record names come from the page itself. */
export function useCrumbs(route: WorkspaceRoute): Crumb[] {
  const live = useLive();
  const titleFor = usePageTitles();
  const title = titleFor(window.location.pathname);
  const top = (page: WorkspaceRoute['page'], to: WorkspaceRoute): Crumb => ({ label: PAGE_LABEL[page] ?? sentence(page), to });
  switch (route.page) {
    case 'worker': {
      const worker = live.workers.find((item) => item.id === route.workerId);
      const name = worker?.name ?? title ?? 'Worker';
      return route.tab === 'overview' ? [top('workers', { page: 'workers' }), { label: name }] : [top('workers', { page: 'workers' }), { label: name, to: { page: 'worker', workerId: route.workerId, tab: 'overview' } }, { label: TAB_LABEL[route.tab] ?? sentence(route.tab) }];
    }
    case 'conversations': {
      if (!route.workerId) return [{ label: 'Conversations' }];
      const worker = live.workers.find((item) => item.id === route.workerId);
      const base = [top('conversations', { page: 'conversations' }), { label: worker?.name ?? 'Worker', to: { page: 'worker' as const, workerId: route.workerId, tab: 'overview' as const } }];
      return [...base, { label: route.threadId === 'new' ? 'New chat' : title ?? 'Chat' }];
    }
    case 'inbox': return route.id ? [top('inbox', { page: 'inbox' }), { label: title ?? sentence(route.kind) }] : [{ label: 'Inbox' }];
    case 'jobs': return route.jobId ? [top('jobs', { page: 'jobs' }), { label: title ?? 'Job' }] : [{ label: queryParam('view') === 'queue' ? 'Jobs · Work queue' : queryParam('view') === 'automation' ? 'Jobs · Automation' : 'Jobs' }];
    case 'results': return route.resultId ? [top('results', { page: 'results' }), { label: title ?? 'Result' }] : [{ label: 'Results' }];
    case 'activity': return route.actionId ? [top('activity', { page: 'activity' }), { label: title ?? 'Action' }] : [{ label: 'Activity' }];
    case 'runs': return route.runId ? [top('runs', { page: 'runs' }), { label: title ?? 'Run' }] : [{ label: 'Runs' }];
    case 'connections': {
      const home = top('connections', { page: 'connections', view: 'home' });
      if (route.view === 'home') return [{ label: 'Connections' }];
      if (route.view === 'directory') return [home, { label: 'Directory' }];
      if (route.view === 'tool') return [home, { label: 'Directory', to: { page: 'connections', view: 'directory' } }, { label: toolBySlug(route.id)?.name ?? 'Tool' }];
      if (route.view === 'access') return [home, { label: 'Access map' }];
      if (route.view === 'identities') return route.id ? [home, { label: 'Agent identities', to: { page: 'connections', view: 'identities' } }, { label: title ?? 'Identity' }] : [home, { label: 'Agent identities' }];
      const tab = queryParam('tab');
      const name = title ?? 'Connection';
      return tab && tab !== 'overview' ? [home, { label: name, to: { page: 'connections', view: 'account', id: route.id } }, { label: TAB_LABEL[tab] ?? sentence(tab) }] : [home, { label: name }];
    }
    case 'settings': return route.section === 'workspace' ? [{ label: 'Settings' }] : [top('settings', { page: 'settings', section: 'workspace' }), { label: route.section === 'billing' ? 'Usage & billing' : 'Developer' }];
    default: return [{ label: PAGE_LABEL[route.page] ?? sentence(route.page) }];
  }
}

/** The ← → pair. Right-click or long-press opens the last ten pages in that direction. */
export function HistoryButtons({ compact = false }: { compact?: boolean }) {
  const history = useHistoryState();
  const [menu, setMenu] = useState<'back' | 'forward' | null>(null);
  const press = useRef<number | null>(null);
  useEffect(() => {
    if (!menu) return;
    const close = (event: PointerEvent) => { if (!(event.target as Element).closest('[data-ws-history]')) setMenu(null); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenu(null); };
    document.addEventListener('pointerdown', close); document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape); };
  }, [menu]);
  const longPress = (direction: 'back' | 'forward') => ({
    onContextMenu: (event: React.MouseEvent) => { event.preventDefault(); setMenu(direction); },
    onPointerDown: (event: ReactPointerEvent) => { if (event.pointerType === 'mouse') return; press.current = window.setTimeout(() => setMenu(direction), 500); },
    onPointerUp: () => { if (press.current) window.clearTimeout(press.current); },
    onPointerLeave: () => { if (press.current) window.clearTimeout(press.current); },
  });
  const previous = history.back[0];
  const next = history.forward[0];
  const label = (entry: typeof previous) => entry?.title || 'previous page';
  const list = menu === 'back' ? history.back : history.forward;
  return <div className='ws-history' data-ws-history data-compact={compact ? 'true' : undefined}>
    <button type='button' className='ws-icon-button' disabled={!history.canBack} aria-label={history.canBack ? `Back to ${label(previous)}` : 'Back'} title={history.canBack ? `Back to ${label(previous)} (⌘[)` : 'Nothing to go back to'} onClick={() => window.history.back()} {...longPress('back')}><ArrowLeft size={16} /></button>
    <button type='button' className='ws-icon-button' disabled={!history.canForward} aria-label={history.canForward ? `Forward to ${label(next)}` : 'Forward'} title={history.canForward ? `Forward to ${label(next)} (⌘])` : 'Nothing ahead'} onClick={() => window.history.forward()} {...longPress('forward')}><ArrowRight size={16} /></button>
    {menu && list.length > 0 && <div className='ws-menu ws-history-menu' role='menu' aria-label={menu === 'back' ? 'Recent pages' : 'Pages ahead'}>
      <p>{menu === 'back' ? 'Go back to' : 'Go forward to'}</p>
      {list.map((entry) => <button key={entry.idx} type='button' role='menuitem' onClick={() => { setMenu(null); goTo(entry.idx); }}><span><strong>{entry.title || entry.url}</strong>{entry.trail && entry.trail !== entry.title && <small>{entry.trail}</small>}</span></button>)}
    </div>}
  </div>;
}

/** The bar above every page: history arrows and the breadcrumb trail. */
export function TopBar({ route }: { route: WorkspaceRoute }) {
  const crumbs = useCrumbs(route);
  const [scrolled, setScrolled] = useState(false);
  const trail = crumbs.map((crumb) => crumb.label).join(' / ');
  useEffect(() => { nameEntry(crumbs[crumbs.length - 1]?.label ?? '', trail); }, [trail]);
  useEffect(() => {
    const update = () => setScrolled(window.scrollY > 4);
    update();
    window.addEventListener('scroll', update, { passive: true });
    return () => window.removeEventListener('scroll', update);
  }, []);
  return <div className='ws-topbar' data-scrolled={scrolled ? 'true' : undefined}>
    <HistoryButtons />
    <nav className='ws-crumbs' aria-label='Breadcrumb'>
      <ol>{crumbs.map((crumb, index) => {
        const last = index === crumbs.length - 1;
        return <li key={`${crumb.label}-${index}`}>
          {index > 0 && <ChevronRight size={13} aria-hidden='true' className='ws-crumb-sep' />}
          {crumb.to && !last ? <a {...linkProps(crumb.to)}>{crumb.label}</a> : <span aria-current={last ? 'page' : undefined}>{crumb.label}</span>}
        </li>;
      })}</ol>
    </nav>
  </div>;
}

/** ⌘[ / ⌘] (Ctrl on Windows) move through history, like Slack. */
export function useHistoryKeys() {
  useEffect(() => {
    function key(event: KeyboardEvent) {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest('input, textarea, [contenteditable="true"]')) return;
      if (event.key === '[') { event.preventDefault(); if (canStep(-1)) window.history.back(); }
      if (event.key === ']') { event.preventDefault(); if (canStep(1)) window.history.forward(); }
    }
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, []);
}
