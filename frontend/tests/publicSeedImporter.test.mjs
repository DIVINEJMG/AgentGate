import test from 'node:test';
import assert from 'node:assert/strict';
import {SeedImporter} from '../scripts/seed-importer.mjs';
import {seed} from '../scripts/public-content-fixture.mjs';
import {normalizedPayload} from '../scripts/seed-content-comparison.mjs';
function harness(failure){
 const state={pages:[],assets:[],navigations:[],globals:[],releases:[],calls:[],uploads:0};let number=0,failed=false;const receipt={};
 const id=()=>`server-${++number}`;
 const request=async(method,endpoint,body)=>{
  state.calls.push([method,endpoint]);let value;const pathname=endpoint.split('?')[0];
  if(method==='GET'&&['/pages','/assets','/releases'].includes(pathname)){const items=state[pathname.slice(1)];value={items,total:items.length};}
  else if(method==='POST'&&pathname==='/assets/upload-intents'){const asset={id:id(),filename:body.filename,checksumSha256:body.checksum_sha256,status:'uploading'};state.assets.push(asset);value={asset,upload:{url:'https://private.invalid/signed-secret',contentType:body.media_type}};}
  else if(pathname.endsWith('/complete')){state.assets.find(asset=>pathname.includes(asset.id)).status='ready';value={};}
  else if(method==='POST'&&pathname==='/pages'){const page={id:id(),canonicalSlug:body.slug,canonicalTitle:body.title,defaultLocale:body.locale,contentType:body.content_type,revisions:[],publication:null};state.pages.push(page);value={page};}
  else if(pathname.startsWith('/pages/')){const page=state.pages.find(item=>pathname.split('/')[2]===item.id);if(method==='POST'){const revision={...body,id:id(),status:'draft'};page.revisions.push(revision);value={revision};}else value=page;}
  else if(method==='GET'&&pathname==='/navigations')value={items:state.navigations};
  else if(method==='POST'&&pathname==='/navigations'){const navigation={...body,id:id(),items:[]};state.navigations.push(navigation);value={navigation};}
  else if(method==='PUT'&&pathname.startsWith('/navigations/')){const navigation=state.navigations.find(item=>pathname.includes(item.id));navigation.items=body.items.map(item=>({...item,metadata:{...item.metadata,openInNewTab:item.openInNewTab}}));value={navigation};}
  else if(method==='GET'&&pathname==='/global-content')value={items:state.globals};
  else if(method==='PUT'&&pathname.startsWith('/global-content/')){const item={id:id(),key:pathname.split('/')[2],...body};state.globals.push(item);value={item};}
  else if(method==='POST'&&pathname==='/releases'){const release={id:id(),name:body.name,status:'draft',items:[]};state.releases.push(release);value={release};}
  else if(pathname.startsWith('/releases/')){const release=state.releases.find(item=>pathname.includes(item.id));if(method==='POST'){release.items.push({resourceType:body.resource_type,resourceId:body.resource_id});value={};}else value={release,items:release.items};}
  else throw Error('unexpected_endpoint:'+method+':'+endpoint);
  if(!failed&&failure?.(method,pathname)){failed=true;throw Error('lost_acknowledgement');}
  return structuredClone(value);
 };
 const run=()=>new SeedImporter({seed,receipt,request,readMedia:async()=>Buffer.from('fixture'),upload:async()=>{state.uploads++;},checkpoint:async value=>{assert.ok(!JSON.stringify(value).includes('signed-secret'));}}).run();
 return {state,receipt,run};
}
test('draft importer remaps dependencies, prepares a draft, and never publishes',async()=>{
 const h=harness();const result=await h.run();assert.equal(result.publicationPerformed,false);assert.equal(result.status,'draft_prepared');assert.equal(h.state.pages.length,seed.pages.length+seed.records.length);assert.ok(h.state.calls.every(([method,path])=>method==='GET'||!/publish|approve|schedule|activate/.test(path)));
 assert.equal(result.releaseAssembly,'pending_editorial_approval');
 assert.equal(h.receipt.pendingReleaseItems.length,29);
 assert.equal(h.state.releases[0].items.length,0);
 assert.ok(h.state.calls.every(([method,path])=>method!=='POST'||!/^\/releases\/[^/]+\/items$/.test(path)));
 const mutations=h.state.calls.filter(([method])=>method!=='GET').length;await h.run();assert.equal(h.state.calls.filter(([method])=>method!=='GET').length,mutations);assert.equal(h.state.uploads,3);
});
for(const [name,match]of [['page allocation',(method,path)=>method==='POST'&&path==='/pages'],['revision creation',(method,path)=>method==='POST'&&path.endsWith('/revisions')],['navigation creation',(method,path)=>method==='POST'&&path==='/navigations'],['navigation update',(method,path)=>method==='PUT'&&path.endsWith('/items')],['release creation',(method,path)=>method==='POST'&&path==='/releases']]){
 test('lost acknowledgement reconciles '+name,async()=>{const h=harness(match);await assert.rejects(h.run(),/lost_acknowledgement/);await h.run();assert.equal(h.state.pages.length,26);assert.equal(h.state.navigations.length,2);assert.equal(h.state.releases.length,1);assert.equal(new Set(h.state.releases[0].items.map(item=>item.resourceType+item.resourceId)).size,h.state.releases[0].items.length);});
}
test('conflicting draft content and no-longer-draft release stop safely',async()=>{
 const h=harness();await h.run();h.state.pages[0].revisions[0].title='Edited by another editor';await assert.rejects(h.run(),/draft_changed/);
 const other=harness();await other.run();other.state.releases[0].status='published';await assert.rejects(other.run(),/release_no_longer_draft/);
});
test('resume after unapproved release-item rejection preserves all drafts without writes',async()=>{
 const h=harness();await h.run();
 delete h.receipt.status;delete h.receipt.pendingReleaseItems;
 const writes=h.state.calls.filter(([method])=>method!=='GET').length;
 const result=await h.run();
 assert.equal(result.status,'draft_prepared');
 assert.equal(result.releaseAssembly,'pending_editorial_approval');
 assert.equal(h.state.calls.filter(([method])=>method!=='GET').length,writes);
 assert.equal(h.state.releases.length,1);
 assert.equal(h.state.pages.length,26);
 assert.equal(h.receipt.pendingReleaseItems.filter(item=>item.resourceType==='page_revision').length,26);
 assert.equal(new Set(h.receipt.pendingReleaseItems.map(item=>item.resourceType+item.resourceId)).size,29);
});
test('resume accepts contract defaults and PostgreSQL JSON object ordering without writes',async()=>{
 const h=harness();await h.run();
 const reorder=value=>Array.isArray(value)?value.map(reorder):value&&typeof value==='object'?Object.fromEntries(Object.entries(value).reverse().map(([key,item])=>[key,reorder(item)])):value;
 for(const page of h.state.pages)for(const revision of page.revisions){
  for(const block of revision.blocks)block.content.payload=normalizedPayload(block.content.payload);
  revision.blocks=reorder(revision.blocks);revision.seo=reorder(revision.seo);
 }
 for(const global of h.state.globals)global.value=reorder(normalizedPayload(global.value));
 for(const navigation of h.state.navigations)navigation.items=reorder(navigation.items);
 const writes=h.state.calls.filter(([method])=>method!=='GET').length;
 assert.equal((await h.run()).status,'draft_prepared');
 assert.equal(h.state.calls.filter(([method])=>method!=='GET').length,writes);
 const block=h.state.pages[0].revisions[0].blocks[0];
 block.content.payload.sections[0].paragraphs[0]+=' changed';
 await assert.rejects(h.run(),/draft_changed_since_import/);
});
test('navigation resume accepts Console position/id sorting but rejects editorial changes',async()=>{
 const h=harness();await h.run();
 for(const navigation of h.state.navigations)navigation.items.sort((a,b)=>a.position-b.position||a.id.localeCompare(b.id));
 const writes=h.state.calls.filter(([method])=>method!=='GET').length;
 assert.equal((await h.run()).status,'draft_prepared');
 assert.equal(h.state.calls.filter(([method])=>method!=='GET').length,writes);
 const item=h.state.navigations[0].items[0];
 for(const [key,value]of [['position',999],['parentId','another-parent'],['label','Edited label'],['destination','another-destination'],['visible',!item.visible],['openInNewTab',!item.openInNewTab]]){
  const previous=item[key];item[key]=value;
  await assert.rejects(h.run(),/navigation_changed_since_import/);
  item[key]=previous;
 }
 const previous=item.metadata;item.metadata={...previous,description:'Edited description'};
 await assert.rejects(h.run(),/navigation_changed_since_import/);
 item.metadata=previous;
 assert.equal((await h.run()).status,'draft_prepared');
});
