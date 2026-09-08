import { useEffect, useState } from 'react';
import ProductApp from './ProductApp';
import PublicSite, { type PublicRoute } from './public/PublicSite';
import { getPendingInvitationCode } from './lib/commercialApi';

type AppRoute = PublicRoute | 'login' | 'signup' | 'app';

const PUBLIC_ROUTES = new Set<PublicRoute>(['home','product','solutions','security','pricing','resources','company','privacy','terms']);

function readRoute(): AppRoute {
  const raw = window.location.hash.replace(/^#\/?/, '').split('?')[0].replace(/\/+$/, '');
  if (!raw) return 'home';
  if (raw === 'login' || raw === 'signup' || raw === 'app') return raw;
  if (PUBLIC_ROUTES.has(raw as PublicRoute)) return raw as PublicRoute;
  return 'home';
}

function navigate(route: AppRoute) {
  window.location.hash = route === 'home' ? '#/' : `#/${route}`;
}

export default function App() {
  const [route, setRoute] = useState<AppRoute>(() => readRoute());

  useEffect(() => {
    const onHashChange = () => setRoute(readRoute());
    window.addEventListener('hashchange', onHashChange);
    if (!window.location.hash) window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#/`);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const invitationPending = Boolean(getPendingInvitationCode());
  if (invitationPending || route === 'login' || route === 'signup' || route === 'app') {
    return <ProductApp entryMode={route === 'signup' ? 'signup' : route === 'app' ? 'app' : 'signin'} onBack={() => navigate('home')} onSignedIn={() => navigate('app')} />;
  }

  return <PublicSite route={route} onNavigate={navigate} />;
}
