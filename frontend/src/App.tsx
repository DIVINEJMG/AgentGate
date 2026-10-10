import { lazy, Suspense, useEffect, useState } from 'react';
import PublicSite from './public/PublicSite';
import {PublicExperience} from './public/PublicExperience';
import {readBootstrap,routePath,navigatePublic} from './public/content/runtime';
import type {PublicConfig} from './public/content/contract';
import { getPendingInvitationCode } from './lib/commercialApi';

const PreviewWorkspace = import.meta.env.DEV ? lazy(() => import('./preview/PreviewWorkspace')) : null;
// Public visitors do not need the authenticated workspace bundle.
const ProductApp=lazy(()=>import('./ProductApp'));
const defaultConfig:PublicConfig={enabled:false,origin:'',siteOrigin:'',contract:'audoryn.public.v1',locale:'en',cacheSeconds:60,timeoutSeconds:5,previewOrigin:''};
const navigate=navigatePublic;

export default function App() {
  const [path, setPath] = useState(()=>routePath(window.location));
  const [selectedId,setSelectedId]=useState(()=>new URLSearchParams(window.location.search).get('selected')||undefined);
  const rawRoute=path==='/'?'home':path.slice(1);
  // Workspace URLs carry their own sub-paths (/app/workers/…, /workspace-preview/…).
  const route=rawRoute.startsWith('app/')?'app':rawRoute.startsWith('workspace-preview/')?'workspace-preview':rawRoute;
  const [bootstrap]=useState(readBootstrap);
  const [config,setConfig]=useState(bootstrap?.config||defaultConfig);

  useEffect(() => {
    const onHashChange = () => {setPath(routePath(window.location));setSelectedId(new URLSearchParams(window.location.search).get('selected')||undefined);};
    window.addEventListener('hashchange', onHashChange);
    window.addEventListener('popstate',onHashChange);
    return () => {window.removeEventListener('hashchange', onHashChange);window.removeEventListener('popstate',onHashChange);};
  }, []);
  useEffect(()=>{
    if(bootstrap)return;
    const controller=new AbortController();void fetch('/_public/config',{credentials:'omit',signal:controller.signal}).then(response=>response.ok?response.json():null).then(value=>{if(value&&!controller.signal.aborted)setConfig(value);}).catch(()=>{});return ()=>controller.abort();
  },[bootstrap]);

  const invitationPending = Boolean(getPendingInvitationCode());
  if (route === 'workspace-preview') {
    return PreviewWorkspace
      ? <Suspense fallback={null}><PreviewWorkspace onExit={() => navigate('home')} /></Suspense>
      : <PublicSite route='home' onNavigate={navigate} />;
  }
  if ((invitationPending && route !== 'terms' && route !== 'privacy') || route === 'login' || route === 'signup' || route === 'app') {
    return <Suspense fallback={null}><ProductApp entryMode={route === 'signup' ? 'signup' : route === 'app' ? 'app' : 'signin'} onBack={() => navigate('home')} onSignedIn={() => navigate('app')} onModeChange={(mode) => navigate(mode === 'signin' ? 'login' : 'signup')} /></Suspense>;
  }

  return <PublicExperience path={path} config={config} initialBundle={bootstrap?.path===path?bootstrap.bundle:undefined} onNavigate={navigate} selectedId={selectedId}/>;
}
