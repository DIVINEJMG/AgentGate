import test, {before} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {createElement} from 'react';
import {renderToString} from 'react-dom/server';
import {bundle} from './helpers/bundle.mjs';

const root = path.resolve(import.meta.dirname, '..');
const css = readFileSync(path.join(root, 'src/site/styles/tokens.css'), 'utf8');
const token = name => {
  const match = css.match(new RegExp(`--${name}:\\s*(#[0-9a-f]{6})`, 'i'));
  assert.ok(match, `token --${name}`);
  return match[1];
};
function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255).map(c => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
const contrast = (a, b) => {
  const [x, y] = [luminance(token(a)), luminance(token(b))].sort((m, n) => n - m);
  return (x + 0.05) / (y + 0.05);
};

test('text on light surfaces meets WCAG AA (4.5:1)', () => {
  for (const text of ['ink', 'ink-muted', 'brand', 'signal-ink', 'hold-ink', 'deny-ink', 'model-ink']) {
    for (const surface of ['cream', 'cream-raised', 'sage']) assert.ok(contrast(text, surface) >= 4.5, `${text} on ${surface}: ${contrast(text, surface).toFixed(2)}`);
  }
  for (const [text, surface] of [['signal-ink', 'signal-tint'], ['hold-ink', 'hold-tint'], ['deny-ink', 'deny-tint'], ['on-brand', 'brand'], ['on-brand', 'brand-hover']]) {
    assert.ok(contrast(text, surface) >= 4.5, `${text} on ${surface}: ${contrast(text, surface).toFixed(2)}`);
  }
});

test('text and signal colours on system surfaces meet WCAG AA (4.5:1)', () => {
  for (const text of ['on-night', 'on-night-muted', 'signal', 'hold', 'deny', 'model']) {
    for (const surface of ['night', 'night-raised', 'forest']) assert.ok(contrast(text, surface) >= 4.5, `${text} on ${surface}: ${contrast(text, surface).toFixed(2)}`);
  }
  assert.ok(contrast('night', 'signal') >= 4.5, 'text on the signal button');
});

test('focus rings are visible (3:1)', () => {
  for (const surface of ['cream', 'sage']) assert.ok(contrast('focus', surface) >= 3);
  for (const surface of ['night', 'forest']) assert.ok(contrast('focus-on-night', surface) >= 3);
});

let html;
before(async () => {
  const {preview} = await bundle({preview: 'src/site/preview/Preview.tsx'});
  html = renderToString(createElement(preview.Preview));
});

test('every home section renders on the server with the built-in content', () => {
  for (const id of ['hero', 'product', 'decision', 'control-path', 'policy', 'operating-model', 'oversight', 'activity', 'connections', 'solutions', 'controls', 'closing']) {
    assert.match(html, new RegExp(`<section id="${id}"`), id);
  }
  for (const marker of ['site-header', 'site-footer', 'site-frame', 'cp-body', 'home-editor', 'home-fan', 'home-metrics']) assert.ok(html.includes(marker), marker);
});

test('navigation and calls to action are real links', () => {
  const anchors = [...html.matchAll(/<a\b[^>]*>/g)].map(match => match[0]);
  assert.ok(anchors.length > 40, `links: ${anchors.length}`);
  assert.ok(anchors.every(anchor => /\shref="[^"]+"/.test(anchor)), 'every <a> has an href');
  // Buttons only for things that act on the page: mobile menu, decision choices, tabs.
  const buttons = [...html.matchAll(/<button\b[^>]*>/g)].map(match => match[0]);
  assert.ok(buttons.every(button => /site-menu-button|role="radio"|role="tab"/.test(button)), buttons.join('\n'));
});

test('one h1, a skip link, labelled navigation and labelled controls', () => {
  assert.equal((html.match(/<h1\b/g) || []).length, 1);
  assert.match(html, /class="site-skip" href="#main"/);
  assert.match(html, /<main id="main"/);
  assert.match(html, /<nav[^>]*aria-label="Main navigation"/);
  assert.match(html, /<nav[^>]*aria-label="Footer navigation"/);
  assert.match(html, /role="radiogroup" aria-label="Choose a decision"/);
  assert.match(html, /role="tablist" aria-label="Choose a solution area"/);
});

test('every demonstration carries the illustrative badge', () => {
  // Control plane, policy code and the record console.
  assert.ok((html.match(/class="site-badge"/g) || []).length >= 3);
});

test('the typing line is readable without motion', () => {
  assert.match(html, /class="site-sr">Every action in GitHub, Slack, Gmail, Google Drive, Google Calendar, the browser is checked before it runs\./);
  assert.match(html, /home-typing-word">GitHub</);
});

test('headlines keep their words apart for assistive technology', () => {
  assert.ok(!/[a-z.]<span class="site-headline-detail">/.test(html));
});

test('every rebuilt page renders with one h1, real links and a skip target', async () => {
  const {preview} = await bundle({preview: 'src/site/preview/Preview.tsx'});
  for (const page of ['home', 'product', 'solutions', 'security', 'pricing']) {
    const markup = renderToString(createElement(preview.Preview, {page}));
    assert.equal((markup.match(/<h1\b/g) || []).length, 1, page);
    assert.match(markup, /<main id="main"/, page);
    assert.ok([...markup.matchAll(/<a\b[^>]*>/g)].every(match => /\shref="[^"]+"/.test(match[0])), page);
    assert.ok(!/[a-z.]<span class="site-headline-detail">/.test(markup), page);
  }
});

test('every built-in link to a section of a rebuilt page lands on an element with that id', async () => {
  const {preview, defaults, routes} = await bundle({preview: 'src/site/preview/Preview.tsx', defaults: 'src/site/content/defaults/index.ts', routes: 'src/site/routes.ts'});
  const pageHtml = {};
  for (const [path, page] of Object.entries(routes.FIXED_PAGES)) pageHtml[path] = renderToString(createElement(preview.Preview, {page}));
  const hrefs = [...JSON.stringify([defaults.defaultSite, defaults.defaultPages]).matchAll(/"href":"(\/[^"#]*)#([^"]+)"/g)];
  let checked = 0;
  for (const [, path, anchor] of hrefs) {
    if (!pageHtml[path]) continue;
    assert.match(pageHtml[path], new RegExp(`id="${anchor}"`), `${path}#${anchor}`);
    checked++;
  }
  assert.ok(checked >= 10, `anchors checked: ${checked}`);
});

test('the anchors each page renders are exactly the contract anchors (x-anchors), which Console checks links against', async () => {
  const {preview, defaults, validate} = await bundle({preview: 'src/site/preview/Preview.tsx', defaults: 'src/site/content/defaults/index.ts', validate: 'src/site/contract/validate.ts'});
  for (const [page, document] of Object.entries(defaults.defaultPages)) {
    const markup = renderToString(createElement(preview.Preview, {page}));
    const main = markup.slice(markup.indexOf('<main'));
    const rendered = [...main.matchAll(/<(?:section|article)[^>]* id="([^"]+)"/g)].map(match => match[1]);
    // The resources library is shown only while articles are published; the preview has none.
    const expected = validate.anchorsOf(document).filter(anchor => !(page === 'resources' && anchor === 'library'));
    assert.deepEqual(rendered.sort(), expected.sort(), page);
  }
});

test('a published v2 article renders as a reading page with its metadata', async () => {
  const {app, bundleModule} = await bundle({app: 'src/site/SiteApp.tsx', bundleModule: 'src/site/content/bundle.ts'});
  const {defaults} = await bundle({defaults: 'src/site/content/defaults/index.ts'});
  const article = JSON.parse(readFileSync(path.join(root, 'src/site/contract/examples/article.valid.json'), 'utf8'));
  const base = bundleModule.defaultBundle('/resources/first-worker', null, 'en', []);
  const markup = renderToString(createElement(app.SiteDocument, {bundle: {...base, status: 200, source: 'published', page: article, site: defaults.defaultSite}, navigate: null}));
  assert.equal((markup.match(/<h1\b/g) || []).length, 1);
  assert.match(markup, /Setting up your first supervised worker/);
  assert.match(markup, /<time dateTime="2026-10-11">11 October 2026<\/time>/);
  assert.match(markup, /href="\/resources"[^>]*>.*All resources/);
  assert.match(markup, /id="role"/);
});
