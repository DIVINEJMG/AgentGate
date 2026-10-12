// Loads verified published content for the redesigned site (audoryn.public.v2).
//
// Delivery protections (unchanged from the current site):
// - documents are read only from the configured storage origin under /cms-public/, without
//   credentials, query strings or encoded path tricks, and never through redirects;
// - every document is checked against its SHA-256 checksum from the release;
// - documents are limited to 4 MiB, releases to 512 pages / 200 resources / 512 assets,
//   media to 50 MiB and verified format;
// - the release pointer is revalidated with ETag after the cache lifetime, and a release
//   older than the one already held is refused;
// - caches are bounded (128 documents / 16 MiB, 8 page bundles);
// - every request has a deadline.
//
// The loader never throws for content problems. Each fixed page renders either a valid
// published v2 document, the last valid one held, or its built-in document; the reasons are
// recorded as codes in `notices` and logged without content.
import {FIXED_PAGES, isContentPath, normalizePath} from '../routes';
import type {SiteConfig} from './config';
import {CONTRACT} from '../contract/contract.generated';
import type {DefaultPage} from './defaults';
import {defaultBundle, pageFromEnvelope, seoFrom, siteFromResources, type BundleAsset, type PageEnvelope, type ReleaseResource, type SiteBundle} from './bundle';

export const MAX_DOCUMENT_BYTES = 4 * 1024 * 1024;
const MAX_CACHED_DOCUMENTS = 128;
const MAX_CACHED_BYTES = 16 * 1024 * 1024;
const MAX_PREVIOUS_BUNDLES = 8;

export type Pointer = {schemaVersion: number; version: number; eventId: string; checksum: string; releaseUrl: string; releaseChecksum: string};
export type PageIndex = {pageId: string; revisionId: string; locale: string; slug: string; title: string; contentType: string; rendererContract: string; url: string; checksum: string};
export type Asset = {id: string; url: string; mediaType: string; checksumSha256: string; sizeBytes: number; width?: number; height?: number; verification?: {formatVerified?: boolean; checksumSha256?: string}};
export type Release = {schemaVersion: number; version: number; checksum: string; publishedAt: string; pages: PageIndex[]; resources: ReleaseResource[]; assets: Asset[]};

export async function sha256(bytes: Uint8Array) {
  const digest = await crypto.subtle.digest('SHA-256', bytes as Uint8Array<ArrayBuffer>);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

class ContentError extends Error {}
const fail = (code: string): never => {throw new ContentError(code);};

export class SiteLoader {
  private documents = new Map<string, {value: unknown; size: number}>();
  private cachedBytes = 0;
  private pending = new Map<string, Promise<unknown>>();
  private current: {pointer: Pointer; release: Release; etag: string; checked: number} | null = null;
  private pointerRequest: Promise<{pointer: Pointer; release: Release}> | null = null;
  private previous = new Map<string, SiteBundle>();
  readonly metrics = {requests: 0, bytes: 0, cacheHits: 0};

  constructor(readonly config: SiteConfig, private fetcher: typeof fetch = fetch, private clock = () => Date.now(), private log: (category: string) => void = category => console.warn('Public content', {category})) {}

  /** Only the storage origin, under its /cms-public/ prefix. */
  private address(value: string) {
    const base = new URL(this.config.origin);
    const url = new URL(value, this.config.origin);
    const prefix = base.pathname.replace(/\/$/, '') + '/cms-public/';
    if (url.origin !== base.origin || url.username || url.password || url.search || url.hash || !url.pathname.startsWith(prefix) || /%2f|%5c|%2e/i.test(url.pathname)) fail('untrusted_artifact_url');
    return url.href;
  }

  private async bytes(url: string, headers: Record<string, string> = {}) {
    this.metrics.requests++;
    const response = await this.fetcher(this.address(url), {headers, signal: AbortSignal.timeout(this.config.timeoutSeconds * 1000), redirect: 'error'});
    if (response.status === 304) return {response, bytes: new Uint8Array()};
    if (!response.ok) fail('public_storage_unavailable');
    if (Number(response.headers.get('content-length') || 0) > MAX_DOCUMENT_BYTES) fail('document_size_limit');
    const reader = response.body?.getReader() ?? fail('empty_document');
    const chunks: Uint8Array[] = [];
    let count = 0;
    try {
      for (;;) {
        const part = await reader.read();
        if (part.done) break;
        count += part.value.length;
        if (count > MAX_DOCUMENT_BYTES) fail('document_size_limit');
        chunks.push(part.value);
      }
    } finally {await reader.cancel();}
    const bytes = new Uint8Array(count);
    let offset = 0;
    for (const chunk of chunks) {bytes.set(chunk, offset); offset += chunk.length;}
    this.metrics.bytes += count;
    return {response, bytes};
  }

  /** A checksummed immutable document, cached and deduplicated. */
  private async document<T>(url: string, digest: string): Promise<T> {
    if (!/^[a-f0-9]{64}$/.test(digest)) fail('invalid_checksum');
    const key = this.address(url) + '#' + digest;
    const cached = this.documents.get(key);
    if (cached) {this.metrics.cacheHits++; return cached.value as T;}
    const waiting = this.pending.get(key);
    if (waiting) return waiting as Promise<T>;
    const work = (async () => {
      const {bytes} = await this.bytes(url);
      if (await sha256(bytes) !== digest) fail('checksum_mismatch');
      const value = JSON.parse(new TextDecoder().decode(bytes));
      this.documents.set(key, {value, size: bytes.length});
      this.cachedBytes += bytes.length;
      while (this.documents.size > MAX_CACHED_DOCUMENTS || this.cachedBytes > MAX_CACHED_BYTES) {
        const [oldest, entry] = this.documents.entries().next().value!;
        this.cachedBytes -= entry.size;
        this.documents.delete(oldest);
      }
      return value;
    })();
    this.pending.set(key, work);
    try {return await work as T;} finally {this.pending.delete(key);}
  }

  /** The current release, revalidated after the cache lifetime. */
  async release() {
    if (this.current && this.clock() - this.current.checked < this.config.cacheSeconds * 1000) return this.current;
    if (this.pointerRequest) return this.pointerRequest;
    const work = (async () => {
      const {response, bytes} = await this.bytes(this.config.origin + '/cms-public/current.json', this.current?.etag ? {'If-None-Match': this.current.etag} : {});
      if (response.status === 304 && this.current) {this.current.checked = this.clock(); return this.current;}
      const pointer = JSON.parse(new TextDecoder().decode(bytes)) as Pointer;
      if (pointer.schemaVersion !== 1 || !Number.isSafeInteger(pointer.version) || pointer.version < 1 || !pointer.eventId || !pointer.checksum) fail('invalid_pointer');
      if (this.current && (pointer.version < this.current.pointer.version || (pointer.version === this.current.pointer.version && pointer.checksum !== this.current.pointer.checksum))) fail('stale_release');
      const release = await this.document<Release>(pointer.releaseUrl, pointer.releaseChecksum);
      if (release.schemaVersion !== 1 || release.version !== pointer.version || release.checksum !== pointer.checksum || !Array.isArray(release.pages) || release.pages.length > 512 || !Array.isArray(release.resources) || release.resources.length > 200 || !Array.isArray(release.assets) || release.assets.length > 512) fail('invalid_release');
      const keys = new Set<string>();
      for (const page of release.pages) {
        if (!isContentPath(page.slug)) fail('invalid_release_path');
        const key = page.locale + ':' + page.slug;
        if (keys.has(key)) fail('duplicate_release_page');
        keys.add(key);
        this.address(page.url);
      }
      const assets = new Set<string>();
      for (const asset of release.assets) {
        this.address(asset.url);
        if (assets.has(asset.id)) fail('duplicate_asset');
        assets.add(asset.id);
        if (!asset.id || !Number.isSafeInteger(asset.sizeBytes) || asset.sizeBytes < 1 || asset.sizeBytes > 50 * 1024 * 1024 || !asset.mediaType || !asset.verification?.formatVerified || !/^[a-f0-9]{64}$/.test(asset.checksumSha256) || asset.verification.checksumSha256 !== asset.checksumSha256) fail('unverified_media');
      }
      this.current = {pointer, release, etag: response.headers.get('etag') || '', checked: this.clock()};
      return this.current;
    })();
    this.pointerRequest = work;
    try {return await work;} finally {this.pointerRequest = null;}
  }

  /** The bundle for a path. Never throws for content problems; see the file header. */
  async load(rawPath: string, signal?: AbortSignal): Promise<SiteBundle> {
    const path = normalizePath(rawPath);
    const locale = this.config.locale;
    if (!isContentPath(path)) return defaultBundle(path, null, locale, ['invalid_path']);
    const page = FIXED_PAGES[path] ?? null;
    if (!this.config.enabled) return defaultBundle(path, page, locale, ['content_disabled']);
    const deadline = AbortSignal.timeout(this.config.timeoutSeconds * 1000);
    const combined = signal ? AbortSignal.any([signal, deadline]) : deadline;
    let onAbort: (() => void) | undefined;
    const expiry = new Promise<never>((_, reject) => {
      onAbort = () => reject(signal?.aborted ? new DOMException('Aborted', 'AbortError') : new ContentError('fetch_deadline_expired'));
      combined.addEventListener('abort', onAbort, {once: true});
      if (combined.aborted) onAbort();
    });
    try {
      return await Promise.race([this.resolve(path, page, locale), expiry]);
    } catch (error) {
      if (signal?.aborted) throw error;
      const code = error instanceof ContentError ? error.message : 'invalid_document';
      this.log(code);
      const held = this.previous.get(locale + ':' + path);
      return held ? {...held, notices: [...held.notices, 'kept_last_valid:' + code]} : defaultBundle(path, page, locale, [code]);
    } finally {
      if (onAbort) combined.removeEventListener('abort', onAbort);
    }
  }

  private async resolve(path: string, page: DefaultPage | null, locale: string): Promise<SiteBundle> {
    const {release} = await this.release().catch(error => {
      if (!this.current) throw error;
      this.log('release_kept_after_pointer_failure');
      return this.current;
    });
    const releaseRef = {version: release.version, checksum: release.checksum};

    const redirect = release.resources.find(item => item.resourceType === 'redirect' && item.data.locale === locale && item.data.fromPath === path && item.data.active);
    if (redirect) {
      const destination = String(redirect.data.toPath);
      if (!(destination.startsWith('/') && !destination.startsWith('//')) && !/^https:\/\//.test(destination)) fail('invalid_redirect');
      return {...defaultBundle(path, null, locale, []), source: 'published', release: releaseRef, redirect: {destination, status: Number(redirect.data.statusCode) === 301 ? 301 : 302}};
    }

    const slugs = new Map(release.pages.filter(item => item.locale === locale).map(item => [item.pageId, item.slug]));
    const {site, notices} = siteFromResources(release.resources, locale, slugs);
    const articles = release.pages.filter(item => item.locale === locale && item.contentType === 'article' && item.rendererContract === CONTRACT).map(item => ({path: item.slug, title: item.title}));
    const assets: Record<string, BundleAsset> = Object.fromEntries(release.assets.map(asset => [asset.id, {url: asset.url, width: asset.width, height: asset.height}]));
    const expected = page;

    const index = release.pages.find(item => item.slug === path && item.locale === locale);
    if (!index) {
      // A page missing from a release that has pages was removed: 404, never the built-in copy.
      // Built-in content stands in only when nothing has been published yet.
      if (!expected || release.pages.length > 0) return {...defaultBundle(path, null, locale, [expected ? 'removed' : 'not_published'], site), source: 'published', release: releaseRef, articles};
      const bundle = {...defaultBundle(path, expected, locale, [...notices, 'page_not_published'], site), release: releaseRef, articles};
      this.log('default_used:page_not_published');
      return bundle;
    }

    const envelope = await this.document<PageEnvelope>(index.url, index.checksum);
    if (envelope.pageId !== index.pageId || envelope.revisionId !== index.revisionId || envelope.slug !== index.slug || envelope.locale !== index.locale || envelope.rendererContract !== index.rendererContract || envelope.contentType !== index.contentType) fail('page_identity_mismatch');
    const result = pageFromEnvelope(envelope, expected ?? 'article', assets);
    if ('notice' in result) {
      this.log('default_used:' + result.notice.replace(/\[.*$/, ''));
      const held = this.previous.get(locale + ':' + path);
      if (held) return {...held, notices: [...held.notices, 'kept_last_valid:' + result.notice]};
      if (!expected) return {...defaultBundle(path, null, locale, [result.notice], site), release: releaseRef};
      return {...defaultBundle(path, expected, locale, [...notices, result.notice], site), release: releaseRef, articles};
    }
    if (this.current && this.current.pointer.version > release.version) fail('stale_release');
    const bundle: SiteBundle = {
      source: 'published', path, status: 200, locale, page: result.page, site, articles,
      seo: seoFrom(envelope.seo, result.page.page, envelope.title, assets), assets, release: releaseRef, notices,
    };
    this.previous.delete(locale + ':' + path);
    this.previous.set(locale + ':' + path, bundle);
    if (this.previous.size > MAX_PREVIOUS_BUNDLES) this.previous.delete(this.previous.keys().next().value!);
    return bundle;
  }
}
