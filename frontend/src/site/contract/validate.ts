// Validates content documents against the audoryn.public.v2 contract.
//
// Two layers: the JSON Schema (shape, lengths, plain-text rules) and the rules a schema cannot
// express (unique keys, safe links, valid dates, plan consistency). Errors carry a field path
// and a code only, never content, so they are safe to log.
import schemaJson from './contract.v2.json';
import {CONTRACT, ITEM_ANCHORS, type ContentDocument, type PageDocument, type Site} from './contract.generated';

export type ContentError = {path: string; code: string};
export type Validation<T> = {ok: true; value: T} | {ok: false; errors: ContentError[]};

type Schema = {
  $ref?: string; oneOf?: Schema[]; type?: string; const?: unknown; enum?: unknown[];
  properties?: Record<string, Schema>; required?: string[]; additionalProperties?: boolean;
  items?: Schema; minItems?: number; maxItems?: number;
  minLength?: number; maxLength?: number; pattern?: string; minimum?: number; maximum?: number;
};
const schema = schemaJson as unknown as Schema & {$defs: Record<string, Schema>};
const MAX_DEPTH = 32;
const MAX_ERRORS = 50;
const patterns = new Map<string, RegExp>();
const pattern = (source: string) => {
  let compiled = patterns.get(source);
  if (!compiled) {compiled = new RegExp(source, 'u'); patterns.set(source, compiled);}
  return compiled;
};
const resolve = (node: Schema): Schema => node.$ref ? resolve(schema.$defs[node.$ref.split('/').pop()!]) : node;

function check(value: unknown, node: Schema, path: string, errors: ContentError[], depth: number): void {
  if (errors.length >= MAX_ERRORS) return;
  if (depth > MAX_DEPTH) {errors.push({path, code: 'too_deep'}); return;}
  node = resolve(node);
  if (node.const !== undefined && value !== node.const) {errors.push({path, code: 'unexpected_value'}); return;}
  if (node.enum && !node.enum.includes(value)) {errors.push({path, code: 'not_allowed'}); return;}
  switch (node.type) {
    case 'object': {
      if (!value || typeof value !== 'object' || Array.isArray(value)) {errors.push({path, code: 'expected_object'}); return;}
      const record = value as Record<string, unknown>;
      for (const name of node.required ?? []) if (record[name] === undefined) errors.push({path: join(path, name), code: 'required'});
      for (const [name, item] of Object.entries(record)) {
        // Own properties only: a field named like a built-in ("constructor") is still unknown.
        const child = node.properties && Object.prototype.hasOwnProperty.call(node.properties, name) ? node.properties[name] : undefined;
        if (child) check(item, child, join(path, name), errors, depth + 1);
        else if (node.additionalProperties === false) errors.push({path: join(path, name), code: 'unknown_field'});
      }
      return;
    }
    case 'array': {
      if (!Array.isArray(value)) {errors.push({path, code: 'expected_list'}); return;}
      if (value.length < (node.minItems ?? 0)) errors.push({path, code: 'too_few_items'});
      if (value.length > (node.maxItems ?? Infinity)) errors.push({path, code: 'too_many_items'});
      value.forEach((item, index) => check(item, node.items!, `${path}[${index}]`, errors, depth + 1));
      return;
    }
    case 'string':
      if (typeof value !== 'string') errors.push({path, code: 'expected_text'});
      else if (value.length < (node.minLength ?? 0)) errors.push({path, code: value ? 'too_short' : 'empty'});
      else if (value.length > (node.maxLength ?? Infinity)) errors.push({path, code: 'too_long'});
      else if (node.pattern && !pattern(node.pattern).test(value)) errors.push({path, code: 'invalid_format'});
      return;
    case 'integer':
    case 'number':
      if (typeof value !== 'number' || !Number.isFinite(value) || (node.type === 'integer' && !Number.isInteger(value))) errors.push({path, code: 'expected_number'});
      else if (value < (node.minimum ?? -Infinity) || value > (node.maximum ?? Infinity)) errors.push({path, code: 'out_of_range'});
      return;
    case 'boolean':
      if (typeof value !== 'boolean') errors.push({path, code: 'expected_boolean'});
      return;
  }
}
const join = (path: string, name: string) => path ? `${path}.${name}` : name;

/** A link destination is a site path or a credential-free https:// URL. */
export function isSafeHref(href: string): boolean {
  if (href.startsWith('/')) return !href.startsWith('//') && !/[\\\s\u0000-\u001f<>]/.test(href) && !/%2f|%5c/i.test(href);
  try {
    const url = new URL(href);
    return url.protocol === 'https:' && !url.username && !url.password && Boolean(url.hostname);
  } catch {return false;}
}

/** The link anchors a page document exposes (contract x-anchors), each with the field that defines it. */
function anchorList(document: {page?: unknown; sections?: unknown}): {anchor: string; path: string}[] {
  const sections = (document.sections ?? {}) as Record<string, Record<string, unknown> | undefined>;
  const found: {anchor: string; path: string}[] = [];
  for (const [key, section] of Object.entries(sections)) {
    if (section) found.push({anchor: key.replace(/[A-Z]/g, c => '-' + c.toLowerCase()), path: `sections.${key}`});
  }
  // Items after sections, so a clash is reported on the item.
  for (const [key, section] of Object.entries(sections)) {
    if (!section) continue;
    const list = ITEM_ANCHORS[String(document.page)]?.[key];
    const items = list ? section[list] : undefined;
    if (Array.isArray(items)) items.forEach((item, index) => {
      if (typeof item?.id === 'string') found.push({anchor: item.id, path: `sections.${key}.${list}[${index}].id`});
    });
  }
  return found;
}

/** Every anchor a link may point to on this page: /product#workforce needs "workforce" here. */
export const anchorsOf = (document: PageDocument): string[] => anchorList(document).map(item => item.anchor);

function rules(document: Record<string, unknown>, errors: ContentError[]): void {
  // Every list item key is unique within its list, and every link is safe.
  const walk = (value: unknown, path: string): void => {
    if (Array.isArray(value)) {
      const seen = new Set<string>();
      value.forEach((item, index) => {
        const id = item && typeof item === 'object' ? ((item as {id?: unknown; key?: unknown}).id ?? (item as {key?: unknown}).key) : undefined;
        if (typeof id === 'string') {
          if (seen.has(id)) errors.push({path: `${path}[${index}]`, code: 'duplicate_key'});
          seen.add(id);
        }
        walk(item, `${path}[${index}]`);
      });
    } else if (value && typeof value === 'object') {
      for (const [name, item] of Object.entries(value)) {
        if (name === 'href' && typeof item === 'string' && !isSafeHref(item)) errors.push({path: join(path, name), code: 'unsafe_link'});
        walk(item, join(path, name));
      }
    }
  };
  walk(document, '');

  // An item anchor must not repeat a section anchor (or another list's), or links become ambiguous.
  const anchors = new Set<string>();
  for (const {anchor, path} of anchorList(document)) {
    if (anchors.has(anchor)) errors.push({path, code: 'duplicate_anchor'});
    anchors.add(anchor);
  }

  const dates: [unknown, string][] = [];
  const sections = document.sections as Record<string, Record<string, unknown>> | undefined;
  if (document.page === 'privacy' || document.page === 'terms') dates.push([sections?.document?.effectiveDate, 'sections.document.effectiveDate']);
  if (document.page === 'article') dates.push([sections?.article?.published, 'sections.article.published']);
  for (const [value, path] of dates) {
    if (typeof value === 'string' && (!Number.isFinite(Date.parse(value)) || new Date(value).toISOString().slice(0, 10) !== value)) errors.push({path, code: 'invalid_date'});
  }

  if (document.page === 'pricing') {
    const plans = ((sections?.plans?.plans ?? []) as {availability?: string; action?: unknown; priceMinor?: number}[]);
    plans.forEach((plan, index) => {
      if (plan.availability === 'contact' && !plan.action) errors.push({path: `sections.plans.plans[${index}].action`, code: 'contact_requires_link'});
      if (plan.availability === 'free-signup' && plan.priceMinor !== 0) errors.push({path: `sections.plans.plans[${index}].priceMinor`, code: 'free_plan_must_be_free'});
    });
  }
}

/** Validates any v2 document: the shared site document or one page. */
export function validateDocument(value: unknown): Validation<ContentDocument> {
  const errors: ContentError[] = [];
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {ok: false, errors: [{path: '', code: 'expected_object'}]};
  const document = value as Record<string, unknown>;
  if (document.contract !== CONTRACT) return {ok: false, errors: [{path: 'contract', code: 'unsupported_contract'}]};
  const name = document.page === 'site' ? 'Site' : typeof document.page === 'string' && document.page ? document.page[0].toUpperCase() + document.page.slice(1) + 'Page' : '';
  const node = Object.prototype.hasOwnProperty.call(schema.$defs, name) ? schema.$defs[name] : undefined;
  if (!node || !schema.oneOf?.some(option => option.$ref === `#/$defs/${name}`)) return {ok: false, errors: [{path: 'page', code: 'unknown_page'}]};
  check(document, node, '', errors, 0);
  if (!errors.length) rules(document, errors);
  return errors.length ? {ok: false, errors} : {ok: true, value: document as ContentDocument};
}

export function validatePage(value: unknown): Validation<PageDocument> {
  const result = validateDocument(value);
  if (result.ok && result.value.page === 'site') return {ok: false, errors: [{path: 'page', code: 'expected_page'}]};
  return result as Validation<PageDocument>;
}

export function validateSite(value: unknown): Validation<Site> {
  const result = validateDocument(value);
  if (result.ok && result.value.page !== 'site') return {ok: false, errors: [{path: 'page', code: 'expected_site'}]};
  return result as Validation<Site>;
}
