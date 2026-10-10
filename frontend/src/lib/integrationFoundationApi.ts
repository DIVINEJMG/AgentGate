import { api } from '../platform/client';
import type { ApiVersion } from './systemApi';

export interface FoundationConnection {
  id: string; displayName: string; provider: string; ownerId: string;
  authorizationState: string; health: string; reason: string; canShare: boolean;
  authenticationStrategy?: 'github_app' | 'static';
}
export interface FoundationResource {
  id: string; connectionId: string; name: string; externalId: string;
  capabilities: string[]; health: string; webUrl: string | null;
}
export interface ShareSubject { id: string; type: 'user' | 'worker'; name: string; }
const path = (version: ApiVersion, org: string) => `/api/${version}/organizations/${org}`;

export async function foundationReadiness(version: ApiVersion, org: string) {
  return (await api.get(`${path(version, org)}/integration-foundation`)).data as { enabled: boolean };
}
export async function foundationConnections(version: ApiVersion, org: string) {
  return (await api.get(`${path(version, org)}/integration-connections`)).data.connections as FoundationConnection[];
}
export async function foundationResources(version: ApiVersion, org: string) {
  return (await api.get(`${path(version, org)}/integration-resources`)).data.resources as FoundationResource[];
}
export async function sharingSubjects(version: ApiVersion, org: string, connection: string) {
  return (await api.get(`${path(version, org)}/integration-connections/${connection}/sharing`)).data as {
    subjects: ShareSubject[]; grants: { subjectId: string; subjectType: string; active: boolean }[];
  };
}
export async function shareConnection(version: ApiVersion, org: string, connection: string, subject: ShareSubject, active: boolean) {
  return api.post(`${path(version, org)}/integration-connections/${connection}/sharing`, {
    subject_type: subject.type, subject_id: subject.id, active,
  });
}
export async function reconnectConnection(version: ApiVersion, org: string, connection: string, credential: string) {
  return api.post(`${path(version, org)}/integration-connections/${connection}/reconnect`, { credential });
}
export async function discoverConnectionResources(version: ApiVersion, org: string, connection: string) {
  return api.post(`${path(version, org)}/integration-connections/${connection}/discover`, {});
}
export async function resumeIntegrationWork(version: ApiVersion, org: string, workItem: string) {
  return api.post(`${path(version, org)}/integration-work/${workItem}/resume`, {});
}
export async function prepareIntegrationTask(version: ApiVersion, org: string, command: string, clarification = '') {
  return api.post(`${path(version, org)}/integration-tasks/${command}/prepare`, { clarification });
}
