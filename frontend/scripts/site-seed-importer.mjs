// Imports the audoryn.public.v2 seed into Console as drafts, creating or updating by stable key.
//
// Stable keys: a page by its path and locale, the site document by its global key
// ("public-site"), navigation by its key ("header", "footer"), an image by its file name and
// checksum. The importer never approves, schedules or publishes; editors do that in Console.
//
// Safety:
//   - It plans first, with reads only. If anything was changed in Console by someone else, it stops
//     before writing anything and names the conflict.
//   - It replaces only what it wrote itself (recorded in the receipt), what is still the previous
//     (v1) content, or what is empty. Page revisions are append-only in Console, so an update adds
//     a draft revision on top; nothing is lost.
//   - Writes are never retried here. (Console also replays a write repeated with the same
//     Idempotency-Key within an hour.) After a failure the next run compares Console with the
//     seed and carries on from where it stopped.
//   - Reads are retried a few times on transport errors and 502, 503 and 504.
import {digest, SEED_CONTRACT, seedUuid} from './site-seed.mjs';

const REASON = 'Import the audoryn.public.v2 website content as draft content for editorial review.';
const RETRYABLE = new Set([502, 503, 504]);

export class ImportConflict extends Error {
  constructor(conflicts) {
    super('import_conflict');
    this.conflicts = conflicts;
  }
}

/** The content of a revision that matters for comparison, whatever Console adds around it. */
export function revisionContent(revision, seoKeys) {
  return {
    locale: revision.locale, title: revision.title, slug: revision.slug,
    blocks: (revision.blocks ?? []).map(block => ({type: block.type, payload: block.content?.payload ?? null})),
    seo: Object.fromEntries(seoKeys.map(key => [key, revision.seo?.[key] ?? null])),
  };
}

/** Navigation items compared by meaning: Console's ordering and its openInNewTab copy are ignored. */
export function navigationContent(items) {
  return items.map(item => {
    const {openInNewTab: _copy, ...metadata} = item.metadata ?? {};
    return {
      id: item.id, parentId: item.parentId ?? null, label: item.label, destinationKind: item.destinationKind,
      destination: item.destination, position: item.position, visible: item.visible,
      openInNewTab: item.openInNewTab ?? item.metadata?.openInNewTab ?? false, metadata,
    };
  }).sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

const isV2 = revision => (revision?.blocks ?? []).some(block => block.content?.payload?.contract === SEED_CONTRACT);
const latestOf = revisions => [...revisions].sort((a, b) => b.revision - a.revision)[0];

export class SiteSeedImporter {
  /**
   * @param {object} options
   * @param {object} options.seed            a verified v2 seed
   * @param {(method: string, endpoint: string, body?: unknown) => Promise<any>} options.request  Console CMS API (/api/v1/cms)
   * @param {'plan' | 'apply'} options.mode  plan reads only; apply also writes
   * @param {object} options.receipt         what earlier runs wrote (mutated and checkpointed)
   * @param {(receipt: object) => Promise<void>} options.checkpoint
   * @param {(media: object) => Promise<Uint8Array>} [options.readMedia]
   * @param {(intent: {url: string, contentType: string}, bytes: Uint8Array) => Promise<void>} [options.upload]
   * @param {boolean} [options.replaceNavigation]  replace navigation that someone else changed
   * @param {(ms: number) => Promise<void>} [options.sleep]
   */
  constructor({seed, request, mode = 'plan', receipt, checkpoint, readMedia, upload, replaceNavigation = false, sleep = ms => new Promise(resolve => setTimeout(resolve, ms))}) {
    if (seed?.contract !== SEED_CONTRACT || seed.publicationAllowed !== false) throw new Error('seed_unsafe');
    Object.assign(this, {seed, request, mode, receipt, checkpoint, readMedia, upload, replaceNavigation, sleep});
    receipt.media ??= {};
    receipt.pages ??= {};
    receipt.globals ??= {};
    receipt.navigations ??= {};
  }

  async read(endpoint) {
    for (let attempt = 1; ; attempt++) {
      try {
        return await this.request('GET', endpoint);
      } catch (error) {
        const status = error.diagnostic?.httpStatus;
        if (attempt >= 3 || (status !== undefined && !RETRYABLE.has(status))) throw error;
        await this.sleep(500 * attempt);
      }
    }
  }

  async write(method, endpoint, body) {
    if (this.mode !== 'apply') throw new Error('write_in_plan_mode');
    return this.request(method, endpoint, body);
  }

  async all(endpoint) {
    const items = [];
    for (let offset = 0; ; offset += 100) {
      const page = await this.read(`${endpoint}${endpoint.includes('?') ? '&' : '?'}limit=100&offset=${offset}`);
      items.push(...page.items);
      if (items.length >= page.total || page.items.length < 100) return items;
      if (offset > 10000) throw new Error('import_inventory_limit');
    }
  }

  async save() {
    await this.checkpoint(this.receipt);
  }

  /** Replaces seed asset IDs and seed page references with Console identities (unknown ones stay as placeholders). */
  resolve(value) {
    if (Array.isArray(value)) return value.map(item => this.resolve(item));
    if (value && typeof value === 'object') {
      const result = Object.fromEntries(Object.entries(value).map(([key, item]) => [key, this.resolve(item)]));
      if (typeof result.asset === 'string' && typeof result.alt === 'string') result.asset = this.receipt.media[result.asset] ?? result.asset;
      if (result.destinationKind === 'page' && typeof result.destination === 'string' && result.destination.startsWith('seed:page/')) {
        result.destination = this.receipt.pages[result.destination.slice(10)]?.pageId ?? result.destination;
      }
      return result;
    }
    return value;
  }

  desiredRevision(page) {
    // Console requires a UUID on every block; a stable one per page keeps revisions comparable.
    const block = {id: seedUuid(`block/${page.key}`), type: 'PublicContent', variant: 'content', theme: 'content', content: {payload: this.resolve(page.document)}};
    return {locale: page.locale, title: page.title, slug: page.slug, blocks: [block], seo: page.seo};
  }

  // ---- plan (reads only) --------------------------------------------------------------------------

  async plan() {
    const actions = [];
    const conflicts = [];
    const conflict = (resource, key, code) => conflicts.push({resource, key, code});

    const assets = await this.all('/assets');
    for (const media of this.seed.media) {
      let asset = assets.find(item => item.id === this.receipt.media[media.id]);
      if (!asset) {
        const matches = assets.filter(item => item.filename === media.filename && item.checksumSha256 === media.checksumSha256 && item.status !== 'archived');
        if (matches.length > 1) {conflict('media', media.filename, 'media_ambiguous'); continue;}
        asset = matches[0];
      }
      if (asset) this.receipt.media[media.id] = asset.id;
      actions.push({resource: 'media', key: media.filename, action: !asset ? 'upload' : asset.status === 'ready' ? 'unchanged' : 'complete', media, asset});
    }

    const pages = (await this.all('/pages')).filter(item => !item.archivedAt);
    for (const page of this.seed.pages) {
      const known = this.receipt.pages[page.key];
      let target = known && pages.find(item => item.id === known.pageId);
      if (!target) {
        const matches = pages.filter(item => item.canonicalSlug === page.slug && item.defaultLocale === page.locale);
        if (matches.length > 1) {conflict('page', page.key, 'page_ambiguous'); continue;}
        target = matches[0];
      }
      if (!target) {actions.push({resource: 'page', key: page.key, action: 'create', page}); continue;}
      if (target.contentType !== page.contentType) {conflict('page', page.key, 'page_type_differs'); continue;}
      this.receipt.pages[page.key] = {...this.receipt.pages[page.key], pageId: target.id};
      const detail = await this.read(`/pages/${target.id}?locale=${encodeURIComponent(page.locale)}`);
      const revisions = detail.revisions ?? [];
      if (revisions.some(revision => revision.status === 'scheduled')) {conflict('page', page.key, 'page_has_scheduled_revision'); continue;}
      const latest = latestOf(revisions);
      const seoKeys = Object.keys(page.seo);
      const wanted = digest(revisionContent(this.desiredRevision(page), seoKeys));
      if (latest && digest(revisionContent(latest, seoKeys)) === wanted) {
        actions.push({resource: 'page', key: page.key, action: 'unchanged', page, pageId: target.id, revisionId: latest.id});
      } else if (!latest || !isV2(latest) || this.receipt.pages[page.key]?.revisionId === latest.id) {
        actions.push({resource: 'page', key: page.key, action: isV2(latest) ? 'update' : 'migrate', page, pageId: target.id, basedOn: latest?.id ?? null});
      } else {
        conflict('page', page.key, 'page_edited_in_console');
      }
    }

    const globals = (await this.read('/global-content?locale=en')).items ?? [];
    for (const global of this.seed.globals) {
      const existing = globals.find(item => item.key === global.key && item.locale === global.locale);
      const wanted = digest(this.resolve(global.value));
      if (!existing) actions.push({resource: 'global', key: global.key, action: 'create', global});
      else if (digest(existing.value) === wanted) actions.push({resource: 'global', key: global.key, action: 'unchanged', global, id: existing.id});
      else if (this.receipt.globals[global.key]?.digest === digest(existing.value)) actions.push({resource: 'global', key: global.key, action: 'update', global});
      else conflict('global', global.key, 'global_edited_in_console');
    }

    const navigations = (await this.read('/navigations?locale=en')).items ?? [];
    for (const navigation of this.seed.navigations) {
      const existing = navigations.find(item => item.id === this.receipt.navigations[navigation.key]?.id) ?? navigations.find(item => item.key === navigation.key && item.locale === navigation.locale);
      if (!existing) {actions.push({resource: 'navigation', key: navigation.key, action: 'create', navigation}); continue;}
      const current = digest(navigationContent(existing.items ?? []));
      const ours = this.receipt.navigations[navigation.key]?.digest === current;
      if (current === digest(navigationContent(this.resolve(navigation.items)))) actions.push({resource: 'navigation', key: navigation.key, action: 'unchanged', navigation, id: existing.id});
      else if (!existing.items?.length || ours) actions.push({resource: 'navigation', key: navigation.key, action: 'update', navigation, id: existing.id});
      else if (this.replaceNavigation) actions.push({resource: 'navigation', key: navigation.key, action: 'replace', navigation, id: existing.id, replacedItems: existing.items.length});
      else conflict('navigation', navigation.key, 'navigation_edited_in_console');
    }
    return {actions, conflicts};
  }

  // ---- apply ----------------------------------------------------------------------------------------

  async apply(actions) {
    for (const step of actions.filter(item => item.resource === 'media' && item.action !== 'unchanged')) {
      const {media} = step;
      let asset = step.asset;
      if (step.action === 'upload') {
        const bytes = await this.readMedia(media);
        const result = await this.write('POST', '/assets/upload-intents', {kind: 'image', filename: media.filename, display_name: media.filename, alt_text: media.altText, media_type: media.mediaType, size_bytes: media.sizeBytes, checksum_sha256: media.checksumSha256, width: media.width, height: media.height});
        asset = result.asset;
        this.receipt.media[media.id] = asset.id;
        await this.save();
        await this.upload(result.upload, bytes);
      }
      // Console checks the stored bytes against the checksum before it marks the image ready.
      try {
        await this.write('POST', `/assets/${asset.id}/complete`, {reason: REASON});
      } catch {
        throw new Error('media_verification_failed');
      }
    }

    for (const step of actions.filter(item => item.resource === 'page' && item.action === 'create')) {
      const {page} = step;
      const result = await this.write('POST', '/pages', {title: page.title, slug: page.slug, locale: page.locale, content_type: page.contentType, reason: REASON});
      this.receipt.pages[page.key] = {pageId: result.page.id};
      await this.save();
      Object.assign(step, {pageId: result.page.id, basedOn: result.revision?.id ?? null});
    }
    for (const step of actions.filter(item => item.resource === 'page')) {
      const {page} = step;
      if (step.action === 'unchanged') {
        this.receipt.pages[page.key] = {pageId: step.pageId, revisionId: step.revisionId};
        continue;
      }
      const result = await this.write('POST', `/pages/${step.pageId}/revisions`, {...this.desiredRevision(page), based_on_revision_id: step.basedOn, reason: REASON});
      this.receipt.pages[page.key] = {pageId: step.pageId, revisionId: result.revision.id};
      step.revisionId = result.revision.id;
      await this.save();
    }

    for (const step of actions.filter(item => item.resource === 'global')) {
      const value = this.resolve(step.global.value);
      if (step.action !== 'unchanged') {
        const result = await this.write('PUT', `/global-content/${encodeURIComponent(step.global.key)}`, {locale: step.global.locale, value, reason: REASON});
        step.id = result.item.id;
      }
      this.receipt.globals[step.global.key] = {id: step.id, digest: digest(value)};
      await this.save();
    }

    for (const step of actions.filter(item => item.resource === 'navigation')) {
      const {navigation} = step;
      const items = this.resolve(navigation.items);
      if (items.some(item => item.destinationKind === 'page' && item.destination.startsWith('seed:'))) throw new Error('navigation_page_unresolved');
      if (step.action === 'create') {
        const result = await this.write('POST', '/navigations', {key: navigation.key, name: navigation.name, locale: navigation.locale, reason: REASON});
        step.id = result.navigation.id;
        this.receipt.navigations[navigation.key] = {id: step.id};
        await this.save();
      }
      if (step.action !== 'unchanged') await this.write('PUT', `/navigations/${step.id}/items`, {items, reason: REASON});
      this.receipt.navigations[navigation.key] = {id: step.id, digest: digest(navigationContent(items))};
      await this.save();
    }
  }

  /** Plans, and in apply mode writes. Returns a report with codes and counts only. */
  async run() {
    const {actions, conflicts} = await this.plan();
    const report = {
      mode: this.mode,
      seed: this.seed.checksum.slice(0, 12),
      actions: actions.map(({resource, key, action, replacedItems}) => ({resource, key, action, ...(replacedItems ? {replacedItems} : {})})),
      publicationPerformed: false,
    };
    if (conflicts.length) throw Object.assign(new ImportConflict(conflicts), {report});
    if (this.mode !== 'apply') return {...report, status: 'planned'};

    if (this.receipt.seedChecksum !== this.seed.checksum) {
      this.receipt.seedChecksum = this.seed.checksum;
      delete this.receipt.status;
    }
    await this.save();
    await this.apply(actions);
    this.receipt.status = 'drafts_prepared';
    await this.save();
    return {
      ...report,
      status: 'drafts_prepared',
      // What an editor adds to a release, once the page revisions are approved.
      releaseItems: [
        ...this.seed.pages.map(page => ({resourceType: 'page_revision', key: page.key, resourceId: this.receipt.pages[page.key].revisionId})),
        ...this.seed.globals.map(global => ({resourceType: 'global_content', key: global.key, resourceId: this.receipt.globals[global.key].id})),
        ...this.seed.navigations.map(navigation => ({resourceType: 'navigation', key: navigation.key, resourceId: this.receipt.navigations[navigation.key].id})),
      ],
    };
  }
}

