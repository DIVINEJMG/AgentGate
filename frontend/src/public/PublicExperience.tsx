import { useEffect, useState } from 'react';
import PublicSite from './PublicSite';
import { PublicContentProvider, staticBundle } from './content/ContentContext';
import { payloadOf, publicRoutes, type ContentBundle, type PublicConfig, type PublicRoute } from './content/contract';
import { ResourcePage } from './ResourcePage';
import { PublicPreview } from './PublicPreview';
const browserPages=new Map<string,{bundle:ContentBundle|null;expires:number}>();

export function PublicExperience({path,config,initialBundle,onNavigate,selectedId}: {path:string;config:PublicConfig;initialBundle?:ContentBundle|null;onNavigate:(destination:string)=>void;selectedId?:string}) {
  const [state,setState]=useState<{path:string;bundle:ContentBundle|null}>({path,bundle:initialBundle||null});
  const [failed,setFailed]=useState(false);
  useEffect(()=>{
    if(path==='/_cms/preview')return;
    const controller=new AbortController();
    if(initialBundle!==undefined&&state.path===path)return ()=>controller.abort();
    const key=config.origin+':'+config.locale+':'+path;const cached=browserPages.get(key);
    if(cached&&cached.expires>Date.now()){setState({path,bundle:cached.bundle});return ()=>controller.abort();}
    void fetch('/_public/content?path='+encodeURIComponent(path),{signal:controller.signal,credentials:'omit'}).then(async response=>{if(!response.ok)throw Error('content_endpoint');const value=await response.json();if(!controller.signal.aborted){browserPages.set(key,{bundle:value.bundle,expires:Date.now()+config.cacheSeconds*1000});if(browserPages.size>8)browserPages.delete(browserPages.keys().next().value!);setState({path,bundle:value.bundle});setFailed(false);}}).catch(()=>{if(!controller.signal.aborted){setState({path,bundle:cached?.bundle||null});setFailed(true);}});
    return ()=>controller.abort();
  },[path,initialBundle]);
  const bundle=state.path===path?state.bundle:null;
  useEffect(()=>{
    if(bundle?.redirect){onNavigate(bundle.redirect.destination);return;}
    const metadata=bundle?.page?.seo;
    if(metadata?.metaTitle)document.title=String(metadata.metaTitle);
    const description=document.querySelector<HTMLMetaElement>('meta[name="description"]');if(description&&metadata?.metaDescription)description.content=String(metadata.metaDescription);
    const anchor=window.location.hash&&!window.location.hash.startsWith('#/')?decodeURIComponent(window.location.hash.slice(1)):null;
    if(anchor)requestAnimationFrame(()=>document.getElementById(anchor)?.scrollIntoView());
  },[bundle,path,onNavigate]);
  if(path==='/_cms/preview')return <PublicPreview config={config} onNavigate={onNavigate}/>;
  if(state.path!==path&&config.enabled)return <main className='public-container' aria-busy='true'><p role='status'>Loading content…</p></main>;
  const known=path==='/'?'home':path.slice(1);
  if(bundle?.removed||(!publicRoutes.includes(known as PublicRoute)&&!bundle?.page))return <main className='public-container'><h1>Page not found</h1><p>This page is unavailable or has been removed.</p><a href='/'>Return to Audoryn</a></main>;
  const profile=bundle?.page?payloadOf(bundle.page).profile:known;
  const content=bundle||staticBundle;
  return <PublicContentProvider bundle={content}>
    {publicRoutes.includes(profile as PublicRoute)?<PublicSite key={path+(bundle?.release?.version||'static')} route={profile as PublicRoute} onNavigate={onNavigate} selectedId={selectedId}/>:bundle?.page?<PublicSite route='resources' onNavigate={onNavigate} content={<ResourcePage page={bundle.page}/>}/>:failed?<p>Content is unavailable.</p>:null}
  </PublicContentProvider>;
}
