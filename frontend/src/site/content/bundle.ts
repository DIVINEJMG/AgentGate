// Turns Console release data (published or preview) into the bundle a page renders: the page
// document, the shared site document, media and metadata. A page is either a valid published
// v2 document or its built-in document, never a mixture of the two.
import {CONTRACT, type Image, type PageDocument, type PageName, type Site} from '../contract/contract.generated';
import {isSafeHref, validatePage, validateSite, type ContentError} from '../contract/validate';
import {defaultPages, defaultSeo, defaultSite, type DefaultPage} from './defaults';

export type Seo = {
  title: string;
  description: string;
  canonicalUrl?: string;
  openGraphTitle?: string;
  openGraphDescription?: string;
  openGraphImage?: string;
  index: boolean;
  structuredData?: unknown;
};
export type BundleAsset = {url: string; width?: number; height?: number};
export type SiteBundle = {
  source: 'published' | 'default' | 'preview';
  path: string;
  status: 200 | 404;
  locale: string;
  page: PageDocument | null;
  site: Site;
  seo: Seo;
  assets: Record<string, BundleAsset>;
  release: {version: number; checksum: string} | null;
  /** Published articles (path and title), listed on the resources page. */
  articles?: {path: string; title: string}[];
  redirect?: {destination: string; status: 301 | 302};
  /** Why built-in content was used anywhere (codes only, safe to log). */
  notices: string[];
};

/** The Console page envelope as it appears in a release or a preview payload. */
export type PageEnvelope = {
  pageId: string; revisionId: string; locale: string; slug: string; title: string; contentType: string;
  rendererContract: string; blocks: {id?: string; type: string; content: {payload: unknown}}[]; seo?: Record<string, unknown>;
};
export type ReleaseResource = {resourceType: string; resourceId: string; data: Record<string, unknown>};
export type ReleaseAsset = {id: string; url: string; width?: number; height?: number};

const describe = (errors: ContentError[]) => errors.slice(0, 5).map(error => `${error.path || '(root)'}:${error.code}`).join(',');

export function defaultBundle(path: string, page: DefaultPage | null, _requestedLocale: string, notices: string[], site = defaultSite): SiteBundle {
  return {
    // Built-in content is English, whatever locale was requested.
    source: 'default', path, status: page ? 200 : 404, locale: 'en', page: page ? defaultPages[page] : null, site,
    seo: page ? {...defaultSeo[page], index: true} : {title: site.notFound.headline, description: site.notFound.body, index: false},
    assets: {}, release: null, notices,
  };
}

/** Reads the v2 page document out of a Console page envelope. */
export function pageFromEnvelope(envelope: PageEnvelope, expected: PageName, assets: Record<string, BundleAsset>): {page: PageDocument} | {notice: string} {
  if (envelope.rendererContract !== CONTRACT) return {notice: 'page_not_v2'};
  if (envelope.blocks?.length !== 1 || envelope.blocks[0].type !== 'PublicContent') return {notice: 'page_unsupported_blocks'};
  const result = validatePage(envelope.blocks[0].content?.payload);
  if (!result.ok) return {notice: `page_invalid[${describe(result.errors)}]`};
  if (result.value.page !== expected) return {notice: 'page_template_mismatch'};
  const missing = imagesOf(result.value).find(image => !assets[image.asset]);
  if (missing) return {notice: 'page_missing_media'};
  return {page: result.value};
}

/** The shared site document from release resources, with Console navigation when both are published. */
export function siteFromResources(resources: ReleaseResource[], locale: string, pageSlugs: Map<string, string>): {site: Site; notices: string[]} {
  const notices: string[] = [];
  let site = defaultSite;
  const global = resources.find(item => item.resourceType === 'global_content' && item.data.key === 'public-site' && item.data.locale === locale);
  if (!global) return {site, notices: ['site_not_published']};
  const published = validateSite(global.data.value);
  if (!published.ok) return {site, notices: [`site_invalid[${describe(published.errors)}]`]};
  site = published.value;

  // Console navigation is part of v2 publishing: it applies only together with a valid v2 site
  // document, so navigation written for the previous site never points into the new pages.

  const header = navigationFrom(resources, 'header', locale, pageSlugs);
  const footer = navigationFrom(resources, 'footer', locale, pageSlugs);
  if (header || footer) {
    const candidate = {...site, navigation: {header: header ?? site.navigation.header, footer: footer ? footer.map(({id, label, children}) => ({id, label, children})) : site.navigation.footer}};
    const result = validateSite(candidate);
    if (result.ok) site = result.value;
    else notices.push(`navigation_invalid[${describe(result.errors)}]`);
  }
  return {site, notices};
}

type NavigationItem = {id: string; parentId: string | null; label: string; position: number; visible: boolean; openInNewTab?: boolean; destinationKind: string; destination: string; metadata?: {description?: string; sectionId?: string; selectedRecordId?: string}};

/** Console navigation (flat items with parents) → site menus. Returns null when not published or unusable. */
function navigationFrom(resources: ReleaseResource[], key: string, locale: string, pageSlugs: Map<string, string>) {
  const resource = resources.find(item => item.resourceType === 'navigation' && item.data.key === key && item.data.locale === locale);
  const items = resource?.data.items as NavigationItem[] | undefined;
  if (!Array.isArray(items)) return null;
  const href = (item: NavigationItem) => {
    const base = item.destinationKind === 'page' ? pageSlugs.get(item.destination) : item.destination;
    if (!base) return null;
    const section = item.metadata?.sectionId && /^[a-z][a-z0-9_-]*$/i.test(item.metadata.sectionId) ? '#' + item.metadata.sectionId : '';
    const value = base + section;
    return isSafeHref(value) ? value : null;
  };
  const link = (item: NavigationItem) => {
    const target = href(item);
    if (!target) return null;
    return {id: 'n-' + item.id.toLowerCase(), label: item.label, href: target, ...(item.metadata?.description ? {description: item.metadata.description} : {}), ...(item.openInNewTab ? {newTab: true} : {})};
  };
  const visible = items.filter(item => item.visible).sort((a, b) => a.position - b.position);
  const menus = [];
  for (const root of visible.filter(item => !item.parentId)) {
    const top = link(root);
    if (!top) return null;
    const children = visible.filter(item => item.parentId === root.id).map(link);
    if (children.some(child => !child)) return null;
    // On a top-level menu, the Console description is the menu's introduction.
    const {description, ...menu} = top;
    menus.push({...menu, ...(description ? {intro: description} : {}), children: children as NonNullable<ReturnType<typeof link>>[]});
  }
  return menus.length ? menus : null;
}

/** Page metadata from the envelope's SEO fields, falling back to the built-in title and description. */
export function seoFrom(raw: Record<string, unknown> | undefined, page: PageName, envelopeTitle: string, assets: Record<string, BundleAsset>): Seo {
  const text = (key: string) => (typeof raw?.[key] === 'string' && raw[key] ? String(raw[key]) : undefined);
  const fallback = page === 'article' ? {title: envelopeTitle + ' · Audoryn', description: defaultSeo.home.description} : defaultSeo[page];
  const canonical = text('canonicalUrl');
  const image = text('openGraphImageAssetId');
  return {
    title: text('metaTitle') ?? fallback.title,
    description: text('metaDescription') ?? fallback.description,
    canonicalUrl: canonical && isSafeHref(canonical) ? canonical : undefined,
    openGraphTitle: text('openGraphTitle'),
    openGraphDescription: text('openGraphDescription'),
    openGraphImage: image ? assets[image]?.url : undefined,
    index: raw?.index !== false,
    structuredData: raw?.structuredData,
  };
}

/** Every image a page document refers to. */
export function imagesOf(value: unknown): Image[] {
  const found: Image[] = [];
  const walk = (node: unknown) => {
    if (Array.isArray(node)) node.forEach(walk);
    else if (node && typeof node === 'object') {
      const record = node as Record<string, unknown>;
      if (typeof record.asset === 'string' && typeof record.alt === 'string') found.push(record as Image);
      Object.values(record).forEach(walk);
    }
  };
  walk(value);
  return found;
}
