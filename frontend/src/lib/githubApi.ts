import { api } from '../platform/client';
import type { ApiVersion } from './systemApi';

const path = (version: ApiVersion, org: string) => `/api/${version}/organizations/${org}`;
export interface GitHubReadiness { enabled: boolean; loginEnabled?: boolean; signInLinked?: boolean; authenticationConfigured: boolean; credentialVaultConfigured?: boolean; eventsEnabled: boolean; codingEnabled: boolean; installationUrl: string | null; }
export interface CodingSession { id: string; resource: string; status: string; baseSha: string; activeSeconds: number; expiresAt: string; artifactId: string | null; initialization?: { stage: string | null; deadlineAt: string | null; fileCount: number; confirmedBundles: number; totalBundles: number }; commands: { id: string; command: string; status: string; output: { stdout?: string; stderr?: string; exitCode?: number; sourceFingerprint?: string; baseSha?: string } }[]; }
export async function githubReadiness(version: ApiVersion, org: string): Promise<GitHubReadiness> {
  return (await api.get(`${path(version, org)}/github/readiness`)).data;
}
export async function authorizeGitHub(version: ApiVersion, org: string, connectionId?: string): Promise<{ authorizationUrl: string }> {
  return (await api.post(`${path(version, org)}/github/authorize${connectionId ? `?connection_id=${encodeURIComponent(connectionId)}` : ''}`, {})).data;
}
export async function githubInstallations(version: ApiVersion, org: string, session: string): Promise<{ installations: { id: string; account: string; suspended: boolean }[] }> {
  return (await api.get(`${path(version, org)}/github/onboarding/${session}`)).data;
}
export async function connectGitHubInstallation(version: ApiVersion, org: string, session: string, installation: string) {
  return api.post(`${path(version, org)}/github/connect`, { onboarding_id: session, installation_id: installation });
}
export async function codingStatus(version: ApiVersion, org: string, workItem: string, signal?: AbortSignal): Promise<{ enabled: boolean; sessions: CodingSession[] }> {
  return (await api.get(`${path(version, org)}/work-items/${workItem}/coding`, {signal})).data;
}
export async function cancelCoding(version: ApiVersion, org: string, workItem: string) {
  return api.post(`${path(version, org)}/work-items/${workItem}/coding/cancel`, {});
}
export async function codingDiff(version: ApiVersion, org: string, workItem: string, session: string): Promise<{ diff: string; available: boolean; truncated?: boolean }> {
  return (await api.get(`${path(version, org)}/work-items/${workItem}/coding/${session}/diff`)).data;
}
