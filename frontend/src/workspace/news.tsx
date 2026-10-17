import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ArrowRight, Bell, CalendarDays, ExternalLink, Megaphone, Sparkles, Wrench, X, Zap } from 'lucide-react';
import type { OrganizationAccess } from '../lib/identityApi';
import type { ApiVersion } from '../lib/systemApi';
import { loadChangelog, loadNews, markNews, type ChangelogEntry, type NewsAnnouncement, type NewsFeed } from '../lib/newsApi';
import { formatDate } from '../lib/planFormat';
import { navigate } from './routes';
import { Ago, Sheet } from './ui';
import './pages/plans.css';
import './news.css';

/** "What's new" (plans, news and upgrade prompts plan, part 3): a bell with the unread count, a
 *  timeline panel, and a one-time window for announcements marked major. */
type Tone = 'new' | 'improvement' | 'fix' | 'event' | 'notice' | 'warning';
type Entry = {
  key: string; tone: Tone; tag: string; title: string; body: string; at: string; image: string | null;
  link: { label: string; url: string } | null; upgrade: boolean; major: boolean; dismissible: boolean;
};
type News = { entries: Entry[]; unread: number; open: () => void };

const REFRESH_MS = 15 * 60_000;
// Only recent entries count as unread, so a new member is not greeted by the whole history.
const UNREAD_DAYS = 30;
const NewsContext = createContext<News | null>(null);

function changelogTone(tag: string | null): [Tone, string] {
  const text = (tag ?? '').trim();
  if (/fix/i.test(text)) return ['fix', 'Fix'];
  if (/improv/i.test(text)) return ['improvement', 'Improvement'];
  if (/new|launch|feature/i.test(text) || !text) return ['new', text || 'New'];
  return ['improvement', text];
}

function fromChangelog(item: ChangelogEntry): Entry {
  const [tone, tag] = changelogTone(item.tag);
  return { key: `news:changelog:${item.id}`, tone, tag, title: item.title, body: item.summary, at: item.published, image: item.image,
    link: { label: 'Read more', url: item.path }, upgrade: false, major: false, dismissible: false };
}

function fromAnnouncement(item: NewsAnnouncement): Entry {
  const event = item.kind === 'event' || item.kind.includes('webinar');
  const tone: Tone = item.severity === 'warning' || item.severity === 'critical' ? 'warning' : event ? 'event' : item.kind.includes('launch') ? 'new' : 'notice';
  const tag = tone === 'warning' ? 'Notice' : event ? 'Event' : tone === 'new' ? 'New' : item.upgrade ? 'Offer' : 'Notice';
  return { key: `news:announcement:${item.id}`, tone, tag, title: item.title, body: item.body, at: item.publishedAt, image: null,
    link: item.cta, upgrade: item.upgrade, major: item.major, dismissible: item.dismissible };
}

const ICONS: Record<Tone, typeof Sparkles> = { new: Sparkles, improvement: Zap, fix: Wrench, event: CalendarDays, notice: Megaphone, warning: Megaphone };

export function NewsProvider({ organization, apiVersion, children }: { organization: OrganizationAccess; apiVersion: ApiVersion; children: ReactNode }) {
  const [feed, setFeed] = useState<NewsFeed | null>(null);
  const [changelog, setChangelog] = useState<ChangelogEntry[]>([]);
  const [panel, setPanel] = useState(false);
  const [shown, setShown] = useState<string | null>(null);
  // What was unread when the panel opened keeps its accent while the panel stays open.
  const [fresh, setFresh] = useState<string[]>([]);

  const refresh = useCallback(() => {
    void loadNews(apiVersion, organization.id).then(setFeed).catch(() => {});
    void loadChangelog().then(setChangelog);
  }, [apiVersion, organization.id]);
  useEffect(() => {
    setFeed(null); refresh();
    const timer = window.setInterval(refresh, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const state = feed?.state ?? {};
  const entries = useMemo(() => [...(feed?.announcements ?? []).map(fromAnnouncement), ...changelog.map(fromChangelog)]
    .filter((entry) => !state[entry.key]?.dismissedAt)
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at)), [feed, changelog]); // eslint-disable-line react-hooks/exhaustive-deps
  const recent = Date.now() - UNREAD_DAYS * 86_400_000;
  const unreadKeys = entries.filter((entry) => !state[entry.key]?.readAt && Date.parse(entry.at) >= recent).map((entry) => entry.key);
  const major = feed ? entries.find((entry) => entry.major && !state[entry.key]?.readAt) : undefined;

  const mark = useCallback((keys: string[], how: 'read' | 'dismissed') => {
    if (!keys.length) return;
    const at = new Date().toISOString();
    setFeed((current) => current && { ...current, state: { ...current.state, ...Object.fromEntries(keys.map((key) => [key, { readAt: at, dismissedAt: how === 'dismissed' ? at : current.state[key]?.dismissedAt ?? null }])) } });
    void markNews(apiVersion, organization.id, keys, how).catch(() => {});
  }, [apiVersion, organization.id]);

  // A major announcement opens once for each person, a moment after the app settles.
  useEffect(() => {
    if (!major || shown || panel) return;
    const timer = window.setTimeout(() => setShown(major.key), 1500);
    return () => window.clearTimeout(timer);
  }, [major?.key, shown, panel]); // eslint-disable-line react-hooks/exhaustive-deps

  const open = useCallback(() => { setFresh(unreadKeys); setPanel(true); mark(unreadKeys, 'read'); }, [mark, unreadKeys.join()]); // eslint-disable-line react-hooks/exhaustive-deps
  const value = useMemo(() => ({ entries, unread: unreadKeys.length, open }), [entries, unreadKeys.length, open]);
  const modal = entries.find((entry) => entry.key === shown);

  return <NewsContext.Provider value={value}>
    {children}
    <Sheet open={panel} onClose={() => setPanel(false)} title="What’s new" subtitle='Product updates, events and notices for your workspace.'>
      {entries.length === 0 ? <p className='nw-empty'>Nothing new yet. Updates and events will appear here.</p>
        : <ol className='nw-timeline'>{entries.map((entry) => <NewsItem key={entry.key} entry={entry} unread={fresh.includes(entry.key)}
          onDismiss={entry.dismissible ? () => mark([entry.key], 'dismissed') : undefined} onLeave={() => setPanel(false)} />)}</ol>}
    </Sheet>
    <MajorWindow entry={modal ?? null} onClose={() => { if (modal) mark([modal.key], 'read'); setShown('closed'); }} />
  </NewsContext.Provider>;
}

function follow(link: { url: string }, leave: () => void) {
  if (link.url.startsWith('/app')) { leave(); navigate(link.url); }
}

function Actions({ entry, onLeave }: { entry: Entry; onLeave: () => void }) {
  return <div className='nw-actions'>
    {entry.upgrade && <button type='button' className='nw-cta' onClick={() => { onLeave(); navigate({ page: 'plans' }); }}>See plans<ArrowRight size={14} /></button>}
    {entry.link && (entry.link.url.startsWith('/app')
      ? <button type='button' className='nw-link' onClick={() => follow(entry.link!, onLeave)}>{entry.link.label}<ArrowRight size={13} /></button>
      : <a className='nw-link' href={entry.link.url} target='_blank' rel='noopener noreferrer'>{entry.link.label}<ExternalLink size={13} /></a>)}
  </div>;
}

function NewsItem({ entry, unread, onDismiss, onLeave }: { entry: Entry; unread: boolean; onDismiss?: () => void; onLeave: () => void }) {
  const Icon = ICONS[entry.tone];
  return <li className='nw-item' data-tone={entry.tone} data-unread={unread ? 'true' : undefined}>
    <span className='nw-dot' aria-hidden='true'><Icon size={13} /></span>
    <article>
      <header>
        <span className='nw-tag'>{entry.tag}</span>
        <time dateTime={entry.at} title={formatDate(entry.at)}><Ago value={entry.at} /></time>
        {unread && <span className='nw-new'>New to you</span>}
        {onDismiss && <button type='button' className='nw-dismiss' aria-label={`Dismiss ${entry.title}`} onClick={onDismiss}><X size={13} /></button>}
      </header>
      <h3>{entry.title}</h3>
      {entry.image && <img src={entry.image} alt='' loading='lazy' />}
      <p>{entry.body}</p>
      <Actions entry={entry} onLeave={onLeave} />
    </article>
  </li>;
}

function MajorWindow({ entry, onClose }: { entry: Entry | null; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (entry && !dialog.open) dialog.showModal();
    if (!entry && dialog.open) dialog.close();
  }, [entry]);
  return <dialog ref={ref} className='up-modal nw-major' aria-labelledby='nw-major-title' onCancel={(event) => { event.preventDefault(); onClose(); }} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    {entry && <>
      <div className='nw-major-art' aria-hidden='true'><span><Sparkles size={22} /></span></div>
      <header className='up-modal-head'>
        <div><p className='pl-eyebrow'>{entry.tag} · {formatDate(entry.at)}</p><h2 id='nw-major-title'>{entry.title}</h2></div>
        <button type='button' className='up-banner-close' aria-label='Close' onClick={onClose}><X size={16} /></button>
      </header>
      <div className='up-modal-body'><p>{entry.body}</p></div>
      <footer className='up-modal-foot'>
        <button type='button' className='pl-cta pl-cta-quiet' onClick={onClose}>Got it</button>
        {entry.upgrade && <button type='button' className='pl-cta' onClick={() => { onClose(); navigate({ page: 'plans' }); }}>See plans<ArrowRight size={15} /></button>}
        {!entry.upgrade && entry.link && (entry.link.url.startsWith('/app')
          ? <button type='button' className='pl-cta' autoFocus onClick={() => { onClose(); navigate(entry.link!.url); }}>{entry.link.label}<ArrowRight size={15} /></button>
          : <a className='pl-cta' href={entry.link.url} target='_blank' rel='noopener noreferrer' onClick={onClose}>{entry.link.label}<ExternalLink size={15} /></a>)}
      </footer>
    </>}
  </dialog>;
}

/** The bell in the top bar: "What's new" with the unread count. */
export function NewsBell({ compact = false }: { compact?: boolean }) {
  const news = useContext(NewsContext);
  if (!news) return null;
  return <button type='button' className='ws-icon-button nw-bell' data-compact={compact ? 'true' : undefined} onClick={news.open}
    aria-label={news.unread ? `What’s new, ${news.unread} unread` : 'What’s new'} title='What’s new'>
    <Bell size={compact ? 17 : 16} />{news.unread > 0 && <span className='nw-count' aria-hidden='true'>{news.unread > 9 ? '9+' : news.unread}</span>}
  </button>;
}
