// Restricted Console preview (/_cms/preview).
//
// Console opens this page in an iframe. The page announces itself with a one-time nonce; only a
// message from the configured Console origin, from the parent window, carrying that nonce, is
// accepted. Only audoryn.public.v2 content is shown.
import {useEffect, useRef, useState, type ReactNode} from 'react';
import {CONTRACT} from '../contract/contract.generated';
import {validatePage} from '../contract/validate';
import {defaultSeo, type DefaultPage} from '../content/defaults';
import {siteFromResources, type BundleAsset, type ReleaseResource, type SiteBundle} from '../content/bundle';
import {SiteDocument} from '../SiteApp';
import type {Release} from '../content/loader';

type PreviewData = {
  previewMode?: string; pinnedReleaseVersion?: number;
  revision?: {pageId?: string; slug?: string; locale?: string; title?: string; seo?: Record<string, unknown>; blocks?: {type: string; content?: {payload?: unknown}}[]};
  assets?: Record<string, string>; navigation?: Record<string, unknown>[]; globalContent?: Record<string, unknown>[];
  unresolvedReferences?: unknown[]; unresolvedAssets?: unknown[];
};

/** Builds a v2 bundle from Console's preview payload. Throws a code when it cannot be shown. */
export function previewSiteBundle(data: PreviewData, published?: {pages: {pageId: string; slug: string; locale: string}[]}): SiteBundle {
  if (data.previewMode !== 'content_inspection' || !data.revision) throw new Error('unsupported_preview_contract');
  if (data.unresolvedReferences?.length || data.unresolvedAssets?.length) throw new Error('unresolved_preview_dependencies');
  const locale = data.revision.locale || 'en';
  const result = validatePage(data.revision.blocks?.[0]?.content?.payload);
  if (!result.ok) throw new Error('invalid_preview_content');
  const assets: Record<string, BundleAsset> = {};
  for (const [id, url] of Object.entries(data.assets ?? {})) {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) throw new Error('invalid_preview_media_url');
    assets[id] = {url};
  }
  const resources: ReleaseResource[] = [
    ...(data.navigation ?? []).map(item => ({resourceType: 'navigation', resourceId: String(item.id), data: item})),
    ...(data.globalContent ?? []).map(item => ({resourceType: 'global_content', resourceId: String(item.id), data: item})),
  ];
  const slugs = new Map((published?.pages ?? []).filter(item => item.locale === locale).map(item => [item.pageId, item.slug]));
  if (data.revision.pageId && data.revision.slug) slugs.set(data.revision.pageId, data.revision.slug);
  const {site, notices} = siteFromResources(resources, locale, slugs);
  const page = result.value;
  const seoBase = page.page === 'article' ? {title: (data.revision.title || 'Article') + ' · Audoryn', description: defaultSeo.home.description} : defaultSeo[page.page as DefaultPage];
  return {
    source: 'preview', path: data.revision.slug || '/', status: 200, locale, page, site,
    seo: {...seoBase, index: false}, assets, release: data.pinnedReleaseVersion ? {version: Number(data.pinnedReleaseVersion), checksum: 'preview'} : null, notices,
  };
}

const isV2 = (data: PreviewData) => (data.revision?.blocks?.[0]?.content?.payload as {contract?: string} | undefined)?.contract === CONTRACT;

function Message({title, children}: {title: string; children: ReactNode}) {
  return (
    <div className='site' style={{display: 'grid', placeItems: 'center', minHeight: '100dvh', padding: '2rem', fontFamily: 'system-ui, sans-serif', background: '#f7f7f3', color: '#171715'}}>
      <main><h1 style={{fontSize: '1.5rem', fontWeight: 500, margin: 0}}>{title}</h1><p role='status' style={{color: '#5f625c'}}>{children}</p></main>
    </div>
  );
}

export function CmsPreview({previewOrigin}: {previewOrigin: string}) {
  const [state, setState] = useState<{bundle?: SiteBundle; error: string}>({error: 'Waiting for Console preview content.'});
  const nonce = useRef('');

  useEffect(() => {
    document.querySelector('meta[name="robots"]')?.setAttribute('content', 'noindex,nofollow');
    if (!previewOrigin || window.parent === window) {setState({error: 'Open this restricted preview from Console.'}); return;}
    nonce.current = crypto.randomUUID();
    let disposed = false;
    // Console may still be loading when this page is ready, so "ready" is repeated every second
    // (15 times at most) until content arrives.
    let attempts = 0;
    const announce = () => {
      attempts += 1;
      window.parent.postMessage({type: 'audoryn.cms.preview.ready', nonce: nonce.current}, previewOrigin);
      if (attempts >= 15) window.clearInterval(repeat);
    };
    const repeat = window.setInterval(announce, 1000);
    const receive = async (event: MessageEvent) => {
      if (event.origin !== previewOrigin || event.source !== window.parent || event.data?.nonce !== nonce.current || event.data?.type !== 'audoryn.cms.preview') return;
      window.clearInterval(repeat);
      try {
        const data = event.data.payload as PreviewData;
        if (!isV2(data)) {
          if (!disposed) setState({error: 'This revision uses the previous content format (audoryn.public.v1), which the website no longer shows.'});
          return;
        }
        const response = await fetch('/_public/preview-reference?version=' + encodeURIComponent(String(data.pinnedReleaseVersion || 0)), {cache: 'no-store', credentials: 'omit'});
        const reference = response.ok ? ((await response.json()).release as Release | null) ?? undefined : undefined;
        const bundle = previewSiteBundle(data, reference);
        if (!disposed) setState({bundle, error: ''});
      } catch {
        if (!disposed) setState({error: 'Preview content is incomplete or uses an unsupported renderer contract.'});
      }
    };
    window.addEventListener('message', receive);
    announce();
    return () => {disposed = true; window.clearInterval(repeat); window.removeEventListener('message', receive);};
  }, [previewOrigin]);

  if (state.bundle) return <SiteDocument bundle={state.bundle} navigate={() => undefined} />;
  return <Message title='Content preview'>{state.error}</Message>;
}
