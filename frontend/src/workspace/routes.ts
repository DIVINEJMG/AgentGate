import { useEffect, useState } from 'react';
import type { AppView } from '../navigation';
import { pushEntry, replaceEntry } from './history';

/** Every workspace location. `view` maps to the existing panel; the rest identifies a record. */
export type WorkspaceRoute =
  | { page: 'home' }
  | { page: 'inbox'; kind?: 'approval' | 'escalation' | 'result'; id?: string; filter?: string }
  | { page: 'workers' }
  | { page: 'worker'; workerId: string; tab: WorkerTab }
  | { page: 'conversations'; workerId?: string; threadId?: string }
  | { page: 'jobs'; jobId?: string }
  | { page: 'results'; resultId?: string }
  | { page: 'memory' }
  | { page: 'activity'; actionId?: string }
  | { page: 'runs'; runId?: string }
  | { page: 'performance' }
  | { page: 'policies' }
  | { page: 'risk' }
  | { page: 'incidents' }
  | { page: 'audit' }
  | { page: 'connections'; view: ConnectionsView; id?: string }
  | { page: 'settings'; section: 'workspace' | 'billing' | 'developer' };

/** home: connected accounts · directory/tool: catalog and one tool · account: one connection · identities · access: the access map */
export type ConnectionsView = 'home' | 'directory' | 'tool' | 'account' | 'identities' | 'access';

export type WorkerTab = 'overview' | 'conversations' | 'jobs' | 'results' | 'memory' | 'settings';
const WORKER_TABS: WorkerTab[] = ['overview', 'conversations', 'jobs', 'results', 'memory', 'settings'];

/** Base path: `/app` normally, `/workspace-preview` for the development sample workspace. */
export function routeBase(): string {
  return window.location.pathname.startsWith('/workspace-preview') || /^#\/?workspace-preview/.test(window.location.hash) ? '/workspace-preview' : '/app';
}

const decode = (value: string | undefined) => (value ? decodeURIComponent(value) : undefined);

export function parseRoute(pathname: string, search = ''): WorkspaceRoute {
  const base = routeBase();
  const rest = pathname.startsWith(base) ? pathname.slice(base.length) : '';
  const parts = rest.split('/').filter(Boolean).map((part) => decodeURIComponent(part));
  const params = new URLSearchParams(search);
  const [head, a, b] = parts;
  switch (head) {
    case undefined: case 'overview': return { page: 'home' };
    case 'inbox': return { page: 'inbox', kind: a === 'approval' || a === 'escalation' || a === 'result' ? a : undefined, id: b, filter: params.get('filter') ?? undefined };
    case 'approvals': return { page: 'inbox', filter: 'approvals', kind: a ? 'approval' : undefined, id: a };
    case 'supervision': return { page: 'inbox', filter: 'escalations', kind: a ? 'escalation' : undefined, id: a };
    case 'workers': return a ? { page: 'worker', workerId: a, tab: WORKER_TABS.includes(b as WorkerTab) ? b as WorkerTab : 'overview' } : { page: 'workers' };
    case 'conversations': return { page: 'conversations', workerId: decode(a), threadId: decode(b) };
    case 'jobs': return { page: 'jobs', jobId: decode(a) };
    case 'results': return { page: 'results', resultId: a };
    case 'memory': return { page: 'memory' };
    case 'activity': return { page: 'activity', actionId: decode(a) };
    case 'runs': return { page: 'runs', runId: decode(a) };
    case 'performance': return { page: 'performance' };
    case 'policies': return { page: 'policies' };
    case 'risk': return { page: 'risk' };
    case 'incidents': return { page: 'incidents' };
    case 'audit': return { page: 'audit' };
    case 'connections':
      if (!a || a === 'integrations') return { page: 'connections', view: 'home' };
      if (a === 'directory') return b ? { page: 'connections', view: 'tool', id: decode(b) } : { page: 'connections', view: 'directory' };
      if (a === 'identities') return { page: 'connections', view: 'identities', id: decode(b) };
      if (a === 'access' || a === 'capabilities') return { page: 'connections', view: 'access' };
      return { page: 'connections', view: 'account', id: decode(a) };
    case 'settings': return { page: 'settings', section: a === 'billing' || a === 'developer' ? a : 'workspace' };
    default: return { page: 'home' };
  }
}

export function formatRoute(route: WorkspaceRoute): string {
  const base = routeBase();
  const join = (...parts: Array<string | undefined>) => base + parts.filter(Boolean).map((part) => '/' + encodeURIComponent(part as string)).join('');
  switch (route.page) {
    case 'home': return base;
    case 'inbox': return join('inbox', route.kind, route.id) + (route.filter ? `?filter=${encodeURIComponent(route.filter)}` : '');
    case 'worker': return join('workers', route.workerId, route.tab === 'overview' ? undefined : route.tab);
    case 'conversations': return join('conversations', route.workerId, route.threadId);
    case 'results': return join('results', route.resultId);
    case 'activity': return join('activity', route.actionId);
    case 'jobs': return join('jobs', route.jobId);
    case 'runs': return join('runs', route.runId);
    case 'connections':
      if (route.view === 'home') return join('connections');
      if (route.view === 'directory') return join('connections', 'directory');
      if (route.view === 'tool') return join('connections', 'directory', route.id);
      if (route.view === 'identities') return join('connections', 'identities', route.id);
      if (route.view === 'access') return join('connections', 'access');
      return join('connections', route.id);
    case 'settings': return join('settings', route.section === 'workspace' ? undefined : route.section);
    default: return join(route.page);
  }
}

/** Existing panels navigate by AppView; translate those calls into URLs. */
export function routeForView(view: AppView): WorkspaceRoute {
  const map: Record<AppView, WorkspaceRoute> = {
    overview: { page: 'home' }, workforce: { page: 'workers' }, conversations: { page: 'conversations' }, jobs: { page: 'jobs' },
    results: { page: 'results' }, supervision: { page: 'inbox', filter: 'escalations' }, approvals: { page: 'inbox', filter: 'approvals' },
    performance: { page: 'performance' }, runtime: { page: 'runs' }, actions: { page: 'activity' }, commercial: { page: 'settings', section: 'billing' },
    memory: { page: 'memory' }, agents: { page: 'connections', view: 'identities' }, integrations: { page: 'connections', view: 'home' },
    capabilities: { page: 'connections', view: 'access' }, policies: { page: 'policies' }, audit: { page: 'audit' }, risk: { page: 'risk' },
    incidents: { page: 'incidents' }, settings: { page: 'settings', section: 'workspace' },
  };
  return map[view];
}

const EVENT = 'audoryn:route';

/** Pushes a new history entry. Query parameters on the current URL (e.g. GitHub setup) are dropped unless kept. */
export function navigate(target: WorkspaceRoute | string, options: { replace?: boolean; keepScroll?: boolean } = {}) {
  const url = typeof target === 'string' ? target : formatRoute(target);
  if (url === window.location.pathname + window.location.search) return;
  if (options.replace) replaceEntry(url); else pushEntry(url);
  window.dispatchEvent(new Event(EVENT));
  if (!options.keepScroll) window.scrollTo({ top: 0 });
}

/** Page filters live in the query string, so a filtered view can be refreshed and shared. */
export function queryParam(name: string) {
  return new URLSearchParams(window.location.search).get(name) ?? '';
}
export function setQuery(patch: Record<string, string | undefined>, options: { push?: boolean } = {}) {
  const params = new URLSearchParams(window.location.search);
  for (const [key, value] of Object.entries(patch)) if (value) params.set(key, value); else params.delete(key);
  const text = params.toString();
  navigate(window.location.pathname + (text ? `?${text}` : ''), { replace: !options.push, keepScroll: true });
}

export function useRoute(): WorkspaceRoute {
  const [route, setRoute] = useState(() => parseRoute(window.location.pathname, window.location.search));
  useEffect(() => {
    // Legacy hash links (#/app) become real paths without adding a history entry.
    if (/^#\/?(app|workspace-preview)/.test(window.location.hash)) {
      const base = routeBase();
      window.history.replaceState(window.history.state, '', base + window.location.search);
    }
    const update = () => setRoute(parseRoute(window.location.pathname, window.location.search));
    update();
    window.addEventListener('popstate', update);
    window.addEventListener(EVENT, update);
    return () => { window.removeEventListener('popstate', update); window.removeEventListener(EVENT, update); };
  }, []);
  return route;
}

/** Same-document link that keeps native behaviour for new-tab and modified clicks. */
export function linkProps(target: WorkspaceRoute) {
  const href = formatRoute(target);
  return {
    href,
    onClick: (event: React.MouseEvent<HTMLAnchorElement>) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      navigate(href);
    },
  };
}

export function sameSection(a: WorkspaceRoute, b: WorkspaceRoute) {
  if (a.page === 'worker' && b.page === 'workers') return true;
  return a.page === b.page;
}
