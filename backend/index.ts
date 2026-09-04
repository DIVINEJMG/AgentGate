import { error, json, requireAuth, router } from '@appdeploy/sdk';
import { AgentDomainError, createAgent, listAgentsForUser, revokeAgentCredential, rotateAgentCredential, serializeAgentV2, setAgentStatus } from './core/agents';
import { createOrganization, listOrganizationsForUser, serializeOrganizationsV1, serializeOrganizationsV2 } from './core/organizations';
import { readCanonicalSystemStatus, serializeV1, serializeV2 } from './core/system';
import { realtimeSubscriptionRoutes } from './realtime-subscribers';

function messageOf(value: unknown) { return value instanceof Error ? value.message : 'Unexpected error.'; }
function statusOf(value: unknown) { return value instanceof AgentDomainError ? value.statusCode : 400; }

export const handler = router({
    'GET /api/_healthcheck': [async () => json({ message: 'Success' })],
    'GET /api/v1/system/status': [async () => json(serializeV1(readCanonicalSystemStatus()))],
    'GET /api/v2/system/status': [async () => json(serializeV2(readCanonicalSystemStatus()))],
    'GET /api/v1/me': [requireAuth(), async (ctx) => json({ user: { id: ctx.user!.userId, email: ctx.user!.email ?? null, name: ctx.user!.name ?? null } })],
    'GET /api/v2/me': [requireAuth(), async (ctx) => json({ identity: { subject: ctx.user!.userId, email: ctx.user!.email ?? null, displayName: ctx.user!.name ?? null, type: 'human' } })],
    'GET /api/v1/organizations': [requireAuth(), async (ctx) => json(serializeOrganizationsV1(await listOrganizationsForUser(ctx.user!.userId)))],
    'GET /api/v2/organizations': [requireAuth(), async (ctx) => json(serializeOrganizationsV2(await listOrganizationsForUser(ctx.user!.userId)))],
    'POST /api/v1/organizations': [requireAuth(), async (ctx) => { try { const body = ctx.body as { name?: unknown }; return json({ organization: await createOrganization(ctx.user!.userId, body?.name) }, 201); } catch (caught) { return error(messageOf(caught), 400); } }],
    'POST /api/v2/organizations': [requireAuth(), async (ctx) => { try { const body = ctx.body as { name?: unknown }; const organization = await createOrganization(ctx.user!.userId, body?.name); return json({ data: { organization: { id: organization.id, name: organization.name, createdAt: organization.createdAt }, membership: { role: organization.role, permissions: organization.permissions } } }, 201); } catch (caught) { return error(messageOf(caught), 400); } }],
    'GET /api/v1/organizations/:organizationId/agents': [requireAuth(), async (ctx) => { try { const agents = await listAgentsForUser(ctx.user!.userId, ctx.params.organizationId); return json({ agents, count: agents.length }); } catch (caught) { return error(messageOf(caught), statusOf(caught)); } }],
    'GET /api/v2/organizations/:organizationId/agents': [requireAuth(), async (ctx) => { try { const agents = await listAgentsForUser(ctx.user!.userId, ctx.params.organizationId); return json({ items: agents.map(serializeAgentV2), total: agents.length }); } catch (caught) { return error(messageOf(caught), statusOf(caught)); } }],
    'POST /api/v1/organizations/:organizationId/agents': [requireAuth(), async (ctx) => { try { const body = ctx.body as { name?: unknown; description?: unknown }; const result = await createAgent(ctx.user!.userId, ctx.params.organizationId, body?.name, body?.description); return json(result, 201); } catch (caught) { return error(messageOf(caught), statusOf(caught)); } }],
    'POST /api/v2/organizations/:organizationId/agents': [requireAuth(), async (ctx) => { try { const body = ctx.body as { name?: unknown; description?: unknown }; const result = await createAgent(ctx.user!.userId, ctx.params.organizationId, body?.name, body?.description); return json({ data: { agent: serializeAgentV2(result.agent), credential: result.credential } }, 201); } catch (caught) { return error(messageOf(caught), statusOf(caught)); } }],
    'PUT /api/v1/organizations/:organizationId/agents/:agentId/lifecycle': [requireAuth(), async (ctx) => { try { const body = ctx.body as { status?: unknown }; return json({ agent: await setAgentStatus(ctx.user!.userId, ctx.params.organizationId, ctx.params.agentId, body?.status) }); } catch (caught) { return error(messageOf(caught), statusOf(caught)); } }],
    'PUT /api/v2/organizations/:organizationId/agents/:agentId/lifecycle': [requireAuth(), async (ctx) => { try { const body = ctx.body as { status?: unknown }; const agent = await setAgentStatus(ctx.user!.userId, ctx.params.organizationId, ctx.params.agentId, body?.status); return json({ data: { agent: serializeAgentV2(agent) } }); } catch (caught) { return error(messageOf(caught), statusOf(caught)); } }],
    'POST /api/v1/organizations/:organizationId/agents/:agentId/credentials/rotate': [requireAuth(), async (ctx) => { try { return json(await rotateAgentCredential(ctx.user!.userId, ctx.params.organizationId, ctx.params.agentId)); } catch (caught) { return error(messageOf(caught), statusOf(caught)); } }],
    'POST /api/v2/organizations/:organizationId/agents/:agentId/credentials/rotate': [requireAuth(), async (ctx) => { try { const result = await rotateAgentCredential(ctx.user!.userId, ctx.params.organizationId, ctx.params.agentId); return json({ data: { agent: serializeAgentV2(result.agent), credential: result.credential } }); } catch (caught) { return error(messageOf(caught), statusOf(caught)); } }],
    'POST /api/v1/organizations/:organizationId/agents/:agentId/credentials/revoke': [requireAuth(), async (ctx) => { try { return json({ agent: await revokeAgentCredential(ctx.user!.userId, ctx.params.organizationId, ctx.params.agentId) }); } catch (caught) { return error(messageOf(caught), statusOf(caught)); } }],
    'POST /api/v2/organizations/:organizationId/agents/:agentId/credentials/revoke': [requireAuth(), async (ctx) => { try { const agent = await revokeAgentCredential(ctx.user!.userId, ctx.params.organizationId, ctx.params.agentId); return json({ data: { agent: serializeAgentV2(agent) } }); } catch (caught) { return error(messageOf(caught), statusOf(caught)); } }],
    ...realtimeSubscriptionRoutes,
});
