import {renderToString} from 'react-dom/server';
import ScrollControl from '../components/ScrollControl';
import {PublicExperience} from './PublicExperience';
import {configFromEnvironment,publicRoutes,type PublicConfig,type ContentBundle,type PublicRoute} from './content/contract';
import {PublicContentLoader} from './content/loader';
let loader:PublicContentLoader|undefined;
let signature='';
const renderCache=new Map<string,string>();
export function runtime(env:Record<string,string|undefined>) {
 const config=configFromEnvironment(env);const next=JSON.stringify(config);
 if(!loader||signature!==next){signature=next;loader=new PublicContentLoader(config);renderCache.clear();}
 return {config,loader};
}
const escape=(value:unknown)=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]!));
export const bootstrapJson=(value:unknown)=>JSON.stringify(value).replace(/</g,'\\u003c').replace(/\u2028/g,'\\u2028').replace(/\u2029/g,'\\u2029');
export async function publicResponse({url,env,template}:{url:string;env:Record<string,string|undefined>;template:string}) {
 const {config,loader}=runtime(env);const request=new URL(url,'http://localhost');const path=request.pathname.replace(/\/$/,'')||'/';
 const headers:Record<string,string>={'Cache-Control':'no-store','Content-Type':'text/html; charset=utf-8'};
 if(path==='/_public/config')return {status:200,headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(config)};
 if(path==='/_public/preview-reference'){
  const reference=config.enabled?await loader.release().catch(()=>null):null;
  return {status:reference&&reference.release.version===Number(request.searchParams.get('version'))?200:409,headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({release:reference?.release||null})};
 }
 if(path==='/_public/content') {
  try{const bundle=await loader.load(request.searchParams.get('path')||'/');return {status:200,headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({bundle})};}catch{return {status:400,headers,body:'Invalid public content request'};}
 }
 if(path==='/sitemap.xml') {
  const release=config.enabled?await loader.release().catch(()=>null):null;
  const candidates=release?release.release.pages.filter(page=>!page.slug.startsWith('/content-fields/')&&!['feature','integration','customer','testimonial','author','category','tag','faq','announcement'].includes(page.contentType)&&page.locale===config.locale):[];
  const paths:string[]=[];
  if(release){
   const started=Date.now();
   for(let offset=0;offset<candidates.length;offset+=4){
    if(Date.now()-started>config.timeoutSeconds*1000)return {status:503,headers,body:'Sitemap verification unavailable.'};
    const batch=await Promise.all(candidates.slice(offset,offset+4).map(page=>loader.load(page.slug)));
    if(batch.some(bundle=>!bundle?.page||bundle.release?.checksum!==release.release.checksum))return {status:503,headers,body:'Sitemap verification unavailable.'};
    for(const bundle of batch)if(bundle!.page!.seo.index!==false)paths.push(bundle!.page!.slug);
   }
  }else paths.push(...publicRoutes.map(route=>route==='home'?'/':'/'+route));
  const body=`<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${paths.map(path=>`<url><loc>${escape(config.siteOrigin+path)}</loc></url>`).join('')}</urlset>`;
  return {status:config.siteOrigin?200:503,headers:{...headers,'Content-Type':'application/xml'},body};
 }
 if(path==='/robots.txt')return {status:200,headers:{...headers,'Content-Type':'text/plain'},body:`User-agent: *\nDisallow: /_cms/\nDisallow: /app\nDisallow: /login\nDisallow: /signup\n${config.siteOrigin?'Sitemap: '+config.siteOrigin+'/sitemap.xml\n':''}`};
 const reserved=/^\/(api|app|login|signup|workspace-preview|invite|auth)(\/|$)/.test(path);
 if(reserved)return {status:200,headers,body:template};
 const bundle=path==='/_cms/preview'?null:await loader.load(path);
 if(bundle?.redirect)return {status:bundle.redirect.status,headers:{...headers,Location:bundle.redirect.destination},body:''};
 const known=publicRoutes.includes((path==='/'?'home':path.slice(1)) as PublicRoute);
 const status=bundle?.removed||!known&&!bundle?.page&&path!=='/_cms/preview'?404:200;
 const seo=bundle?.page?.seo||{};
 const title=seo.metaTitle||(path==='/'?'Audoryn · AI workforce, under control':known?path.slice(1)[0].toUpperCase()+path.slice(2)+' · Audoryn':'Page not found · Audoryn');
 const canonicalCandidate=new URL(String(seo.canonicalUrl||path),config.siteOrigin||'http://localhost');
 const canonical=canonicalCandidate.origin===(config.siteOrigin||'http://localhost')?canonicalCandidate.href:config.siteOrigin+path;
 const description=seo.metaDescription||'Audoryn is an SOT product for creating, operating and governing an AI workforce with explicit human control.';
 const image=bundle?.release?.assets.find(asset=>asset.id===seo.openGraphImageAssetId);
 const head=`<title>${escape(title)}</title><meta name="description" content="${escape(description)}"/>${config.siteOrigin?`<link rel="canonical" href="${escape(canonical)}"/><meta property="og:url" content="${escape(canonical)}"/>`:''}<meta property="og:title" content="${escape(seo.openGraphTitle||title)}"/><meta property="og:description" content="${escape(seo.openGraphDescription||description)}"/>${image?`<meta property="og:image" content="${escape(image.url)}"/><meta name="twitter:card" content="summary_large_image"/>`:''}<meta name="robots" content="${status===404||path==='/_cms/preview'||seo.index===false?'noindex,nofollow':'index,follow'}"/>${seo.structuredData?`<script type="application/ld+json">${bootstrapJson(seo.structuredData)}</script>`:''}`;
 const selectedId=request.searchParams.get('selected')||undefined;
 const renderKey=path+':'+(selectedId||'')+':'+config.locale+':'+(bundle?.release?.checksum||'static');
 let body=renderCache.get(renderKey);
 if(!body){body=renderPublic(path,config,bundle,selectedId);renderCache.set(renderKey,body);if(renderCache.size>32)renderCache.delete(renderCache.keys().next().value!);}
 const bootstrap=`<script id="public-content-bootstrap" type="application/json">${bootstrapJson({path,config,bundle})}</script>`;
 return {status,headers,body:template.replace(/<title>.*?<\/title>/s,'').replace(/<meta name="description"[^>]*>/,'').replace('</head>',head+'</head>').replace(/<html([^>]*)lang="[^"]*"/,'<html$1lang="'+escape(bundle?.page?.locale||'en')+'"').replace('<div id="root"></div>',`<div id="root">${body}</div>${bootstrap}`)};
}
export function renderPublic(path:string,config:PublicConfig,bundle:ContentBundle|null,selectedId?:string) {return renderToString(<><PublicExperience path={path} config={config} initialBundle={bundle} selectedId={selectedId} onNavigate={()=>{}}/><ScrollControl/></>);}
