import { useCallback, useEffect, useState } from 'react';
import { listActions, type ActionRecord } from '../../lib/actionApi';
import { listAgents, type AgentIdentity } from '../../lib/agentApi';
import { getAgentCapabilityProfile, listCapabilityCatalog, type AgentCapabilityProfile, type CapabilityAction, type CapabilityCatalog, type CapabilityResource } from '../../lib/capabilityApi';
import { githubReadiness, type GitHubReadiness } from '../../lib/githubApi';
import type { OrganizationAccess } from '../../lib/identityApi';
import { foundationConnections, foundationReadiness, foundationResources, type FoundationConnection, type FoundationResource } from '../../lib/integrationFoundationApi';
import { integrationSecurityStatus, listIntegrations, type IntegrationConnection, type IntegrationProvider, type IntegrationSecurityStatus } from '../../lib/integrationApi';
import { listPolicies, type PolicyRecord } from '../../lib/policyApi';
import { loadSystemStatus, type ApiVersion } from '../../lib/systemApi';

/* ---------- The tool directory ---------- */

export type SignIn = 'github_app' | 'token' | 'none';
export type Risk = 'low' | 'medium' | 'high' | 'critical';
export type ToolAbility = { label: string; detail: string; risk: Risk; approval?: boolean };
export type Tool = {
  provider: IntegrationProvider;
  slug: string;
  name: string;
  category: string;
  summary: string;
  about: string;
  signIn: SignIn;
  credential: 'optional' | 'required' | 'disabled';
  available: boolean;
  unavailableReason?: string;
  abilities: ToolAbility[];
  reaches: string;
};

/** What each tool lets a worker do, taken from the backend provider catalogs. */
export const TOOLS: Tool[] = [
  {
    provider: 'github', slug: 'github', name: 'GitHub', category: 'Code', signIn: 'github_app', credential: 'optional', available: true,
    summary: 'Repositories, issues, pull requests, workflows and a coding workspace.',
    about: 'Workers read and work in the repositories you grant. Access is installed as a GitHub App, so you choose the repositories on GitHub and can change them there at any time.',
    reaches: 'The repositories you select when installing the GitHub App.',
    abilities: [
      { label: 'Read code and history', detail: 'Files, trees, branches, tags, commits, comparisons and code search.', risk: 'low' },
      { label: 'Work with issues and discussions', detail: 'Read, open, comment, label, and manage milestones and project items.', risk: 'medium' },
      { label: 'Branches, commits and pull requests', detail: 'Create branches and commits, open and update pull requests, request reviewers.', risk: 'medium' },
      { label: 'Coding workspace', detail: 'Open an isolated checkout, edit and patch files, run commands and read the diff.', risk: 'medium' },
      { label: 'Merge and submit reviews', detail: 'Merging a pull request or submitting a review always waits for a person.', risk: 'high', approval: true },
      { label: 'Run and publish workflows', detail: 'Dispatching, re-running or cancelling a workflow, and publishing a release, always wait for a person.', risk: 'high', approval: true },
    ],
  },
  {
    provider: 'slack', slug: 'slack', name: 'Slack', category: 'Communication', signIn: 'token', credential: 'required', available: true,
    summary: 'Workspace identity, channel discovery and approved messages.',
    about: 'Connect with a Slack token. Workers can see the workspace and its channels; posting a message always waits for a person.',
    reaches: 'The workspace and channels visible to the token you provide.',
    abilities: [
      { label: 'Read workspace identity', detail: 'The connected workspace and the account acting in it.', risk: 'low' },
      { label: 'List channels', detail: 'A bounded list of channels visible to the token.', risk: 'medium' },
      { label: 'Post a message', detail: 'A bounded message to a connected channel, after approval.', risk: 'high', approval: true },
    ],
  },
  {
    provider: 'gmail', slug: 'gmail', name: 'Gmail', category: 'Communication', signIn: 'token', credential: 'required', available: true,
    summary: 'Mailbox identity and the latest message headers. Read only.',
    about: 'Connect with an access token. Workers can read who the mailbox belongs to and short metadata for the five most recent messages. Nothing can be sent.',
    reaches: 'The mailbox the token belongs to.',
    abilities: [
      { label: 'Read mailbox identity', detail: 'The mailbox address and message counts.', risk: 'low' },
      { label: 'Read recent messages', detail: 'Sender, subject and a short snippet for the five most recent messages.', risk: 'medium' },
    ],
  },
  {
    provider: 'google_drive', slug: 'google-drive', name: 'Google Drive', category: 'Files', signIn: 'token', credential: 'required', available: true,
    summary: 'Account identity and recently changed files. Read only.',
    about: 'Connect with an access token. Workers can see which account is connected and metadata for recently modified files. File contents are not read.',
    reaches: 'The Drive account the token belongs to.',
    abilities: [
      { label: 'Read account identity', detail: 'The connected Drive account.', risk: 'low' },
      { label: 'Read recent files', detail: 'Names and dates of recently modified files.', risk: 'medium' },
    ],
  },
  {
    provider: 'google_calendar', slug: 'google-calendar', name: 'Google Calendar', category: 'Scheduling', signIn: 'token', credential: 'required', available: true,
    summary: 'Primary calendar and the next ten events. Read only.',
    about: 'Connect with an access token. Workers can read the primary calendar’s identity and timezone, and the next ten upcoming events.',
    reaches: 'The primary calendar of the token’s account.',
    abilities: [
      { label: 'Read calendar identity', detail: 'The primary calendar and its timezone.', risk: 'low' },
      { label: 'Read upcoming events', detail: 'The next ten events on the primary calendar.', risk: 'medium' },
    ],
  },
  {
    provider: 'browser', slug: 'browser', name: 'Governed Browser', category: 'Web', signIn: 'none', credential: 'optional', available: true,
    summary: 'An isolated browser for websites that have no API.',
    about: 'Workers use a sandboxed browser limited to the sites and paths you allow. Every step is checked and recorded. A stored login is optional and is never shown to the AI.',
    reaches: 'Only the start page, the sites you allow and the paths you permit.',
    abilities: [
      { label: 'Read and navigate pages', detail: 'Open allowed pages, follow links, scroll, go back and forward.', risk: 'low' },
      { label: 'Fill in fields', detail: 'Click, type, select and check fields without submitting a form.', risk: 'medium' },
      { label: 'Sign in with a stored login', detail: 'Uses the encrypted login you provide; the values never reach the AI.', risk: 'medium' },
      { label: 'Submit forms', detail: 'Submitting is high risk and follows your approval rules.', risk: 'high' },
    ],
  },
  {
    provider: 'generic_mcp', slug: 'rest-mcp', name: 'REST / MCP endpoints', category: 'Custom', signIn: 'token', credential: 'disabled', available: false,
    unavailableReason: 'Waiting on outbound-address allowlisting, so custom endpoints cannot be reached safely yet.',
    summary: 'Connect your own API or MCP server.', about: '', reaches: '', abilities: [],
  },
];

export const toolFor = (provider: string) => TOOLS.find((tool) => tool.provider === provider);
export const toolBySlug = (slug?: string) => TOOLS.find((tool) => tool.slug === slug);
export const SIGN_IN_LABEL: Record<SignIn, string> = { github_app: 'GitHub App', token: 'Access token', none: 'No sign-in needed' };
export const RISK_TONE: Record<string, 'ok' | 'info' | 'warn' | 'danger'> = { low: 'ok', medium: 'info', high: 'warn', critical: 'danger' };
export const HEALTH_TONE: Record<string, 'ok' | 'warn' | 'danger' | 'neutral'> = { connected: 'ok', healthy: 'ok', degraded: 'warn', error: 'danger', disconnected: 'neutral' };

/* ---------- One loader for every Connections page ---------- */

export type ConnectionsData = {
  integrations: IntegrationConnection[];
  security: IntegrationSecurityStatus | null;
  catalog: CapabilityCatalog | null;
  agents: AgentIdentity[];
  profiles: Record<string, AgentCapabilityProfile>;
  actions: ActionRecord[];
  foundation: { enabled: boolean; connections: FoundationConnection[]; resources: FoundationResource[] } | null;
  github: GitHubReadiness | null;
  policies: PolicyRecord[];
  gateway: 'operational' | 'unreachable' | null;
  loadedAt: number;
};

const cache = new Map<string, ConnectionsData>();
const settled = <T,>(result: PromiseSettledResult<T>, fallback: T) => result.status === 'fulfilled' ? result.value : fallback;

async function loadAll(version: ApiVersion, organizationId: string): Promise<ConnectionsData> {
  const [integrations, security, catalog, agents, actions, foundationOn, github, policies, system] = await Promise.allSettled([
    listIntegrations(version, organizationId), integrationSecurityStatus(version, organizationId), listCapabilityCatalog(version, organizationId),
    listAgents(version, organizationId), listActions(version, organizationId), foundationReadiness(version, organizationId),
    githubReadiness(version, organizationId), listPolicies(version, organizationId), loadSystemStatus(version),
  ]);
  if (integrations.status === 'rejected') throw integrations.reason;
  const agentList = settled(agents, [] as AgentIdentity[]);
  const profileResults = await Promise.allSettled(agentList.map((agent) => getAgentCapabilityProfile(version, organizationId, agent.id)));
  const profiles: Record<string, AgentCapabilityProfile> = {};
  profileResults.forEach((result, index) => { if (result.status === 'fulfilled') profiles[agentList[index].id] = result.value; });
  let foundation: ConnectionsData['foundation'] = null;
  if (foundationOn.status === 'fulfilled' && foundationOn.value.enabled) {
    const [connections, resources] = await Promise.allSettled([foundationConnections(version, organizationId), foundationResources(version, organizationId)]);
    foundation = { enabled: true, connections: settled(connections, []), resources: settled(resources, []) };
  }
  return {
    integrations: integrations.value, security: settled(security, null), catalog: settled(catalog, null), agents: agentList, profiles,
    actions: settled(actions, [] as ActionRecord[]), foundation, github: settled(github, null), policies: settled(policies, [] as PolicyRecord[]),
    gateway: system.status === 'fulfilled' ? 'operational' : 'unreachable', loadedAt: Date.now(),
  };
}

/** Shows the last known data immediately, then refreshes in the background. */
export function useConnections(organization: OrganizationAccess, version: ApiVersion) {
  const key = `${organization.id}:${version}`;
  const [data, setData] = useState<ConnectionsData | null>(() => cache.get(key) ?? null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const refresh = useCallback(async () => {
    setRefreshing(true);
    try { const next = await loadAll(version, organization.id); cache.set(key, next); setData(next); setError(null); }
    catch (caught) { setError(errorText(caught)); }
    finally { setRefreshing(false); }
  }, [key, organization.id, version]);
  useEffect(() => { setData(cache.get(key) ?? null); void refresh(); }, [key, refresh]);
  /** Apply a local change (after a successful write) without waiting for a reload. */
  const patch = useCallback((change: (current: ConnectionsData) => ConnectionsData) => {
    setData((current) => { if (!current) return current; const next = change(current); cache.set(key, next); return next; });
  }, [key]);
  return { data, error, refreshing, refresh, patch };
}

export function errorText(value: unknown) {
  const data = value as { response?: { data?: { error?: string; detail?: unknown } }; message?: string };
  return (typeof data.response?.data?.detail === 'string' ? data.response.data.detail : data.response?.data?.error) || data.message || 'The connection request failed.';
}

/* ---------- Derived views ---------- */

export function resourcesFor(data: ConnectionsData, integrationId: string): CapabilityResource[] {
  return data.catalog?.resources.filter((resource) => resource.integrationId === integrationId) ?? [];
}
export function scopesFor(data: ConnectionsData, integrationId: string): string[] {
  return [...new Set(resourcesFor(data, integrationId).flatMap((resource) => resource.actions.map((action) => action.scope)))].sort();
}
export function actionsFor(data: ConnectionsData, integrationId: string): Array<CapabilityAction & { resource: string }> {
  const seen = new Map<string, CapabilityAction & { resource: string }>();
  for (const resource of resourcesFor(data, integrationId)) for (const action of resource.actions) if (!seen.has(action.scope)) seen.set(action.scope, { ...action, resource: resource.displayName });
  return [...seen.values()];
}
export function activityFor(data: ConnectionsData, integrationId: string): ActionRecord[] {
  const resourceIds = new Set(resourcesFor(data, integrationId).map((resource) => resource.id));
  return data.actions.filter((action) => action.request.integrationId === integrationId || resourceIds.has(action.request.resourceId)).sort((a, b) => Date.parse(b.requestedAt) - Date.parse(a.requestedAt));
}
/** Agent identities that declare at least one scope this connection offers. */
export function identitiesFor(data: ConnectionsData, integrationId: string) {
  const scopes = new Set(scopesFor(data, integrationId));
  return data.agents.map((agent) => ({ agent, declared: (data.profiles[agent.id]?.declaredScopes ?? []).filter((scope) => scopes.has(scope)) })).filter((entry) => entry.declared.length > 0);
}
/** The enabled policies whose selectors could match a request through this connection. */
export function policiesFor(data: ConnectionsData, integrationId: string) {
  const resources = resourcesFor(data, integrationId);
  const ids = new Set(resources.map((resource) => resource.id));
  const scopes = new Set(scopesFor(data, integrationId));
  return data.policies.filter((policy) => policy.status === 'enabled'
    && (!policy.selectors.resourceIds.length || policy.selectors.resourceIds.some((id) => ids.has(id)))
    && (!policy.selectors.scopes.length || policy.selectors.scopes.some((scope) => scopes.has(scope))));
}
/** What the current rules do to one action: the highest-priority matching rule wins; nothing matching means blocked. */
export function ruleFor(data: ConnectionsData, resourceIds: string[], action: CapabilityAction) {
  const matches = data.policies.filter((policy) => policy.status === 'enabled'
    && (!policy.selectors.resourceIds.length || policy.selectors.resourceIds.some((id) => resourceIds.includes(id)))
    && (!policy.selectors.scopes.length || policy.selectors.scopes.includes(action.scope))
    && (!policy.selectors.actions.length || policy.selectors.actions.includes(action.action))
    && (!policy.selectors.risks.length || policy.selectors.risks.includes(action.risk)));
  const order = { deny: 0, require_approval: 1, allow: 2 } as Record<string, number>;
  const winner = [...matches].sort((a, b) => b.priority - a.priority || (order[a.effect] ?? 3) - (order[b.effect] ?? 3))[0];
  return winner ? { effect: winner.effect, policy: winner } : { effect: 'deny' as const, policy: null };
}
export function workerCountFor(data: ConnectionsData, integrationId: string, workers: Array<{ agentIdentityId: string; status: string }>) {
  const identities = new Set(identitiesFor(data, integrationId).map((entry) => entry.agent.id));
  return workers.filter((worker) => worker.status !== 'archived' && identities.has(worker.agentIdentityId)).length;
}
export const foundationFor = (data: ConnectionsData, integrationId: string) => data.foundation?.connections.find((connection) => connection.id === integrationId) ?? null;
