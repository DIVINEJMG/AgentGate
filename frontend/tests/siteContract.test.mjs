import test, {before} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync, readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import {bundle} from './helpers/bundle.mjs';

const root = path.resolve(import.meta.dirname, '..');
const read = file => JSON.parse(readFileSync(path.join(root, file), 'utf8'));
const schema = read('src/site/contract/contract.v2.json');
const invalid = read('src/site/contract/examples/invalid.json');
const media = read('content-seed/audoryn-public.v2.seed.json').media;
let v, d;
before(async () => ({validate: v, defaults: d} = await bundle({validate: 'src/site/contract/validate.ts', defaults: 'src/site/content/defaults/index.ts'})));

const clone = value => structuredClone(value);
const kebab = key => key.replace(/[A-Z]/g, c => '-' + c.toLowerCase());
const pathOf = page => page === 'home' ? '/' : '/' + page;
function set(document, segments, value) {
  let node = document;
  for (const segment of segments.slice(0, -1)) node = node[segment];
  const last = segments.at(-1);
  if (value === null) Array.isArray(node) ? node.splice(last, 1) : delete node[last];
  else node[last] = value;
}

test('generated contract files are current', () => {
  execFileSync(process.execPath, ['scripts/site-contract.mjs', '--check'], {cwd: root, stdio: 'pipe'});
});

test('every built-in document is valid', () => {
  const site = v.validateSite(d.defaultSite);
  assert.ok(site.ok, JSON.stringify(site.errors));
  for (const [page, document] of Object.entries(d.defaultPages)) {
    const result = v.validatePage(document);
    assert.ok(result.ok, `${page}: ${JSON.stringify(result.errors)}`);
    assert.equal(document.page, page);
  }
});

test('there is a built-in document for every page template except articles, with every section', () => {
  const templates = Object.keys(schema['x-pages']).filter(page => page !== 'article');
  assert.deepEqual(Object.keys(d.defaultPages).sort(), templates.sort());
  for (const page of templates) assert.deepEqual(Object.keys(d.defaultPages[page].sections).sort(), [...schema['x-pages'][page]].sort(), page);
});

test('the article example is valid', () => {
  const result = v.validatePage(read('src/site/contract/examples/article.valid.json'));
  assert.ok(result.ok, JSON.stringify(result.errors));
});

test('the navigation-limits site example is valid and at the contract maximums', () => {
  const site = read('src/site/contract/examples/site.limits.json');
  const result = v.validateSite(site);
  assert.ok(result.ok, JSON.stringify(result.errors));
  const header = schema.$defs.Site.properties.navigation.properties.header;
  assert.equal(site.navigation.header.length, header.maxItems);
  assert.ok(site.navigation.header.every(menu => menu.children.length === header.items.properties.children.maxItems));
  assert.equal(site.navigation.footer.length, schema.$defs.Site.properties.navigation.properties.footer.maxItems);
});

for (const example of invalid.cases) {
  test(`rejects: ${example.name}`, () => {
    const document = clone(example.page === 'site' ? d.defaultSite : d.defaultPages[example.page]);
    const [segments, value] = example.set;
    set(document, segments, example.repeat ? value.repeat(example.repeat) : value);
    const result = v.validateDocument(document);
    assert.equal(result.ok, false, 'document should be rejected');
    assert.ok(result.errors.some(error => error.path === example.expect.path && error.code === example.expect.code), JSON.stringify(result.errors));
  });
}

test('errors name the field and never include content', () => {
  const document = clone(d.defaultPages.home);
  document.sections.hero.headline = 'SECRET-VALUE\n';
  const result = v.validateDocument(document);
  assert.equal(result.ok, false);
  assert.ok(!JSON.stringify(result.errors).includes('SECRET-VALUE'));
});

test('site and page validators do not accept each other', () => {
  assert.equal(v.validatePage(d.defaultSite).ok, false);
  assert.equal(v.validateSite(d.defaultPages.home).ok, false);
  assert.equal(v.validateDocument(null).ok, false);
  assert.equal(v.validateDocument([]).ok, false);
});

test('safe link rules', () => {
  for (const href of ['/', '/pricing', '/security#control-path', 'https://sot-org.onrender.com/']) assert.ok(v.isSafeHref(href), href);
  for (const href of ['//evil.example', 'http://example.com', 'javascript:alert(1)', 'https://u:p@example.com', '/a\\b', '/%2f%2fevil', 'mailto:x@example.com']) assert.ok(!v.isSafeHref(href), href);
});

test('built-in images exist in the v2 seed, the bundle map and the site assets folder', () => {
  const ids = new Set(media.map(item => item.id));
  const used = [...JSON.stringify(d.defaultPages).matchAll(/"asset":"([0-9a-f-]{36})"/g)].map(match => match[1]);
  assert.ok(used.length >= 3);
  for (const id of used) {
    assert.ok(ids.has(id), id);
    assert.ok(d.defaultMedia[id], id);
    assert.equal(d.defaultMedia[id].file, media.find(item => item.id === id).filename);
    assert.ok(existsSync(path.join(root, 'src/site/assets', d.defaultMedia[id].file)), id);
  }
});

test('every internal link in built-in content points to a real page and section', () => {
  const app = new Set(['/login', '/signup', '/app']);
  const anchors = {};
  for (const [page, document] of Object.entries(d.defaultPages)) {
    const ids = new Set(Object.keys(document.sections).map(kebab));
    if (page === 'solutions') for (const item of document.sections.solutions.items) ids.add(item.id);
    anchors[pathOf(page)] = ids;
  }
  const hrefs = [...JSON.stringify([d.defaultSite, d.defaultPages]).matchAll(/"href":"([^"]+)"/g)].map(match => match[1]);
  assert.ok(hrefs.length > 50);
  for (const href of hrefs) {
    if (href.startsWith('https://')) continue;
    const [route, anchor] = href.split('#');
    if (app.has(route)) continue;
    assert.ok(anchors[route], `unknown page in ${href}`);
    if (anchor) assert.ok(anchors[route].has(anchor), `unknown section in ${href}`);
  }
});

test('built-in copy follows the honesty rules', () => {
  const text = JSON.stringify([d.defaultSite, d.defaultPages]);
  for (const page of ['home', 'security']) assert.match(d.defaultPages[page].sections.decision.demo.disclosure, /illustrative/i);
  assert.match(d.defaultPages.product.sections.workspace.disclosure, /illustrative/i);
  assert.match(d.defaultPages.product.sections.runtime.disclosure, /illustrative/i);
  assert.match(d.defaultPages.security.sections.record.disclosure, /illustrative/i);
  // Paid plans are never presented as purchasable.
  for (const plan of d.defaultPages.pricing.sections.plans.plans.filter(item => item.priceMinor > 0)) assert.notEqual(plan.availability, 'free-signup');
  // Integrations that exist only as a framework are not advertised.
  assert.ok(!/\bMCP\b|REST \//.test(text));
});
