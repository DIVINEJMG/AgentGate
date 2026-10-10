import {createHash} from 'node:crypto';
import {contentDigest,normalizedPayload,revisionDigest,navigationDigest} from './seed-content-comparison.mjs';
const digest=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const reason='Import reviewed AgentGate public website baseline as draft content.';
export class SeedImporter {
 constructor({seed,request,upload,readMedia,receipt,checkpoint}){Object.assign(this,{seed,request,upload,readMedia,receipt,checkpoint});}
 async save(){await this.checkpoint(this.receipt);}
 async all(endpoint){const result=[];for(let offset=0;;offset+=100){const page=await this.request('GET',`${endpoint}${endpoint.includes('?')?'&':'?'}limit=100&offset=${offset}`);result.push(...page.items);if(result.length>=page.total||page.items.length<100)return result;if(offset>10000)throw Error('import_inventory_limit');}}
 remap(value){
  if(Array.isArray(value))return value.map(item=>this.remap(item));
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,this.remap(item)]));
  return typeof value==='string'?(this.receipt.ids[value]||value):value;
 }
 async run(){
  const fingerprint=digest(this.seed);if(this.receipt.seedChecksum&&this.receipt.seedChecksum!==fingerprint)throw Error('seed_changed_during_import');
  this.receipt.seedChecksum=fingerprint;this.receipt.ids||={};this.receipt.revisions||={};this.receipt.assets||={};this.receipt.structures||={};await this.save();
  const assets=await this.all('/assets');
  for(const media of this.seed.media){
   let asset=assets.find(item=>item.id===this.receipt.ids[media.id]);
   if(!asset){const matches=assets.filter(item=>item.filename===media.filename&&item.checksumSha256===media.checksumSha256);if(matches.length>1)throw Error('ambiguous_asset_recovery');asset=matches[0];}
   if(!asset){const result=await this.request('POST','/assets/upload-intents',{kind:'image',filename:media.filename,display_name:media.filename,alt_text:media.altText,media_type:media.mediaType,size_bytes:media.sizeBytes,checksum_sha256:media.checksumSha256,width:media.width,height:media.height});asset=result.asset;this.receipt.ids[media.id]=asset.id;await this.save();await this.upload(result.upload,await this.readMedia(media));}
   this.receipt.ids[media.id]=asset.id;await this.save();
   if(asset.status!=='ready'){
    // An interrupted PUT may already have succeeded. Complete verifies the remote bytes first.
    try{await this.request('POST',`/assets/${asset.id}/complete`,{reason});}catch{throw Error('asset_verification_failed_preserved_upload');}
   }
   this.receipt.assets[media.id]=true;await this.save();
  }
  const existing=await this.all('/pages');
  const content=[...this.seed.records,...this.seed.pages];
  // Allocate identities first, then save revisions with the remapped dependency graph.
  for(const page of content){
   let selected=existing.find(item=>item.id===this.receipt.ids[page.pageId]);
   if(!selected){const matches=existing.filter(item=>item.canonicalSlug===page.slug&&item.defaultLocale===page.locale);if(matches.length>1)throw Error('ambiguous_page_recovery');selected=matches[0];}
   if(selected){
    if(selected.contentType!==page.contentType||selected.canonicalTitle!==page.title)throw Error('existing_page_conflict');
    const detail=await this.request('GET',`/pages/${selected.id}?locale=${encodeURIComponent(page.locale)}`);
    if(detail.publication&&!this.receipt.ids[page.pageId])throw Error('existing_published_page_conflict');
    if(!this.receipt.ids[page.pageId]&&detail.revisions.some(revision=>revision.blocks?.length))throw Error('existing_draft_page_conflict');
   }else{const result=await this.request('POST','/pages',{title:page.title,slug:page.slug,locale:page.locale,content_type:page.contentType,reason});selected=result.page;existing.push(selected);}
   this.receipt.ids[page.pageId]=selected.id;await this.save();
  }
  for(const page of content){
   const desired={locale:page.locale,title:page.title,slug:page.slug,blocks:this.remap(page.blocks),seo:this.remap(page.seo)};
   const detail=await this.request('GET',`/pages/${this.receipt.ids[page.pageId]}?locale=${page.locale}`);
   const matching=detail.revisions.find(revision=>revision.status==='draft'&&revisionDigest(revision)===revisionDigest(desired));
   if(matching){this.receipt.revisions[page.pageId]=matching.id;await this.save();continue;}
   if(this.receipt.revisions[page.pageId]||detail.revisions.some(revision=>revision.blocks?.length))throw Error('draft_changed_since_import');
   const result=await this.request('POST',`/pages/${this.receipt.ids[page.pageId]}/revisions`,{...desired,reason});this.receipt.revisions[page.pageId]=result.revision.id;await this.save();
  }
  const navigations=(await this.request('GET','/navigations?locale=en')).items;
  for(const navigation of this.seed.navigations){
   let existing=navigations.find(item=>item.id===this.receipt.structures[navigation.key]||item.key===navigation.key);
   if(existing&&!this.receipt.structures[navigation.key]&&(!this.receipt.structures[navigation.key+':creating']||existing.name!==navigation.name||existing.locale!==navigation.locale||existing.items?.length))throw Error('existing_navigation_conflict');
   if(!existing){this.receipt.structures[navigation.key+':creating']=true;await this.save();existing=(await this.request('POST','/navigations',{key:navigation.key,name:navigation.name,locale:navigation.locale,reason})).navigation;}
   this.receipt.structures[navigation.key]=existing.id;await this.save();
   const desired=this.remap(navigation.items);
   const matches=navigationDigest(existing.items||[])===navigationDigest(desired);
   if((existing.items?.length||this.receipt.structures[navigation.key+':items'])&&!matches)throw Error('navigation_changed_since_import');
   if(!matches)await this.request('PUT',`/navigations/${existing.id}/items`,{items:desired,reason});
   this.receipt.structures[navigation.key+':items']=true;await this.save();
  }
  const globals=(await this.request('GET','/global-content?locale=en')).items;
  for(const global of this.seed.globals){
   const desired=this.remap(global.value);const existing=globals.find(item=>item.key===global.key&&item.locale===global.locale);
   if(existing&&contentDigest(normalizedPayload(existing.value))!==contentDigest(normalizedPayload(desired)))throw Error('existing_global_content_conflict');
   const item=existing||(await this.request('PUT','/global-content/'+global.key,{locale:global.locale,value:desired,reason})).item;this.receipt.structures[global.key]=item.id;await this.save();
  }
  if(!this.receipt.release){
   // A lost create acknowledgement cannot safely be replayed without reconciling the named draft.
   const releases=await this.all('/releases');const name='AgentGate baseline '+fingerprint.slice(0,12);
   const matches=releases.filter(item=>item.name===name);if(matches.length>1||matches[0]&&matches[0].status!=='draft')throw Error('release_reconciliation_conflict');
   const release=matches[0]||(await this.request('POST','/releases',{name,reason})).release;this.receipt.release=release.id;await this.save();
  }
  const detail=await this.request('GET','/releases/'+this.receipt.release);
  if(detail.release?.status!=='draft')throw Error('release_no_longer_draft');
  const expected=new Set([...Object.values(this.receipt.revisions).map(id=>'page_revision:'+id),...this.seed.navigations.map(item=>'navigation:'+this.receipt.structures[item.key]),...this.seed.globals.map(item=>'global_content:'+this.receipt.structures[item.key])]);
  if((detail.items||[]).some(item=>!expected.has(item.resourceType+':'+item.resourceId)))throw Error('unexpected_release_item');
  // Console requires approved page revisions in release items. This importer
  // must not approve content or bypass that gate; keep proposed items locally
  // for the editor and preserve the empty draft release for later assembly.
  this.receipt.pendingReleaseItems=[];
  let position=0;
  for(const [type,ids]of [['page_revision',Object.values(this.receipt.revisions)],['navigation',this.seed.navigations.map(item=>this.receipt.structures[item.key])],['global_content',this.seed.globals.map(item=>this.receipt.structures[item.key])]]){
   for(const id of ids){this.receipt.pendingReleaseItems.push({resourceType:type,resourceId:id,position});position++;}
  }
  this.receipt.status='draft_prepared';await this.save();return {status:'draft_prepared',pages:content.length,assets:this.seed.media.length,releaseId:this.receipt.release,releaseAssembly:'pending_editorial_approval',proposedReleaseItems:this.receipt.pendingReleaseItems.length,publicationPerformed:false};
 }
}
