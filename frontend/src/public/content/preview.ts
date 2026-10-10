import {copiesFromPages,CONTRACT,type ContentBundle,type Page,type Release} from './contract';
import {validateCompleteness,validatePage} from './validation';

export function previewBundle(data:unknown,publishedRelease?:Release):ContentBundle {
 if(!data||typeof data!=='object')throw Error('invalid_preview');
 const value=data as Record<string,unknown>;
 if((value.unresolvedReferences as unknown[])?.length||(value.unresolvedAssets as unknown[])?.length)throw Error('unresolved_preview_dependencies');
 const revision=value.revision as Record<string,unknown>;
 if(!revision||value.previewMode!=='content_inspection')throw Error('unsupported_preview_contract');
 const make=(source:Record<string,unknown>):Page=>{
  const blocks=source.blocks as Page['blocks'];
  const profile=blocks?.[0]?.content?.payload?.profile;
  return {...source,pageId:source.pageId as string,revisionId:(source.revisionId||source.id) as string,contentType:(source.contentType||profile) as string,rendererContract:CONTRACT} as Page;
 };
 const page=make(revision);const records=((value.records||[]) as Record<string,unknown>[]).map(make);[page,...records].forEach(validatePage);
 const assets=Object.entries((value.assets||{}) as Record<string,string>).map(([id,url])=>{
  const parsed=new URL(url);if(parsed.protocol!=='https:'||parsed.username||parsed.password)throw Error('invalid_private_media_url');
  const published=publishedRelease?.assets.find(asset=>asset.id===id);
  return {id,url,mediaType:published?.mediaType||'application/octet-stream',checksumSha256:published?.checksumSha256||'',sizeBytes:published?.sizeBytes||0,altText:published?.altText,verification:{formatVerified:Boolean(published),checksumSha256:published?.checksumSha256||''}};
 });
 const resources:Release['resources']=[];
 for(const navigation of (value.navigation||[]) as Record<string,unknown>[])resources.push({resourceType:'navigation',resourceId:String(navigation.id),data:navigation});
 for(const global of (value.globalContent||[]) as Record<string,unknown>[])resources.push({resourceType:'global_content',resourceId:String(global.id),data:global});
 if(publishedRelease&&publishedRelease.version!==Number(value.pinnedReleaseVersion))throw Error('preview_release_changed');
 const indexes=new Map((publishedRelease?.pages||[]).map(item=>[item.pageId,item]));
 for(const item of [page,...records])indexes.set(item.pageId,{...item,url:'',checksum:''});
 const release:Release={schemaVersion:1,version:Number(value.pinnedReleaseVersion||0),checksum:'private-preview',publishedAt:'',pages:[...indexes.values()],resources,assets};
 const globals=resources.filter(item=>item.resourceType==='global_content').map(item=>({...page,pageId:item.resourceId,blocks:[{id:item.resourceId,type:'PublicContent',content:{payload:item.data.value as Page['blocks'][0]['content']['payload']}}]}));
 const result:ContentBundle={source:'preview',page,records,release,copies:copiesFromPages([page,...records,...globals])};validateCompleteness(result);return result;
}
