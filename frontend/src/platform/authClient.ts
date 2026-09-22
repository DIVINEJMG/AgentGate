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

export const auth = Object.freeze({
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
      window.location.assign('/');
    }
  },
});
