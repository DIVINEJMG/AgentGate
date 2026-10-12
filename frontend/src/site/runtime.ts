// Browser helpers for the public site: the server's bootstrap, the current path (including
// links from the earlier hash-based site) and in-page navigation.
import type {SiteBundle} from './content/bundle';

/** What the server embeds in each page it renders (see server.tsx). */
export type Bootstrap = {path: string; site: SiteBundle | null; cacheSeconds?: number; previewOrigin?: string};

export function readBootstrap(): Bootstrap | null {
  const element = document.getElementById('public-content-bootstrap');
  if (!element?.textContent) return null;
  try {return JSON.parse(element.textContent) as Bootstrap;} catch {return null;}
}

/** The path to render. Old links such as /#pricing or /#/login still reach their page. */
export function routePath(location: Pick<Location, 'pathname' | 'hash' | 'search'>): string {
  const legacy = location.hash.match(/^#\/?(home|product|solutions|security|pricing|resources|company|privacy|terms|login|signup|app|workspace-preview)(?:\?|$)/);
  if (legacy) return legacy[1] === 'home' ? '/' : '/' + legacy[1];
  return location.pathname.replace(/\/$/, '') || '/';
}

/** Moves to a same-origin path without reloading; https links leave the site. */
export function navigatePublic(destination: string) {
  if (destination.startsWith('https://')) {window.location.assign(destination); return;}
  const path = destination === 'home' ? '/' : destination.startsWith('/') ? destination : '/' + destination;
  const url = new URL(path, window.location.origin);
  if (url.origin !== window.location.origin) throw new Error('unsafe_navigation');
  window.history.pushState(null, '', url.pathname + url.search + url.hash);
  window.dispatchEvent(new PopStateEvent('popstate'));
}
