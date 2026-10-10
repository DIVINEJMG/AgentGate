export const CONTRACT = 'audoryn.public.v1';
export const publicRoutes = ['home','product','solutions','security','pricing','resources','company','privacy','terms'] as const;
export type PublicRoute = typeof publicRoutes[number];
export type Json = null | boolean | number | string | Json[] | {[key: string]: Json};
export type Payload = {contract?: string; profile: string; sections?: {id: string; heading: string; paragraphs?: string[]; points?: string[]}[]; entries?: {id: string; label: string; record_ids?: string[]}[]; [key: string]: unknown};
export type Page = {pageId: string; revisionId: string; locale: string; slug: string; title: string; contentType: string; rendererContract: string; blocks: {id: string; type: string; content: {payload: Payload}}[]; seo: Record<string, unknown>};
export type PageIndex = Omit<Page, 'blocks' | 'seo'> & {url: string; checksum: string};
export type Asset = {id: string; url: string; mediaType: string; checksumSha256: string; sizeBytes: number; altText?: string; width?: number; height?: number; verification: {formatVerified: boolean; checksumSha256: string}};
export type Resource = {resourceType: string; resourceId: string; data: Record<string, unknown>};
export type Release = {schemaVersion: number; version: number; checksum: string; publishedAt: string; pages: PageIndex[]; resources: Resource[]; assets: Asset[]};
export type Pointer = {schemaVersion: number; version: number; eventId: string; checksum: string; releaseUrl: string; releaseChecksum: string};
export type ContentBundle = {source: 'static' | 'published' | 'preview'; page: Page | null; records: Page[]; release: Release | null; copies: Record<string, Record<string,string>>; removed?: boolean; redirect?: {destination: string; status: 301 | 302}};
export type PublicConfig = {enabled: boolean; origin: string; siteOrigin: string; contract: string; locale: string; cacheSeconds: number; timeoutSeconds: number; previewOrigin: string};

export function configFromEnvironment(env: Record<string,string | undefined>): PublicConfig {
  const origin = env.PUBLIC_CONTENT_ORIGIN || '';
  const siteOrigin = env.PUBLIC_SITE_ORIGIN || '';
  for (const [value, allowPath] of [[origin,true], [siteOrigin,false], [env.PUBLIC_CONTENT_PREVIEW_ORIGIN || '',false]] as const) {
    if (!value) continue;
    const parsed = new URL(value);
    if (parsed.username || parsed.password || parsed.search || parsed.hash || !allowPath && parsed.pathname !== '/' || /%2f|%5c|%2e/i.test(parsed.pathname) || !['https:','http:'].includes(parsed.protocol) || parsed.protocol === 'http:' && !['localhost','127.0.0.1'].includes(parsed.hostname)) throw new Error('invalid_public_origin');
  }
  function seconds(name: string, fallback: number, max: number) {
    const value = Number(env[name] ?? fallback);
    if (!Number.isFinite(value) || value < 1 || value > max) throw new Error(`invalid_${name.toLowerCase()}`);
    return value;
  }
  const enabled = env.PUBLIC_CONTENT_ENABLED === 'true' && Boolean(origin);
  if (env.PUBLIC_CONTENT_CONTRACT && env.PUBLIC_CONTENT_CONTRACT !== CONTRACT) throw new Error('unsupported_contract');
  return {enabled,origin:origin?new URL(origin).href.replace(/\/$/,''):'',siteOrigin:siteOrigin?new URL(siteOrigin).origin:'',contract:CONTRACT,locale:env.PUBLIC_CONTENT_DEFAULT_LOCALE || 'en',cacheSeconds:seconds('PUBLIC_CONTENT_POINTER_CACHE_SECONDS',60,3600),timeoutSeconds:seconds('PUBLIC_CONTENT_FETCH_TIMEOUT_SECONDS',5,60),previewOrigin:env.PUBLIC_CONTENT_PREVIEW_ORIGIN?new URL(env.PUBLIC_CONTENT_PREVIEW_ORIGIN).origin:''};
}

export function safeDestination(value: string) {
  if (value.startsWith('/') && !value.startsWith('//') && !/[\\\u0000-\u001f]/.test(value)) return value;
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password) throw new Error('invalid_destination');
  return value;
}

export function payloadOf(page: Page): Payload {
  if (page.rendererContract !== CONTRACT || page.blocks.length !== 1 || page.blocks[0].type !== 'PublicContent') throw new Error('unsupported_renderer');
  const payload = page.blocks[0].content.payload;
  if (payload.contract !== CONTRACT) throw new Error('unsupported_contract');
  return payload;
}

export function copiesFromPages(pages: Page[]) {
  const copies: ContentBundle['copies'] = {};
  const byId = new Map(pages.map(page => [page.pageId,page]));
  for (const page of pages) {
    const payload = payloadOf(page);
    for (const entry of payload.entries || []) {
      if (!entry.label.startsWith('copy:')) continue;
      const scope = entry.label.slice(5);
      const dictionary = copies[scope] ||= {};
      for (const id of entry.record_ids || []) {
        const record = byId.get(id);
        if (!record) throw new Error('unresolved_copy_record');
        for (const section of payloadOf(record).sections || []) {
          const value = section.paragraphs?.[0] ?? section.heading;
          if (section.id in dictionary && dictionary[section.id] !== value) throw new Error('conflicting_copy_field');
          dictionary[section.id] = value;
        }
      }
    }
  }
  return copies;
}
