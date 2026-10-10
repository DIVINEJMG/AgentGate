import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  Activity, Brain, BriefcaseBusiness, ChevronsUpDown, FileText, Gauge, House, Inbox, LogOut, Menu, MessagesSquare, Monitor, Moon,
  PanelLeftClose, PanelLeftOpen, Plug, Scale, ScrollText, Search, Settings, ShieldAlert, Siren, Sun, Users, Waypoints, X,
} from 'lucide-react';
import type { OrganizationAccess } from '../lib/identityApi';
import { useLive } from './live';
import { formatRoute, linkProps, navigate, sameSection, type WorkspaceRoute } from './routes';
import type { ThemeChoice } from './theme';
import { Avatar, Kbd } from './ui';
import { startHistory } from './history';
import { HistoryButtons, TopBar, useHistoryKeys } from './TopBar';
import { PageScrollbar, useEdgeFades } from './Scrollbar';

type NavItem = { label: string; to: WorkspaceRoute; icon: typeof House; badge?: number };

export function Shell({ route, organization, organizations, accountName, accountEmail, theme, onTheme, onSwitchOrganization, onSignOut, preview, children }: {
  route: WorkspaceRoute;
  organization: OrganizationAccess;
  organizations: OrganizationAccess[];
  accountName: string;
  accountEmail?: string | null;
  theme: ThemeChoice;
  onTheme: (theme: ThemeChoice) => void;
  onSwitchOrganization: (organization: OrganizationAccess) => void;
  onSignOut?: () => void;
  preview?: { onExit: () => void };
  children: ReactNode;
}) {
  const live = useLive();
  const [collapsed, setCollapsed] = useState(() => { try { return window.localStorage.getItem('audoryn.sidebar.collapsed') === 'true'; } catch { return false; } });
  const [mobileOpen, setMobileOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [menu, setMenu] = useState<'org' | 'account' | null>(null);

  const routeKey = JSON.stringify(route);
  startHistory();
  useHistoryKeys();
  useEdgeFades(routeKey);
  // The pre-paint background in index.html only covers the moment before mount.
  useEffect(() => { document.documentElement.style.removeProperty('background'); }, []);
  useEffect(() => { setMobileOpen(false); setMenu(null); }, [route]);
  useEffect(() => {
    function key(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setPaletteOpen((open) => !open); }
      if (event.key === 'Escape') { setMenu(null); setMobileOpen(false); }
    }
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, []);
  useEffect(() => {
    if (!menu) return;
    const close = (event: PointerEvent) => { if (!(event.target as Element).closest('[data-ws-menu]')) setMenu(null); };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [menu]);

  function toggleCollapsed() {
    setCollapsed((current) => { try { window.localStorage.setItem('audoryn.sidebar.collapsed', String(!current)); } catch { /* Session-only preference. */ } return !current; });
  }

  const main: NavItem[] = [
    { label: 'Home', to: { page: 'home' }, icon: House },
    { label: 'Inbox', to: { page: 'inbox' }, icon: Inbox, badge: live.inboxCount || undefined },
    { label: 'Workers', to: { page: 'workers' }, icon: Users },
    { label: 'Conversations', to: { page: 'conversations' }, icon: MessagesSquare },
    { label: 'Jobs', to: { page: 'jobs' }, icon: BriefcaseBusiness },
    { label: 'Results', to: { page: 'results' }, icon: FileText },
  ];
  const oversight: NavItem[] = [
    { label: 'Activity', to: { page: 'activity' }, icon: Activity },
    { label: 'Runs', to: { page: 'runs' }, icon: Waypoints },
    { label: 'Performance', to: { page: 'performance' }, icon: Gauge },
    { label: 'Policies', to: { page: 'policies' }, icon: Scale },
    { label: 'Risk', to: { page: 'risk' }, icon: ShieldAlert },
    { label: 'Incidents', to: { page: 'incidents' }, icon: Siren },
    { label: 'Audit', to: { page: 'audit' }, icon: ScrollText },
  ];
  const setup: NavItem[] = [
    { label: 'Connections', to: { page: 'connections', view: 'home' }, icon: Plug },
    { label: 'Memory', to: { page: 'memory' }, icon: Brain },
  ];
  const pinned = live.workers.filter((worker) => worker.status !== 'archived').slice(0, 6);

  const item = (entry: NavItem) => {
    const active = sameSection(route, entry.to) || (entry.to.page === 'conversations' && route.page === 'conversations');
    const Icon = entry.icon;
    return <li key={entry.label}><a className='ws-nav-item' aria-current={active ? 'page' : undefined} title={collapsed ? entry.label : undefined} {...linkProps(entry.to)}>
      <Icon size={17} strokeWidth={1.7} aria-hidden='true' /><span className='ws-nav-label'>{entry.label}</span>
      {entry.badge !== undefined && <span className='ws-nav-badge' aria-label={`${entry.badge} need you`}>{entry.badge}</span>}
    </a></li>;
  };

  return <div className='ws-app' data-collapsed={collapsed ? 'true' : undefined}>
    {preview && <div className='ws-preview-bar'><strong>Workspace preview</strong><span>Sample data · nothing is saved</span><button type='button' onClick={preview.onExit}>Exit preview</button></div>}
    <div className='ws-mobile-bar'>
      <button type='button' className='ws-icon-button' aria-label='Open navigation' aria-expanded={mobileOpen} aria-controls='ws-sidebar' onClick={() => setMobileOpen(true)}><Menu size={18} /></button>
      <HistoryButtons compact />
      <span className='ws-mobile-org'>{organization.name}</span>
      <button type='button' className='ws-icon-button' aria-label='Search' onClick={() => setPaletteOpen(true)}><Search size={17} /></button>
      <a className='ws-icon-button ws-mobile-inbox' aria-label={`Inbox, ${live.inboxCount} need you`} {...linkProps({ page: 'inbox' })}><Inbox size={17} />{live.inboxCount > 0 && <i />}</a>
    </div>
    {mobileOpen && <button type='button' className='ws-scrim' aria-label='Close navigation' onClick={() => setMobileOpen(false)} />}
    <aside id='ws-sidebar' className='ws-sidebar' data-open={mobileOpen ? 'true' : undefined} aria-label='Workspace navigation'>
      <div className='ws-sidebar-top' data-ws-menu>
        <button type='button' className='ws-org' aria-haspopup='menu' aria-expanded={menu === 'org'} onClick={() => setMenu(menu === 'org' ? null : 'org')}>
          <img src='/audoryn-mark.png' alt='' width={22} height={22} />
          <span className='ws-org-copy'><strong>{organization.name}</strong><small>{organization.role}</small></span>
          <ChevronsUpDown size={14} aria-hidden='true' />
        </button>
        <button type='button' className='ws-icon-button ws-mobile-close' aria-label='Close navigation' onClick={() => setMobileOpen(false)}><X size={17} /></button>
        {menu === 'org' && <div className='ws-menu' role='menu'>
          <p>Organizations</p>
          {organizations.map((candidate) => <button key={candidate.id} type='button' role='menuitemradio' aria-checked={candidate.id === organization.id} onClick={() => { setMenu(null); onSwitchOrganization(candidate); }}><Avatar name={candidate.name} size={22} /><span>{candidate.name}</span></button>)}
          <hr />
          <a role='menuitem' {...linkProps({ page: 'settings', section: 'workspace' })}><Settings size={15} />Workspace settings</a>
        </div>}
      </div>
      <button type='button' className='ws-search' onClick={() => setPaletteOpen(true)}><Search size={15} aria-hidden='true' /><span>Search or jump to…</span><Kbd>⌘K</Kbd></button>
      <nav className='ws-nav' aria-label='Primary'>
        <ul>{main.map(item)}</ul>
        <p className='ws-nav-group'>Oversight</p>
        <ul>{oversight.map(item)}</ul>
        <p className='ws-nav-group'>Setup</p>
        <ul>{setup.map(item)}</ul>
        {pinned.length > 0 && <>
          <p className='ws-nav-group'>Your workers</p>
          <ul>{pinned.map((worker) => {
            const active = route.page === 'conversations' && route.workerId === worker.id || route.page === 'worker' && route.workerId === worker.id;
            const state = live.liveness(worker);
            return <li key={worker.id}><a className='ws-nav-item ws-nav-worker' aria-current={active ? 'page' : undefined} title={collapsed ? worker.name : undefined} {...linkProps({ page: 'conversations', workerId: worker.id })}>
              <Avatar name={worker.name} seed={worker.id} size={20} live={state} /><span className='ws-nav-label'>{worker.name}</span>
              {state === 'working' && <span className='ws-nav-live'>Working</span>}
            </a></li>;
          })}</ul>
        </>}
      </nav>
      <div className='ws-sidebar-bottom' data-ws-menu>
        {item({ label: 'Settings & billing', to: { page: 'settings', section: 'workspace' }, icon: Settings })}
        <button type='button' className='ws-account' aria-haspopup='menu' aria-expanded={menu === 'account'} onClick={() => setMenu(menu === 'account' ? null : 'account')}>
          <Avatar name={accountName} size={26} /><span className='ws-nav-label'><strong>{accountName}</strong>{accountEmail && <small>{accountEmail}</small>}</span>
        </button>
        {menu === 'account' && <div className='ws-menu ws-menu-up' role='menu'>
          <p>Appearance</p>
          <div className='ws-theme' role='radiogroup' aria-label='Appearance'>
            {([['system', 'System', Monitor], ['light', 'Light', Sun], ['dark', 'Dark', Moon]] as const).map(([value, label, Icon]) => <button key={value} type='button' role='radio' aria-checked={theme === value} onClick={() => onTheme(value)}><Icon size={14} />{label}</button>)}
          </div>
          {onSignOut && <><hr /><button type='button' role='menuitem' onClick={onSignOut}><LogOut size={15} />Sign out</button></>}
        </div>}
        <button type='button' className='ws-collapse' aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} aria-expanded={!collapsed} onClick={toggleCollapsed}>{collapsed ? <PanelLeftOpen size={16} /> : <><PanelLeftClose size={16} /><span>Collapse</span></>}</button>
      </div>
    </aside>
    <main className='ws-main' id='ws-main'><TopBar route={route} />{children}</main>
    <PageScrollbar routeKey={routeKey} />
    <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} items={[...main, ...oversight, ...setup, { label: 'Settings', to: { page: 'settings', section: 'workspace' }, icon: Settings }, { label: 'Usage & billing', to: { page: 'settings', section: 'billing' }, icon: Settings }]} />
  </div>;
}

function CommandPalette({ open, onClose, items }: { open: boolean; onClose: () => void; items: NavItem[] }) {
  const live = useLive();
  const ref = useRef<HTMLDialogElement>(null);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) { setQuery(''); setActive(0); dialog.showModal(); }
    if (!open && dialog.open) dialog.close();
  }, [open]);
  const results = useMemo(() => {
    const term = query.trim().toLowerCase();
    const pages = items.map((entry) => ({ key: 'page-' + entry.label, label: entry.label, hint: 'Go to', to: entry.to, icon: <entry.icon size={16} aria-hidden='true' /> }));
    const workers = live.workers.flatMap((worker) => [
      { key: 'chat-' + worker.id, label: `Message ${worker.name}`, hint: 'Conversation', to: { page: 'conversations', workerId: worker.id } as WorkspaceRoute, icon: <Avatar name={worker.name} seed={worker.id} size={18} /> },
      { key: 'worker-' + worker.id, label: `${worker.name}`, hint: 'Worker profile', to: { page: 'worker', workerId: worker.id, tab: 'overview' } as WorkspaceRoute, icon: <Avatar name={worker.name} seed={worker.id} size={18} /> },
    ]);
    return [...pages, ...workers].filter((entry) => !term || entry.label.toLowerCase().includes(term) || entry.hint.toLowerCase().includes(term)).slice(0, 12);
  }, [items, live.workers, query]);
  function go(index: number) { const entry = results[index]; if (!entry) return; onClose(); navigate(formatRoute(entry.to)); }
  return <dialog ref={ref} className='ws-palette' aria-label='Search or jump to' onCancel={(event) => { event.preventDefault(); onClose(); }} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    {open && <>
      <label className='ws-palette-input'><Search size={16} aria-hidden='true' /><input autoFocus value={query} placeholder='Search pages and workers…' aria-label='Search pages and workers' aria-controls='ws-palette-results' aria-activedescendant={results[active] ? `ws-palette-${results[active].key}` : undefined}
        onChange={(event) => { setQuery(event.target.value); setActive(0); }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown') { event.preventDefault(); setActive((index) => Math.min(index + 1, results.length - 1)); }
          if (event.key === 'ArrowUp') { event.preventDefault(); setActive((index) => Math.max(index - 1, 0)); }
          if (event.key === 'Enter') { event.preventDefault(); go(active); }
        }} /><Kbd>esc</Kbd></label>
      <ul id='ws-palette-results' role='listbox'>
        {results.map((entry, index) => <li key={entry.key} id={`ws-palette-${entry.key}`} role='option' aria-selected={index === active} onMouseEnter={() => setActive(index)} onClick={() => go(index)}>{entry.icon}<span>{entry.label}</span><small>{entry.hint}</small></li>)}
        {!results.length && <li className='ws-palette-empty'>No matches for “{query}”.</li>}
      </ul>
      <footer><span><Kbd>↑</Kbd><Kbd>↓</Kbd> to move</span><span><Kbd>↵</Kbd> to open</span></footer>
    </>}
  </dialog>;
}
