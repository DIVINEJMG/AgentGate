import {useEffect,useRef,useState} from 'react';
import {PublicContentProvider} from './content/ContentContext';
import {payloadOf,publicRoutes,type ContentBundle,type PublicConfig,type PublicRoute} from './content/contract';
import {previewBundle} from './content/preview';
import PublicSite from './PublicSite';
import {ResourcePage} from './ResourcePage';

export function PublicPreview({config,onNavigate}:{config:PublicConfig;onNavigate:(path:string)=>void}) {
 const [bundle,setBundle]=useState<ContentBundle|null>(null);const [error,setError]=useState('Waiting for Console preview content.');const nonce=useRef<string>('');
 useEffect(()=>{
  document.querySelector('meta[name="robots"]')?.setAttribute('content','noindex,nofollow');
  if(!config.previewOrigin||window.parent===window){setError('Open this restricted preview from Console.');return;}
  nonce.current=crypto.randomUUID();
  let disposed=false;
  const receive=async(event:MessageEvent)=>{
   if(event.origin!==config.previewOrigin||event.source!==window.parent||event.data?.nonce!==nonce.current||event.data?.type!=='audoryn.cms.preview')return;
   try{
    const data=event.data.payload;const response=await fetch('/_public/preview-reference?version='+encodeURIComponent(data.pinnedReleaseVersion||0),{cache:'no-store',credentials:'omit'});
    const reference=response.ok?(await response.json()).release:undefined;
    const result=previewBundle(data,reference);if(!disposed){setBundle(result);setError('');}
   }catch{if(!disposed){setBundle(null);setError('Preview content is incomplete or uses an unsupported renderer contract.');}}
  };
  window.addEventListener('message',receive);window.parent.postMessage({type:'audoryn.cms.preview.ready',nonce:nonce.current},config.previewOrigin);
  return()=>{disposed=true;window.removeEventListener('message',receive);};
 },[config.previewOrigin]);
 if(!bundle?.page)return <main className='public-container'><h1>Content preview</h1><p role='status'>{error}</p></main>;
 const profile=payloadOf(bundle.page).profile;
 return <PublicContentProvider bundle={bundle}>{publicRoutes.includes(profile as PublicRoute)?<PublicSite route={profile as PublicRoute} onNavigate={onNavigate}/>:<ResourcePage page={bundle.page}/>}</PublicContentProvider>;
}
