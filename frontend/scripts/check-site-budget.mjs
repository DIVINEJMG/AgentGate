// Performance budget for the public site. Run after `npm run build`:
//   node scripts/check-site-budget.mjs
// Measures what a visitor's browser downloads before any interaction (the entry script, its
// static imports and their stylesheets, compressed) and the server-rendered HTML of every public
// page. Lazy chunks (the workspace, legacy renderer) are not part of a public first load.
import {readFile} from 'node:fs/promises';
import {gzipSync} from 'node:zlib';
import path from 'node:path';

const BUDGET = {entryJsGzip: 120 * 1024, entryCssGzip: 25 * 1024, htmlGzip: 25 * 1024};
const root = path.resolve(import.meta.dirname, '..');
const manifest = JSON.parse(await readFile(path.join(root, 'dist/.vite/manifest.json'), 'utf8'));
const gzip = async file => gzipSync(await readFile(path.join(root, 'dist', file)), {level: 9}).length;

const seen = new Set();
let js = 0, css = 0;
async function walk(key) {
  if (seen.has(key)) return;
  seen.add(key);
  const chunk = manifest[key];
  js += await gzip(chunk.file);
  for (const sheet of chunk.css ?? []) css += await gzip(sheet);
  for (const next of chunk.imports ?? []) await walk(next);
}
await walk('index.html');

const {publicResponse} = await import('../server-dist/public-site.mjs');
const template = await readFile(path.join(root, 'server-dist/template.html'), 'utf8');
const pages = ['/', '/product', '/solutions', '/security', '/pricing', '/resources', '/company', '/privacy', '/terms'];
const rows = [];
let failed = false;
const kb = bytes => (bytes / 1024).toFixed(1) + ' KB';
const line = (name, value, limit) => {
  const ok = value <= limit;
  if (!ok) failed = true;
  rows.push({measure: name, size: kb(value), budget: kb(limit), result: ok ? 'ok' : 'OVER'});
};
line('First-load JavaScript (gzip)', js, BUDGET.entryJsGzip);
line('First-load CSS (gzip)', css, BUDGET.entryCssGzip);
for (const page of pages) {
  const response = await publicResponse({url: page, env: {PUBLIC_SITE_ORIGIN: 'https://audoryn.example'}, template});
  if (response.status !== 200) {failed = true; rows.push({measure: `HTML ${page}`, size: `status ${response.status}`, budget: '200', result: 'FAIL'}); continue;}
  line(`HTML ${page} (gzip, raw ${kb(Buffer.byteLength(response.body))})`, gzipSync(response.body, {level: 9}).length, BUDGET.htmlGzip);
}
console.table(rows);
if (failed) {console.error('Public site budget exceeded.'); process.exit(1);}
console.log('Public site budget met.');
