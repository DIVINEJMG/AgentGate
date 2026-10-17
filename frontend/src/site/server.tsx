// The public site's server: every request the frontend host does not answer with a static file.
// Public pages render here with their metadata. It also answers the in-page navigation endpoint,
// the Console preview shell and its release reference, the sitemap and robots.txt, and returns
// the application shell for the workspace routes.
//
// Frame policy: no page may be framed (frame-ancestors 'none'), except the Console preview,
// which only the configured Console origin may frame.
import {renderToString} from 'react-dom/server';
import ScrollControl from '../components/ScrollControl';
import {siteConfigFromEnvironment, type SiteConfig} from './content/config';
import type {SiteBundle} from './content/bundle';
import {SiteLoader} from './content/loader';
import {CmsPreview} from './preview/CmsPreview';
import {FIXED_PAGES, normalizePath} from './routes';
import {SiteDocument} from './SiteApp';

let loader: SiteLoader | undefined;
let signature = '';
const renderCache = new Map<string, string>();

export function siteRuntime(env: Record<string, string | undefined>) {
  const config = siteConfigFromEnvironment(env);
  const next = JSON.stringify(config);
  if (!loader || signature !== next) {signature = next; loader = new SiteLoader(config); renderCache.clear();}
  return {config, loader};
}

const ENTITIES: Record<string, string> = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
export function escapeHtml(value: unknown) {
  return String(value ?? '').replace(/[&<>"']/g, char => ENTITIES[char]);
}
/** JSON safe to embed in a <script> element. */
export function scriptJson(value: unknown) {
  return JSON.stringify(value).replace(/</g, '\\u003c').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
}

type Response = {status: number; headers: Record<string, string>; body: string};
const noStore = {'Cache-Control': 'no-store'};
const json = (status: number, value: unknown): Response => ({status, headers: {...noStore, 'Content-Type': 'application/json'}, body: JSON.stringify(value)});

/** Frame policy for an HTML response. */
export function framePolicy(path: string, previewOrigin: string): Record<string, string> {
  if (path === '/_cms/preview') return {'Content-Security-Policy': `frame-ancestors ${previewOrigin || "'none'"}`};
  return {'Content-Security-Policy': "frame-ancestors 'none'", 'X-Frame-Options': 'DENY'};
}

/** Routes owned by the application (the authenticated workspace); they get its shell. */
const APPLICATION = /^\/(api|app|login|signup|workspace-preview|invite|auth)(\/|$)/;

export function headFor(bundle: SiteBundle, config: SiteConfig) {
  const {seo} = bundle;
  const origin = config.siteOrigin;
  const canonicalCandidate = new URL(seo.canonicalUrl || bundle.path, origin || 'http://localhost');
  const canonical = origin ? (canonicalCandidate.origin === origin ? canonicalCandidate.href : origin + bundle.path) : '';
  const robots = bundle.status === 404 || !seo.index ? 'noindex,nofollow' : 'index,follow';
  return [
    `<title>${escapeHtml(seo.title)}</title>`,
    `<meta name="description" content="${escapeHtml(seo.description)}"/>`,
    canonical ? `<link rel="canonical" href="${escapeHtml(canonical)}"/><meta property="og:url" content="${escapeHtml(canonical)}"/>` : '',
    `<meta property="og:type" content="website"/>`,
    `<meta property="og:title" content="${escapeHtml(seo.openGraphTitle || seo.title)}"/>`,
    `<meta property="og:description" content="${escapeHtml(seo.openGraphDescription || seo.description)}"/>`,
    seo.openGraphImage ? `<meta property="og:image" content="${escapeHtml(seo.openGraphImage)}"/><meta name="twitter:card" content="summary_large_image"/>` : '',
    `<meta name="robots" content="${robots}"/>`,
    seo.structuredData ? `<script type="application/ld+json">${scriptJson(seo.structuredData)}</script>` : '',
  ].join('');
}

function htmlResponse(template: string, status: number, head: string, body: string, bootstrap: unknown, lang: string, frame: Record<string, string>): Response {
  const html = template
    .replace(/<title>.*?<\/title>/s, '')
    .replace(/<meta name="description"[^>]*>/, '')
    .replace('</head>', head + '</head>')
    .replace(/<html([^>]*)lang="[^"]*"/, `<html$1lang="${escapeHtml(lang)}"`)
    .replace('<div id="root"></div>', `<div id="root">${body}</div><script id="public-content-bootstrap" type="application/json">${scriptJson(bootstrap)}</script>`);
  return {status, headers: {...noStore, 'Content-Type': 'text/html; charset=utf-8', ...frame}, body: html};
}

async function sitemap(config: SiteConfig, loader: SiteLoader): Promise<Response> {
  if (!config.siteOrigin) return {status: 503, headers: noStore, body: 'Sitemap unavailable: PUBLIC_SITE_ORIGIN is not set.'};
  const paths: string[] = [];
  for (const page of Object.keys(FIXED_PAGES)) {
    const bundle = await loader.load(page);
    if (bundle.status === 200 && bundle.seo.index) paths.push(page);
  }
  for (const article of (await loader.load('/resources')).articles ?? []) {
    const bundle = await loader.load(article.path);
    if (bundle.page?.page === 'article' && bundle.seo.index && !paths.includes(article.path)) paths.push(article.path);
  }
  const body = `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${paths.map(item => `<url><loc>${escapeHtml(config.siteOrigin + item)}</loc></url>`).join('')}</urlset>`;
  return {status: 200, headers: {...noStore, 'Content-Type': 'application/xml'}, body};
}

/** Answers one request. `template` is the built index.html. */
export async function publicResponse({url, env, template}: {url: string; env: Record<string, string | undefined>; template: string}): Promise<Response> {
  const {config, loader} = siteRuntime(env);
  const request = new URL(url, 'http://localhost');
  const path = normalizePath(request.pathname);

  if (APPLICATION.test(path)) return {status: 200, headers: {...noStore, 'Content-Type': 'text/html; charset=utf-8', ...framePolicy(path, '')}, body: template};

  if (path === '/robots.txt') {
    const body = `User-agent: *\nDisallow: /_cms/\nDisallow: /app\nDisallow: /login\nDisallow: /signup\n${config.siteOrigin ? 'Sitemap: ' + config.siteOrigin + '/sitemap.xml\n' : ''}`;
    return {status: 200, headers: {...noStore, 'Content-Type': 'text/plain'}, body};
  }
  if (path === '/sitemap.xml') return sitemap(config, loader);

  // In-page navigation: the bundle for any path (not-found bundles included).
  if (path === '/_public/site') return json(200, {bundle: await loader.load(normalizePath(request.searchParams.get('path') || '/'))});

  // The workspace app's "What's new" lists the published changelog entries.
  if (path === '/_public/changelog') return json(200, {entries: await loader.changelog()});

  // The Console preview asks for the release it was pinned to, to resolve links to other pages.
  if (path === '/_public/preview-reference') {
    const current = config.enabled ? await loader.release().catch(() => null) : null;
    const matches = current !== null && current.release.version === Number(request.searchParams.get('version'));
    return json(matches ? 200 : 409, {release: current?.release ?? null});
  }

  if (path === '/_cms/preview') {
    const head = `<title>Content preview · Audoryn</title><meta name="robots" content="noindex,nofollow"/>`;
    const body = renderToString(<><CmsPreview previewOrigin={config.previewOrigin} /><ScrollControl /></>);
    return htmlResponse(template, 200, head, body, {path, site: null, cacheSeconds: config.cacheSeconds, previewOrigin: config.previewOrigin}, config.locale, framePolicy(path, config.previewOrigin));
  }

  const bundle = await loader.load(path);
  if (bundle.redirect) return {status: bundle.redirect.status, headers: {...noStore, Location: bundle.redirect.destination}, body: ''};
  const key = `${path}:${bundle.release?.checksum ?? 'built-in'}:${bundle.source}:${bundle.notices.join('|')}`;
  let body = renderCache.get(key);
  if (!body) {
    body = renderToString(<><SiteDocument bundle={bundle} navigate={null} /><ScrollControl /></>);
    renderCache.set(key, body);
    if (renderCache.size > 32) renderCache.delete(renderCache.keys().next().value!);
  }
  return htmlResponse(template, bundle.status, headFor(bundle, config), body, {path, site: bundle, cacheSeconds: config.cacheSeconds}, bundle.locale, framePolicy(path, config.previewOrigin));
}
