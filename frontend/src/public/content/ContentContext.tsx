import { createContext, useContext, type ReactNode } from 'react';
import baseline from '../../../content-seed/static-copy.json';
import bindings from '../../../content-seed/collection-bindings.json';
import { payloadOf, type ContentBundle } from './contract';
import {publicIcons} from './icons';

export const staticCopies: Record<string,Record<string,string>> = baseline;
export const staticBundle: ContentBundle = {source:'static',page:null,records:[],release:null,copies:staticCopies};
const Context = createContext<ContentBundle>(staticBundle);
export function PublicContentProvider({bundle,children}: {bundle: ContentBundle; children: ReactNode}) {
  return <Context.Provider value={bundle}>{children}</Context.Provider>;
}
export function usePublicContent() { return useContext(Context); }
export function usePublicText(scope: string) {
  const bundle = usePublicContent();
  return (field: string, original: string): string => {
    const branding=bundle.release?.resources.find(item=>item.resourceType==='global_content'&&item.data.key==='public-branding'&&item.data.locale===bundle.page?.locale)?.data.value as {sections?:{id:string;heading:string;paragraphs?:string[]}[]}|undefined;
    const identity=branding?.sections?.find(item=>item.id==='identity');
    if(scope==='PublicSite'&&identity){
      if(['copy-audoryn-ada8d1a9d6','copy-audoryn-882e539bc3'].includes(field))return field==='copy-audoryn-882e539bc3'?identity.heading.toUpperCase():identity.heading;
      if(field==='copy-an-sot-product-a0e73f58fc')return identity.paragraphs![0];
      if(field==='copy-audoryn-controlled-autonomous-workforce-infra-7d3f2f219a')return identity.paragraphs![1];
    }
    const value = bundle.copies[scope]?.[field];
    if (value !== undefined) return value;
    if (bundle.source === 'static') return original;
    // Published pages are prevalidated as complete. Never silently mix draft/baseline fields.
    throw new Error(`missing_public_field:${scope}:${field}`);
  };
}

export function usePublicNavigate(callback:(destination:string)=>void) {
 const bundle=usePublicContent();
 return (destination:string)=>{
  const key=destination==='signup'?'primary-signup':destination==='login'?'primary-signin':null;
  const global=bundle.release?.resources.find(item=>item.resourceType==='global_content'&&item.data.key==='public-branding'&&item.data.locale===bundle.page?.locale)?.data.value as {sections?:{id:string;links?:{destination:string}[]}[]}|undefined;
  const link=key?global?.sections?.find(item=>item.id===key)?.links?.[0]?.destination:undefined;
  callback(link||destination);
 };
}

export function usePublicCollection<T extends object>(scope: string, name: string, defaults: readonly T[]): (T & {id:string;availability?:string;planKey?:string;currency?:string;billingPeriod?:string;contactDestination?:string})[] {
  const bundle = usePublicContent();
  const binding = (bindings as Record<string,{profile:string;target:string;items:{id:string;identity:string;defaults:Record<string,unknown>;fields:Record<string,string>}[]}>)[`${scope}/${name}`];
  if (!binding) throw new Error('unknown_collection');
  if (bundle.source === 'static' || !bundle.page) return defaults.map((item,i)=>({...item,id:binding.items[i].id,...scope==='PricingShowcase'?{availability:binding.items[i].identity==='Free'?'free_signup':'unavailable',planKey:binding.items[i].identity.toLowerCase(),currency:'USD',billingPeriod:'month'}:{}}));
  const payload = payloadOf(bundle.page);
  const items = (payload[binding.target] as Record<string,unknown>[]).filter(item=>binding.target!=='entries'||item.category===({CompanyShowcase:'portfolio',ResourcesShowcase:'directory',SecurityShowcase:'control',ProductShowcase:'integration'} as Record<string,string>)[scope]);
  return items.map((item,i)=>{
    const original = binding.items.find(entry=>entry.id === item.id);
    const index = original ? binding.items.indexOf(original) : 0;
    const result: Record<string,unknown> = {...defaults[index],id:item.id};
    if(typeof item.icon==='string')result.icon=publicIcons[item.icon];
    for (const [from,to] of Object.entries(original?.fields || binding.items[0].fields)) result[from] = item[to];
    if (scope === 'SolutionsShowcase') result.tools = (item.tools as string[]).join(' · ');
    if (scope === 'CompanyShowcase') result.href=(item.links as {destination:string}[])?.[0]?.destination;
    if (scope === 'ResourcesShowcase') result.route=(item.links as {destination:string}[])?.[0]?.destination;
    if (scope === 'PricingShowcase') {result.price=String(Number(item.price_minor)/100);result.limit=String(item.worker_capacity_display);result.planKey=item.plan_key;result.availability=item.availability;result.currency=item.currency;result.billingPeriod=item.billing_period;result.contactDestination=(item.links as {destination:string}[])?.[0]?.destination;}
    if ('number' in result) result.number=String(i+1).padStart(2,'0');
    if ('index' in result) result.index=String(i+1).padStart(2,'0');
    return result as T & {id:string;availability?:string;planKey?:string;currency?:string;billingPeriod?:string};
  });
}

export function usePublicImage(key: string, local: string): string {
  const bundle = usePublicContent();
  if (!bundle.page || bundle.source === 'static') return local;
  const section = payloadOf(bundle.page).sections?.find(item=>item.id === 'media-'+key) as {media?:{asset_id:string}[]} | undefined;
  const asset = bundle.release?.assets.find(item=>item.id === section?.media?.[0]?.asset_id);
  if (!asset) throw new Error('missing_public_image');
  return asset.url;
}
