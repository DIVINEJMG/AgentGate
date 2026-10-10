import { api } from './apiClient';

export interface AuthUser {
  userId: string;
  email?: string | null;
  name?: string | null;
}

export interface AuthCredentials {
  email: string;
  password: string;
  name?: string;
}

type SessionPayload = {
  user: { id?: string; userId?: string; email?: string | null; name?: string | null } | null;
  accessToken?: string;
  tokenType?: string;
  githubSetupId?: string | null;
};

function normalizeUser(payload: SessionPayload): AuthUser | null {
  const user = payload.user;
  if (!user) return null;
  const userId = user.userId ?? user.id;
  return userId ? { userId, email: user.email ?? null, name: user.name ?? null } : null;
}

function persistSession(payload: SessionPayload): AuthUser {
  const user = normalizeUser(payload);
  if (!user || !payload.accessToken) throw new Error('Authentication response was incomplete.');
  window.localStorage.setItem('audoryn.access_token', payload.accessToken);
  return user;
}

// One exchange per callback, including React StrictMode's repeated effects.
let githubCompletion: Promise<AuthUser | null> | undefined;

async function startGitHub(purpose: 'signin' | 'signup' | 'link') {
  const bytes = crypto.getRandomValues(new Uint8Array(48));
  const verifier = btoa(String.fromCharCode(...bytes)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  const challenge = Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
  window.sessionStorage.setItem('audoryn.github.verifier', verifier);
  const {data} = await api.post<{authorizationUrl: string}>('/api/v2/auth/github/start', {
    purpose, browser_challenge: challenge,
  });
  const destination = new URL(data.authorizationUrl);
  if (destination.origin !== 'https://github.com') throw new Error('Unexpected sign-in destination.');
  window.location.assign(destination.href);
}

async function completeGitHub(): Promise<AuthUser | null> {
  if (githubCompletion) return githubCompletion;
  const url = new URL(window.location.href);
  const installationFlow = url.searchParams.get('github_installation');
  const installationId = url.searchParams.get('github_installation_id');
  if (installationFlow) {
    url.searchParams.delete('github_installation');url.searchParams.delete('github_installation_id');
    window.history.replaceState({}, '', url);
    if (/^[0-9a-f-]{36}$/i.test(installationFlow) && installationId && /^[0-9]{1,20}$/.test(installationId)) {
      // A return hint grants no session or repository access. The backend checks
      // the authenticated flow owner and live installation access on continuation.
      window.sessionStorage.setItem('audoryn.github.setup', installationFlow);
      window.sessionStorage.setItem('audoryn.github.installation', JSON.stringify({flowId:installationFlow,installationId}));
    }
  }
  const flowId = url.searchParams.get('github_auth');
  if (!flowId) return null;
  url.searchParams.delete('github_auth');
  window.history.replaceState({}, '', url);
  githubCompletion = (async () => {
    const verifier = window.sessionStorage.getItem('audoryn.github.verifier');
    if (!verifier) throw new Error('Complete GitHub sign-in in the browser that started it.');
    try {
      const {data} = await api.post<SessionPayload>('/api/v2/auth/github/exchange', {
        flow_id: flowId, browser_verifier: verifier,
      });
      const user = persistSession(data);
      // A handoff ID grants no access; the backend checks its authenticated owner.
      window.sessionStorage.removeItem('audoryn.github.setup');
      window.sessionStorage.removeItem('audoryn.github.installation');
      if (data.githubSetupId) window.sessionStorage.setItem('audoryn.github.setup', data.githubSetupId);
      return user;
    } finally {
      window.sessionStorage.removeItem('audoryn.github.verifier');
    }
  })();
  const pending = githubCompletion;
  // Share only the in-flight exchange. Later hydration must verify the live session.
  void pending.then(
    () => {if (githubCompletion === pending) githubCompletion = undefined;},
    () => {if (githubCompletion === pending) githubCompletion = undefined;},
  );
  return pending;
}

export const auth = Object.freeze({
  startGitHub,
  completeGitHub,
  async githubEnabled(): Promise<boolean> {
    const {data} = await api.get<{enabled: boolean}>('/api/v2/auth/github/readiness');
    return data.enabled;
  },
  async getUser(): Promise<AuthUser | null> {
    const token = window.localStorage.getItem('audoryn.access_token');
    if (!token) return null;
    try {
      const response = await api.get<SessionPayload>('/api/v2/auth/session');
      return normalizeUser(response.data);
    } catch (error) {
      if (typeof error === 'object' && error !== null && 'status' in error &&
          (error as { status?: number }).status === 401) {
        window.localStorage.removeItem('audoryn.access_token');
        return null;
      }
      throw error;
    }
  },

  async signIn(credentials: AuthCredentials): Promise<AuthUser> {
    const response = await api.post<SessionPayload>('/api/v2/auth/login', {
      email: credentials.email,
      password: credentials.password,
    });
    return persistSession(response.data);
  },

  async signUp(credentials: AuthCredentials): Promise<AuthUser> {
    const response = await api.post<SessionPayload>('/api/v2/auth/signup', credentials);
    return persistSession(response.data);
  },

  async signOut() {
    try { await api.post('/api/v2/auth/logout'); }
    finally {
      window.localStorage.removeItem('audoryn.access_token');
      window.sessionStorage.removeItem('audoryn.github.setup');
      window.sessionStorage.removeItem('audoryn.github.verifier');
      window.sessionStorage.removeItem('audoryn.github.installation');
      window.location.assign('/');
    }
  },
});
