import test, {before} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {bundle} from './helpers/bundle.mjs';
import {buildSiteSeed, digest, verifySiteSeed} from '../scripts/site-seed.mjs';
import {SiteSeedImporter} from '../scripts/site-seed-importer.mjs';

const root = path.resolve(import.meta.dirname, '..');
const onDisk = JSON.parse(readFileSync(path.join(root, 'content-seed/audoryn-public.v2.seed.json'), 'utf8'));
let seed, m;
before(async () => {
  seed = await buildSiteSeed();
  m = await bundle({bundle: 'src/site/content/bundle.ts', defaults: 'src/site/content/defaults/index.ts', validate: 'src/site/contract/validate.ts'});
});

// ---- the seed ---------------------------------------------------------------------------------------

test('the committed seed is current and verifies (run npm run site:seed after changing defaults)', async () => {
  assert.deepEqual(onDisk, seed);
  await verifySiteSeed(onDisk);
});

test('the seed is drafts of exactly the built-in documents', () => {
  assert.equal(seed.status, 'draft');
  assert.equal(seed.publicationAllowed, false);
  assert.deepEqual(seed.pages.map(page => page.key), Object.keys(m.defaults.defaultPages));
  for (const page of seed.pages) assert.deepEqual(page.document, m.defaults.defaultPages[page.key]);
  assert.deepEqual(seed.globals[0].value, m.defaults.defaultSite);
});

test('verification rejects an edited seed, unsafe flags and changed media', async () => {
  const edited = structuredClone(onDisk);
  edited.pages[0].document.sections.hero.headline = 'Edited';
  await assert.rejects(verifySiteSeed(edited), /seed_checksum_mismatch/);
  const unsafe = structuredClone(onDisk);
  unsafe.publicationAllowed = true;
  await assert.rejects(verifySiteSeed(unsafe), /seed_unsafe/);
  const media = structuredClone(onDisk);
  media.media[0].sizeBytes += 1;
  const {checksum: _old, ...body} = media;
  await assert.rejects(verifySiteSeed({...body, checksum: digest(body)}), /seed_media_changed/);
});

test('published as a release, the seed renders the same site as the built-in content', () => {
  // Console identities: pages and assets get new IDs; navigation keeps its seed item IDs.
  const pageIds = Object.fromEntries(seed.pages.map(page => [page.key, 'console-page-' + page.key]));
  const assetIds = Object.fromEntries(seed.media.map((media, index) => [media.id, `00000000-0000-4000-8000-00000000000${index}`]));
  const resolve = value => JSON.parse(JSON.stringify(value).replace(/"seed:page\/([a-z]+)"/g, (_, key) => JSON.stringify(pageIds[key])).replace(/"([0-9a-f-]{36})"/g, (match, id) => (assetIds[id] ? JSON.stringify(assetIds[id]) : match)));
  const assets = Object.fromEntries(Object.values(assetIds).map(id => [id, {url: `https://storage.example/cms-public/${id}.webp`}]));
  for (const page of seed.pages) {
    const result = m.bundle.pageFromEnvelope({pageId: pageIds[page.key], revisionId: 'r', locale: 'en', slug: page.slug, title: page.title, contentType: 'page', rendererContract: 'audoryn.public.v2', blocks: [{type: 'PublicContent', content: {payload: resolve(page.document)}}]}, page.key, assets);
    assert.ok('page' in result, `${page.key}: ${result.notice}`);
  }
  const resources = [
    {resourceType: 'global_content', resourceId: 'g', data: {key: 'public-site', locale: 'en', value: seed.globals[0].value}},
    ...seed.navigations.map(navigation => ({resourceType: 'navigation', resourceId: navigation.key, data: {key: navigation.key, locale: 'en', items: resolve(navigation.items)}})),
  ];
  const slugs = new Map(seed.pages.map(page => [pageIds[page.key], page.slug]));
  const {site, notices} = m.bundle.siteFromResources(resources, 'en', slugs);
  assert.deepEqual(notices, []);
  const withoutIds = menus => JSON.parse(JSON.stringify(menus, (key, value) => (key === 'id' ? undefined : value)));
  assert.deepEqual(withoutIds(site.navigation), withoutIds(m.defaults.defaultSite.navigation));
});

test('every navigation anchor exists on its page', () => {
  for (const navigation of seed.navigations) for (const item of navigation.items) {
    if (!item.metadata.sectionId) continue;
    const page = seed.pages.find(entry => 'seed:page/' + entry.key === item.destination);
    assert.ok(m.validate.anchorsOf(page.document).includes(item.metadata.sectionId), item.label);
  }
});

// ---- a fake Console ---------------------------------------------------------------------------------

function fakeConsole() {
  let next = 0;
  const id = () => `00000000-0000-4000-8000-${String(++next).padStart(12, '0')}`;
  const state = {pages: [], assets: [], navigations: [], globals: [], calls: [], uploads: 0, failures: []};
  const list = items => ({items, total: items.length});
  const handle = (method, endpoint, body) => {
    const [pathname, query = ''] = endpoint.split('?');
    const parts = pathname.split('/').filter(Boolean);
    if (method !== 'GET' && /publish|approve|schedule|submit|ready|releases/.test(pathname)) throw new Error('forbidden_endpoint');
    if (method === 'GET' && pathname === '/assets') return list(state.assets);
    if (method === 'POST' && pathname === '/assets/upload-intents') {
      const asset = {id: id(), filename: body.filename, checksumSha256: body.checksum_sha256, status: 'uploading'};
      state.assets.push(asset);
      return {asset, upload: {url: 'https://storage.invalid/signed-secret', contentType: body.media_type}};
    }
    if (method === 'POST' && parts[0] === 'assets' && parts[2] === 'complete') {state.assets.find(asset => asset.id === parts[1]).status = 'ready'; return {};}
    if (method === 'GET' && pathname === '/pages') return list(state.pages.map(({revisions: _r, ...page}) => page));
    if (method === 'POST' && pathname === '/pages') {
      if (state.pages.some(page => page.canonicalSlug === body.slug)) throw Object.assign(new Error('console_request_rejected_409'), {diagnostic: {httpStatus: 409}});
      const page = {id: id(), canonicalSlug: body.slug, canonicalTitle: body.title, defaultLocale: body.locale, contentType: body.content_type, archivedAt: null, revisions: []};
      const revision = {id: id(), revision: 1, locale: body.locale, status: 'draft', title: body.title, slug: body.slug, blocks: [], seo: {}};
      page.revisions.push(revision);
      state.pages.push(page);
      return {page, revision};
    }
    if (parts[0] === 'pages') {
      const page = state.pages.find(item => item.id === parts[1]);
      if (method === 'GET') return {page, locale: new URLSearchParams(query).get('locale'), revisions: page.revisions, publication: null};
      // Console stores blocks with its own block IDs and SEO defaults added.
      // Like Console, refuse a block without a UUID.
      if (body.blocks.some(block => !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(block.id ?? ''))) throw Object.assign(new Error('console_request_rejected_400'), {diagnostic: {httpStatus: 400}});
      const revision = {id: id(), revision: page.revisions.length + 1, locale: body.locale, status: 'draft', title: body.title, slug: body.slug, blocks: structuredClone(body.blocks), seo: {canonicalUrl: null, ...body.seo}, basedOnRevisionId: body.based_on_revision_id};
      page.revisions.push(revision);
      return {revision};
    }
    if (method === 'GET' && pathname === '/global-content') return {items: state.globals};
    if (method === 'PUT' && parts[0] === 'global-content') {
      let item = state.globals.find(entry => entry.key === parts[1] && entry.locale === body.locale);
      if (!item) state.globals.push(item = {id: id(), key: parts[1], locale: body.locale});
      item.value = structuredClone(body.value);
      return {item};
    }
    if (method === 'GET' && pathname === '/navigations') return {items: state.navigations};
    if (method === 'POST' && pathname === '/navigations') {
      const navigation = {id: id(), key: body.key, name: body.name, locale: body.locale, items: []};
      state.navigations.push(navigation);
      return {navigation};
    }
    if (method === 'PUT' && parts[0] === 'navigations') {
      const navigation = state.navigations.find(item => item.id === parts[1]);
      // Console keeps openInNewTab in metadata too, and lists items in its own order.
      navigation.items = body.items.map(item => ({...item, metadata: {...item.metadata, openInNewTab: item.openInNewTab}})).reverse();
      return {navigation};
    }
    throw new Error(`unexpected_endpoint:${method}:${endpoint}`);
  };
  const request = async (method, endpoint, body) => {
    state.calls.push([method, endpoint]);
    const failure = state.failures.findIndex(rule => rule.when(method, endpoint));
    if (failure >= 0 && state.failures[failure].before) {
      const rule = state.failures.splice(failure, 1)[0];
      throw rule.error ?? new Error('transport_failure');
    }
    const value = structuredClone(handle(method, endpoint, body));
    if (failure >= 0) {state.failures.splice(failure, 1); throw new Error('lost_acknowledgement');}
    return value;
  };
  return {state, request};
}

function importer(consoleApi, options = {}) {
  const receipt = options.receipt ?? {};
  const saved = [];
  const run = new SiteSeedImporter({
    seed: options.seed ?? seed, request: consoleApi.request, mode: options.mode ?? 'apply', receipt, replaceNavigation: options.replaceNavigation,
    readMedia: async () => Buffer.from('bytes'), upload: async () => {consoleApi.state.uploads++;},
    checkpoint: async value => {saved.push(JSON.stringify(value));}, sleep: async () => {},
  });
  return {receipt, saved, run: () => run.run()};
}
const writes = state => state.calls.filter(([method]) => method !== 'GET');
const reseed = mutate => {
  const {checksum: _old, ...body} = structuredClone(seed);
  mutate(body);
  return {...body, checksum: digest(body)};
};

test('an empty Console receives every draft once, and a second run writes nothing', async () => {
  const api = fakeConsole();
  const first = importer(api);
  const result = await first.run();
  assert.equal(result.status, 'drafts_prepared');
  assert.equal(result.publicationPerformed, false);
  assert.equal(api.state.pages.length, 9);
  assert.equal(api.state.uploads, 3);
  assert.ok(api.state.assets.every(asset => asset.status === 'ready'));
  assert.equal(api.state.globals.length, 1);
  assert.equal(api.state.navigations.length, 2);
  assert.equal(result.releaseItems.length, 12);
  // Pages use Console asset IDs; navigation points at Console pages.
  const home = api.state.pages.find(page => page.canonicalSlug === '/').revisions.at(-1);
  assert.ok(JSON.stringify(home.blocks).includes(api.state.assets[0].id));
  assert.ok(!JSON.stringify(api.state.navigations).includes('seed:page'));
  assert.ok(first.saved.every(value => !value.includes('signed-secret')), 'the receipt never holds upload URLs');

  const count = writes(api.state).length;
  const again = await importer(api, {receipt: first.receipt}).run();
  assert.equal(writes(api.state).length, count);
  assert.ok(again.actions.every(action => action.action === 'unchanged'));
});

test('plan mode reads only', async () => {
  const api = fakeConsole();
  const result = await importer(api, {mode: 'plan'}).run();
  assert.equal(result.status, 'planned');
  assert.equal(writes(api.state).length, 0);
  assert.equal(result.actions.filter(action => action.action === 'create').length, 12);
});

test('a changed seed updates only the changed page, on top of the importer\'s own draft', async () => {
  const api = fakeConsole();
  const first = importer(api);
  await first.run();
  const changed = reseed(body => {body.pages.find(page => page.key === 'pricing').document.sections.hero.headline = 'Plans for every team.';});
  const result = await importer(api, {seed: changed, receipt: first.receipt}).run();
  assert.deepEqual(result.actions.filter(action => action.action !== 'unchanged'), [{resource: 'page', key: 'pricing', action: 'update'}]);
  const pricing = api.state.pages.find(page => page.canonicalSlug === '/pricing');
  assert.equal(pricing.revisions.length, 3);
  assert.equal(pricing.revisions.at(-1).basedOnRevisionId, pricing.revisions[1].id);
});

test('a v1 Console migrates: pages get a v2 draft on top, navigation needs explicit replacement', async () => {
  const api = fakeConsole();
  for (const page of seed.pages) {
    api.state.pages.push({id: `v1-${page.key}`, canonicalSlug: page.slug, canonicalTitle: page.title, defaultLocale: 'en', contentType: 'page', archivedAt: null, revisions: [
      {id: `v1-${page.key}-r1`, revision: 1, locale: 'en', status: 'published', title: page.title, slug: page.slug, blocks: [{type: 'PublicContent', content: {payload: {contract: 'audoryn.public.v1'}}}], seo: {}},
    ]});
  }
  api.state.navigations.push({id: 'nav-header', key: 'header', name: 'Public header', locale: 'en', items: [{id: 'old', parentId: null, label: 'Product', destinationKind: 'page', destination: 'v1-product', position: 0, visible: true, metadata: {sectionId: 'product-workforce'}}]});

  await assert.rejects(importer(api).run(), error => error.conflicts.length === 1 && error.conflicts[0].code === 'navigation_edited_in_console');
  assert.equal(writes(api.state).length, 0, 'nothing is written when anything conflicts');

  const result = await importer(api, {replaceNavigation: true}).run();
  assert.equal(result.actions.filter(action => action.action === 'migrate').length, 9);
  assert.deepEqual(result.actions.find(action => action.key === 'header'), {resource: 'navigation', key: 'header', action: 'replace', replacedItems: 1});
  for (const page of api.state.pages) {
    assert.equal(page.revisions.length, 2, 'the v1 revision is kept');
    assert.equal(page.revisions[1].basedOnRevisionId, page.revisions[0].id);
  }
  const header = api.state.navigations.find(item => item.key === 'header');
  assert.ok(header.items.some(item => item.destination === 'v1-product' && item.metadata.sectionId === 'workforce'));
});

test('content changed in Console by someone else stops the import before any write', async () => {
  const cases = [
    ['page_edited_in_console', state => {
      const page = state.pages.find(item => item.canonicalSlug === '/company');
      const latest = page.revisions.at(-1);
      page.revisions.push({...structuredClone(latest), id: 'editor-draft', revision: latest.revision + 1, title: 'Company (edited)'});
    }],
    ['global_edited_in_console', state => {state.globals[0].value = {...state.globals[0].value, brand: {...state.globals[0].value.brand, tagline: 'Edited in Console'}};}],
    ['navigation_edited_in_console', state => {state.navigations[1].items[0].label = 'Edited';}],
    ['page_has_scheduled_revision', state => {state.pages[0].revisions.at(-1).status = 'scheduled';}],
  ];
  for (const [code, edit] of cases) {
    const api = fakeConsole();
    const first = importer(api);
    await first.run();
    edit(api.state);
    const changed = reseed(body => {body.pages[0].document.sections.hero.headline = 'A new headline.'; body.globals[0].value.brand.tagline = 'A new tagline.'; body.navigations[1].items[0].label = 'New';});
    const count = writes(api.state).length;
    await assert.rejects(importer(api, {seed: changed, receipt: first.receipt}).run(), error => error.conflicts?.some(item => item.code === code), code);
    assert.equal(writes(api.state).length, count, `${code}: no writes`);
  }
});

test('an interrupted import resumes without duplicates', async () => {
  const points = [
    ['upload intent', (method, endpoint) => method === 'POST' && endpoint === '/assets/upload-intents'],
    ['page creation', (method, endpoint) => method === 'POST' && endpoint === '/pages'],
    ['revision', (method, endpoint) => method === 'POST' && endpoint.endsWith('/revisions')],
    ['global', (method, endpoint) => method === 'PUT' && endpoint.startsWith('/global-content/')],
    ['navigation creation', (method, endpoint) => method === 'POST' && endpoint === '/navigations'],
    ['navigation items', (method, endpoint) => method === 'PUT' && endpoint.endsWith('/items')],
  ];
  for (const [name, when] of points) {
    const api = fakeConsole();
    api.state.failures.push({when});
    const receipt = {};
    const first = importer(api, {receipt});
    await assert.rejects(first.run(), /lost_acknowledgement/, name);
    await importer(api, {receipt}).run();
    assert.equal(api.state.pages.length, 9, name);
    assert.equal(api.state.globals.length, 1, name);
    assert.equal(api.state.navigations.length, 2, name);
    assert.ok(api.state.pages.every(page => page.revisions.filter(revision => revision.blocks.length).length === 1), `${name}: one v2 revision per page`);
    const count = writes(api.state).length;
    await importer(api, {receipt}).run();
    assert.equal(writes(api.state).length, count, `${name}: settled`);
  }
});

test('reads are retried on 502-504 and transport errors; writes and other errors are not', async () => {
  const api = fakeConsole();
  const unavailable = () => Object.assign(new Error('console_request_rejected_503'), {diagnostic: {httpStatus: 503}});
  api.state.failures.push({when: (method, endpoint) => method === 'GET' && endpoint.startsWith('/pages?'), before: true, error: unavailable()});
  api.state.failures.push({when: (method, endpoint) => method === 'GET' && endpoint.startsWith('/pages?'), before: true});
  await importer(api).run();
  assert.equal(api.state.pages.length, 9);

  const denied = fakeConsole();
  denied.state.failures.push({when: method => method === 'GET', before: true, error: Object.assign(new Error('console_request_rejected_403'), {diagnostic: {httpStatus: 403}})});
  await assert.rejects(importer(denied).run(), /rejected_403/);
  assert.equal(denied.state.calls.length, 1);

  const failingWrite = fakeConsole();
  failingWrite.state.failures.push({when: (method, endpoint) => method === 'POST' && endpoint === '/pages', before: true, error: unavailable()});
  await assert.rejects(importer(failingWrite).run(), /rejected_503/);
  assert.equal(failingWrite.state.calls.filter(([method, endpoint]) => method === 'POST' && endpoint === '/pages').length, 1);
});

test('the importer never calls approval, scheduling, publishing or release endpoints', async () => {
  const api = fakeConsole();
  await importer(api, {replaceNavigation: true}).run();
  assert.ok(api.state.calls.every(([, endpoint]) => !/publish|approve|schedule|submit|ready|releases/.test(endpoint)));
  assert.throws(() => new SiteSeedImporter({seed: {...seed, publicationAllowed: true}, request: api.request, receipt: {}, checkpoint: async () => {}}), /seed_unsafe/);
});
