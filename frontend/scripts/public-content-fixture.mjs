import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
export const seed=JSON.parse(readFileSync(new URL('../content-seed/audoryn-public.seed.json',import.meta.url)));
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
export function releaseFixture(origin,version=1,input=seed) {
 const objects=new Map();const encode=value=>Buffer.from(JSON.stringify(value));
 const pages=[...input.pages,...input.records].map(page=>{const bytes=encode(page);const url=`${origin}/cms-public/releases/${version}-fixture/pages/${page.pageId}/en.json`;objects.set(new URL(url).pathname,bytes);return {...Object.fromEntries(['pageId','revisionId','locale','slug','title','contentType','rendererContract'].map(key=>[key,page[key]])),url,checksum:hash(bytes)};});
 const release={schemaVersion:1,version,checksum:hash(encode(input)),publishedAt:'2026-10-09T00:00:00Z',pages,resources:[...input.navigations.map((data,index)=>({resourceType:'navigation',resourceId:'navigation-'+index,data})),...input.globals.map(data=>({resourceType:'global_content',resourceId:data.id,data}))],assets:input.media.map(media=>({...media,url:`${origin}/cms-public/assets/${media.checksumSha256}/${media.filename}`,verification:{formatVerified:true,checksumSha256:media.checksumSha256}}))};
 const releaseUrl=`${origin}/cms-public/releases/${version}-fixture/release.json`;const bytes=encode(release);objects.set(new URL(releaseUrl).pathname,bytes);objects.set('/cms-public/current.json',encode({schemaVersion:1,version,eventId:'fixture',checksum:release.checksum,releaseUrl,releaseChecksum:hash(bytes)}));
 return {objects,release};
}
