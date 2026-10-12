// Imports the audoryn.public.v2 seed into a Console as drafts. Never approves, schedules or publishes.
//
//   node scripts/import-site-seed.mjs            offline: verify the seed, no network calls
//   node scripts/import-site-seed.mjs --plan     read Console and report what would change
//   node scripts/import-site-seed.mjs --apply    write the drafts (stops on any conflict first)
//   add --replace-navigation to replace header and footer navigation that was changed in Console
//
// --plan and --apply read CONSOLE_IMPORT_ORIGIN and CONSOLE_IMPORT_TOKEN from the process
// environment only (never from a file). The receipt (content-seed/import-receipt.v2.json, git-ignored)
// records what this importer wrote, so later runs update their own drafts and nothing else.
import {readFile, rename, writeFile} from 'node:fs/promises';
import {createHash, randomUUID} from 'node:crypto';
import path from 'node:path';
import {verifySiteSeed} from './site-seed.mjs';
import {ImportConflict, SiteSeedImporter} from './site-seed-importer.mjs';
import {createConsoleSeedRequest} from './seed-console-request.mjs';

const root = path.resolve(import.meta.dirname, '..');
const seed = await verifySiteSeed(JSON.parse(await readFile(path.join(root, 'content-seed/audoryn-public.v2.seed.json'), 'utf8')));
const flags = new Set(process.argv.slice(2));
const mode = flags.has('--apply') ? 'apply' : flags.has('--plan') ? 'plan' : 'offline';

if (mode === 'offline') {
  console.log(JSON.stringify({mode, seed: seed.checksum.slice(0, 12), pages: seed.pages.length, media: seed.media.length, globals: seed.globals.length, navigations: seed.navigations.length, externalCalls: 0, publicationPerformed: false}, null, 2));
  process.exit(0);
}

const origin = process.env.CONSOLE_IMPORT_ORIGIN;
const token = process.env.CONSOLE_IMPORT_TOKEN;
if (!origin || !token) {
  console.error('Set CONSOLE_IMPORT_ORIGIN and CONSOLE_IMPORT_TOKEN in the process environment.');
  process.exit(1);
}
const url = new URL(origin);
const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
if (url.username || url.password || url.search || url.hash || url.pathname !== '/' || (url.protocol !== 'https:' && !local)) {
  console.error('CONSOLE_IMPORT_ORIGIN must be an https origin (or a local http origin) with no path.');
  process.exit(1);
}

const receiptPath = path.join(root, 'content-seed/import-receipt.v2.json');
let receipt = {};
try {
  receipt = JSON.parse(await readFile(receiptPath, 'utf8'));
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
}
if (receipt.consoleOrigin && receipt.consoleOrigin !== url.origin) {
  console.error('The receipt belongs to another Console. Move it aside to import into this one.');
  process.exit(1);
}
receipt.consoleOrigin = url.origin;
receipt.importId ??= randomUUID();

const send = createConsoleSeedRequest({origin: url.origin, token, importId: receipt.importId});
// Plan mode cannot write, whatever the importer asks for.
const request = (method, endpoint, body) => (mode !== 'apply' && method !== 'GET' ? Promise.reject(new Error('write_in_plan_mode')) : send(method, endpoint, body));

const importer = new SiteSeedImporter({
  seed, request, mode, receipt, replaceNavigation: flags.has('--replace-navigation'),
  checkpoint: async value => {
    await writeFile(receiptPath + '.tmp', JSON.stringify(value, null, 2) + '\n', {mode: 0o600});
    await rename(receiptPath + '.tmp', receiptPath);
  },
  readMedia: async media => {
    const file = path.resolve(root, media.path);
    const bytes = await readFile(file);
    if (bytes.length !== media.sizeBytes || createHash('sha256').update(bytes).digest('hex') !== media.checksumSha256) throw new Error('media_changed');
    return bytes;
  },
  upload: async (intent, bytes) => {
    const response = await fetch(intent.url, {method: 'PUT', headers: {'Content-Type': intent.contentType}, body: bytes, signal: AbortSignal.timeout(60000), redirect: 'error'});
    if (!response.ok) throw new Error('media_upload_failed');
  },
});

try {
  console.log(JSON.stringify(await importer.run(), null, 2));
} catch (error) {
  // Codes, counts and endpoints only: never server messages, tokens or upload URLs.
  const output = error instanceof ImportConflict
    ? {status: 'stopped_before_writing', conflicts: error.conflicts, actions: error.report.actions}
    : {status: 'stopped', category: /^[a-z_0-9]+$/.test(error.message) ? error.message : 'import_transport_failure', ...error.diagnostic, receiptPreserved: mode === 'apply'};
  console.error(JSON.stringify({...output, publicationPerformed: false}, null, 2));
  process.exitCode = 1;
}
