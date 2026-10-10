import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const schema=JSON.parse(readFileSync(new URL('../content-seed/schema.json',import.meta.url),'utf8'));

function normalize(value,shape) {
 if(shape.$ref)return normalize(value,schema.$defs[shape.$ref.split('/').pop()]);
 if(shape.anyOf){
  const choice=shape.anyOf.find(item=>item.$ref&&value!==null||item.type==='null'&&value===null||item.type==='object'&&value&&typeof value==='object'&&!Array.isArray(value));
  return choice?normalize(value,choice):value;
 }
 if(Array.isArray(value))return value.map(item=>normalize(item,shape.items||{}));
 if(value&&typeof value==='object'){
  const result={...value};
  for(const [key,property]of Object.entries(shape.properties||{})){
   if(!(key in result)&&!(shape.required||[]).includes(key)){
    if('default' in property)result[key]=structuredClone(property.default);
    // Pydantic default_factory=list appears as a nonrequired array without
    // a JSON Schema default. Only declared contract arrays get this default.
    else if(property.type==='array')result[key]=[];
   }
   if(key in result)result[key]=normalize(result[key],property);
  }
  return result;
 }
 return value;
}
function canonical(value){
 if(Array.isArray(value))return value.map(canonical);
 if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])]));
 return value;
}
export const contentDigest=value=>createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
export const normalizedPayload=value=>normalize(value,schema);
export function navigationDigest(items){
 const normalized=items.map(item=>{
  const metadata={...item.metadata};
  const openInNewTab=item.openInNewTab??metadata.openInNewTab??false;
  delete metadata.openInNewTab;
  return {id:item.id,parentId:item.parentId??null,label:item.label,
   destinationKind:item.destinationKind,destination:item.destination,
   position:item.position,visible:item.visible,openInNewTab,metadata};
 });
 if(new Set(normalized.map(item=>item.id)).size!==normalized.length)throw Error('duplicate_navigation_identity');
 // API order is position/id across the entire tree; editorial ordering is
 // carried by each item's parent and position, not this response array.
 normalized.sort((left,right)=>left.id<right.id?-1:left.id>right.id?1:0);
 return contentDigest(normalized);
}
export function revisionDigest(revision){
 return contentDigest({locale:revision.locale,title:revision.title,slug:revision.slug,
  blocks:revision.blocks.map(block=>block.type==='PublicContent'?{...block,variant:block.variant??'content',theme:block.theme??'content',content:{...block.content,payload:normalizedPayload(block.content.payload)}}:block),seo:revision.seo});
}
