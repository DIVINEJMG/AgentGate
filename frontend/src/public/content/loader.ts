import { copiesFromPages, payloadOf, safeDestination, type ContentBundle, type Page, type Pointer, type PublicConfig, type Release } from './contract';
import { validateCompleteness, validatePage } from './validation';

export const MAX_DOCUMENT_BYTES=4*1024*1024;
export async function sha256(bytes: Uint8Array) {return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes as Uint8Array<ArrayBuffer>)),byte=>byte.toString(16).padStart(2,'0')).join('');}
const reserved=/^\/(api|app|login|signup|workspace-preview|invite|auth|_public|_cms)(\/|$)/;
export function publicPath(value:string) {if(!value.startsWith('/')||value.startsWith('//')||/[\\\u0000-\u001f?#]/.test(value)||reserved.test(value))throw new Error('reserved_or_invalid_path');return value;}

// Caches are bounded per frontend instance. No CMS/Neon requests reach the customer backend.
export class PublicContentLoader {
  private documents=new Map<string,unknown>();
  private documentSizes=new Map<string,number>();
  private cachedBytes=0;
  private pending=new Map<string,Promise<unknown>>();
  private current:{pointer:Pointer;release:Release;etag:string;checked:number}|null=null;
  private pointerRequest:Promise<{pointer:Pointer;release:Release}>|null=null;
  private previous=new Map<string,ContentBundle>();
  readonly metrics={requests:0,bytes:0,cacheHits:0};
  constructor(readonly config:PublicConfig,private fetcher:typeof fetch=fetch,private clock=()=>Date.now()){}
  private address(value:string) {
    const url=new URL(value,this.config.origin);
    const base=new URL(this.config.origin);
    const prefix=base.pathname.replace(/\/$/,'')+'/cms-public/';
    if(url.origin!==base.origin||url.username||url.password||url.search||url.hash||!url.pathname.startsWith(prefix)||/%2f|%5c|%2e/i.test(url.pathname))throw new Error('untrusted_artifact_url');
    return url.href;
  }
  private async bytes(url:string,headers:Record<string,string>={}) {
    this.metrics.requests++;
    const response=await this.fetcher(this.address(url),{headers,signal:AbortSignal.timeout(this.config.timeoutSeconds*1000),redirect:'error'});
    if(response.status===304)return {response,bytes:new Uint8Array()};
    if(!response.ok)throw new Error('public_storage_unavailable');
    if(Number(response.headers.get('content-length')||0)>MAX_DOCUMENT_BYTES)throw new Error('document_size_limit');
    const reader=response.body?.getReader();const chunks:Uint8Array[]=[];let count=0;
    if(!reader)throw new Error('empty_document');
    try{for(;;){const result=await reader.read();if(result.done)break;count+=result.value.length;if(count>MAX_DOCUMENT_BYTES)throw new Error('document_size_limit');chunks.push(result.value);}}finally{await reader.cancel();}
    const bytes=new Uint8Array(count);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}this.metrics.bytes+=count;return {response,bytes};
  }
  private async document<T>(url:string,digest:string):Promise<T> {
    if(!/^[a-f0-9]{64}$/.test(digest))throw new Error('invalid_checksum');
    const key=this.address(url)+'#'+digest;
    if(this.documents.has(key)){this.metrics.cacheHits++;return this.documents.get(key) as T;}
    if(this.pending.has(key))return this.pending.get(key) as Promise<T>;
    const work=(async()=>{const {bytes}=await this.bytes(url);if(await sha256(bytes)!==digest)throw new Error('checksum_mismatch');const value=JSON.parse(new TextDecoder().decode(bytes));this.documents.set(key,value);this.documentSizes.set(key,bytes.length);this.cachedBytes+=bytes.length;while(this.documents.size>128||this.cachedBytes>16*1024*1024){const oldest=this.documents.keys().next().value!;this.cachedBytes-=this.documentSizes.get(oldest)||0;this.documentSizes.delete(oldest);this.documents.delete(oldest);}return value;})();
    this.pending.set(key,work);try{return await work;}finally{this.pending.delete(key);}
  }
  async release() {
    if(this.current&&this.clock()-this.current.checked<this.config.cacheSeconds*1000)return this.current;
    if(this.pointerRequest)return this.pointerRequest;
    const work=(async()=>{
      const {response,bytes}=await this.bytes(this.config.origin+'/cms-public/current.json',this.current?.etag?{'If-None-Match':this.current.etag}:{});
      if(response.status===304&&this.current){this.current.checked=this.clock();return this.current;}
      const pointer=JSON.parse(new TextDecoder().decode(bytes)) as Pointer;
      if(pointer.schemaVersion!==1||!Number.isSafeInteger(pointer.version)||pointer.version<1||!pointer.eventId||!pointer.checksum)throw new Error('invalid_pointer');
      if(this.current&&(pointer.version<this.current.pointer.version||pointer.version===this.current.pointer.version&&pointer.checksum!==this.current.pointer.checksum))throw new Error('stale_release');
      const release=await this.document<Release>(pointer.releaseUrl,pointer.releaseChecksum);
      if(release.schemaVersion!==1||release.version!==pointer.version||release.checksum!==pointer.checksum||!Array.isArray(release.pages)||release.pages.length>512||!Array.isArray(release.resources)||release.resources.length>200||!Array.isArray(release.assets)||release.assets.length>512)throw new Error('invalid_release');
      const paths=new Set<string>();const ids=new Set<string>();
      for(const page of release.pages){publicPath(page.slug);const key=page.locale+':'+page.slug;if(paths.has(key)||ids.has(page.pageId+':'+page.locale))throw new Error('duplicate_release_page');paths.add(key);ids.add(page.pageId+':'+page.locale);this.address(page.url);}
      const assetIds=new Set<string>();
      for(const asset of release.assets){this.address(asset.url);if(assetIds.has(asset.id))throw new Error('duplicate_asset');assetIds.add(asset.id);if(!asset.id||!Number.isSafeInteger(asset.sizeBytes)||asset.sizeBytes<1||asset.sizeBytes>50*1024*1024||!asset.mediaType||!asset.verification?.formatVerified||!/^[a-f0-9]{64}$/.test(asset.checksumSha256)||asset.verification.checksumSha256!==asset.checksumSha256)throw new Error('unverified_media');}
      this.current={pointer,release,etag:response.headers.get('etag')||'',checked:this.clock()};return this.current;
    })();
    this.pointerRequest=work;try{return await work;}finally{this.pointerRequest=null;}
  }
  async load(path:string,signal?:AbortSignal):Promise<ContentBundle|null> {
    publicPath(path);
    if(signal?.aborted)throw new DOMException('Aborted','AbortError');
    const deadline=AbortSignal.timeout(this.config.timeoutSeconds*1000);
    const combined=signal?AbortSignal.any([signal,deadline]):deadline;
    let rejectDeadline:(()=>void)|undefined;
    const expiry=new Promise<never>((_,reject)=>{rejectDeadline=()=>reject(signal?.aborted?new DOMException('Aborted','AbortError'):new DOMException('Public content deadline expired','TimeoutError'));combined.addEventListener('abort',rejectDeadline,{once:true});if(combined.aborted)rejectDeadline();});
    try{return await Promise.race([this.loadWithinBudget(path,combined),expiry]);}
    catch(error){if(signal?.aborted)throw error;console.warn('Public content fallback',{category:'fetch_deadline_expired'});return this.previous.get(this.config.locale+':'+path)||null;}
    finally{if(rejectDeadline)combined.removeEventListener('abort',rejectDeadline);}
  }
  private async loadWithinBudget(path:string,signal:AbortSignal):Promise<ContentBundle|null> {
    publicPath(path);if(!this.config.enabled)return null;
    const key=this.config.locale+':'+path;
    try{
      const {release}=await this.release().catch(error=>{if(this.current){console.warn('Public release retained',{category:'pointer_unavailable_or_rejected'});return this.current;}throw error;});
      if(signal?.aborted)throw new DOMException('Aborted','AbortError');
      const redirect=release.resources.find(item=>item.resourceType==='redirect'&&item.data.locale===this.config.locale&&item.data.fromPath===path&&item.data.active);
      if(redirect){const destination=safeDestination(String(redirect.data.toPath));return {source:'published',page:null,records:[],release,copies:{},redirect:{destination,status:Number(redirect.data.statusCode)===301?301:302}};}
      const selected=release.pages.find(page=>page.slug===path&&page.locale===this.config.locale);
      if(!selected){if(release.pages.some(page=>page.slug===path))throw new Error('unavailable_locale');this.previous.delete(key);return {source:'published',page:null,records:[],release,copies:{},removed:true};}
      const documents=new Map<string,Page>();let totalDocumentBytes=0;
      const read=async(id:string):Promise<void>=>{
        if(signal.aborted)throw new DOMException('Aborted','AbortError');
        if(documents.has(id))return;
        if(documents.size>=100)throw new Error('reference_limit');
        const index=release.pages.find(page=>page.pageId===id&&page.locale===selected.locale);
        if(!index)throw new Error('unresolved_reference');
        const page=await this.document<Page>(index.url,index.checksum);
        totalDocumentBytes+=this.documentSizes.get(this.address(index.url)+'#'+index.checksum)||0;
        if(totalDocumentBytes>8*1024*1024)throw new Error('page_dependency_size_limit');
        if(page.pageId!==index.pageId||page.revisionId!==index.revisionId||page.slug!==index.slug||page.locale!==index.locale||page.rendererContract!==index.rendererContract||page.contentType!==index.contentType)throw new Error('page_identity_mismatch');
        const payload=validatePage(page);documents.set(id,page);
        const references=new Set<string>();
        const walk=(value:unknown)=>{if(Array.isArray(value))value.forEach(walk);else if(value&&typeof value==='object')for(const [name,item]of Object.entries(value)){if(name==='record_ids'&&Array.isArray(item))item.forEach(id=>references.add(String(id)));else walk(item);}};walk(payload);
        for(const reference of references)await read(reference);
      };
      await read(selected.pageId);
      for(const resource of release.resources.filter(item=>item.data.locale===selected.locale)){
        if(resource.resourceType==='global_content'){const refs=(resource.data.value as {entries?:{record_ids?:string[]}[]})?.entries||[];for(const entry of refs)for(const id of entry.record_ids||[])await read(id);}
      }
      const page=documents.get(selected.pageId)!;const records=[...documents.values()].filter(item=>item!==page);
      const globals=release.resources.filter(item=>item.resourceType==='global_content'&&item.data.locale===selected.locale).map(item=>({...page,pageId:item.resourceId,blocks:[{id:item.resourceId,type:'PublicContent',content:{payload:item.data.value as ReturnType<typeof payloadOf>}}]}));
      const bundle:ContentBundle={source:'published',page,records,release,copies:copiesFromPages([...documents.values(),...globals])};validateCompleteness(bundle);
      if(signal?.aborted)throw new DOMException('Aborted','AbortError');
      if(payloadOf(page).profile==='feature')throw new Error('internal_copy_record');
      if(this.current&&this.current.pointer.version>release.version)throw new Error('stale_release');
      this.previous.set(key,bundle);if(this.previous.size>8)this.previous.delete(this.previous.keys().next().value!);
      return bundle;
    }catch(error){if(signal?.aborted)throw error;console.warn('Public content fallback',{category:error instanceof Error&&/^[a-z_]+$/.test(error.message)?error.message:'invalid_document'});return this.previous.get(key)||null;}
  }
}
