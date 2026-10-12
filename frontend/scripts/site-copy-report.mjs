// Writes a readable review document of the built-in site copy, page by page, using the editor
// titles from the contract. Run after changing copy:  node scripts/site-copy-report.mjs
import {readFileSync, writeFileSync} from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const read = file => JSON.parse(readFileSync(path.join(root, file), 'utf8'));
const schema = read('src/site/contract/contract.v2.json');
const pages = ['site', 'home', 'product', 'solutions', 'security', 'pricing', 'resources', 'company', 'privacy', 'terms'];
const resolve = node => node?.$ref ? resolve(schema.$defs[node.$ref.split('/').pop()]) : node;
const lines = [];
const out = text => lines.push(text);

function render(value, node, depth) {
  node = resolve(node) || {};
  const pad = '  '.repeat(depth);
  if (Array.isArray(value)) {
    value.forEach((item, index) => {
      if (item && typeof item === 'object') {out(`${pad}- **${index + 1}.**`); render(item, node.items, depth + 1);}
      else out(`${pad}- ${item}`);
    });
  } else if (value && typeof value === 'object') {
    for (const [key, item] of Object.entries(value)) {
      if (key === 'contract' || key === 'page' || key === 'id' || key === 'asset') continue;
      const field = node.properties?.[key] || {};
      const child = resolve(field) || {};
      const label = field.title || child.title || key;
      if (item && typeof item === 'object') {out(`${pad}- *${label}*`); render(item, child, depth + 1);}
      else out(`${pad}- *${label}:* ${item}`);
    }
  }
}

out('# Built-in copy for review');
out('');
out('Generated from `frontend/src/site/content/defaults` by `scripts/site-copy-report.mjs`. This is the');
out('text the new site shows until Console publishes v2 content, and the text the Console seed will');
out('contain. Field names are the editor titles Console will show.');
for (const page of pages) {
  const document = read(`src/site/content/defaults/${page}.json`);
  out('');
  out(`## ${page === 'site' ? 'Shared (every page)' : page[0].toUpperCase() + page.slice(1)}`);
  if (page === 'site') {render(document, schema.$defs.Site, 0); continue;}
  const definition = schema.$defs[page[0].toUpperCase() + page.slice(1) + 'Page'];
  for (const [key, section] of Object.entries(document.sections)) {
    const node = resolve(definition.properties.sections.properties[key]);
    out('');
    out(`### ${key} — ${node.title} (anchor \`#${key.replace(/[A-Z]/g, c => '-' + c.toLowerCase())}\`)`);
    out('');
    render(section, node, 0);
  }
}
writeFileSync(path.join(root, '../docs/public-site/copy-review.md'), lines.join('\n') + '\n');
console.log('Wrote docs/public-site/copy-review.md');
