// Read-only acceptance through the website's actual loader and environment.
import {build, loadEnv} from 'vite';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const result=await build({root,configFile:false,logLevel:'error',build:{write:false,minify:false,ssr:path.join(root,'src/public/content/loader.ts'),rollupOptions:{output:{format:'es'}}}});
const chunk=result.output.find(item=>item.type==='chunk'&&item.isEntry);
const {PublicContentLoader}=await import('data:text/javascript;base64,'+Buffer.from(chunk.code).toString('base64'));
const env={...loadEnv('development',root,''),...process.env};
const config={enabled:true,origin:env.PUBLIC_CONTENT_ORIGIN,siteOrigin:env.PUBLIC_SITE_ORIGIN||'',contract:'audoryn.public.v1',locale:'en',cacheSeconds:60,timeoutSeconds:Number(env.PUBLIC_CONTENT_FETCH_TIMEOUT_SECONDS||5),previewOrigin:''};
if(!config.origin)throw Error('PUBLIC_CONTENT_ORIGIN is required');
const loader=new PublicContentLoader(config);
const pages=[];const started=performance.now();
for(const route of ['/','/product','/solutions','/security','/pricing','/resources','/company','/privacy','/terms']){
 const bundle=await loader.load(route);
 if(bundle?.source!=='published')throw Error('Published content verification failed for '+route);
 pages.push({route,version:bundle.release.version,source:bundle.source});
}
const before=loader.metrics.requests;
await loader.load('/');
console.log(JSON.stringify({status:'verified',pages,metrics:loader.metrics,warmRequests:loader.metrics.requests-before,elapsedMs:Math.round(performance.now()-started)},null,2));
