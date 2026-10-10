import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,access} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {seed,releaseFixture} from './public-content-fixture.mjs';
import handler from '../../api/public-site.mjs';

const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
let fixture;
let requests=0;
let bytes=0;
const storage=createServer((request,response)=>{
 const document=fixture.objects.get(new URL(request.url,'http://localhost').pathname);
 if(!document){response.writeHead(404);response.end();return;}
 requests++;bytes+=document.length;
 response.setHeader('Content-Type','application/json');response.end(document);
});
await new Promise(resolve=>storage.listen(0,'127.0.0.1',resolve));
const origin='http://127.0.0.1:'+storage.address().port;
const settings={PUBLIC_CONTENT_ENABLED:'true',PUBLIC_CONTENT_ORIGIN:origin,PUBLIC_SITE_ORIGIN:'https://public.example',PUBLIC_CONTENT_POINTER_CACHE_SECONDS:'1'};
const previous=Object.fromEntries(Object.keys(settings).map(key=>[key,process.env[key]]));
Object.assign(process.env,settings);
async function request(url) {
 const result={status:200,headers:{},body:''};
 await handler({url},{set statusCode(value){result.status=value;},setHeader(name,value){result.headers[name]=value;},end(body){result.body=body;}});
 return result;
}
const revalidation=()=>new Promise(resolve=>setTimeout(resolve,1100));
try {
 await assert.rejects(access(new URL('../dist/index.html',import.meta.url)));
 assert.match(await readFile(new URL('../server-dist/template.html',import.meta.url),'utf8'),/assets\//);
 fixture=releaseFixture(origin);
 const home=await request('/');assert.equal(home.status,200);assert.match(home.body,/Give AI workers real work/);
 const warmRequests=requests;await request('/');assert.equal(requests,warmRequests,'warm HTML reuses verified documents');

 const updated=structuredClone(seed);updated.pages[0].seo.metaTitle='Verified runtime CMS update';
 updated.pages[0].blocks[0].content.payload.hero_scenes[0].description='Content changed without a frontend rebuild.';
 fixture=releaseFixture(origin,2,updated);await revalidation();
 const changed=await request('/');assert.match(changed.body,/<title>Verified runtime CMS update<\/title>/);assert.match(changed.body,/Content changed without a frontend rebuild/);

 const article=structuredClone(updated.pages.find(page=>page.slug==='/resources'));
 article.pageId='d073a4f4-19ae-41dd-a8bf-39b6a1bcd441';article.revisionId='d073a4f4-19ae-41dd-a8bf-39b6a1bcd442';article.slug='/resources/local-acceptance';article.title='Local acceptance article';article.contentType='article';
 article.blocks[0].content.payload={contract:'audoryn.public.v1',profile:'article',sections:[{id:'intro',heading:'A verified article',paragraphs:['Fixture content, not an invented public article.']}],entries:article.blocks[0].content.payload.entries.filter(entry=>entry.label==='copy:PublicSite')};
 updated.pages.push(article);
 fixture=releaseFixture(origin,3,updated);
 fixture.release.resources.push({resourceType:'redirect',resourceId:'local-redirect',data:{locale:'en',fromPath:'/old-product',toPath:'/product',statusCode:301,active:true}});
 const releasePath=new URL(JSON.parse(fixture.objects.get('/cms-public/current.json')).releaseUrl).pathname;
 const releaseBytes=Buffer.from(JSON.stringify(fixture.release));fixture.objects.set(releasePath,releaseBytes);
 const pointer=JSON.parse(fixture.objects.get('/cms-public/current.json'));pointer.releaseChecksum=hash(releaseBytes);fixture.objects.set('/cms-public/current.json',Buffer.from(JSON.stringify(pointer)));
 await revalidation();
 const detail=await request(article.slug);assert.equal(detail.status,200);assert.match(detail.body,/A verified article/);
 const redirect=await request('/old-product');assert.equal(redirect.status,301);assert.equal(redirect.headers.Location,'/product');
 const sitemap=await request('/sitemap.xml');assert.equal(sitemap.status,200);assert.match(sitemap.body,/resources\/local-acceptance/);assert.ok(!sitemap.body.includes('content-fields'));
 assert.equal((await request('/unpublished-path')).status,404);
 const application=await request('/app');assert.equal(application.status,200);assert.ok(!application.body.includes('public-content-bootstrap'));
 assert.match((await request('/_cms/preview')).body,/noindex,nofollow/);

 updated.pages=updated.pages.filter(page=>page.slug!=='/privacy');
 updated.navigations.forEach(navigation=>{navigation.items=navigation.items.filter(item=>item.destination!==seed.pages.find(page=>page.slug==='/privacy').pageId);});
 fixture=releaseFixture(origin,4,updated);await revalidation();assert.equal((await request('/privacy')).status,404);
 console.log(JSON.stringify({status:'passed',runtimeUpdateWithoutRebuild:true,verifiedRemoval:true,resourceDetails:true,redirectStatus:301,privateTemplate:true,warmRequests:0,storageRequests:requests,transferredBytes:bytes,externalWrites:0},null,2));
}finally{
 for(const [key,value]of Object.entries(previous)){if(value===undefined)delete process.env[key];else process.env[key]=value;}
 await new Promise(resolve=>storage.close(resolve));
}
