import {readFile,writeFile,rename} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash,randomUUID} from 'node:crypto';
import {SeedImporter} from './seed-importer.mjs';
import {validateSeed} from './validate-public-seed.mjs';
import {createConsoleSeedRequest} from './seed-console-request.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const seed=JSON.parse(await readFile(path.join(root,'content-seed/audoryn-public.seed.json'),'utf8'));
if(seed.publicationAllowed!==false||seed.status!=='draft')throw Error('unsafe_seed');
await validateSeed(seed,root);
if(!process.argv.includes('--apply')){
 console.log(JSON.stringify({mode:'dry_run',pages:seed.pages.length,records:seed.records.length,media:seed.media.length,navigations:seed.navigations.length,globals:seed.globals.length,externalCalls:0,publicationPerformed:false},null,2));
}else{
 const origin=process.env.CONSOLE_IMPORT_ORIGIN;const token=process.env.CONSOLE_IMPORT_TOKEN;
 if(!origin||!token)throw Error('Set CONSOLE_IMPORT_ORIGIN and CONSOLE_IMPORT_TOKEN in the process environment.');
 const url=new URL(origin);if(url.username||url.password||url.search||url.hash||url.pathname!=='/'||url.protocol!=='https:'&&!['localhost','127.0.0.1'].includes(url.hostname))throw Error('invalid_console_origin');
 const receiptPath=path.join(root,'content-seed/import-receipt.json');let receipt={};
 try{receipt=JSON.parse(await readFile(receiptPath,'utf8'));}catch(error){if(error.code!=='ENOENT')throw error;}
 if(receipt.consoleOrigin&&receipt.consoleOrigin!==url.origin)throw Error('receipt_belongs_to_another_console');
 receipt.consoleOrigin=url.origin;
 // SeedImporter saves this receipt before its first API request, including
 // when upgrading an existing receipt created before idempotency support.
 receipt.importId||=randomUUID();
 const request=createConsoleSeedRequest({origin:url.origin,token,importId:receipt.importId});
 const importer=new SeedImporter({seed,receipt,request,readMedia:async media=>{
  const file=path.resolve(root,media.path);if(!file.startsWith(root+path.sep))throw Error('unsafe_media_path');const bytes=await readFile(file);if(bytes.length!==media.sizeBytes||createHash('sha256').update(bytes).digest('hex')!==media.checksumSha256)throw Error('media_changed');return bytes;
 },upload:async(intent,bytes)=>{const response=await fetch(intent.url,{method:'PUT',headers:{'Content-Type':intent.contentType},body:bytes,signal:AbortSignal.timeout(60000),redirect:'error'});if(!response.ok)throw Error('media_upload_failed');},checkpoint:async value=>{await writeFile(receiptPath+'.tmp',JSON.stringify(value,null,2)+'\n',{mode:0o600});await rename(receiptPath+'.tmp',receiptPath);}});
 try{console.log(JSON.stringify(await importer.run(),null,2));}catch(error){console.error(JSON.stringify({status:'stopped',category:/^[a-z_0-9]+$/.test(error.message)?error.message:'import_transport_failure',...error.diagnostic,receiptPreserved:true,publicationPerformed:false}));process.exitCode=1;}
}
