import { api } from './apiClient';
import { authUrl } from './config';

export interface AuthUser {
  userId: string;
  email?: string | null;
  name?: string | null;
}

type SessionPayload =
  | { user: { id?: string; userId?: string; email?: string | null; name?: string | null } | null }
  | { identity: { subject: string; email?: string | null; displayName?: string | null } | null };

function normalizeUser(payload: SessionPayload): AuthUser | null {
  if ('user' in payload) {
    const user = payload.user;
    if (!user) return null;
    const userId = user.userId ?? user.id;
    return userId ? { userId, email: user.email ?? null, name: user.name ?? null } : null;
  }
  const identity = payload.identity;
  return identity ? { userId: identity.subject, email: identity.email ?? null, name: identity.displayName ?? null } : null;
}

export const auth = Object.freeze({
  async getUser(): Promise<AuthUser | null> {
    try {
      const response = await api.get<SessionPayload>('/api/v2/auth/session');
      return normalizeUser(response.data);
    } catch (error) {
      if (typeof error === 'object' && error !== null && 'status' in error &&
          (error as { status?: number }).status === 401) return null;
      throw error;
    }
  },

  async signIn(_options?: { scope?: string }) {
    const returnTo = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    window.location.assign(`${authUrl('/api/v2/auth/login')}?returnTo=${encodeURIComponent(returnTo)}`);
  },

  async signOut() {
    try { await api.post('/api/v2/auth/logout'); }
    finally {
      window.localStorage.removeItem('audoryn.access_token');
      window.location.assign('/');
    }
  },
});
