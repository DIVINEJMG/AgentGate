import test from 'node:test';
import assert from 'node:assert/strict';
import {createConsoleSeedRequest} from '../scripts/seed-console-request.mjs';

test('draft mutation keys survive client recreation and distinguish operations',async()=>{
 const calls=[];
 const config={origin:'http://localhost:8001',token:'private-token',importId:'saved-import-id',fetchImpl:async(url,options)=>{
  calls.push({url,...options});
  if(options.method!=='GET')assert.match(options.headers['Idempotency-Key'],/^seed-[a-f0-9]{64}$/);
  return Response.json({ok:true});
 }};
 const request=createConsoleSeedRequest(config);
 await request('POST','/assets/upload-intents',{filename:'first.webp'});
 await createConsoleSeedRequest({...config,token:'renewed-token'})('POST','/assets/upload-intents',{filename:'first.webp'});
 await request('POST','/assets/upload-intents',{filename:'second.webp'});
 await request('PUT','/navigations/one/items',{items:[]});
 await request('GET','/assets?limit=100&offset=0');
 const key=index=>calls[index].headers['Idempotency-Key'];
 assert.equal(key(0),key(1));
 assert.notEqual(key(0),key(2));
 assert.notEqual(key(0),key(3));
 assert.equal(key(4),undefined);
 assert.equal(calls[0].url,'http://localhost:8001/api/v1/cms/assets/upload-intents');
 assert.equal(calls[0].body,JSON.stringify({filename:'first.webp'}));
});
test('draft request adapter still forbids publication and requires an import identity',async()=>{
 let dispatched=0;
 const request=createConsoleSeedRequest({origin:'http://localhost:8001',token:'private',fetchImpl:async()=>{dispatched++;return Response.json({});}});
 await assert.rejects(request('POST','/releases/one/publish',{}),/publication_forbidden/);
 await assert.rejects(request('POST','/pages',{}),/import_identity_required/);
 assert.equal(dispatched,0);
});
test('request adapter retains sanitized server rejection diagnostics',async()=>{
 const request=createConsoleSeedRequest({origin:'http://localhost:8001',token:'private',importId:'saved',fetchImpl:async()=>Response.json({error:{code:'idempotency_unavailable'}},{status:503})});
 await assert.rejects(request('POST','/pages',{}),error=>error.diagnostic.serverCode==='idempotency_unavailable'&&error.diagnostic.endpoint==='/api/v1/cms/pages');
});
