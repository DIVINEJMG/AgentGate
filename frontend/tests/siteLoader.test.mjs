import test, {before} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {bundle} from './helpers/bundle.mjs';

let L, D, S, P, C, R;
before(async () => ({loader: L, defaults: D, server: S, preview: P, config: C, runtime: R} = await bundle({
  loader: 'src/site/content/loader.ts', defaults: 'src/site/content/defaults/index.ts', server: 'src/site/server.tsx', preview: 'src/site/preview/CmsPreview.tsx',
  config: 'src/site/content/config.ts', runtime: 'src/site/runtime.ts',
})));

const ORIGIN = 'https://storage.example';
const hash = text => createHash('sha256').update(text).digest('hex');
const config = (over = {}) => ({enabled: true, origin: ORIGIN, siteOrigin: 'https://audoryn.example', locale: 'en', cacheSeconds: 60, timeoutSeconds: 2, previewOrigin: 'https://console.example', ...over});
const clone = value => structuredClone(value);
const HOME_ID = '0830144d-e938-587f-8754-b3d7be4144f9';
const PRODUCT_ID = 'e8fd14d1-08b4-5f23-84e7-f13a64aebd5a';
const SEED_IDS = ['de5cb793-7e09-5703-8021-c31ecd15ab0c', '262bf108-0e0b-58ab-835f-9bd73d8c353c', 'a201f4ad-5d40-51eb-8948-a5c1b09319d1'];
const SEED_ASSETS = SEED_IDS.map(id => ({id, url: `${ORIGIN}/cms-public/${id}.webp`, mediaType: 'image/webp', checksumSha256: 'b'.repeat(64), sizeBytes: 100, width: 1600, height: 900, verification: {formatVerified: true, checksumSha256: 'b'.repeat(64)}}));

function envelope(payload, over = {}) {
  return {pageId: HOME_ID, revisionId: 'rev-1', locale: 'en', slug: '/', title: 'Home', contentType: 'page', rendererContract: 'audoryn.public.v2',
    blocks: [{id: 'b1', type: 'PublicContent', content: {payload}}], seo: {metaTitle: 'Published title', metaDescription: 'Published description'}, ...over};
}

/** A fake Console storage serving one release. Returns the fetcher plus counters. */
function storage({origin = ORIGIN, version = 1, pages = [], resources = [], assets = SEED_ASSETS, tamper = {}, checksums = {}, pointerStatus = 200, etag = '"v' + version + '"', hang = false, extra = {}} = {}) {
  const files = new Map();
  const index = pages.map((page, i) => {
    const text = JSON.stringify(page);
    const url = `${origin}/cms-public/pages/${i}-${version}.json`;
    files.set(url, tamper[i] ?? text);
    return {pageId: page.pageId, revisionId: page.revisionId, locale: page.locale, slug: page.slug, title: page.title, contentType: page.contentType, rendererContract: page.rendererContract, url: extra.pageUrl ?? url, checksum: checksums[i] ?? hash(text)};
  });
  const release = {schemaVersion: 1, version, checksum: 'c' + version, publishedAt: '2026-10-11', pages: index, resources, assets};
  const releaseText = JSON.stringify(release);
  const releaseUrl = `${origin}/cms-public/releases/${version}.json`;
  files.set(releaseUrl, releaseText);
  files.set(`${origin}/cms-public/current.json`, JSON.stringify({schemaVersion: 1, version, eventId: 'e' + version, checksum: 'c' + version, releaseUrl, releaseChecksum: hash(releaseText)}));
  const seen = [];
  const fetcher = async (url, init) => {
    seen.push({url, init});
    assert.equal(init.redirect, 'error', 'redirects are never followed');
    if (hang) return new Promise(() => {});
    if (url.endsWith('current.json')) {
      if (pointerStatus !== 200) return new Response('down', {status: pointerStatus});
      if (init.headers?.['If-None-Match'] === etag) return new Response(null, {status: 304});
    }
    const body = files.get(url);
    if (body === undefined) return new Response('missing', {status: 404});
    return new Response(body, {status: 200, headers: {etag}});
  };
  return {fetcher, seen, files};
}

const home = () => clone(D.defaultPages.home);
const silent = () => {};
const make = (store, over = {}, clock) => new L.SiteLoader(config(over), store.fetcher, clock ?? (() => 0), silent);

test('content disabled: built-in page, no requests', async () => {
  const store = storage({pages: [envelope(home())]});
  const result = await make(store, {enabled: false}).load('/');
  assert.equal(result.source, 'default');
  assert.deepEqual(result.notices, ['content_disabled']);
  assert.equal(store.seen.length, 0);
});

test('a valid published v2 page renders with its metadata', async () => {
  const doc = home();
  doc.sections.hero.headline = 'Published headline.';
  const result = await make(storage({pages: [envelope(doc)]})).load('/');
  assert.equal(result.source, 'published');
  assert.equal(result.page.sections.hero.headline, 'Published headline.');
  assert.equal(result.seo.title, 'Published title');
  assert.ok(result.notices.includes('site_not_published'));
});

test('published site document and Console navigation replace the built-in ones', async () => {
  const site = clone(D.defaultSite);
  site.brand.name = 'Audoryn Cloud';
  const productPage = {...envelope(home()), pageId: PRODUCT_ID, slug: '/product', title: 'Product'};
  productPage.blocks[0].content.payload = clone(D.defaultPages.product);
  const header = {key: 'header', locale: 'en', items: [
    {id: 'AAAA0000-0000-0000-0000-000000000001', parentId: null, position: 0, label: 'Product', destinationKind: 'page', destination: PRODUCT_ID, visible: true, metadata: {description: 'Intro.'}},
    {id: 'AAAA0000-0000-0000-0000-000000000002', parentId: 'AAAA0000-0000-0000-0000-000000000001', position: 0, label: 'Workforce', destinationKind: 'page', destination: PRODUCT_ID, visible: true, metadata: {sectionId: 'workforce'}},
    {id: 'AAAA0000-0000-0000-0000-000000000003', parentId: null, position: 1, label: 'Hidden', destinationKind: 'url', destination: '/x', visible: false},
  ]};
  const result = await make(storage({pages: [envelope(home()), productPage], resources: [
    {resourceType: 'global_content', resourceId: 'g', data: {key: 'public-site', locale: 'en', value: site}},
    {resourceType: 'navigation', resourceId: 'n', data: header},
  ]})).load('/');
  assert.equal(result.site.brand.name, 'Audoryn Cloud');
  assert.equal(result.site.navigation.header.length, 1);
  assert.equal(result.site.navigation.header[0].href, '/product');
  assert.equal(result.site.navigation.header[0].intro, 'Intro.');
  assert.equal(result.site.navigation.header[0].children[0].href, '/product#workforce');
  assert.ok(!result.notices.some(notice => notice.startsWith('site')));
});

test('a v1 page falls back to the built-in v2 page', async () => {
  const result = await make(storage({pages: [envelope({contract: 'audoryn.public.v1', profile: 'home'}, {rendererContract: 'audoryn.public.v1'})]})).load('/');
  assert.equal(result.source, 'default');
  assert.deepEqual(result.page, D.defaultPages.home);
  assert.ok(result.notices.includes('page_not_v2'));
});

test('an invalid v2 page falls back, and the notice names the field but not the content', async () => {
  const doc = home();
  doc.sections.hero.headline = 'SECRET\nheadline';
  const result = await make(storage({pages: [envelope(doc)]})).load('/');
  assert.equal(result.source, 'default');
  const notice = result.notices.find(item => item.startsWith('page_invalid'));
  assert.match(notice, /sections\.hero\.headline:invalid_format/);
  assert.ok(!JSON.stringify(result.notices).includes('SECRET'));
});

test('the last valid published page is kept when a newer publish is invalid', async () => {
  let now = 0;
  const good = home();
  good.sections.hero.headline = 'Good version.';
  const first = storage({version: 1, pages: [envelope(good)]});
  const bad = home();
  bad.sections.hero.headline = '';
  const second = storage({version: 2, pages: [envelope(bad, {revisionId: 'rev-2'})]});
  let active = first;
  const loader = new L.SiteLoader(config(), (url, init) => active.fetcher(url, init), () => now, silent);
  assert.equal((await loader.load('/')).page.sections.hero.headline, 'Good version.');
  active = second; now = 120_000;
  const result = await loader.load('/');
  assert.equal(result.page.sections.hero.headline, 'Good version.');
  assert.ok(result.notices.some(item => item.startsWith('kept_last_valid')));
});

test('a checksum mismatch is refused', async () => {
  const result = await make(storage({pages: [envelope(home())], tamper: {0: JSON.stringify(envelope({...home(), page: 'home'}, {title: 'Changed'}))}})).load('/');
  assert.equal(result.source, 'default');
  assert.deepEqual(result.notices, ['checksum_mismatch']);
});

test('documents outside the storage origin and prefix are refused', async () => {
  for (const pageUrl of ['https://evil.example/cms-public/p.json', `${ORIGIN}/other/p.json`, `${ORIGIN}/cms-public/%2e%2e/p.json`, `${ORIGIN}/cms-public/p.json?x=1`]) {
    const result = await make(storage({pages: [envelope(home())], extra: {pageUrl}})).load('/');
    assert.deepEqual(result.notices, ['untrusted_artifact_url'], pageUrl);
  }
});

test('documents over 4 MiB are refused', async () => {
  const big = 'x'.repeat(4 * 1024 * 1024 + 10);
  const result = await make(storage({pages: [envelope(home())], tamper: {0: big}})).load('/');
  assert.deepEqual(result.notices, ['document_size_limit']);
});

test('unverified media in a release is refused', async () => {
  const asset = {id: 'a1', url: `${ORIGIN}/cms-public/a1.webp`, mediaType: 'image/webp', checksumSha256: 'a'.repeat(64), sizeBytes: 10, verification: {formatVerified: false, checksumSha256: 'a'.repeat(64)}};
  const result = await make(storage({pages: [envelope(home())], assets: [asset]})).load('/');
  assert.deepEqual(result.notices, ['unverified_media']);
});

test('a page referring to media the release does not contain falls back', async () => {
  const result = await make(storage({pages: [envelope(home())], assets: []})).load('/');
  // The page uses three images; this release contains none of them, so the published copy is refused.
  assert.equal(result.source, 'default');
  assert.ok(result.notices.includes('page_missing_media'));
});

test('published media is resolved from verified release assets', async () => {
  const result = await make(storage({pages: [envelope(home())]})).load('/');
  assert.equal(result.source, 'published');
  assert.equal(result.assets[SEED_IDS[0]].url, `${ORIGIN}/cms-public/${SEED_IDS[0]}.webp`);
  assert.equal(result.assets[SEED_IDS[0]].width, 1600);
});

test('an active redirect resource redirects', async () => {
  const result = await make(storage({pages: [], resources: [{resourceType: 'redirect', resourceId: 'r', data: {locale: 'en', fromPath: '/', toPath: '/product', statusCode: 301, active: true}}]})).load('/');
  assert.deepEqual(result.redirect, {destination: '/product', status: 301});
});

test('the pointer is revalidated with ETag and the release is not downloaded again', async () => {
  let now = 0;
  const store = storage({pages: [envelope(home())]});
  const loader = new L.SiteLoader(config(), store.fetcher, () => now, silent);
  await loader.load('/');
  const releaseFetches = () => store.seen.filter(item => item.url.includes('/releases/')).length;
  now = 120_000;
  await loader.load('/');
  assert.equal(releaseFetches(), 1);
  assert.ok(store.seen.some(item => item.init.headers?.['If-None-Match']));
});

test('an older release than the one held is refused and the newer one kept', async () => {
  let now = 0;
  const newer = home();
  newer.sections.hero.headline = 'Version two.';
  const two = storage({version: 2, pages: [envelope(newer)]});
  const one = storage({version: 1, pages: [envelope(home())]});
  let active = two;
  const loader = new L.SiteLoader(config(), (url, init) => active.fetcher(url, init), () => now, silent);
  assert.equal((await loader.load('/')).page.sections.hero.headline, 'Version two.');
  active = one; now = 120_000;
  assert.equal((await loader.load('/')).page.sections.hero.headline, 'Version two.');
});

test('when storage becomes unreachable, the last verified release keeps serving', async () => {
  let now = 0;
  const doc = home();
  doc.sections.hero.headline = 'Still here.';
  const up = storage({pages: [envelope(doc)]});
  const down = storage({pointerStatus: 503});
  let active = up;
  const loader = new L.SiteLoader(config(), (url, init) => active.fetcher(url, init), () => now, silent);
  await loader.load('/');
  active = down; now = 120_000;
  assert.equal((await loader.load('/')).page.sections.hero.headline, 'Still here.');
});

test('a request that never answers falls back within the deadline', async () => {
  const started = Date.now();
  const result = await make(storage({hang: true}), {timeoutSeconds: 1}).load('/');
  assert.equal(result.source, 'default');
  assert.ok(Date.now() - started < 3000);
});

test('documents are cached and requests deduplicated', async () => {
  const store = storage({pages: [envelope(home())]});
  const loader = make(store);
  await Promise.all([loader.load('/'), loader.load('/'), loader.load('/')]);
  await loader.load('/');
  assert.equal(store.seen.filter(item => item.url.includes('/pages/')).length, 1);
});

test('unknown and reserved paths are 404, never content', async () => {
  const loader = make(storage({pages: [envelope(home())]}));
  for (const path of ['/nope', '/app', '/_public/x', '//evil', '/a?b']) {
    const result = await loader.load(path);
    assert.equal(result.status, 404, path);
    assert.equal(result.page, null, path);
  }
});

test('page metadata is escaped, canonical stays on the site origin, 404 is not indexed', async () => {
  const doc = home();
  const store = storage({pages: [envelope(doc, {seo: {metaTitle: 'A </title><script>x()</script>', metaDescription: '"quoted"', canonicalUrl: 'https://evil.example/'}})]});
  const result = await make(store).load('/');
  const head = S.headFor({...result, source: 'published', seo: {...result.seo, title: 'A </title><script>x()</script>', canonicalUrl: 'https://evil.example/'}}, config());
  assert.ok(!head.includes('<script>x()'));
  assert.match(head, /<link rel="canonical" href="https:\/\/audoryn\.example\/"/);
  const missing = await make(storage()).load('/nope');
  assert.match(S.headFor(missing, config()), /noindex,nofollow/);
  assert.equal(S.scriptJson({a: '</script><script>'}).includes('</script>'), false);
});

test('the server: frame policy, application shell, endpoints, robots and sitemap', async () => {
  const product = {...envelope(clone(D.defaultPages.product), {seo: {metaTitle: 'Product', index: false}}), pageId: PRODUCT_ID, revisionId: 'rev-2', slug: '/product', title: 'Product'};
  const store = storage({pages: [envelope(home()), product]});
  const env = {PUBLIC_CONTENT_ENABLED: 'true', PUBLIC_CONTENT_ORIGIN: ORIGIN, PUBLIC_SITE_ORIGIN: 'https://audoryn.example', PUBLIC_CONTENT_PREVIEW_ORIGIN: 'https://console.example'};
  const template = '<!doctype html><html lang="en"><head><title>App</title></head><body><div id="root"></div></body></html>';
  const realFetch = globalThis.fetch;
  globalThis.fetch = store.fetcher;
  try {
    const get = url => S.publicResponse({url, env, template});
    const page = await get('/');
    assert.equal(page.status, 200);
    assert.equal(page.headers['Content-Security-Policy'], "frame-ancestors 'none'");
    assert.equal(page.headers['X-Frame-Options'], 'DENY');
    assert.match(page.body, /Published title/);
    assert.match(page.body, /id="public-content-bootstrap"/);

    const preview = await get('/_cms/preview');
    assert.equal(preview.headers['Content-Security-Policy'], 'frame-ancestors https://console.example');
    assert.equal(preview.headers['X-Frame-Options'], undefined);
    assert.match(preview.body, /noindex,nofollow/);

    const application = await get('/app/workers');
    assert.equal(application.body, template);
    assert.equal(application.headers['Content-Security-Policy'], "frame-ancestors 'none'");

    const navigation = JSON.parse((await get('/_public/site?path=/nope')).body);
    assert.equal(navigation.bundle.status, 404);
    for (const removed of ['/_public/content', '/_public/config']) assert.equal((await get(removed)).status, 404, removed);

    assert.equal(JSON.parse((await get('/_public/preview-reference?version=1')).body).release.version, 1);
    assert.equal((await get('/_public/preview-reference?version=2')).status, 409);

    assert.match((await get('/robots.txt')).body, /Sitemap: https:\/\/audoryn\.example\/sitemap\.xml/);
    const sitemap = (await get('/sitemap.xml')).body;
    assert.match(sitemap, /<loc>https:\/\/audoryn\.example\/<\/loc>/);
    assert.ok(!sitemap.includes('/product<'), 'a page published with index: false is left out');
    assert.ok(!sitemap.includes('/pricing<'), 'a fixed page missing from the release is removed, so left out');
  } finally {
    globalThis.fetch = realFetch;
  }
});

test('without a Console origin, the preview cannot be framed at all', () => {
  assert.equal(S.framePolicy('/_cms/preview', '')['Content-Security-Policy'], "frame-ancestors 'none'");
});

test('configuration has no built-in origin and refuses credentialed, insecure or path origins', () => {
  const plain = C.siteConfigFromEnvironment({});
  assert.equal(plain.enabled, false);
  assert.equal(plain.origin, '');
  assert.equal(C.siteConfigFromEnvironment({PUBLIC_CONTENT_ENABLED: 'true'}).enabled, false);
  for (const env of [
    {PUBLIC_CONTENT_ORIGIN: 'https://secret@storage.example'},
    {PUBLIC_CONTENT_ORIGIN: 'http://storage.example'},
    {PUBLIC_CONTENT_ORIGIN: 'https://storage.example/a?b=1'},
    {PUBLIC_SITE_ORIGIN: 'https://audoryn.example/path'},
    {PUBLIC_CONTENT_PREVIEW_ORIGIN: 'https://console.example/#x'},
    {PUBLIC_CONTENT_FETCH_TIMEOUT_SECONDS: '0'},
  ]) assert.throws(() => C.siteConfigFromEnvironment(env), /invalid/, JSON.stringify(env));
  assert.equal(C.siteConfigFromEnvironment({PUBLIC_CONTENT_ORIGIN: 'http://localhost:9000/bucket'}).origin, 'http://localhost:9000/bucket');
});

test('a storage origin with a bucket path loads its content and refuses a sibling bucket', async () => {
  const bucket = ORIGIN + '/public-bucket';
  const assets = SEED_ASSETS.map(asset => ({...asset, url: asset.url.replace(ORIGIN, bucket)}));
  const result = await make(storage({origin: bucket, pages: [envelope(home())], assets}), {origin: bucket}).load('/');
  assert.equal(result.source, 'published');
  const sibling = await make(storage({origin: bucket, pages: [envelope(home())], assets, extra: {pageUrl: ORIGIN + '/other-bucket/cms-public/p.json'}}), {origin: bucket}).load('/');
  assert.deepEqual(sibling.notices, ['untrusted_artifact_url']);
});

test('releases with duplicate pages or assets, unsafe paths or mismatched identities are refused', async () => {
  const twice = await make(storage({pages: [envelope(home()), envelope(home(), {revisionId: 'rev-9'})]})).load('/');
  assert.deepEqual(twice.notices, ['duplicate_release_page']);
  const assets = await make(storage({pages: [envelope(home())], assets: [SEED_ASSETS[0], SEED_ASSETS[0]]})).load('/');
  assert.deepEqual(assets.notices, ['duplicate_asset']);
  const unsafe = await make(storage({pages: [envelope(home(), {slug: '/app'})]})).load('/');
  assert.deepEqual(unsafe.notices, ['invalid_release_path']);
  const lying = JSON.stringify(envelope(home(), {pageId: PRODUCT_ID}));
  const mismatch = await make(storage({pages: [envelope(home())], tamper: {0: lying}, checksums: {0: hash(lying)}})).load('/');
  assert.deepEqual(mismatch.notices, ['page_identity_mismatch']);
});

test('an aborted navigation never accepts a response', async () => {
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(make(storage({pages: [envelope(home())]})).load('/', controller.signal), /Abort/);
});

test('paths, old hash links and in-page anchors pick the expected page', () => {
  for (const [pathname, hash, expected] of [['/product', '', '/product'], ['/', '#product', '/product'], ['/', '#/solutions', '/solutions'], ['/', '#/login', '/login'], ['/privacy', '#legal-information', '/privacy'], ['/pricing/', '', '/pricing']]) {
    assert.equal(R.routePath({pathname, hash, search: ''}), expected, pathname + hash);
  }
});

test('the Console preview accepts v2 content and refuses unsafe or incomplete payloads', () => {
  const data = {previewMode: 'content_inspection', pinnedReleaseVersion: 2, revision: {pageId: HOME_ID, slug: '/', locale: 'en', title: 'Home', blocks: [{type: 'PublicContent', content: {payload: home()}}]},
    assets: {'de5cb793-7e09-5703-8021-c31ecd15ab0c': 'https://signed.example/a.webp'}, navigation: [], globalContent: []};
  const result = P.previewSiteBundle(data);
  assert.equal(result.source, 'preview');
  assert.equal(result.seo.index, false);
  assert.equal(result.assets['de5cb793-7e09-5703-8021-c31ecd15ab0c'].url, 'https://signed.example/a.webp');
  assert.throws(() => P.previewSiteBundle({...data, assets: {x: 'http://insecure.example/a.webp'}}), /invalid_preview_media_url/);
  assert.throws(() => P.previewSiteBundle({...data, unresolvedAssets: ['x']}), /unresolved_preview_dependencies/);
  const broken = clone(data);
  broken.revision.blocks[0].content.payload.sections.hero.headline = '';
  assert.throws(() => P.previewSiteBundle(broken), /invalid_preview_content/);
});

test('Console navigation from the previous site is ignored until a v2 site document is published', async () => {
  const header = {key: 'header', locale: 'en', items: [{id: 'AAAA0000-0000-0000-0000-000000000009', parentId: null, position: 0, label: 'Old menu', destinationKind: 'url', destination: '/product#product-workforce', visible: true}]};
  const result = await make(storage({pages: [envelope(home())], resources: [{resourceType: 'navigation', resourceId: 'n', data: header}]})).load('/');
  assert.deepEqual(result.site.navigation, D.defaultSite.navigation);
  assert.ok(result.notices.includes('site_not_published'));
});

test('a fixed page removed from a published release is 404 and never resurrected from built-in content', async () => {
  const product = {...envelope(clone(D.defaultPages.product)), pageId: PRODUCT_ID, slug: '/product', title: 'Product'};
  const result = await make(storage({pages: [product]})).load('/');
  assert.equal(result.status, 404);
  assert.equal(result.page, null);
  assert.ok(result.notices.includes('removed'));
});

test('with nothing published yet, fixed pages use their built-in content', async () => {
  const result = await make(storage({pages: []})).load('/');
  assert.equal(result.status, 200);
  assert.equal(result.source, 'default');
});
