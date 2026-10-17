import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ArrowLeft, ArrowRight, Bell, CalendarDays, ExternalLink, Megaphone, Sparkles, Wrench, X, Zap } from 'lucide-react';
import type { OrganizationAccess } from '../lib/identityApi';
import type { ApiVersion } from '../lib/systemApi';
import { loadChangelog, loadNews, markNews, type ChangelogEntry, type NewsAnnouncement, type NewsFeed, type NewsMedia } from '../lib/newsApi';
import { formatDate } from '../lib/planFormat';
import { navigate } from './routes';
import { Ago, Sheet } from './ui';
import './pages/plans.css';
import './news.css';

/** "What's new" (plans, news and upgrade prompts plan, part 3): a bell with the unread count, a
 *  timeline panel, and a one-time window for announcements marked major. */
type Tone = 'new' | 'improvement' | 'fix' | 'event' | 'notice' | 'warning';
type Entry = {
  key: string; tone: Tone; tag: string; title: string; body: string; at: string; image: string | null; media: NewsMedia | null;
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
  return { key: `news:changelog:${item.id}`, tone, tag, title: item.title, body: item.summary, at: item.published, image: item.image, media: null,
    link: { label: 'Read more', url: item.path }, upgrade: false, major: false, dismissible: false };
}

function fromAnnouncement(item: NewsAnnouncement): Entry {
  const event = item.kind === 'event' || item.kind.includes('webinar');
  const tone: Tone = item.severity === 'warning' || item.severity === 'critical' ? 'warning' : event ? 'event' : item.kind.includes('launch') ? 'new' : 'notice';
  const tag = tone === 'warning' ? 'Notice' : event ? 'Event' : tone === 'new' ? 'New' : item.upgrade ? 'Offer' : 'Notice';
  return { key: `news:announcement:${item.id}`, tone, tag, title: item.title, body: item.body, at: item.publishedAt, image: item.media?.image ?? null, media: item.media ?? null,
    link: item.cta, upgrade: item.upgrade, major: item.major, dismissible: item.dismissible };
}

const ICONS: Record<Tone, typeof Sparkles> = { new: Sparkles, improvement: Zap, fix: Wrench, event: CalendarDays, notice: Megaphone, warning: Megaphone };

export function NewsProvider({ organization, apiVersion, children }: { organization: OrganizationAccess; apiVersion: ApiVersion; children: ReactNode }) {
  const [feed, setFeed] = useState<NewsFeed | null>(null);
  const [changelog, setChangelog] = useState<ChangelogEntry[]>([]);
  const [panel, setPanel] = useState(false);
  // The major announcements in the open window; 'closed' once seen on this visit.
  const [shown, setShown] = useState<string[] | 'closed' | null>(null);
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
  const majors = feed ? entries.filter((entry) => entry.major && !state[entry.key]?.readAt).map((entry) => entry.key) : [];

  const mark = useCallback((keys: string[], how: 'read' | 'dismissed') => {
    if (!keys.length) return;
    const at = new Date().toISOString();
    setFeed((current) => current && { ...current, state: { ...current.state, ...Object.fromEntries(keys.map((key) => [key, { readAt: at, dismissedAt: how === 'dismissed' ? at : current.state[key]?.dismissedAt ?? null }])) } });
    void markNews(apiVersion, organization.id, keys, how).catch(() => {});
  }, [apiVersion, organization.id]);

  // Major announcements open once for each person, a moment after the app settles: one window
  // with a page for each.
  useEffect(() => {
    if (!majors.length || shown || panel) return;
    const timer = window.setTimeout(() => setShown(majors), 1500);
    return () => window.clearTimeout(timer);
  }, [majors.join(), shown, panel]); // eslint-disable-line react-hooks/exhaustive-deps

  const open = useCallback(() => { setFresh(unreadKeys); setPanel(true); mark(unreadKeys, 'read'); }, [mark, unreadKeys.join()]); // eslint-disable-line react-hooks/exhaustive-deps
  const value = useMemo(() => ({ entries, unread: unreadKeys.length, open }), [entries, unreadKeys.length, open]);
  const pages = Array.isArray(shown) ? entries.filter((entry) => shown.includes(entry.key)) : [];

  return <NewsContext.Provider value={value}>
    {children}
    <Sheet open={panel} onClose={() => setPanel(false)} title="What’s new" subtitle='Product updates, events and notices for your workspace.'>
      {entries.length === 0 ? <p className='nw-empty'>Nothing new yet. Updates and events will appear here.</p>
        : <ol className='nw-timeline'>{entries.map((entry) => <NewsItem key={entry.key} entry={entry} unread={fresh.includes(entry.key)}
          onDismiss={entry.dismissible ? () => mark([entry.key], 'dismissed') : undefined} onLeave={() => setPanel(false)} />)}</ol>}
    </Sheet>
    <MajorWindow pages={pages} onClose={() => { mark(pages.map((entry) => entry.key), 'read'); setShown('closed'); }} />
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
      {entry.image ? <img className='nw-thumb' src={entry.image} alt='' loading='lazy' />
        // A video without a still frame: its first frame, playing (muted) only while pointed at.
        : entry.media?.video && <video className='nw-thumb' src={entry.media.video} muted loop playsInline preload='metadata' aria-label={entry.media.alt}
          onMouseEnter={(event) => { if (!reducedMotion()) void event.currentTarget.play().catch(() => {}); }}
          onMouseLeave={(event) => event.currentTarget.pause()} />}
      <p>{entry.body}</p>
      <Actions entry={entry} onLeave={onLeave} />
    </article>
  </li>;
}

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;

/** The window's media at 16:9: the published video (muted and looping, only while the window is
 *  open; its still frame under reduced motion), the image with its dark-mode version, or the app's
 *  own cover when there is none or it fails to load. */
function Cover({ entry }: { entry: Entry }) {
  const [broken, setBroken] = useState<string | null>(null);
  const media = entry.media && broken !== entry.key ? entry.media : null;
  if (!media) {
    const Icon = ICONS[entry.tone];
    return <div className='nw-cover' data-tone={entry.tone} aria-hidden='true'>
      <img className='nw-cover-mark' src='/audoryn-mark.png' alt='' />
      <span className='nw-cover-word'>{entry.tag}</span>
      <span className='nw-cover-icon'><Icon size={22} /></span>
    </div>;
  }
  const failed = () => setBroken(entry.key);
  return <div className='nw-cover nw-cover-media'>
    {media.video && (!reducedMotion() || !media.image)
      // Under reduced motion a video without a still frame shows its first frame, paused.
      ? <video key={media.video} src={media.video} poster={media.image} muted loop autoPlay={!reducedMotion()} playsInline preload='metadata' aria-label={media.alt} onError={failed} />
      : <>
        <img className={media.imageDark ? 'nw-media-light' : undefined} src={media.image} alt={media.alt} onError={failed} />
        {media.imageDark && <img className='nw-media-dark' src={media.imageDark} alt={media.alt} onError={failed} />}
      </>}
  </div>;
}

function MajorWindow({ pages, onClose }: { pages: Entry[]; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [page, setPage] = useState(0);
  const open = pages.length > 0;
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) { setPage(0); dialog.showModal(); }
    if (!open && dialog.open) dialog.close();
  }, [open]);
  const entry = pages[Math.min(page, pages.length - 1)];
  const last = page >= pages.length - 1;
  const go = (step: number) => setPage((current) => Math.max(0, Math.min(pages.length - 1, current + step)));
  return <dialog ref={ref} className='up-modal nw-major' aria-labelledby='nw-major-title'
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
    onKeyDown={(event) => { if (pages.length > 1 && (event.key === 'ArrowRight' || event.key === 'ArrowLeft')) { event.preventDefault(); go(event.key === 'ArrowRight' ? 1 : -1); } }}>
    {entry && <>
      <Cover entry={entry} />
      <button type='button' className='up-banner-close nw-major-close' aria-label='Close' onClick={onClose}><X size={16} /></button>
      <div className='nw-major-text' aria-live='polite'>
        <p className='pl-eyebrow'>{entry.tag} · {formatDate(entry.at)}{pages.length > 1 && ` · ${page + 1} of ${pages.length}`}</p>
        <h2 id='nw-major-title'>{entry.title}</h2>
        <p className='nw-major-body'>{entry.body}</p>
      </div>
      <footer className='up-modal-foot nw-major-foot'>
        {pages.length > 1 && <div className='nw-dots' role='group' aria-label='Announcements'>
          {pages.map((item, index) => <button key={item.key} type='button' aria-label={`${index + 1} of ${pages.length}: ${item.title}`} aria-current={index === page ? 'true' : undefined} onClick={() => setPage(index)} />)}
        </div>}
        {page > 0 && <button type='button' className='pl-cta pl-cta-quiet' onClick={() => go(-1)}><ArrowLeft size={15} />Back</button>}
        {!last ? <button type='button' className='pl-cta' autoFocus onClick={() => go(1)}>Next<ArrowRight size={15} /></button> : <>
          <button type='button' className='pl-cta pl-cta-quiet' onClick={onClose}>{entry.upgrade || entry.link ? 'Not now' : 'Got it'}</button>
          {entry.upgrade && <button type='button' className='pl-cta' autoFocus onClick={() => { onClose(); navigate({ page: 'plans' }); }}>See plans<ArrowRight size={15} /></button>}
          {!entry.upgrade && entry.link && (entry.link.url.startsWith('/app')
            ? <button type='button' className='pl-cta' autoFocus onClick={() => { onClose(); navigate(entry.link!.url); }}>{entry.link.label}<ArrowRight size={15} /></button>
            : <a className='pl-cta' href={entry.link.url} target='_blank' rel='noopener noreferrer' onClick={onClose}>{entry.link.label}<ExternalLink size={15} /></a>)}
        </>}
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
