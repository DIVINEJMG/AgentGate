// A published v2 release built from the seed, laid out the way Console's public storage lays it
// out (pointer, release manifest, page envelopes). Used by verify-public-host.mjs. No network.
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {seedUuid} from './site-seed.mjs';

const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');

export const pageIdOf = key => seedUuid('page/' + key);

export async function readSeed() {
  return JSON.parse(await readFile(path.resolve(import.meta.dirname, '../content-seed/audoryn-public.v2.seed.json'), 'utf8'));
}

/**
 * Release `version` for `origin` (the storage origin, which may include a path).
 * `pages` are seed pages ({key, slug, locale, title, contentType, document, seo}); extra pages may
 * give `pageId` instead of `key`. Navigation items pointing to a page not in `pages` are left out,
 * as Console's release check requires. Returns the stored objects by path name, and the release.
 */
export function releaseFixture(origin, version, seed, {pages = seed.pages, redirects = []} = {}) {
  const host = new URL(origin).origin;
  const base = new URL(origin).pathname.replace(/\/$/, '') + '/cms-public';
  const objects = new Map();
  const put = (pathname, value) => {
    const bytes = Buffer.from(JSON.stringify(value));
    objects.set(pathname, bytes);
    return {url: host + pathname, checksum: sha256(bytes)};
  };

  const index = pages.map(page => {
    const pageId = page.pageId ?? pageIdOf(page.key);
    const envelope = {
      pageId, revisionId: seedUuid(`revision/${page.slug}/${version}`), locale: page.locale, slug: page.slug, title: page.title,
      contentType: page.contentType, rendererContract: page.document.contract,
      blocks: [{id: seedUuid('block/' + page.slug), type: 'PublicContent', content: {payload: page.document}}], seo: page.seo ?? {},
    };
    const stored = put(`${base}/releases/${version}/pages/${pageId}/${page.locale}.json`, envelope);
    const {blocks: _blocks, seo: _seo, ...identity} = envelope;
    return {...identity, ...stored};
  });

  const published = new Set(index.map(page => page.pageId));
  const destination = value => value.startsWith('seed:page/') ? pageIdOf(value.slice('seed:page/'.length)) : value;
  const resources = [
    ...seed.globals.map(item => ({resourceType: 'global_content', resourceId: seedUuid('global/' + item.key), data: item})),
    ...seed.navigations.map(navigation => {
      const items = navigation.items.map(item => (item.destinationKind === 'page' ? {...item, destination: destination(item.destination)} : item));
      const kept = items.filter(item => item.destinationKind !== 'page' || published.has(item.destination));
      return {resourceType: 'navigation', resourceId: seedUuid('navigation/' + navigation.key), data: {...navigation, items: kept}};
    }),
    ...redirects.map((redirect, i) => ({resourceType: 'redirect', resourceId: 'redirect-' + i, data: {locale: 'en', active: true, ...redirect}})),
  ];
  const assets = seed.media.map(media => ({
    id: media.id, url: `${host}${base}/assets/${media.id}`, mediaType: media.mediaType, checksumSha256: media.checksumSha256,
    sizeBytes: media.sizeBytes, width: media.width, height: media.height, verification: {formatVerified: true, checksumSha256: media.checksumSha256},
  }));

  const release = {schemaVersion: 1, version, checksum: sha256(`release/${version}`), publishedAt: '2026-10-12T00:00:00Z', pages: index, resources, assets};
  const stored = put(`${base}/releases/${version}/release.json`, release);
  put(`${base}/current.json`, {schemaVersion: 1, version, eventId: `fixture-${version}`, checksum: release.checksum, releaseUrl: stored.url, releaseChecksum: stored.checksum});
  return {objects, release};
}
