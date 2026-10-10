import test,{before} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {build} from 'vite';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import react from '@vitejs/plugin-react';
import React from 'react';
import {renderToString} from 'react-dom/server';
const root=path.resolve(import.meta.dirname,'..');
const seed=JSON.parse(readFileSync(path.join(root,'content-seed/audoryn-public.seed.json')));
let contract,validation,Loader,Context,Site,entry,baseline,preview,modelValidation,runtime;
before(async()=>{
 // Compile once without dev-server watchers or module-runner transport. External
 // file URLs keep React identical to the renderer used by these tests.
 const require=createRequire(import.meta.url),id=path.join(root,'cms-test-entry.ts');
 const exports=[['contract','src/public/content/contract.ts'],['validation','src/public/content/validation.ts'],['Context','src/public/content/ContentContext.tsx'],['entry','src/public/entry-server.tsx'],['preview','src/public/content/preview.ts'],['modelValidation','src/public/content/modelValidation.ts'],['runtime','src/public/content/runtime.ts']].map(([name,file])=>`export * as ${name} from ${JSON.stringify(path.join(root,file))};`).join('\n')+
  `export {PublicContentLoader as Loader} from ${JSON.stringify(path.join(root,'src/public/content/loader.ts'))}; export {default as Site} from ${JSON.stringify(path.join(root,'src/public/PublicSite.tsx'))}; export {default as baseline} from ${JSON.stringify(path.join(root,'content-seed/baseline/PublicSite.tsx'))};`;
 const result=await build({root,configFile:false,logLevel:'error',plugins:[
  {name:'cms-test-entry',resolveId(source){if(source===id)return id;},load(source){if(source===id)return exports;}},
  {name:'test-package-identity',enforce:'pre',resolveId(source){if(!source.startsWith('.')&&!source.startsWith('/')&&!source.startsWith('\0')&&!path.isAbsolute(source)&&source!=='cms-test-entry')return {id:source.startsWith('node:')?source:pathToFileURL(require.resolve(source)).href,external:true};}},
  react(),{name:'baseline-assets',resolveId(source,importer){if(importer?.includes('/content-seed/baseline/')&&(source.endsWith('.css')||source.includes('/assets/')||source==='./heroWorld'||source==='./controlWorld'))return path.join(root,'src/public',source==='./heroWorld'||source==='./controlWorld'?source+'.ts':source);}},
 ],build:{ssr:id,write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true}}}});
 const chunk=result.output.find(item=>item.type==='chunk'&&item.isEntry);
 assert.ok(chunk,'CMS test bundle must be present');
 ({contract,validation,Loader,Context,Site,entry,baseline,preview,modelValidation,runtime}=await import('data:text/javascript;base64,'+Buffer.from(chunk.code).toString('base64')));
});
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const configuration=()=>contract.configFromEnvironment({PUBLIC_CONTENT_ENABLED:'true',PUBLIC_CONTENT_ORIGIN:'https://content.example',PUBLIC_SITE_ORIGIN:'https://site.example'});
function fixture(version=1,pages=[...seed.pages,...seed.records]){
 const objects=new Map();const json=value=>Buffer.from(JSON.stringify(value));
 const index=pages.map(page=>{const bytes=json(page);const url=`https://content.example/cms-public/releases/${version}-test/pages/${page.pageId}/en.json`;objects.set(url,bytes);return Object.fromEntries(['pageId','revisionId','locale','slug','title','contentType','rendererContract'].map(key=>[key,page[key]])).valueOf()&&{...Object.fromEntries(['pageId','revisionId','locale','slug','title','contentType','rendererContract'].map(key=>[key,page[key]])),url,checksum:hash(bytes)};});
 const release={schemaVersion:1,version,checksum:'a'.repeat(64),publishedAt:'2026-10-09T00:00:00Z',pages:index,resources:[...seed.navigations.map((navigation,i)=>({resourceType:'navigation',resourceId:'navigation-'+i,data:navigation})),...seed.globals.map(global=>({resourceType:'global_content',resourceId:'global',data:global}))],assets:seed.media.map(media=>({...media,url:'https://content.example/cms-public/assets/'+media.checksumSha256+'/'+media.id,verification:{formatVerified:true,checksumSha256:media.checksumSha256}}))};
 const releaseUrl=`https://content.example/cms-public/releases/${version}-test/release.json`;const bytes=json(release);objects.set(releaseUrl,bytes);objects.set('https://content.example/cms-public/current.json',json({schemaVersion:1,version,eventId:'test',checksum:release.checksum,releaseUrl,releaseChecksum:hash(bytes)}));
 return {objects,release,fetch:async(url,options)=>{const bytes=objects.get(url);if(!bytes)return new Response('',{status:404});if(options?.headers?.['If-None-Match']==='"current"'&&url.endsWith('current.json'))return new Response(null,{status:304});return new Response(bytes,{headers:{etag:'"current"','content-type':'application/json'}});}};
}
test('seed represents nine pages and all records validate the Console contract',()=>{
 assert.equal(seed.pages.length,9);for(const page of [...seed.pages,...seed.records])validation.validatePage(page);
 assert.equal(seed.publicationAllowed,false);assert.equal(seed.media.length,3);assert.ok(seed.navigations.find(item=>item.key==='footer'));
});
test('configuration has no hardcoded production origin and rejects credentialed or unsupported origins',()=>{
 assert.equal(contract.configFromEnvironment({}).enabled,false);assert.equal(contract.configFromEnvironment({}).origin,'');
 assert.equal(contract.configFromEnvironment({PUBLIC_CONTENT_ENABLED:'true'}).enabled,false);
 assert.throws(()=>contract.configFromEnvironment({PUBLIC_CONTENT_ORIGIN:'https://secret@host.example'}),/invalid/);
 assert.throws(()=>contract.configFromEnvironment({PUBLIC_CONTENT_CONTRACT:'unknown'}),/unsupported/);
});
test('loader verifies the complete release and reuses documents without per-component requests',async()=>{
 const data=fixture();const loader=new Loader(configuration(),data.fetch);
 const bundle=await loader.load('/');assert.equal(bundle.source,'published');assert.ok(bundle.records.length>3);
 const count=loader.metrics.requests;await loader.load('/');assert.equal(loader.metrics.requests,count);assert.ok(loader.metrics.cacheHits>0);
 for(const page of seed.pages){const result=await loader.load(page.slug);assert.equal(result.page.pageId,page.pageId);}
 console.log('CMS fixture metrics',JSON.stringify(loader.metrics));
});
test('bucket-path origins load published content and reject sibling bucket URLs',async()=>{
 const data=fixture();const origin='https://content.example/public-bucket';
 const config=contract.configFromEnvironment({PUBLIC_CONTENT_ENABLED:'true',PUBLIC_CONTENT_ORIGIN:origin});
 assert.equal(config.origin,origin);
 const objects=new Map([...data.objects].map(([url,bytes])=>[url.replace('https://content.example',origin),Buffer.from(bytes.toString().replaceAll('https://content.example',origin))]));
 // Recompute checksums after rebasing URLs in release metadata.
 const pointer=JSON.parse(objects.get(origin+'/cms-public/current.json'));
 pointer.releaseChecksum=hash(objects.get(pointer.releaseUrl));
 objects.set(origin+'/cms-public/current.json',Buffer.from(JSON.stringify(pointer)));
 const fetcher=async url=>new Response(objects.get(url)||'',{status:objects.has(url)?200:404});
 const loader=new Loader(config,fetcher);assert.equal((await loader.load('/')).source,'published');
 const bad=JSON.parse(objects.get(origin+'/cms-public/current.json'));bad.releaseUrl=bad.releaseUrl.replace('/public-bucket/','/private-bucket/');
 objects.set(origin+'/cms-public/current.json',Buffer.from(JSON.stringify(bad)));
 assert.equal(await new Loader(config,fetcher).load('/'),null);
});
test('missing origin, malformed documents, checksum corruption and incomplete content use fallback',async()=>{
 assert.equal(await new Loader(contract.configFromEnvironment({}),()=>{throw Error('must_not_call');}).load('/'),null);
 const data=fixture();const home=seed.pages[0];data.objects.set(`https://content.example/cms-public/releases/1-test/pages/${home.pageId}/en.json`,Buffer.from('{}'));
 assert.equal(await new Loader(configuration(),data.fetch).load('/'),null);
 const incomplete=structuredClone([...seed.pages,...seed.records]);incomplete.find(page=>page.slug.startsWith('/content-fields/publicsite-')).blocks[0].content.payload.sections=[];
 assert.equal(await new Loader(configuration(),fixture(1,incomplete).fetch).load('/'),null);
});
test('confirmed removal never resurrects the static page',async()=>{
 const data=fixture(2,[...seed.pages,...seed.records].filter(page=>page.slug!=='/pricing'));const result=await new Loader(configuration(),data.fetch).load('/pricing');assert.equal(result.removed,true);
});
test('stale activation, untrusted URLs, duplicate identities and unsupported contracts are rejected',async()=>{
 const pages=structuredClone([...seed.pages,...seed.records]);pages[0].blocks[0].content.payload.hero_scenes[1].id=pages[0].blocks[0].content.payload.hero_scenes[0].id;assert.equal(await new Loader(configuration(),fixture(1,pages).fetch).load('/'),null);
 const data=fixture();const pointer=JSON.parse(data.objects.get('https://content.example/cms-public/current.json'));pointer.releaseUrl='https://attacker.example/cms-public/release.json';data.objects.set('https://content.example/cms-public/current.json',Buffer.from(JSON.stringify(pointer)));assert.equal(await new Loader(configuration(),data.fetch).load('/'),null);
 for(const pathname of ['/api/x','/app','//evil','/auth'])await assert.rejects(new Loader(configuration(),fixture().fetch).load(pathname),/invalid_path/);
});
test('aborted navigation does not accept a response',async()=>{const controller=new AbortController();controller.abort();await assert.rejects(new Loader(configuration(),fixture().fetch).load('/',controller.signal),/Aborted/);});
test('all nine pages produce matching text for static fallback and the seeded published release',async()=>{
 const loader=new Loader(configuration(),fixture().fetch);
 const text=html=>html.replace(/<[^>]*>/g,'').replace(/\s+/g,' ').trim();
 for(const page of seed.pages){const profile=page.blocks[0].content.payload.profile;const bundle=await loader.load(page.slug);
  const local=renderToString(React.createElement(Context.PublicContentProvider,{bundle:Context.staticBundle},React.createElement(Site,{route:profile,onNavigate:()=>{}})));
  const remote=renderToString(React.createElement(Context.PublicContentProvider,{bundle},React.createElement(Site,{route:profile,onNavigate:()=>{}})));
  assert.equal(text(remote),text(local),profile);
 }
});
test('original captured components match fallback copy',async()=>{
 const text=html=>html.replace(/<[^>]*>/g,'').replace(/\s+/g,' ').trim();
 for(const route of contract.publicRoutes){assert.equal(text(renderToString(React.createElement(Site,{route,onNavigate:()=>{}}))),text(renderToString(React.createElement(baseline,{route,onNavigate:()=>{}}))),route);}
});
test('frontend SSR returns actual public HTML, metadata, 404 and protected application shell',async()=>{
 const template='<html lang="en"><head><title>Old</title></head><body><div id="root"></div></body></html>';
 const env={PUBLIC_SITE_ORIGIN:'https://site.example'};
 const home=await entry.publicResponse({url:'/',env,template});assert.equal(home.status,200);assert.match(home.body,/Give AI workers real work/);assert.match(home.body,/rel="canonical"/);assert.match(home.body,/public-content-bootstrap/);
 const missing=await entry.publicResponse({url:'/absent',env,template});assert.equal(missing.status,404);
 const app=await entry.publicResponse({url:'/app',env,template});assert.equal(app.body,template);
 assert.ok(!entry.bootstrapJson({text:'</script>'}).includes('</script>'));
});

test('conditional pointer refresh retains verified content and rejects an older release',async()=>{
 let now=0;let active=fixture(2);const loader=new Loader(configuration(),(...args)=>active.fetch(...args),()=>now);
 await loader.load('/product');const before=loader.metrics.requests;now=61000;await loader.load('/product');assert.equal(loader.metrics.requests,before+1);
 active=fixture(1);now=122000;const result=await loader.load('/product');assert.equal(result.release.version,2);
});

test('CMS text and reordered solutions change without altering stable selection',async()=>{
 const input=structuredClone([...seed.pages,...seed.records]);const solution=input.find(page=>page.slug==='/solutions');solution.blocks[0].content.payload.solutions.reverse();
 const chosen=solution.blocks[0].content.payload.solutions[1];chosen.label='Reviewed CMS solution';
 const bundle=await new Loader(configuration(),fixture(3,input).fetch).load('/solutions');
 const html=renderToString(React.createElement(Context.PublicContentProvider,{bundle},React.createElement(Site,{route:'solutions',selectedId:chosen.id,onNavigate:()=>{}})));
 assert.match(html,/Reviewed CMS solution/);assert.match(html,new RegExp(chosen.worker));
});

test('wrong-locale references and missing social media reject a complete page set',async()=>{
 const input=structuredClone([...seed.pages,...seed.records]);input.find(page=>page.slug.startsWith('/content-fields/publicsite-')).locale='fr';assert.equal(await new Loader(configuration(),fixture(3,input).fetch).load('/'),null);
 const social=structuredClone([...seed.pages,...seed.records]);social[0].seo.openGraphImageAssetId='missing';assert.equal(await new Loader(configuration(),fixture(3,social).fetch).load('/'),null);
});

test('preview uses the actual pinned Console payload and refuses unresolved dependencies',async()=>{
 const adapt=preview.previewBundle;
 const data=fixture();const payload={revision:{...seed.pages[0],id:seed.pages[0].revisionId},records:seed.records,assets:Object.fromEntries(data.release.assets.map(asset=>[asset.id,asset.url+'?private-token=in-memory-only'])),navigation:seed.navigations,globalContent:seed.globals,pinnedReleaseVersion:1,unresolvedReferences:[],unresolvedAssets:[],previewMode:'content_inspection'};
 const result=adapt(payload,data.release);assert.equal(result.source,'preview');assert.equal(result.release.checksum,'private-preview');
 assert.throws(()=>adapt({...payload,unresolvedReferences:['missing']},data.release),/unresolved_preview/);assert.throws(()=>adapt(payload,{...data.release,version:2}),/preview_release_changed/);
});

test('GLB inspection rejects external resources, malformed bytes and checksum mismatch',async()=>{
 const {validateModel}=modelValidation;
 const model=value=>{let json=JSON.stringify(value);while(json.length%4)json+=' ';const bytes=new ArrayBuffer(20+json.length);const view=new DataView(bytes);for(const [offset,value]of [[0,0x46546c67],[4,2],[8,bytes.byteLength],[12,json.length],[16,0x4e4f534a]])view.setUint32(offset,value,true);new Uint8Array(bytes,20).set(new TextEncoder().encode(json));return bytes;};
 await validateModel(model({asset:{version:'2.0'}}));await assert.rejects(validateModel(new ArrayBuffer(10)),/size/);await assert.rejects(validateModel(model({asset:{version:'2.0'},images:[{uri:'https://untrusted.invalid/image'}]})),/external/);await assert.rejects(validateModel(model({asset:{version:'2.0'}}),'0'.repeat(64)),/checksum/);
});

test('invalid calendar dates, absent legal dates and missing renditions reject publication',async()=>{
 assert.throws(()=>validation.validateSchema('2026-02-31',{type:'string',format:'date'}),/schema_date/);
 const legal=structuredClone(seed.pages.find(page=>page.slug==='/privacy'));delete legal.blocks[0].content.payload.effective_date;
 assert.throws(()=>validation.validatePage(legal),/missing_legal_date/);
 const bundle=await new Loader(configuration(),fixture().fetch).load('/');
 bundle.page.blocks[0].content.payload.hero_scenes[0].media=[{asset_id:seed.media[0].id,role:'image',rendition_asset_ids:['absent']}];
 assert.throws(()=>validation.validateCompleteness(bundle),/missing_media_dependency/);
});

test('published SSR metadata and sitemap respect indexing while fallback keeps its English locale',async()=>{
 const pages=structuredClone([...seed.pages,...seed.records]);pages.find(page=>page.slug==='/privacy').seo.index=false;
 pages[0].seo.metaTitle='CMS title';pages[0].seo.openGraphImageAssetId=seed.media[0].id;
 const data=fixture(6,pages);const previous=globalThis.fetch;globalThis.fetch=data.fetch;
 const template='<html lang="en"><head><title>Old</title></head><body><div id="root"></div></body></html>';
 const env={PUBLIC_CONTENT_ENABLED:'true',PUBLIC_CONTENT_ORIGIN:'https://content.example',PUBLIC_SITE_ORIGIN:'https://seo.example'};
 try {
  const home=await entry.publicResponse({url:'/',env,template});assert.match(home.body,/<title>CMS title<\/title>/);assert.match(home.body,/og:image/);
  const sitemap=await entry.publicResponse({url:'/sitemap.xml',env,template});assert.equal(sitemap.status,200);assert.ok(!sitemap.body.includes('/privacy</loc>'));assert.ok(!sitemap.body.includes('/content-fields/'));
  const fallback=await entry.publicResponse({url:'/',env:{PUBLIC_CONTENT_DEFAULT_LOCALE:'fr'},template});assert.match(fallback.body,/<html lang="en">/);
 }finally{globalThis.fetch=previous;}
});

test('a hanging provider remains bounded by the complete page-fetch deadline',async()=>{
 const config={...configuration(),timeoutSeconds:1};const started=Date.now();
 assert.equal(await new Loader(config,()=>new Promise(()=>{})).load('/'),null);
 assert.ok(Date.now()-started<2500);
});

test('editorial copy cannot turn an existing external link into a script URL',async()=>{
 const pages=structuredClone([...seed.pages,...seed.records]);
 const record=pages.find(page=>page.slug.startsWith('/content-fields/companyshowcase-'));
 record.blocks[0].content.payload.sections.find(section=>section.paragraphs[0]==='https://sot-org.onrender.com/').paragraphs[0]='javascript:alert(1)';
 assert.equal(await new Loader(configuration(),fixture(7,pages).fetch).load('/company'),null);
});

test('the illustrative disclosure is consumed from the typed demonstration',async()=>{
 const pages=structuredClone([...seed.pages,...seed.records]);
 pages.find(page=>page.slug==='/product').blocks[0].content.payload.demonstration.disclosure='Illustrative records; this is not customer activity.';
 const bundle=await new Loader(configuration(),fixture(8,pages).fetch).load('/product');
 const html=renderToString(React.createElement(Context.PublicContentProvider,{bundle},React.createElement(Site,{route:'product',onNavigate:()=>{}})));
 assert.match(html,/Illustrative records; this is not customer activity\./);
});

test('normal paths, legacy hashes and section anchors select the expected public entry',async()=>{
 const {routePath}=runtime;
 for(const [pathname,hash,expected]of [['/product','','/product'],['/','#product','/product'],['/','#/solutions','/solutions'],['/','#/login','/login'],['/privacy','#legal-information','/privacy']])assert.equal(routePath({pathname,hash,search:''}),expected);
});

test('every seeded navigation anchor and selection exists in its published destination',()=>{
 for(const navigation of seed.navigations)for(const item of navigation.items){
  const target=seed.pages.find(page=>page.pageId===item.destination&&page.locale===navigation.locale);assert.ok(target);
  const payload=target.blocks[0].content.payload;
  if(item.metadata?.sectionId)assert.ok(payload.sections.some(section=>section.id===item.metadata.sectionId));
  if(item.metadata?.selectedRecordId)assert.ok(Object.values(payload).some(value=>Array.isArray(value)&&value.some(entry=>entry?.id===item.metadata.selectedRecordId)));
 }
});
