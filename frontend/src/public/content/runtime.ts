import type {ContentBundle, PublicConfig} from './contract';
export type Bootstrap={config:PublicConfig;path:string;bundle:ContentBundle|null};
declare global {interface Window { __PUBLIC_BOOTSTRAP__?:Bootstrap }}
export function readBootstrap():Bootstrap|null {
  const element=document.getElementById('public-content-bootstrap');
  if(!element?.textContent)return null;
  try{return JSON.parse(element.textContent) as Bootstrap;}catch{return null;}
}

export function routePath(location:Pick<Location,'pathname'|'hash'|'search'>):string {
  const legacy=location.hash.match(/^#\/?(home|product|solutions|security|pricing|resources|company|privacy|terms|login|signup|app|workspace-preview)(?:\?|$)/);
  if(legacy)return legacy[1]==='home'?'/':'/'+legacy[1];
  return location.pathname.replace(/\/$/,'')||'/';
}
export function navigatePublic(destination:string) {
  if(destination.startsWith('https://')){window.location.assign(destination);return;}
  const path=destination==='home'?'/':destination.startsWith('/')?destination:'/'+destination;
  const url=new URL(path,window.location.origin);
  if(url.origin!==window.location.origin)throw new Error('unsafe_navigation');
  window.history.pushState(null,'',url.pathname+url.search+url.hash);
  window.dispatchEvent(new PopStateEvent('popstate'));
}
