// The audoryn.public.v2 Console seed, generated from the site's built-in documents.
//
// One source: the documents in src/site/content/defaults are both what the site shows when Console
// has published nothing and what this seed imports into Console, so the two cannot disagree.
// The seed holds drafts only. Console references that do not exist yet are placeholders:
//   - page destinations in navigation are "seed:page/<key>" (the importer resolves the Console page);
//   - images keep their seed asset IDs (the importer replaces them with the Console asset IDs).
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {bundle} from './bundle-ts.mjs';

export const SEED_CONTRACT = 'audoryn.public.v2';
const root = path.resolve(import.meta.dirname, '..');
const MEDIA_DIR = 'src/site/assets';
const sha256 = value => createHash('sha256').update(value).digest('hex');

/** Deterministic UUID (version 5 layout) for seed-owned Console identities, such as navigation items. */
export function seedUuid(key) {
  const hex = sha256('audoryn-public-v2:' + key).slice(0, 32).split('');
  hex[12] = '5';
  hex[16] = '8';
  const s = hex.join('');
  return `${s.slice(0, 8)}-${s.slice(8, 12)}-${s.slice(12, 16)}-${s.slice(16, 20)}-${s.slice(20)}`;
}

/** Canonical JSON digest: key order never changes the result. */
export function digest(value) {
  const canonical = node => Array.isArray(node) ? node.map(canonical)
    : node && typeof node === 'object' ? Object.fromEntries(Object.keys(node).sort().map(key => [key, canonical(node[key])]))
    : node;
  return sha256(JSON.stringify(canonical(value)));
}

let modules;
const load = () => modules ??= bundle({validate: 'src/site/contract/validate.ts', defaults: 'src/site/content/defaults/index.ts'});

const titleOf = key => key === 'home' ? 'Home' : key[0].toUpperCase() + key.slice(1);

/** Builds the seed and checks it completely. Throws an Error with a code on any problem. */
export async function buildSiteSeed() {
  const {validate, defaults} = await load();
  const fail = code => {throw new Error(code);};
  const site = defaults.defaultSite;
  if (!validate.validateSite(site).ok) fail('seed_site_invalid');

  const pages = Object.entries(defaults.defaultPages).map(([key, document]) => {
    const result = validate.validatePage(document);
    if (!result.ok) fail(`seed_page_invalid:${key}:${result.errors[0].path}:${result.errors[0].code}`);
    const seo = defaults.defaultSeo[key];
    return {key, slug: key === 'home' ? '/' : '/' + key, locale: 'en', title: titleOf(key), contentType: 'page', document, seo: {metaTitle: seo.title, metaDescription: seo.description}};
  });
  const bySlug = new Map(pages.map(page => [page.slug, page]));

  // Media: every image the documents use, with the bytes it must have in Console.
  const usage = new Map();
  for (const page of pages) for (const image of imagesIn(page.document)) {
    if (!defaults.defaultMedia[image.asset]) fail('seed_image_unknown');
    if (!usage.has(image.asset)) usage.set(image.asset, image.alt);
  }
  const media = [];
  for (const [id, alt] of usage) {
    const entry = defaults.defaultMedia[id];
    const file = `${MEDIA_DIR}/${entry.file}`;
    const bytes = await readFile(path.join(root, file));
    media.push({id, path: file, filename: entry.file, mediaType: 'image/webp', width: entry.width, height: entry.height, sizeBytes: bytes.length, checksumSha256: sha256(bytes), altText: alt});
  }

  // Navigation: Console's flat items, built from the site document's own menus.
  const destination = href => {
    if (!href.startsWith('/')) return {destinationKind: 'url', destination: href};
    const [location, sectionId] = href.split('#');
    const page = bySlug.get(location);
    if (!page) return {destinationKind: 'path', destination: location, ...(sectionId ? {sectionId} : {})};
    if (sectionId && !validate.anchorsOf(page.document).includes(sectionId)) fail(`seed_anchor_unknown:${href}`);
    return {destinationKind: 'page', destination: `seed:page/${page.key}`, ...(sectionId ? {sectionId} : {})};
  };
  const item = (key, link, parentId, position, description) => {
    const {sectionId, ...target} = destination(link.href);
    return {
      id: seedUuid(key), parentId, position, label: link.label, ...target, visible: true, openInNewTab: Boolean(link.newTab),
      metadata: {...(description ? {description} : {}), ...(sectionId ? {sectionId} : {})},
    };
  };
  const header = [];
  site.navigation.header.forEach((menu, position) => {
    const parent = item(`nav/header/${menu.id}`, menu, null, position, menu.intro);
    header.push(parent, ...menu.children.map((child, index) => item(`nav/header/${menu.id}/${child.id}`, child, parent.id, index, child.description)));
  });
  const footer = [];
  site.navigation.footer.forEach((group, position) => {
    // A footer group is a heading; Console needs a destination, so it uses its first link's.
    const parent = item(`nav/footer/${group.id}`, {label: group.label, href: group.children[0].href}, null, position);
    footer.push(parent, ...group.children.map((child, index) => item(`nav/footer/${group.id}/${child.id}`, child, parent.id, index, child.description)));
  });

  const seed = {
    schemaVersion: 2, contract: SEED_CONTRACT, status: 'draft', publicationAllowed: false,
    pages, media,
    globals: [{key: 'public-site', locale: 'en', value: site}],
    navigations: [{key: 'header', name: 'Public header', locale: 'en', items: header}, {key: 'footer', name: 'Public footer', locale: 'en', items: footer}],
  };
  return {...seed, checksum: digest(seed)};
}

/** Every image ({asset, alt}) inside a document. */
export function imagesIn(value) {
  const found = [];
  const walk = node => {
    if (Array.isArray(node)) node.forEach(walk);
    else if (node && typeof node === 'object') {
      if (typeof node.asset === 'string' && typeof node.alt === 'string') found.push(node);
      Object.values(node).forEach(walk);
    }
  };
  walk(value);
  return found;
}

/** Checks a seed read from disk: shape, documents, references and media bytes. */
export async function verifySiteSeed(seed) {
  const {validate} = await load();
  const fail = code => {throw new Error(code);};
  if (seed?.contract !== SEED_CONTRACT || seed.schemaVersion !== 2) fail('seed_unsupported');
  if (seed.status !== 'draft' || seed.publicationAllowed !== false) fail('seed_unsafe');
  const {checksum, ...body} = seed;
  if (checksum !== digest(body)) fail('seed_checksum_mismatch');
  const keys = new Set();
  for (const page of seed.pages) {
    if (keys.has(page.key) || !validate.validatePage(page.document).ok || page.document.page !== page.key) fail('seed_page_invalid');
    keys.add(page.key);
  }
  for (const global of seed.globals) if (global.key !== 'public-site' || !validate.validateSite(global.value).ok) fail('seed_global_invalid');
  const mediaIds = new Set(seed.media.map(item => item.id));
  for (const page of seed.pages) for (const image of imagesIn(page.document)) if (!mediaIds.has(image.asset)) fail('seed_image_unknown');
  for (const navigation of seed.navigations) for (const item of navigation.items) {
    if (item.destinationKind === 'page' && !keys.has(item.destination.replace(/^seed:page\//, ''))) fail('seed_navigation_unresolved');
  }
  for (const item of seed.media) {
    const file = path.resolve(root, item.path);
    if (!file.startsWith(path.join(root, MEDIA_DIR) + path.sep)) fail('seed_media_path_unsafe');
    const bytes = await readFile(file);
    if (bytes.length !== item.sizeBytes || sha256(bytes) !== item.checksumSha256) fail('seed_media_changed');
  }
  return seed;
}
