import schema from '../../../content-seed/schema.json';
import baseline from '../../../content-seed/static-copy.json';
import bindings from '../../../content-seed/collection-bindings.json';
import { CONTRACT, payloadOf, safeDestination, type ContentBundle, type Page } from './contract';
import {headerNavigation} from './navigation';

type Schema = { $ref?:string;anyOf?:Schema[];enum?:unknown[];const?:unknown;type?:string;properties?:Record<string,Schema>;required?:string[];additionalProperties?:boolean;items?:Schema;minItems?:number;maxItems?:number;minLength?:number;maxLength?:number;pattern?:string;format?:string;minimum?:number;maximum?:number;exclusiveMinimum?:number;$defs?:Record<string,Schema> };
export function validateSchema(value: unknown, shape: Schema = schema as Schema, depth = 0): void {
  if (depth > 24) throw new Error('content_depth_limit');
  if (shape.$ref) return validateSchema(value,(schema as Schema).$defs![shape.$ref.split('/').pop()!],depth+1);
  if (shape.anyOf) {for(const alternative of shape.anyOf){try{validateSchema(value,alternative,depth+1);return;}catch{/* try the next declared alternative */}}throw new Error('schema_union');}
  if (shape.const !== undefined && value !== shape.const || shape.enum && !shape.enum.includes(value)) throw new Error('schema_enum');
  if (shape.type === 'object') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('schema_object');
    const object=value as Record<string,unknown>;
    if (Object.keys(object).length > 256) throw new Error('schema_object_limit');
    for(const key of shape.required || [])if(!(key in object))throw new Error('schema_required');
    for(const [key,item]of Object.entries(object)){if(shape.properties?.[key])validateSchema(item,shape.properties[key],depth+1);else if(shape.additionalProperties===false)throw new Error('schema_unknown_field');}
  } else if(shape.type==='array') {
    if(!Array.isArray(value)||value.length<(shape.minItems||0)||value.length>(shape.maxItems??512))throw new Error('schema_array');
    if(shape.items)for(const item of value)validateSchema(item,shape.items,depth+1);
  } else if(shape.type==='string') {
    if(typeof value!=='string'||value.length<(shape.minLength||0)||value.length>(shape.maxLength??10000)||shape.pattern&&!new RegExp(shape.pattern).test(value))throw new Error('schema_string');
    if(shape.format==='uuid'&&!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value))throw new Error('schema_uuid');
    if(shape.format==='date'&&(!/^\d{4}-\d{2}-\d{2}$/.test(value)||!Number.isFinite(Date.parse(value))||new Date(value).toISOString().slice(0,10)!==value))throw new Error('schema_date');
  } else if(shape.type==='number'||shape.type==='integer') {
    if(typeof value!=='number'||!Number.isFinite(value)||shape.type==='integer'&&!Number.isInteger(value)||shape.minimum!==undefined&&value<shape.minimum||shape.maximum!==undefined&&value>shape.maximum||shape.exclusiveMinimum!==undefined&&value<=shape.exclusiveMinimum)throw new Error('schema_number');
  } else if(shape.type==='null'&&value!==null)throw new Error('schema_null');
  else if(shape.type==='boolean'&&typeof value!=='boolean')throw new Error('schema_boolean');
}

export function validatePage(page: Page) {
  if(!page||!page.pageId||!page.revisionId||!page.locale||!page.slug||!Array.isArray(page.blocks)||!page.seo)throw new Error('invalid_page');
  const payload=payloadOf(page);validateSchema(payload);
  for(const key of ['metaTitle','metaDescription','openGraphTitle','openGraphDescription','canonicalUrl'])if(page.seo[key]!==undefined&&typeof page.seo[key]!=='string')throw new Error('invalid_seo');
  if(page.seo.canonicalUrl)safeDestination(String(page.seo.canonicalUrl));
  if(page.seo.index!==undefined&&typeof page.seo.index!=='boolean')throw new Error('invalid_seo');
  const object=payload as Record<string,unknown>;
  for(const binding of Object.values(bindings))if(binding.profile===payload.profile){
    const items=(object[binding.target]||[]) as Record<string,unknown>[];
    for(const item of items){
      if(binding.target==='entries'&&!['portfolio','directory','control','integration'].includes(String(item.category)))continue;
      for(const [source,field]of Object.entries(binding.items[0].fields)){
        const original=(binding.items[0].defaults as Record<string,unknown>)[source];
        if(Array.isArray(original)?!Array.isArray(item[field]):typeof item[field]!=='string')throw new Error('incomplete_collection_content');
      }
    }
  }
  for(const collection of ['sections','entries','hero_scenes','journey','operating_areas','solutions','security_decisions','plans']){
    const items=(object[collection]||[]) as {id:string}[];
    if(new Set(items.map(item=>item.id)).size!==items.length)throw new Error('duplicate_content_identity');
  }
  if(payload.profile==='home'){
    if(JSON.stringify((object.hero_scenes as {scene:string}[]).map(item=>item.scene))!==JSON.stringify(['autonomy','control','oversight']))throw new Error('unsupported_hero_order');
    if(JSON.stringify((object.journey as {phase:string}[]).map(item=>item.phase))!==JSON.stringify(['defined','policy','approval','recorded']))throw new Error('unsupported_journey_order');
  }
  if(payload.profile==='security'&&new Set((payload.security_decisions as {decision:string}[]).map(item=>item.decision)).size!==3)throw new Error('incomplete_security_decisions');
  if(payload.profile==='solutions'&&!(payload.solutions as unknown[])?.length)throw new Error('missing_solutions');
  if(['privacy','terms'].includes(payload.profile)&&!payload.effective_date)throw new Error('missing_legal_date');
  if(payload.profile==='pricing'&&!(payload.plans as unknown[])?.length)throw new Error('missing_plans');
  if(payload.profile==='pricing')for(const plan of payload.plans as {availability:string;links?:unknown[]}[])if(plan.availability==='contact'&&!plan.links?.length)throw new Error('missing_plan_contact');
  const walk=(value:unknown):void=>{if(Array.isArray(value)){for(const item of value)walk(item);}else if(value&&typeof value==='object'){for(const [key,item]of Object.entries(value)){if(key==='destination'&&typeof item==='string')safeDestination(item);walk(item);}}};walk(payload);
  return payload;
}

const profiles:Record<string,string[]>={home:['PublicSite','HomeHero','HomeJourney','HomeOperatingView','HomeGateScene'],product:['PublicSite','ProductShowcase'],solutions:['PublicSite','SolutionsShowcase'],security:['PublicSite','SecurityShowcase'],pricing:['PublicSite','PricingShowcase'],resources:['PublicSite','ResourcesShowcase'],company:['PublicSite','CompanyShowcase'],privacy:['PublicSite','LegalShowcase'],terms:['PublicSite','LegalShowcase']};
export function validateCompleteness(bundle: ContentBundle) {
  if(!bundle.page)throw new Error('missing_page');
  const profile=payloadOf(bundle.page).profile;
  for(const scope of profiles[profile] || (profile==='feature'?[]:['PublicSite']))for(const key of Object.keys((baseline as Record<string,Record<string,string>>)[scope]))if(typeof bundle.copies[scope]?.[key]!=='string')throw new Error('incomplete_renderer_content');
  for(const scope of profiles[profile]||['PublicSite'])for(const [field,original]of Object.entries((baseline as Record<string,Record<string,string>>)[scope]))if(original.startsWith('https://'))safeDestination(bundle.copies[scope][field]);
  if(profile!=='feature'&&(!headerNavigation(bundle)||!headerNavigation(bundle,'footer')))throw new Error('missing_shared_navigation');
  if(bundle.page.rendererContract!==CONTRACT)throw new Error('unsupported_renderer');
  const assets=new Set(bundle.release?.assets.map(item=>item.id));
  if(bundle.page.seo.openGraphImageAssetId&&!assets.has(String(bundle.page.seo.openGraphImageAssetId)))throw new Error('missing_social_image');
  const inspect=(value:unknown):void=>{if(Array.isArray(value))value.forEach(inspect);else if(value&&typeof value==='object')for(const [key,item]of Object.entries(value)){if(['asset_id','poster_asset_id','fallback_asset_id'].includes(key)&&typeof item==='string'&&!assets.has(item))throw new Error('missing_media_dependency');if(['caption_asset_ids','rendition_asset_ids'].includes(key)&&Array.isArray(item)&&item.some(id=>!assets.has(id)))throw new Error('missing_media_dependency');inspect(item);}};
  for(const page of [bundle.page,...bundle.records])inspect(payloadOf(page));
  if(profile==='home'&&(!Array.isArray(payloadOf(bundle.page).operating_areas)||(payloadOf(bundle.page).operating_areas as unknown[]).length!==4))throw new Error('incomplete_operating_areas');
  const branding=bundle.release?.resources.find(item=>item.resourceType==='global_content'&&item.data.key==='public-branding'&&item.data.locale===bundle.page?.locale)?.data.value;
  if(!branding)throw new Error('missing_branding');validateSchema(branding);
  const sections=(branding as {sections?:{id:string;paragraphs?:string[];links?:unknown[]}[]}).sections||[];
  if(!sections.find(item=>item.id==='identity'&&(item.paragraphs?.length||0)>=2)||!sections.find(item=>item.id==='primary-signup'&&item.links?.length)||!sections.find(item=>item.id==='primary-signin'&&item.links?.length))throw new Error('incomplete_branding');
  const links=(branding as {sections?:{links?:{destination:string}[]}[]}).sections?.flatMap(item=>item.links||[])||[];
  for(const link of links)safeDestination(link.destination);
}
