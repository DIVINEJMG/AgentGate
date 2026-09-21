import { api } from '@appdeploy/client';
import type { ApiVersion } from './systemApi';

export type AgentStatus = 'active' | 'suspended' | 'disabled';
export type CredentialStatus = 'active' | 'revoked';

export interface AgentIdentity {
    id: string;
    organizationId: string;
    name: string;
    description: string;
    ownerUserId: string;
    status: AgentStatus;
    credential: { status: CredentialStatus; fingerprint: string; version: number; scopes: string[]; expiresAt: string | null; lastUsedAt: string | null };
    createdAt: string;
    updatedAt: string;
}

export interface CredentialReveal { secret: string; fingerprint: string; version: number; scopes: string[]; createdAt: string; expiresAt: string | null; }

interface V2Agent {
    identity: { id: string; name: string; status: AgentStatus; createdAt: string; updatedAt: string };
    profile: { description: string };
    ownership: { organizationId: string; ownerUserId: string };
    credential: AgentIdentity['credential'];
}

function normalizeV2(item: V2Agent): AgentIdentity { return { ...item.identity, ...item.profile, ...item.ownership, credential: item.credential }; }

export async function listAgents(version: ApiVersion, organizationId: string): Promise<AgentIdentity[]> {
    const response = await api.get(`/api/${version}/organizations/${organizationId}/agents`);
    if (version === 'v1') return response.data.agents as AgentIdentity[];
    return (response.data.items as V2Agent[]).map(normalizeV2);
}

export async function registerAgent(version: ApiVersion, organizationId: string, name: string, description: string): Promise<{ agent: AgentIdentity; credential: CredentialReveal }> {
    const response = await api.post(`/api/${version}/organizations/${organizationId}/agents`, { name, description });
    if (version === 'v1') return response.data as { agent: AgentIdentity; credential: CredentialReveal };
    return { agent: normalizeV2(response.data.data.agent as V2Agent), credential: response.data.data.credential as CredentialReveal };
}

export async function setAgentLifecycle(version: ApiVersion, organizationId: string, agentId: string, status: AgentStatus): Promise<AgentIdentity> {
    const response = await api.put(`/api/${version}/organizations/${organizationId}/agents/${agentId}/lifecycle`, { status });
    if (version === 'v1') return response.data.agent as AgentIdentity;
    return normalizeV2(response.data.data.agent as V2Agent);
}

export async function rotateCredential(version: ApiVersion, organizationId: string, agentId: string): Promise<{ agent: AgentIdentity; credential: CredentialReveal }> {
    const response = await api.post(`/api/${version}/organizations/${organizationId}/agents/${agentId}/credentials/rotate`);
    if (version === 'v1') return response.data as { agent: AgentIdentity; credential: CredentialReveal };
    return { agent: normalizeV2(response.data.data.agent as V2Agent), credential: response.data.data.credential as CredentialReveal };
}

export async function revokeCredential(version: ApiVersion, organizationId: string, agentId: string): Promise<AgentIdentity> {
    const response = await api.post(`/api/${version}/organizations/${organizationId}/agents/${agentId}/credentials/revoke`);
    if (version === 'v1') return response.data.agent as AgentIdentity;
    return normalizeV2(response.data.data.agent as V2Agent);
}
