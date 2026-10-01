import type { AgentIdentity } from '../lib/agentApi';
import type { IntegrationConnection, IntegrationProvider } from '../lib/integrationApi';

export type ProviderOption = { provider: IntegrationProvider; name: string; description: string; credential: 'optional' | 'required' | 'disabled'; state: 'available' | 'guarded' };
export type ConnectionFilter = 'all' | 'connected' | 'not_connected';
export type IdentityFilter = 'all' | AgentIdentity['status'];

export function visibleProviders(providers: ProviderOption[], connections: IntegrationConnection[], query: string, filter: ConnectionFilter): ProviderOption[] {
  const text = query.trim().toLocaleLowerCase();
  return providers.filter(option => {
    const connected = connections.some(item => item.provider === option.provider && item.status !== 'disconnected');
    return (!text || `${option.name} ${option.description}`.toLocaleLowerCase().includes(text)) && (filter === 'all' || (filter === 'connected' ? connected : !connected));
  });
}

export function visibleIdentities(agents: AgentIdentity[], query: string, filter: IdentityFilter): AgentIdentity[] {
  const text = query.trim().toLocaleLowerCase();
  return agents.filter(agent => (filter === 'all' || agent.status === filter) && (!text || `${agent.name} ${agent.description} ${agent.id}`.toLocaleLowerCase().includes(text)));
}

export function scopeChanges(original: string[], draft: string[]) {
  return { added: draft.filter(scope => !original.includes(scope)), removed: original.filter(scope => !draft.includes(scope)) };
}
