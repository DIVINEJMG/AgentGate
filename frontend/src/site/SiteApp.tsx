// Renders a content bundle for the redesigned site, on the server and in the browser.
// In the browser, moving between pages fetches the next bundle from /_public/site.
import {useEffect, useState} from 'react';
import type {PageDocument} from './contract/contract.generated';
import {builtInMedia, SiteContentProvider, useSite, type MediaResolver} from './content/SiteContent';
import type {SiteBundle} from './content/bundle';
import {SiteShell} from './components/SiteShell';
import {Actions, Headline, Section} from './components/primitives';
import {NavigationProvider} from './navigation';
import {ArticlePage, CompanyPage, LegalPage, ResourcesPage} from './pages/content/ContentPages';
import {HomePage} from './pages/home/HomePage';
import {PricingPage} from './pages/pricing/PricingPage';
import {ProductPage} from './pages/product/ProductPage';
import {SecurityPage} from './pages/security/SecurityPage';
import {SolutionsPage} from './pages/solutions/SolutionsPage';

/** Published and preview images come from the bundle; built-in images from the site itself. */
export function mediaFor(bundle: SiteBundle): MediaResolver {
  if (bundle.source === 'default') return builtInMedia;
  return image => {
    const asset = bundle.assets[image.asset];
    return asset ? {src: asset.url, alt: image.alt, width: asset.width, height: asset.height} : null;
  };
}

function PageView({bundle}: {bundle: SiteBundle}) {
  const page: PageDocument | null = bundle.page;
  switch (page?.page) {
    case 'home': return <HomePage page={page} />;
    case 'product': return <ProductPage page={page} />;
    case 'solutions': return <SolutionsPage page={page} />;
    case 'security': return <SecurityPage page={page} />;
    case 'pricing': return <PricingPage page={page} />;
    case 'resources': return <ResourcesPage page={page} articles={bundle.articles ?? []} />;
    case 'company': return <CompanyPage page={page} />;
    case 'privacy': case 'terms': return <LegalPage page={page} />;
    case 'article': return <ArticlePage page={page} />;
    default: return <NotFound />;
  }
}

function NotFound() {
  const site = useSite();
  return (
    <Section id='not-found' tone='night' className='site-not-found'>
      <span className='site-not-found-line' aria-hidden='true' />
      <Headline as='h1' size='hero' headline={site.notFound.headline} />
      <p className='site-lead'>{site.notFound.body}</p>
      <Actions primary={site.notFound.action} night />
    </Section>
  );
}

/** Full page: shell plus page. Pure rendering; used by the server and by SiteApp. */
export function SiteDocument({bundle, navigate}: {bundle: SiteBundle; navigate: ((href: string) => void) | null}) {
  return (
    <SiteContentProvider site={bundle.site} media={mediaFor(bundle)} path={bundle.path}>
      <NavigationProvider navigate={navigate}>
        <SiteShell>
          <PageView bundle={bundle} />
        </SiteShell>
      </NavigationProvider>
    </SiteContentProvider>
  );
}

const cache = new Map<string, {bundle: SiteBundle; expires: number}>();

/** Browser entry for redesigned pages: hydrates the server bundle, fetches on navigation. */
export function SiteApp({path, initial, cacheSeconds, navigate}: {path: string; initial?: SiteBundle; cacheSeconds: number; navigate: (href: string) => void}) {
  const [bundle, setBundle] = useState<SiteBundle | undefined>(initial?.path === path ? initial : cache.get(path)?.bundle);

  useEffect(() => {
    if (bundle?.path === path) return;
    const cached = cache.get(path);
    if (cached && cached.expires > Date.now()) {setBundle(cached.bundle); return;}
    const controller = new AbortController();
    fetch('/_public/site?path=' + encodeURIComponent(path), {signal: controller.signal, credentials: 'omit'})
      .then(response => (response.ok ? response.json() : Promise.reject(new Error('site_endpoint'))))
      .then((value: {bundle: SiteBundle}) => {
        if (controller.signal.aborted) return;
        cache.set(path, {bundle: value.bundle, expires: Date.now() + cacheSeconds * 1000});
        if (cache.size > 8) cache.delete(cache.keys().next().value!);
        setBundle(value.bundle);
      })
      .catch(() => {if (!controller.signal.aborted) window.location.assign(path);});
    return () => controller.abort();
  }, [path, bundle?.path, cacheSeconds]);

  useEffect(() => {
    if (!bundle || bundle.path !== path) return;
    if (bundle.redirect) {navigate(bundle.redirect.destination); return;}
    document.title = bundle.seo.title;
    document.querySelector<HTMLMetaElement>('meta[name="description"]')?.setAttribute('content', bundle.seo.description);
    const anchor = window.location.hash.length > 1 ? decodeURIComponent(window.location.hash.slice(1)) : '';
    requestAnimationFrame(() => (anchor ? document.getElementById(anchor)?.scrollIntoView() : undefined));
  }, [bundle, path, navigate]);

  if (!bundle || bundle.path !== path) return <div className='site' aria-busy='true' style={{minHeight: '100dvh'}} />;
  return <SiteDocument bundle={bundle} navigate={navigate} />;
}
