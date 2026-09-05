import { db } from '@appdeploy/sdk';
import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { getOrganizationForUser } from './organizations';
import { appendAuditEventBestEffort } from './audit';

export type AgentStatus = 'active' | 'suspended' | 'disabled';
export type CredentialStatus = 'active' | 'revoked';

interface AgentRecord {
    organizationId: string;
    name: string;
    description: string;
    ownerUserId: string;
    status: AgentStatus;
    credentialRecordId: string;
    credentialFingerprint: string;
    credentialStatus: CredentialStatus;
    credentialVersion: number;
    credentialScopes: string[];
    credentialExpiresAt: string | null;
    credentialLastUsedAt: string | null;
    createdAt: string;
    updatedAt: string;
}

interface CredentialRecord {
    organizationId: string;
    agentId: string;
    credentialHash: string;
    version: number;
    scopes: string[];
    createdAt: string;
    expiresAt: string | null;
    lastUsedAt: string | null;
    revokedAt: string | null;
}

export class AgentDomainError extends Error {
    statusCode: number;
    constructor(message: string, statusCode = 400) { super(message); this.statusCode = statusCode; }
}

function agentsTable(organizationId: string) { return `agents:${organizationId}`; }
function credentialsTable(organizationId: string) { return `agent_credentials:${organizationId}`; }

async function requireAgentAccess(userId: string, organizationId: string, permission: 'agents.read' | 'agents.manage') {
    const organization = await getOrganizationForUser(userId, organizationId);
    if (!organization) throw new AgentDomainError('Organization not found or access denied.', 404);
    if (!organization.permissions.includes(permission)) throw new AgentDomainError('Forbidden: your organization role does not grant this agent permission.', 403);
    return organization;
}

function generateCredential() {
    const secret = `agt_sk_${randomBytes(32).toString('base64url')}`;
    const hash = createHash('sha256').update(secret).digest('hex');
    return { secret, hash, fingerprint: `sha256:${hash.slice(0, 12)}` };
}

function publicAgent(id: string, record: AgentRecord) {
    return {
        id,
        organizationId: record.organizationId,
        name: record.name,
        description: record.description,
        ownerUserId: record.ownerUserId,
        status: record.status,
        credential: {
            status: record.credentialStatus,
            fingerprint: record.credentialFingerprint,
            version: record.credentialVersion,
            scopes: record.credentialScopes,
            expiresAt: record.credentialExpiresAt,
            lastUsedAt: record.credentialLastUsedAt,
        },
        createdAt: record.createdAt,
        updatedAt: record.updatedAt,
    };
}

async function loadAgent(organizationId: string, agentId: string) {
    const [record] = await db.get<AgentRecord>(agentsTable(organizationId), [agentId]);
    if (!record) throw new AgentDomainError('Agent identity not found.', 404);
    return record;
}

async function updateAgent(organizationId: string, agentId: string, record: AgentRecord) {
    const [updated] = await db.update(agentsTable(organizationId), [{ id: agentId, record }]);
    if (!updated) throw new AgentDomainError('Failed to update agent identity.', 500);
    return publicAgent(agentId, record);
}

async function markCredentialRevoked(organizationId: string, credentialRecordId: string) {
    if (!credentialRecordId) return;
    const [credential] = await db.get<CredentialRecord>(credentialsTable(organizationId), [credentialRecordId]);
    if (!credential || credential.revokedAt) return;
    await db.update(credentialsTable(organizationId), [{ id: credentialRecordId, record: { ...credential, revokedAt: new Date().toISOString() } }]);
}

export async function createAgent(userId: string, organizationId: string, rawName: unknown, rawDescription: unknown) {
    await requireAgentAccess(userId, organizationId, 'agents.manage');
    const name = typeof rawName === 'string' ? rawName.trim() : '';
    const description = typeof rawDescription === 'string' ? rawDescription.trim() : '';
    if (name.length < 2 || name.length > 80) throw new AgentDomainError('Agent name must be between 2 and 80 characters.');
    if (description.length > 240) throw new AgentDomainError('Agent description must be 240 characters or fewer.');

    const createdAt = new Date().toISOString();
    const scopes = ['agent.authenticate'];
    const initialRecord: AgentRecord = {
        organizationId,
        name,
        description,
        ownerUserId: userId,
        status: 'active',
        credentialRecordId: '',
        credentialFingerprint: '',
        credentialStatus: 'active',
        credentialVersion: 1,
        credentialScopes: scopes,
        credentialExpiresAt: null,
        credentialLastUsedAt: null,
        createdAt,
        updatedAt: createdAt,
    };

    const [agentId] = await db.add(agentsTable(organizationId), [initialRecord]);
    if (!agentId) throw new AgentDomainError('Failed to register agent identity.', 500);

    const credential = generateCredential();
    const [credentialRecordId] = await db.add(credentialsTable(organizationId), [{
        organizationId,
        agentId,
        credentialHash: credential.hash,
        version: 1,
        scopes,
        createdAt,
        expiresAt: null,
        lastUsedAt: null,
        revokedAt: null,
    }]);
    if (!credentialRecordId) {
        await db.delete(agentsTable(organizationId), [agentId]);
        throw new AgentDomainError('Failed to issue agent credential.', 500);
    }

    const finalRecord = { ...initialRecord, credentialRecordId, credentialFingerprint: credential.fingerprint };
    const [updated] = await db.update(agentsTable(organizationId), [{ id: agentId, record: finalRecord }]);
    if (!updated) {
        await db.delete(credentialsTable(organizationId), [credentialRecordId]);
        await db.delete(agentsTable(organizationId), [agentId]);
        throw new AgentDomainError('Failed to finalize agent identity.', 500);
    }

    const agent=publicAgent(agentId,finalRecord); await appendAuditEventBestEffort({organizationId,eventType:'agent.registered',category:'identity',severity:'info',actor:{type:'human',id:userId,label:null},resource:{type:'agent',id:agentId,name},correlationId:null,outcome:'created',summary:`Agent ${name} registered.`,metadata:{credentialFingerprint:credential.fingerprint}}); return {agent,credential:{secret:credential.secret,fingerprint:credential.fingerprint,version:1,scopes,createdAt,expiresAt:null}};
}

export async function listAgentsForUser(userId: string, organizationId: string) {
    await requireAgentAccess(userId, organizationId, 'agents.read');
    const { items } = await db.list<AgentRecord>(agentsTable(organizationId), { limit: 100 });
    return items.map((item) => publicAgent(item.id, item)).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export async function setAgentStatus(userId: string, organizationId: string, agentId: string, rawStatus: unknown) {
    await requireAgentAccess(userId, organizationId, 'agents.manage');
    if (rawStatus !== 'active' && rawStatus !== 'suspended' && rawStatus !== 'disabled') throw new AgentDomainError('Agent status must be active, suspended, or disabled.');
    const status = rawStatus as AgentStatus;
    const current = await loadAgent(organizationId, agentId);
    if (current.status === 'disabled' && status !== 'disabled') throw new AgentDomainError('Disabled agent identities cannot be reactivated.', 409);
    if (status === 'active' && current.credentialStatus !== 'active') throw new AgentDomainError('Rotate the agent credential before activating this identity.', 409);
    const updatedAt = new Date().toISOString();
    const next: AgentRecord = status === 'disabled'
        ? { ...current, status, credentialStatus: 'revoked', updatedAt }
        : { ...current, status, updatedAt };
    const agent = await updateAgent(organizationId, agentId, next);
    if (status === 'disabled') await markCredentialRevoked(organizationId, current.credentialRecordId); await appendAuditEventBestEffort({organizationId,eventType:'agent.lifecycle.changed',category:'identity',severity:status==='disabled'?'warning':'info',actor:{type:'human',id:userId,label:null},resource:{type:'agent',id:agentId,name:agent.name},correlationId:null,outcome:status,summary:`Agent lifecycle changed to ${status}.`,metadata:{previousStatus:current.status}}); return agent;
}

export async function rotateAgentCredential(userId: string, organizationId: string, agentId: string) {
    await requireAgentAccess(userId, organizationId, 'agents.manage');
    const current = await loadAgent(organizationId, agentId);
    if (current.status === 'disabled') throw new AgentDomainError('Disabled agent identities cannot receive new credentials.', 409);

    const generated = generateCredential();
    const version = current.credentialVersion + 1;
    const createdAt = new Date().toISOString();
    const scopes = current.credentialScopes.length ? current.credentialScopes : ['agent.authenticate'];
    const [newCredentialId] = await db.add(credentialsTable(organizationId), [{
        organizationId,
        agentId,
        credentialHash: generated.hash,
        version,
        scopes,
        createdAt,
        expiresAt: null,
        lastUsedAt: null,
        revokedAt: null,
    }]);
    if (!newCredentialId) throw new AgentDomainError('Failed to rotate agent credential.', 500);

    const next: AgentRecord = {
        ...current,
        credentialRecordId: newCredentialId,
        credentialFingerprint: generated.fingerprint,
        credentialStatus: 'active',
        credentialVersion: version,
        credentialScopes: scopes,
        credentialExpiresAt: null,
        credentialLastUsedAt: null,
        updatedAt: createdAt,
    };
    try {
        const agent = await updateAgent(organizationId, agentId, next);
        await markCredentialRevoked(organizationId, current.credentialRecordId); await appendAuditEventBestEffort({organizationId,eventType:'agent.credential.rotated',category:'security',severity:'warning',actor:{type:'human',id:userId,label:null},resource:{type:'agent',id:agentId,name:agent.name},correlationId:null,outcome:'rotated',summary:'Agent credential rotated.',metadata:{version,fingerprint:generated.fingerprint}}); return { agent, credential: { secret: generated.secret, fingerprint: generated.fingerprint, version, scopes, createdAt, expiresAt: null } };
    } catch (caught) {
        await db.delete(credentialsTable(organizationId), [newCredentialId]);
        throw caught;
    }
}

export async function revokeAgentCredential(userId: string, organizationId: string, agentId: string) {
    await requireAgentAccess(userId, organizationId, 'agents.manage');
    const current = await loadAgent(organizationId, agentId);
    if (current.credentialStatus === 'revoked') return publicAgent(agentId, current);
    const updatedAt = new Date().toISOString();
    const next: AgentRecord = { ...current, status: current.status === 'disabled' ? 'disabled' : 'suspended', credentialStatus: 'revoked', updatedAt };
    const agent = await updateAgent(organizationId, agentId, next);
    await markCredentialRevoked(organizationId, current.credentialRecordId); await appendAuditEventBestEffort({organizationId,eventType:'agent.credential.revoked',category:'security',severity:'warning',actor:{type:'human',id:userId,label:null},resource:{type:'agent',id:agentId,name:agent.name},correlationId:null,outcome:'revoked',summary:'Agent credential revoked; identity suspended.',metadata:{fingerprint:current.credentialFingerprint}}); return agent;
}

export async function authenticateAgentCredential(organizationId: string, agentId: string, rawSecret: unknown) {
    const secret = typeof rawSecret === 'string' ? rawSecret.trim() : '';
    if (!/^agt_sk_[A-Za-z0-9_-]{20,120}$/.test(secret)) throw new AgentDomainError('Invalid agent credential.', 401);
    const current = await loadAgent(organizationId, agentId);
    if (current.organizationId !== organizationId || current.status !== 'active' || current.credentialStatus !== 'active' || !current.credentialRecordId) throw new AgentDomainError('Agent identity is not permitted to execute actions.', 403);
    const [credential] = await db.get<CredentialRecord>(credentialsTable(organizationId), [current.credentialRecordId]);
    if (!credential || credential.organizationId !== organizationId || credential.agentId !== agentId || credential.revokedAt || !credential.scopes.includes('agent.authenticate')) throw new AgentDomainError('Agent credential is unavailable or revoked.', 401);
    if (credential.expiresAt && new Date(credential.expiresAt).getTime() <= Date.now()) throw new AgentDomainError('Agent credential has expired.', 401);
    const suppliedHash = createHash('sha256').update(secret).digest();
    const expectedHash = Buffer.from(credential.credentialHash, 'hex');
    if (suppliedHash.length !== expectedHash.length || !timingSafeEqual(suppliedHash, expectedHash)) throw new AgentDomainError('Invalid agent credential.', 401);
    const lastUsedAt = new Date().toISOString();
    const [credentialUpdated] = await db.update(credentialsTable(organizationId), [{ id: current.credentialRecordId, record: { ...credential, lastUsedAt } }]);
    const next = { ...current, credentialLastUsedAt: lastUsedAt, updatedAt: lastUsedAt };
    const [agentUpdated] = await db.update(agentsTable(organizationId), [{ id: agentId, record: next }]);
    if (!credentialUpdated || !agentUpdated) throw new AgentDomainError('Agent authentication metadata could not be persisted. Execution is denied.', 500);
    return publicAgent(agentId, next);
}

export async function validateAgentForApprovalResume(organizationId: string, agentId: string, expectedFingerprint: string) { const current = await loadAgent(organizationId, agentId); if (current.organizationId !== organizationId || current.status !== 'active' || current.credentialStatus !== 'active' || !current.credentialRecordId) throw new AgentDomainError('Agent identity is no longer permitted to execute actions.', 403); if (current.credentialFingerprint !== expectedFingerprint) throw new AgentDomainError('Agent credential changed while the action was awaiting approval. Stale authority cannot resume.', 409); const [credential] = await db.get<CredentialRecord>(credentialsTable(organizationId), [current.credentialRecordId]); if (!credential || credential.revokedAt || credential.organizationId !== organizationId || credential.agentId !== agentId || !credential.scopes.includes('agent.authenticate')) throw new AgentDomainError('Agent credential is unavailable or revoked.', 401); if (credential.expiresAt && new Date(credential.expiresAt).getTime() <= Date.now()) throw new AgentDomainError('Agent credential expired while the action was awaiting approval.', 401); return publicAgent(agentId, current); }

export async function getAgentForRuntime(organizationId: string, agentId: string) { const record = await loadAgent(organizationId, agentId); if (record.organizationId !== organizationId) throw new AgentDomainError('Agent identity not found.', 404); return publicAgent(agentId, record); }

export async function getAgentForUser(userId: string, organizationId: string, agentId: string) { await requireAgentAccess(userId, organizationId, 'agents.read'); return getAgentForRuntime(organizationId, agentId); }

export function serializeAgentV2(agent: ReturnType<typeof publicAgent>) {
    return {
        identity: { id: agent.id, name: agent.name, status: agent.status, createdAt: agent.createdAt, updatedAt: agent.updatedAt },
        profile: { description: agent.description },
        ownership: { organizationId: agent.organizationId, ownerUserId: agent.ownerUserId },
        credential: agent.credential,
    };
}
