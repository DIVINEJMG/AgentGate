// Top navigation: the established Audoryn style (thin bar, centred links, full-width menu panel
// with an introduction and numbered links), built from real links.
//
// Mouse: hovering a menu item opens its panel after a short delay. Keyboard: Arrow Down on a
// menu item opens the panel and moves into it; Escape closes it and returns focus. Touch screens
// and narrow screens use the Menu button, which opens a full-height sheet.
//
// The bar never grows with its content. When Console adds menus or longer labels, the space
// between the links shrinks (from 28px down to 12px); when even that does not fit, the bar
// switches to the Menu button, so links never overlap the actions or push the page sideways.
import {useEffect, useId, useRef, useState, type KeyboardEvent} from 'react';
import {ArrowUpRight} from 'lucide-react';
import {fill, useCurrentPath, useSite} from '../content/SiteContent';
import {SiteLink} from '../navigation';
import './header.css';

const OPEN_DELAY = 280;
const SWITCH_DELAY = 110;
const CLOSE_DELAY = 220;
const MAX_GAP = 28;
const MIN_GAP = 12;
/** Space kept between the links and the wordmark or the actions on each side. */
const SIDE_SPACE = 32;

const pathOf = (href: string) => href.split(/[?#]/)[0] || '/';
const isCurrent = (current: string, href: string) => {
  const path = pathOf(href);
  return path === '/' ? current === '/' : current === path || current.startsWith(path + '/');
};

export function Header() {
  const site = useSite();
  const current = useCurrentPath();
  const menus = site.navigation.header;
  const [open, setOpen] = useState<string | null>(null);
  const [sheet, setSheet] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  // Space between the top links, or 'compact' when they do not fit and the Menu button is used.
  const [fit, setFit] = useState<number | 'compact'>(MAX_GAP);
  const [sheetTop, setSheetTop] = useState(0);
  const header = useRef<HTMLElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const wordmark = useRef<HTMLAnchorElement>(null);
  const actions = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const links = useRef<Record<string, HTMLAnchorElement | null>>({});
  const panel = useRef<HTMLDivElement>(null);
  const sheetButton = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const sheetId = useId();

  const clear = () => {if (timer.current) clearTimeout(timer.current); timer.current = null;};
  const schedule = (next: string | null, delay: number) => {clear(); timer.current = setTimeout(() => setOpen(next), delay);};
  const hasChildren = (id: string) => Boolean(menus.find(menu => menu.id === id)?.children.length);

  useEffect(() => () => clear(), []);
  useEffect(() => {setOpen(null); setSheet(false);}, [current]);
  useEffect(() => {
    const update = () => setScrolled(window.scrollY > 4);
    update();
    window.addEventListener('scroll', update, {passive: true});
    return () => window.removeEventListener('scroll', update);
  }, []);
  useEffect(() => {
    const measure = () => {
      if (!bar.current || !list.current || !wordmark.current || !actions.current) return;
      const links = [...list.current.querySelectorAll<HTMLElement>(':scope > li > a')];
      // Hidden by the narrow-screen or touch rules: those use the Menu button anyway.
      if (!links.length || !links[0].offsetWidth) return;
      const text = links.reduce((sum, link) => sum + link.scrollWidth, 0);
      const style = getComputedStyle(bar.current);
      const inner = bar.current.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      // The links stay centred, so both sides keep room for the wider of the wordmark and the actions.
      const side = Math.max(wordmark.current.offsetWidth, actions.current.scrollWidth) + SIDE_SPACE;
      const room = (inner - 2 * side - text) / Math.max(links.length - 1, 1);
      setFit(room >= MIN_GAP ? Math.min(MAX_GAP, Math.floor(room)) : 'compact');
    };
    measure();
    const observer = new ResizeObserver(measure);
    if (bar.current) observer.observe(bar.current);
    if (list.current) observer.observe(list.current);
    document.fonts?.ready.then(measure).catch(() => undefined);
    return () => observer.disconnect();
  }, [menus]);
  useEffect(() => {
    if (fit === 'compact') {clear(); setOpen(null);}
    else setSheet(false);
  }, [fit]);
  useEffect(() => {
    if (!sheet) return;
    // The sheet starts below the header (and the announcement bar, if it is still in view).
    const place = () => setSheetTop(Math.max(0, Math.round(header.current?.getBoundingClientRect().bottom ?? 0)));
    place();
    window.addEventListener('resize', place);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const close = (event: globalThis.KeyboardEvent) => {if (event.key === 'Escape') {setSheet(false); sheetButton.current?.focus();}};
    window.addEventListener('keydown', close);
    return () => {document.body.style.overflow = previous; window.removeEventListener('keydown', close); window.removeEventListener('resize', place);};
  }, [sheet]);

  function onMenuKey(event: KeyboardEvent<HTMLAnchorElement>, id: string) {
    if (event.key === 'ArrowDown' && hasChildren(id)) {
      event.preventDefault();
      clear();
      setOpen(id);
      requestAnimationFrame(() => panel.current?.querySelector<HTMLAnchorElement>('a')?.focus());
    }
  }
  function onHeaderKey(event: KeyboardEvent<HTMLElement>) {
    if (event.key === 'Escape' && open) {
      const id = open;
      clear();
      setOpen(null);
      links.current[id]?.focus();
    }
  }

  const active = menus.find(menu => menu.id === open && menu.children.length);
  const announcement = site.announcement;

  return (
    <>
      {announcement && (
        <div className='site-announcement'>
          <div className='site-container'>
            <span>{announcement.text}</span>
            {announcement.link && <SiteLink href={announcement.link.href}>{announcement.link.label} <ArrowUpRight size={14} aria-hidden='true' /></SiteLink>}
          </div>
        </div>
      )}
      <header
        ref={header}
        className={`site-header${scrolled || open ? ' is-raised' : ''}${fit === 'compact' ? ' is-compact' : ''}`}
        onPointerLeave={event => {if (event.pointerType === 'mouse') schedule(null, CLOSE_DELAY);}}
        onKeyDown={onHeaderKey}
        onBlur={event => {if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {clear(); setOpen(null);}}}
      >
        <div ref={bar} className='site-header-bar'>
          <SiteLink href='/' ref={wordmark} className='site-wordmark' aria-label={site.labels.home} onPointerEnter={() => schedule(null, SWITCH_DELAY)}>
            <img src='/audoryn-mark.png' alt='' width={22} height={22} />
            <span>{site.brand.name}</span>
          </SiteLink>

          <nav className='site-nav' aria-label={site.labels.mainNavigation} aria-hidden={fit === 'compact' || undefined}>
            <ul ref={list} style={typeof fit === 'number' && fit !== MAX_GAP ? {gap: fit} : undefined}>
              {menus.map(menu => (
                <li key={menu.id}>
                  <SiteLink
                    href={menu.href}
                    newTab={menu.newTab}
                    tabIndex={fit === 'compact' ? -1 : undefined}
                    ref={(element: HTMLAnchorElement | null) => {links.current[menu.id] = element;}}
                    className={isCurrent(current, menu.href) || open === menu.id ? 'is-active' : undefined}
                    aria-current={isCurrent(current, menu.href) ? 'page' : undefined}
                    aria-expanded={menu.children.length ? open === menu.id : undefined}
                    aria-controls={menu.children.length ? panelId : undefined}
                    onPointerEnter={event => {if (event.pointerType === 'mouse') schedule(menu.children.length ? menu.id : null, open ? SWITCH_DELAY : OPEN_DELAY);}}
                    onKeyDown={event => onMenuKey(event, menu.id)}
                  >
                    {menu.label}
                  </SiteLink>
                </li>
              ))}
            </ul>
          </nav>

          <div ref={actions} className='site-header-actions' aria-hidden={fit === 'compact' || undefined} onPointerEnter={() => schedule(null, SWITCH_DELAY)}>
            <SiteLink href={site.actions.signIn.href} className='site-signin' tabIndex={fit === 'compact' ? -1 : undefined}>{site.actions.signIn.label}</SiteLink>
            <SiteLink href={site.actions.getStarted.href} className='site-header-cta' tabIndex={fit === 'compact' ? -1 : undefined}>{site.actions.getStarted.label}</SiteLink>
          </div>

          <button ref={sheetButton} type='button' className='site-menu-button' aria-expanded={sheet} aria-controls={sheetId} onClick={() => setSheet(value => !value)}>
            {sheet ? site.labels.closeMenu : site.labels.openMenu}
          </button>
        </div>

        <div id={panelId} ref={panel} className='site-menu-panel' hidden={!active} onPointerEnter={clear}>
          {active && (
            <div className='site-menu-panel-inner'>
              <div className='site-menu-intro'>
                <span className='site-menu-trail'>{site.brand.name} / {active.label}</span>
                <strong>{active.label}</strong>
                {active.intro && <p>{active.intro}</p>}
                <SiteLink href={active.href} className='site-menu-explore'>
                  {fill(site.labels.exploreMenu, {label: active.label})} <ArrowUpRight size={15} aria-hidden='true' />
                </SiteLink>
              </div>
              {/* Up to four links: two columns. Five or six: three narrower columns, so the panel keeps its height. */}
              <ul className={`site-menu-links${active.children.length > 4 ? ' is-dense' : ''}`}>
                {active.children.map((link, index) => (
                  <li key={link.id}>
                    <SiteLink href={link.href} newTab={link.newTab}>
                      <span className='site-menu-number' aria-hidden='true'>{String(index + 1).padStart(2, '0')}</span>
                      <span>
                        <strong>{link.label}</strong>
                        {link.description && <small>{link.description}</small>}
                      </span>
                      <ArrowUpRight className='site-menu-arrow' size={16} aria-hidden='true' />
                    </SiteLink>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </header>

      {/* Outside the header: the header's blur makes it the frame for fixed children, which
          would squeeze the sheet into the header's own height. */}
      <div id={sheetId} className={`site-sheet${fit === 'compact' ? ' is-compact' : ''}`} hidden={!sheet} style={{top: sheetTop}}>
        <nav aria-label={site.labels.mainNavigation}>
          {menus.map(menu => (
            <div key={menu.id} className='site-sheet-group'>
              <SiteLink href={menu.href} newTab={menu.newTab} className='site-sheet-top' aria-current={isCurrent(current, menu.href) ? 'page' : undefined}>{menu.label}</SiteLink>
              {menu.children.length > 0 && (
                <ul>
                  {menu.children.map(link => <li key={link.id}><SiteLink href={link.href} newTab={link.newTab}>{link.label}</SiteLink></li>)}
                </ul>
              )}
            </div>
          ))}
        </nav>
        <div className='site-sheet-actions'>
          <SiteLink href={site.actions.signIn.href}>{site.actions.signIn.label}</SiteLink>
          <SiteLink href={site.actions.getStarted.href}>{site.actions.getStarted.label}</SiteLink>
        </div>
      </div>
    </>
  );
}
