// Writes the audoryn.public.v2 Console seed from the built-in documents.
//   node scripts/build-site-seed.mjs           write content-seed/audoryn-public.v2.seed.json
//   node scripts/build-site-seed.mjs --check   fail if the file is out of date
import {readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {buildSiteSeed} from './site-seed.mjs';

const file = path.resolve(import.meta.dirname, '../content-seed/audoryn-public.v2.seed.json');
const seed = await buildSiteSeed();
const body = JSON.stringify(seed, null, 2) + '\n';
const summary = {pages: seed.pages.length, media: seed.media.length, globals: seed.globals.length, navigationItems: seed.navigations.reduce((n, item) => n + item.items.length, 0), checksum: seed.checksum.slice(0, 12)};

if (process.argv.includes('--check')) {
  const current = await readFile(file, 'utf8').catch(() => '');
  if (current !== body) {console.error('The v2 seed is out of date. Run: npm run site:seed'); process.exit(1);}
  console.log('The v2 seed is current.', JSON.stringify(summary));
} else {
  await writeFile(file, body);
  console.log('Wrote', path.relative(process.cwd(), file), JSON.stringify(summary));
}
