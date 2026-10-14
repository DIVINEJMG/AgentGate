import { lazy, Suspense, useEffect, useState } from 'react';
import { getPendingInvitationCode } from './lib/commercialApi';
import {SiteApp} from './site/SiteApp';
import {CmsPreview} from './site/preview/CmsPreview';
import {navigatePublic, readBootstrap, routePath} from './site/runtime';

// The sample workspace ("Try with sample data"): local fixtures only, no network requests.
const PreviewWorkspace = lazy(() => import('./preview/PreviewWorkspace'));
// Public visitors do not need the authenticated workspace bundle.
const ProductApp=lazy(()=>import('./ProductApp'));
const navigate=navigatePublic;

export default function App() {
  const [path, setPath] = useState(()=>routePath(window.location));
  const rawRoute=path==='/'?'home':path.slice(1);
  // Workspace URLs carry their own sub-paths (/app/workers/…, /workspace-preview/…).
  // A GitHub setup return addressed to the site root (older links) still belongs to the workspace.
  const githubSetup=rawRoute==='home'&&new URLSearchParams(window.location.search).has('github_setup');
  const route=githubSetup||rawRoute.startsWith('app/')?'app':rawRoute.startsWith('workspace-preview/')?'workspace-preview':rawRoute;
  const [bootstrap]=useState(readBootstrap);

  useEffect(() => {
    const onHashChange = () => setPath(routePath(window.location));
    window.addEventListener('hashchange', onHashChange);
    window.addEventListener('popstate',onHashChange);
    return () => {window.removeEventListener('hashchange', onHashChange);window.removeEventListener('popstate',onHashChange);};
  }, []);

  const invitationPending = Boolean(getPendingInvitationCode());
  const cacheSeconds = bootstrap?.cacheSeconds ?? 60;
  if (route === 'workspace-preview') {
    return <Suspense fallback={null}><PreviewWorkspace onExit={() => navigate('home')} /></Suspense>;
  }
  if ((invitationPending && route !== 'terms' && route !== 'privacy') || route === 'login' || route === 'signup' || route === 'app') {
    return <Suspense fallback={null}><ProductApp entryMode={route === 'signup' ? 'signup' : route === 'app' ? 'app' : 'signin'} onBack={() => navigate('home')} onSignedIn={() => navigate('app')} onModeChange={(mode) => navigate(mode === 'signin' ? 'login' : 'signup')} /></Suspense>;
  }

  // The Console preview, then every public page: fixed pages, articles and not found.
  if (path === '/_cms/preview') return <CmsPreview previewOrigin={bootstrap?.previewOrigin ?? ''} />;
  return <SiteApp path={path} initial={bootstrap?.path === path ? bootstrap.site ?? undefined : undefined} cacheSeconds={cacheSeconds} navigate={navigate} />;
}
