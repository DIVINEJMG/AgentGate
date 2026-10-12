// Checks the built public site through the Vercel function (api/public-site.mjs), against a local
// storage server holding v2 releases built from the seed. Run after `npm run build`. No network.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,access} from 'node:fs/promises';
import {readSeed,releaseFixture,pageIdOf} from './site-release-fixture.mjs';
import handler from '../../api/public-site.mjs';

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
const settings={PUBLIC_CONTENT_ENABLED:'true',PUBLIC_CONTENT_ORIGIN:origin,PUBLIC_SITE_ORIGIN:'https://public.example',PUBLIC_CONTENT_PREVIEW_ORIGIN:'https://console.example',PUBLIC_CONTENT_POINTER_CACHE_SECONDS:'1'};
const previous=Object.fromEntries(Object.keys(settings).map(key=>[key,process.env[key]]));
Object.assign(process.env,settings);
async function request(url) {
 const result={status:200,headers:{},body:''};
 await handler({url},{set statusCode(value){result.status=value;},setHeader(name,value){result.headers[name]=value;},end(body){result.body=body;}});
 return result;
}
const revalidation=()=>new Promise(resolve=>setTimeout(resolve,1100));
const seed=await readSeed();
try {
 // The SSR template stays private so / always reaches the renderer.
 await assert.rejects(access(new URL('../dist/index.html',import.meta.url)));
 assert.match(await readFile(new URL('../server-dist/template.html',import.meta.url),'utf8'),/assets\//);

 fixture=releaseFixture(origin,1,seed);
 const home=await request('/');assert.equal(home.status,200);assert.match(home.body,/Put AI workers to work/);
 assert.equal(home.headers['Content-Security-Policy'],"frame-ancestors 'none'");assert.equal(home.headers['X-Frame-Options'],'DENY');
 const company=await request('/company');assert.equal(company.status,200);assert.match(company.body,/A practice of turning ideas into working software/);
 const warmRequests=requests;await request('/');assert.equal(requests,warmRequests,'warm HTML reuses verified documents');

 // Runtime CMS update: a new release changes the page without a frontend rebuild.
 const updated=structuredClone(seed);const companyPage=updated.pages.find(page=>page.key==='company');
 companyPage.seo={...companyPage.seo,metaTitle:'Verified runtime CMS update'};companyPage.document.sections.about.paragraphs=['Content changed without a frontend rebuild.'];
 fixture=releaseFixture(origin,2,updated);await revalidation();
 const changed=await request('/company');assert.match(changed.body,/<title>Verified runtime CMS update<\/title>/);assert.match(changed.body,/Content changed without a frontend rebuild/);

 // An article, a redirect, and a page left over from the previous content (v1), which is ignored.
 const article={pageId:'d073a4f4-19ae-41dd-a8bf-39b6a1bcd441',slug:'/resources/local-acceptance',locale:'en',title:'Local acceptance article',contentType:'article',seo:{},
  document:JSON.parse(await readFile(new URL('../src/site/contract/examples/article.valid.json',import.meta.url),'utf8'))};
 const leftover={pageId:'d073a4f4-19ae-41dd-a8bf-39b6a1bcd443',slug:'/content-fields/home',locale:'en',title:'Home copy',contentType:'page',seo:{},document:{contract:'audoryn.public.v1',profile:'copy',entries:[]}};
 fixture=releaseFixture(origin,3,updated,{pages:[...updated.pages,article,leftover],redirects:[{fromPath:'/old-product',toPath:'/product',statusCode:301}]});
 await revalidation();
 const detail=await request(article.slug);assert.equal(detail.status,200);assert.match(detail.body,/Setting up your first supervised worker/);
 assert.equal((await request(leftover.slug)).status,404);
 const redirect=await request('/old-product');assert.equal(redirect.status,301);assert.equal(redirect.headers.Location,'/product');
 const sitemap=await request('/sitemap.xml');assert.equal(sitemap.status,200);assert.match(sitemap.body,/resources\/local-acceptance/);assert.ok(!sitemap.body.includes('content-fields'));
 assert.equal((await request('/unpublished-path')).status,404);
 const application=await request('/app');assert.equal(application.status,200);assert.ok(!application.body.includes('public-content-bootstrap'));
 const preview=await request('/_cms/preview');assert.match(preview.body,/noindex,nofollow/);assert.equal(preview.headers['Content-Security-Policy'],'frame-ancestors https://console.example');
 const reference=await request('/_public/preview-reference?version=3');assert.equal(reference.status,200);assert.equal(JSON.parse(reference.body).release.version,3);
 for(const removed of ['/_public/content?path=/','/_public/config'])assert.equal((await request(removed)).status,404,removed);

 // A page removed from the release is 404, and never comes back from built-in content.
 fixture=releaseFixture(origin,4,updated,{pages:updated.pages.filter(page=>page.key!=='privacy')});await revalidation();
 assert.equal((await request('/privacy')).status,404);
 assert.ok(!JSON.parse(JSON.stringify(fixture.release.resources)).some(item=>item.data.items?.some(entry=>entry.destination===pageIdOf('privacy'))));
 console.log(JSON.stringify({status:'passed',runtimeUpdateWithoutRebuild:true,verifiedRemoval:true,articles:true,previousContentIgnored:true,redirectStatus:301,framePolicy:true,privateTemplate:true,warmRequests:0,storageRequests:requests,transferredBytes:bytes,externalWrites:0},null,2));
}finally{
 for(const [key,value]of Object.entries(previous)){if(value===undefined)delete process.env[key];else process.env[key]=value;}
 await new Promise(resolve=>storage.close(resolve));
}
