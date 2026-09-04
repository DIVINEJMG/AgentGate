import { api, auth, type AuthUser } from '@appdeploy/client';
import type { ApiVersion } from './systemApi';

export type Role = 'owner' | 'admin' | 'security_manager' | 'operator' | 'approver' | 'viewer';
export interface OrganizationAccess { id: string; name: string; createdAt: string; role: Role; permissions: string[]; }

export async function currentUser(): Promise<AuthUser | null> { return auth.getUser(); }
export async function signIn() { return auth.signIn({ scope: 'openid email profile offline_access' }); }
export async function signOut() { return auth.signOut(); }
export async function listOrganizations(version: ApiVersion): Promise<OrganizationAccess[]> {
  const response = await api.get(`/api/${version}/organizations`);
  if (version === 'v1') return response.data.organizations as OrganizationAccess[];
  return response.data.items.map((item: any) => ({ ...item.organization, ...item.membership }));
}
export async function createOrganization(version: ApiVersion, name: string): Promise<OrganizationAccess> {
  const response = await api.post(`/api/${version}/organizations`, { name });
  if (version === 'v1') return response.data.organization as OrganizationAccess;
  return { ...response.data.data.organization, ...response.data.data.membership } as OrganizationAccess;
}
