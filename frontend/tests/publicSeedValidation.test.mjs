import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {validateSeed} from '../scripts/validate-public-seed.mjs';

const root=path.resolve(import.meta.dirname,'..');
const seed=JSON.parse(await readFile(path.join(root,'content-seed/audoryn-public.seed.json'),'utf8'));

test('CLI validator accepts the complete captured seed without a development server',async()=>{
 await validateSeed(seed,root);
});
test('CLI validator still rejects incompatible content',async()=>{
 const invalid=structuredClone(seed);
 invalid.pages[0].blocks[0].content.payload.profile='unsupported-profile';
 await assert.rejects(validateSeed(invalid,root));
});
test('CLI validator still rejects media checksum mismatch',async()=>{
 const invalid=structuredClone(seed);
 invalid.media[0].checksumSha256='0'.repeat(64);
 await assert.rejects(validateSeed(invalid,root),/media_changed/);
});
