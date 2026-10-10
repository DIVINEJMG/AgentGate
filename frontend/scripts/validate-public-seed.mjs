import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {build} from 'vite';

let validatorPromise;
function loadValidator(root) {
 // Bundle the shared validator directly: CLI validation needs no dev server,
 // watcher, or SSR module-runner transport.
 return validatorPromise ??= build({
  root,configFile:false,logLevel:'silent',
  build:{ssr:path.join(root,'src/public/content/validation.ts'),write:false,minify:false,
   rollupOptions:{output:{format:'es',inlineDynamicImports:true}}},
 }).then(result=>{
  const chunk=result.output.find(item=>item.type==='chunk'&&item.isEntry);
  if(!chunk)throw Error('seed_validator_bundle_missing');
  return import('data:text/javascript;base64,'+Buffer.from(chunk.code).toString('base64'));
 }).catch(error=>{validatorPromise=undefined;throw error;});
}

export async function validateSeed(seed,root) {
  const {validatePage,validateSchema}=await loadValidator(root);
  for(const page of [...seed.pages,...seed.records])validatePage(page);
  for(const global of seed.globals)validateSchema(global.value);
  const identities=[...seed.pages,...seed.records].map(page=>page.pageId);
  if(new Set(identities).size!==identities.length)throw Error('duplicate_seed_identity');
  for(const navigation of seed.navigations)for(const item of navigation.items){
   if(!item.visible||item.destinationKind!=='page')continue;
   const page=[...seed.pages,...seed.records].find(page=>page.pageId===item.destination&&page.locale===navigation.locale);
   if(!page)throw Error('unresolved_seed_navigation');
   const payload=page.blocks[0].content.payload;
   if(item.metadata?.sectionId&&!payload.sections?.some(section=>section.id===item.metadata.sectionId))throw Error('unresolved_seed_anchor');
   const selected=item.metadata?.selectedRecordId;
   if(selected&&!Object.values(payload).some(value=>Array.isArray(value)&&value.some(entry=>entry?.id===selected)))throw Error('unresolved_seed_selection');
  }
  for(const media of seed.media){
   const file=path.resolve(root,media.path);
   if(!file.startsWith(root+path.sep))throw Error('unsafe_media_path');
   const bytes=await readFile(file);
   if(bytes.length!==media.sizeBytes||createHash('sha256').update(bytes).digest('hex')!==media.checksumSha256)throw Error('media_changed');
  }
}
