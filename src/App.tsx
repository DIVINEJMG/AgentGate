import { useEffect, useState } from 'react';
import { Activity, Bot, Building2, GitBranch, LogOut, Menu, Server, ShieldCheck } from 'lucide-react';
import type { AuthUser } from '@appdeploy/client';
import AgentsPanel from './components/AgentsPanel';
import { IdentityLoading, OrganizationOnboarding, SignInGate } from './components/IdentityGate';
import MetricCard from './components/MetricCard';
import Sidebar, { type AppView } from './components/Sidebar';
import { listAgents } from './lib/agentApi';
import { createOrganization, currentUser, listOrganizations, signIn, signOut, type OrganizationAccess } from './lib/identityApi';
import { loadSystemStatus, type ApiVersion, type SystemStatus } from './lib/systemApi';

function App() {
    const [mobileNavOpen, setMobileNavOpen] = useState(false);
    const [view, setView] = useState<AppView>('overview');
    const [apiVersion, setApiVersion] = useState<ApiVersion>('v1');
    const [status, setStatus] = useState<SystemStatus | null>(null);
    const [statusError, setStatusError] = useState(false);
    const [user, setUser] = useState<AuthUser | null>(null);
    const [organizations, setOrganizations] = useState<OrganizationAccess[]>([]);
    const [agentCount, setAgentCount] = useState(0);
    const [identityLoading, setIdentityLoading] = useState(true);
    const [authError, setAuthError] = useState<string | null>(null);

    async function hydrateIdentity(version: ApiVersion, knownUser?: AuthUser | null) {
        const resolvedUser = knownUser === undefined ? await currentUser() : knownUser;
        setUser(resolvedUser);
        if (!resolvedUser) { setOrganizations([]); setAgentCount(0); return; }
        const nextOrganizations = await listOrganizations(version);
        setOrganizations(nextOrganizations);
        if (nextOrganizations[0]) {
            try { setAgentCount((await listAgents(version, nextOrganizations[0].id)).length); } catch { setAgentCount(0); }
        } else setAgentCount(0);
    }

    useEffect(() => {
        let active = true;
        Promise.all([
            loadSystemStatus(apiVersion).then((result) => { if (active) { setStatus(result); setStatusError(false); } }).catch(() => { if (active) { setStatus(null); setStatusError(true); } }),
            hydrateIdentity(apiVersion).catch(() => { if (active) setAuthError('AgentGate could not verify your identity context.'); }),
        ]).finally(() => { if (active) setIdentityLoading(false); });
        return () => { active = false; };
    }, [apiVersion]);

    async function handleSignIn() {
        setAuthError(null);
        try { const result = await signIn(); setIdentityLoading(true); await hydrateIdentity(apiVersion, result.user); }
        catch (caught) { const code = (caught as { code?: string }).code; setAuthError(code === 'popup_blocked' ? 'Allow popups for AgentGate, then try again.' : code === 'popup_closed' ? 'Sign in was cancelled.' : 'Secure sign in failed.'); }
        finally { setIdentityLoading(false); }
    }

    async function handleCreateOrganization(name: string) { const organization = await createOrganization(apiVersion, name); setOrganizations([organization]); setAgentCount(0); }
    async function handleSignOut() { await signOut(); setUser(null); setOrganizations([]); setAgentCount(0); setView('overview'); }

    if (identityLoading) return <IdentityLoading />;
    if (!user) return <SignInGate onSignIn={handleSignIn} error={authError} />;
    if (organizations.length === 0) return <OrganizationOnboarding user={user} apiVersion={apiVersion} onCreate={handleCreateOrganization} />;

    const organization = organizations[0];
    const operational = status?.state === 'operational' && !statusError;

    return <div className="app-shell"><Sidebar open={mobileNavOpen} onClose={() => setMobileNavOpen(false)} activeView={view} onNavigate={setView} /><main className="main-shell"><header className="topbar"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMobileNavOpen(true)}><Menu size={18} /></button><div className="topbar-path"><span>AgentGate</span><span className="path-separator">/</span><span>{organization.name}</span><span className="path-separator">/</span><strong>{view === 'overview' ? 'Control plane' : 'Agents'}</strong></div><div className="topbar-actions"><span className="foundation-badge">FOUNDATION 3</span><span className={`system-dot ${operational ? 'is-ok' : statusError ? 'is-error' : ''}`} /><span className="system-label">{operational ? 'Operational' : 'Unavailable'}</span><button className="account-button" onClick={handleSignOut} title="Sign out"><span className="account-name">{user.name || user.email || 'Account'}</span><LogOut size={14} /></button></div></header><div className="content-wrap">{view === 'agents' ? <AgentsPanel organization={organization} user={user} apiVersion={apiVersion} onApiVersionChange={setApiVersion} onCountChange={setAgentCount} /> : <Overview organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} status={status} statusError={statusError} operational={operational} agentCount={agentCount} /> }<footer className="product-footer"><span>AgentGate</span><span>·</span><span>An SOT Product</span><span className="footer-spacer" /><span>{organization.name} · {organization.role.replace('_', ' ')}</span></footer></div></main></div>;
}

function Overview({ organization, apiVersion, onApiVersionChange, status, statusError, operational, agentCount }: { organization: OrganizationAccess; apiVersion: ApiVersion; onApiVersionChange: (version: ApiVersion) => void; status: SystemStatus | null; statusError: boolean; operational: boolean; agentCount: number; }) {
    return <><section className="page-heading"><div><p className="eyebrow">CONTROL PLANE</p><h1>Operational overview</h1><p className="page-subtitle">Security state for <strong>{organization.name}</strong>. Your membership is scoped as {organization.role.replace('_', ' ')}.</p></div><div className="api-switcher" aria-label="API compatibility view"><span>Compatibility view</span><div className="segmented"><button className={apiVersion === 'v1' ? 'active' : ''} onClick={() => onApiVersionChange('v1')}>API v1</button><button className={apiVersion === 'v2' ? 'active' : ''} onClick={() => onApiVersionChange('v2')}>API v2</button></div></div></section>{statusError && <section className="fail-closed-banner" role="alert"><ShieldCheck size={20} /><div><strong>Control plane unavailable</strong><p>System state could not be verified. AgentGate is failing closed until the control plane can be confirmed.</p></div></section>}<section className="metrics-grid"><MetricCard label="Control plane" value={operational ? 'Operational' : 'Unavailable'} helper="Runtime status" icon={<Activity size={18} />} tone={operational ? 'success' : 'danger'} /><MetricCard label="Organization role" value={organization.role.replace('_', ' ')} helper={`${organization.permissions.length} granted permissions`} icon={<Building2 size={18} />} /><MetricCard label="API lifecycle" value={status ? `${status.currentApiVersion} current` : '—'} helper={status ? `${status.supportedApiVersions.join(' + ')} supported` : 'Version contract'} icon={<GitBranch size={18} />} /><MetricCard label="Registered agents" value={String(agentCount)} helper="Separate non-human identities" icon={<Bot size={18} />} /></section><section className="dashboard-grid"><article className="panel readiness-panel"><div className="panel-heading"><div><p className="panel-kicker">SYSTEM READINESS</p><h2>Foundation status</h2></div><span className="status-chip success">Healthy</span></div><div className="readiness-list"><ReadinessRow title="Human identity & tenant gate" detail="Authenticated membership controls organization access" status="Ready" /><ReadinessRow title="Agent identity registry" detail="Non-human identities have ownership and lifecycle state" status="Ready" /><ReadinessRow title="Hash-only credentials" detail="Plaintext agent secrets are issued once and never persisted" status="Ready" /><ReadinessRow title="Integration framework" detail="Provider adapters begin in Foundation 4" status="Next" /></div></article><article className="panel lifecycle-panel"><div className="panel-heading"><div><p className="panel-kicker">IDENTITY SEPARATION</p><h2>Human ≠ agent</h2></div><Server size={18} className="panel-icon" /></div><div className="version-stack"><div className="version-row"><div><strong>Human session</strong><span>AppDeploy user authentication</span></div><span className="status-chip info">HUMAN</span></div><div className="version-row"><div><strong>Agent credential</strong><span>Independent non-human identity</span></div><span className="status-chip neutral">AGENT</span></div></div><p className="panel-note">An agent never inherits the owner&apos;s session. Foundation 3 creates an independent identity and credential boundary for every registered AI worker.</p></article><article className="panel security-panel"><div className="panel-heading"><div><p className="panel-kicker">CREDENTIAL POSTURE</p><h2>Hash-only storage</h2></div><ShieldCheck size={18} className="panel-icon" /></div><div className="security-callout"><strong>Copy once</strong><p>Plaintext credentials exist only during issuance. AgentGate persists a SHA-256 digest, fingerprint, scope and lifecycle metadata.</p></div><ul className="control-list"><li><span className="control-mark" />Credential rotation invalidates the authoritative predecessor</li><li><span className="control-mark" />Credential revocation automatically suspends the identity</li><li><span className="control-mark" />Disabled identities cannot be reactivated or re-keyed</li></ul></article><article className="panel attention-panel"><div className="panel-heading"><div><p className="panel-kicker">EXECUTION</p><h2>Still intentionally disabled</h2></div><span className="status-chip neutral">NOT ENABLED</span></div><div className="empty-state"><div className="empty-icon"><ShieldCheck size={20} /></div><strong>Identity before authority</strong><p>Agents can now exist securely, but they still cannot touch external tools. Integrations, capabilities, policies and the action gateway arrive in later foundations.</p></div></article></section></>;
}

function ReadinessRow({ title, detail, status }: { title: string; detail: string; status: 'Ready' | 'Next' }) { return <div className="readiness-row"><div><strong>{title}</strong><span>{detail}</span></div><span className={`status-chip ${status === 'Ready' ? 'success' : 'neutral'}`}>{status.toUpperCase()}</span></div>; }

export default App;
