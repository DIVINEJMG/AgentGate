import { useEffect, useState } from 'react';
import type { AuthUser } from '@appdeploy/client';
import AgentsPanel from './components/AgentsPanel';
import ActionsPanel from './components/ActionsPanel';
import ApprovalsPanel from './components/ApprovalsPanel';
import AuditPanel from './components/AuditPanel';
import CapabilitiesPanel from './components/CapabilitiesPanel';
import IntegrationsPanel from './components/IntegrationsPanel';
import PoliciesPanel from './components/PoliciesPanel';
import RiskPanel from './components/RiskPanel';
import IncidentsPanel from './components/IncidentsPanel';
import SettingsPanel from './components/SettingsPanel';
import WorkforcePanel from './components/WorkforcePanel';
import JobsPanel from './components/JobsPanel';
import SupervisionPanel from './components/SupervisionPanel';
import PerformancePanel from './components/PerformancePanel';
import ResultsPanel from './components/ResultsPanel';
import RuntimePanel from './components/RuntimePanel';
import CommercialPanel from './components/CommercialPanel';
import InvitationGate from './components/InvitationGate';
import MemoryPanel from './components/MemoryPanel';
import { IdentityLoading, OrganizationOnboarding, SignInGate } from './components/IdentityGate';
import Sidebar from './components/Sidebar';
import { listAgents } from './lib/agentApi';
import { createOrganization, currentUser, listOrganizations, signIn, signOut, type OrganizationAccess } from './lib/identityApi';
import { listIntegrations } from './lib/integrationApi';
import { loadSystemStatus, type ApiVersion, type SystemStatus } from './lib/systemApi';
import { clearPendingInvitationCode, getPendingInvitationCode } from './lib/commercialApi';
import { loadPerformance, type PerformanceWorkspace, type ActivityItem } from './lib/performanceApi';
import { labelForView, SECTION_ITEMS, sectionForView, type AppView } from './navigation';
import './gateway.css';
import './audit.css';
import './risk.css';
import './incidents.css';
import './product.css';
import './workforce.css';
import './jobs.css';
import './memory.css';
import './supervision.css';
import './performance.css';
import './results.css';
import './commercial.css';
import './r1.css';
import './r3.css';

type EntryMode = 'signin' | 'signup' | 'app';

export default function ProductApp({ entryMode, onBack, onSignedIn }: { entryMode: EntryMode; onBack: () => void; onSignedIn: () => void }) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [view, setView] = useState<AppView>('overview');
  const [apiVersion, setApiVersion] = useState<ApiVersion>('v1');
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [statusError, setStatusError] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [organizations, setOrganizations] = useState<OrganizationAccess[]>([]);
  const [agentCount, setAgentCount] = useState(0);
  const [integrationCount, setIntegrationCount] = useState(0);
  const [performance, setPerformance] = useState<PerformanceWorkspace | null>(null);
  const [identityLoading, setIdentityLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [pendingInvite, setPendingInvite] = useState<string | null>(() => getPendingInvitationCode());

  async function hydrateIdentity(version: ApiVersion, knownUser?: AuthUser | null) {
    const resolvedUser = knownUser === undefined ? await currentUser() : knownUser;
    setUser(resolvedUser);
    if (!resolvedUser) {
      setOrganizations([]);
      setAgentCount(0);
      setIntegrationCount(0);
      setPerformance(null);
      return;
    }
    if (entryMode !== 'app') onSignedIn();
    const nextOrganizations = await listOrganizations(version);
    setOrganizations(nextOrganizations);
    if (nextOrganizations[0]) {
      const organizationId = nextOrganizations[0].id;
      const [agents, integrations, performanceSnapshot] = await Promise.all([
        listAgents(version, organizationId).catch(() => []),
        listIntegrations(version, organizationId).catch(() => []),
        loadPerformance(version, organizationId).catch(() => null),
      ]);
      setAgentCount(agents.length);
      setIntegrationCount(integrations.filter((item) => item.status !== 'disconnected').length);
      setPerformance(performanceSnapshot);
    } else {
      setAgentCount(0);
      setIntegrationCount(0);
      setPerformance(null);
    }
  }

  useEffect(() => {
    let active = true;
    Promise.all([
      loadSystemStatus(apiVersion).then((result) => {
        if (active) {
          setStatus(result);
          setStatusError(false);
        }
      }).catch(() => {
        if (active) {
          setStatus(null);
          setStatusError(true);
        }
      }),
      hydrateIdentity(apiVersion).catch(() => {
        if (active) setAuthError('Audoryn could not verify your identity context.');
      }),
    ]).finally(() => {
      if (active) setIdentityLoading(false);
    });
    return () => {
      active = false;
    };
  }, [apiVersion]);

  async function handleSignIn() {
    setAuthError(null);
    try {
      const result = await signIn();
      setIdentityLoading(true);
      await hydrateIdentity(apiVersion, result.user);
      onSignedIn();
    } catch (caught) {
      const code = (caught as { code?: string }).code;
      setAuthError(code === 'popup_blocked' ? 'Allow popups for Audoryn, then try again.' : code === 'popup_closed' ? 'Sign in was cancelled.' : 'Secure sign in failed.');
    } finally {
      setIdentityLoading(false);
    }
  }

  async function handleCreateOrganization(name: string) {
    const organization = await createOrganization(apiVersion, name);
    setOrganizations([organization]);
    setAgentCount(0);
    setIntegrationCount(0);
    setPerformance(null);
    onSignedIn();
  }

  async function handleSignOut() {
    await signOut();
    setUser(null);
    setOrganizations([]);
    setAgentCount(0);
    setIntegrationCount(0);
    setPerformance(null);
    setView('overview');
    onBack();
  }

  function handleInviteJoined(organization: OrganizationAccess) {
    clearPendingInvitationCode();
    setPendingInvite(null);
    setOrganizations((current) => [organization, ...current.filter((item) => item.id !== organization.id)]);
    setView('commercial');
    onSignedIn();
  }

  function handleInviteAbandon() {
    clearPendingInvitationCode();
    setPendingInvite(null);
  }

  if (identityLoading) return <IdentityLoading />;
  if (!user) return <SignInGate onSignIn={handleSignIn} error={authError} mode={entryMode === 'signup' ? 'signup' : 'signin'} onBack={onBack} />;
  if (pendingInvite) return <InvitationGate code={pendingInvite} apiVersion={apiVersion} onJoined={handleInviteJoined} onAbandon={handleInviteAbandon} />;
  if (organizations.length === 0) return <OrganizationOnboarding user={user} apiVersion={apiVersion} onCreate={handleCreateOrganization} />;

  const organization = organizations[0];
  const operational = status?.state === 'operational' && !statusError;
  const section = sectionForView(view);
  const tabs = SECTION_ITEMS[section];

  function renderView() {
    if (view === 'workforce') return <WorkforcePanel organization={organization} user={user!} apiVersion={apiVersion} onApiVersionChange={setApiVersion} onNavigate={setView} />;
    if (view === 'jobs') return <JobsPanel organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} onNavigate={setView} />;
    if (view === 'results') return <ResultsPanel organization={organization} apiVersion={apiVersion} />;
    if (view === 'supervision') return <SupervisionPanel organization={organization} user={user!} apiVersion={apiVersion} onApiVersionChange={setApiVersion} />;
    if (view === 'performance') return <PerformancePanel organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} />;
    if (view === 'runtime') return <RuntimePanel organization={organization} apiVersion={apiVersion} onWorkChanged={() => void hydrateIdentity(apiVersion, user)} />;
    if (view === 'commercial') return <CommercialPanel organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} />;
    if (view === 'memory') return <MemoryPanel organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} />;
    if (view === 'agents') return <AgentsPanel organization={organization} user={user!} apiVersion={apiVersion} onApiVersionChange={setApiVersion} onCountChange={setAgentCount} />;
    if (view === 'integrations') return <IntegrationsPanel organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} onCountChange={setIntegrationCount} />;
    if (view === 'capabilities') return <CapabilitiesPanel organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} />;
    if (view === 'policies') return <PoliciesPanel organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} />;
    if (view === 'actions') return <ActionsPanel organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} />;
    if (view === 'approvals') return <ApprovalsPanel organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} />;
    if (view === 'audit') return <AuditPanel organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} />;
    if (view === 'risk') return <RiskPanel organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} />;
    if (view === 'incidents') return <IncidentsPanel organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} />;
    if (view === 'settings') return <SettingsPanel organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} onWorkspaceUpdated={(name) => setOrganizations((current) => current.map((item) => item.id === organization.id ? { ...item, name } : item))} />;
    return <Overview organization={organization} operational={operational} statusError={statusError} performance={performance} integrationCount={integrationCount} agentCount={agentCount} />;
  }

  return <div className='app-shell'>
    <Sidebar open={mobileNavOpen} onClose={() => setMobileNavOpen(false)} activeView={view} onNavigate={setView} organizationName={organization.name} />
    <main className='main-shell'>
      <header className='topbar'>
        <div className='topbar-title'><span>{organization.name}</span><strong>{labelForView(view)}</strong></div>
        <div className='topbar-actions'>
          <span className={`system-dot ${operational ? 'is-ok' : statusError ? 'is-error' : ''}`} />
          <span className='system-label'>{operational ? 'Systems normal' : 'System unavailable'}</span>
          <button className='account-button' onClick={handleSignOut} title='Sign out'><span className='account-name'>{user.name || user.email || 'Account'}</span><span className='account-action'>Sign out</span></button>
        </div>
      </header>
      {tabs.length > 1 && <nav className='section-nav' aria-label={`${section} navigation`}>{tabs.map((tab) => <button key={tab.view} className={view === tab.view ? 'active' : ''} onClick={() => setView(tab.view)}>{tab.label}</button>)}</nav>}
      <div className='content-wrap'>{renderView()}</div>
    </main>
    <nav className='mobile-primary-nav' aria-label='Mobile primary navigation'>
      <button className={view === 'overview' ? 'active' : ''} onClick={() => setView('overview')}>Home</button>
      <button className={section === 'workforce' ? 'active' : ''} onClick={() => setView('workforce')}>Workforce</button>
      <button className={section === 'operations' ? 'active' : ''} onClick={() => setView('results')}>Operations</button>
      <button className={section === 'governance' || section === 'connections' || section === 'organization' ? 'active' : ''} onClick={() => setMobileNavOpen(true)}>More</button>
    </nav>
  </div>;
}

function Overview({ organization, operational, statusError, performance, integrationCount, agentCount }: { organization: OrganizationAccess; operational: boolean; statusError: boolean; performance: PerformanceWorkspace | null; integrationCount: number; agentCount: number }) {
  const summary = performance?.summary;
  const attentionCount = summary ? summary.escalations + summary.incidents + summary.failed : 0;
  const attention = performance?.activity.filter((item) => attentionStatus(item)).slice(0, 5) ?? [];
  const recent = performance?.activity.slice(0, 7) ?? [];
  const headline = !operational ? 'Audoryn needs attention.' : attentionCount > 0 ? `${attentionCount} items need attention.` : 'Your workforce is running normally.';

  return <div className='home-page'>
    <section className='home-hero'>
      <span className={`home-state ${operational ? 'ok' : 'bad'}`}>{operational ? 'Operational' : 'Unavailable'}</span>
      <h1>{headline}</h1>
      <p>See what your AI workforce is doing, what needs human attention, and where work is moving next inside <strong>{organization.name}</strong>.</p>
    </section>
    {statusError && <div className='fail-closed-banner'><strong>Control plane unavailable.</strong><p>Audoryn is failing closed until system state can be verified.</p></div>}
    <section className='home-stats'>
      <div><span>Workers</span><strong>{summary ? `${summary.activeWorkers}/${summary.workers}` : '—'}</strong><small>active</small></div>
      <div><span>Runs</span><strong>{summary?.runs ?? '—'}</strong><small>observed</small></div>
      <div><span>Success rate</span><strong>{summary ? `${summary.successRate}%` : '—'}</strong><small>terminal runs</small></div>
      <div><span>Needs attention</span><strong>{summary ? attentionCount : '—'}</strong><small>failures + escalations + incidents</small></div>
    </section>
    <section className='home-columns'>
      <div className='home-section'>
        <div className='home-section-head'><div><h2>Needs your attention</h2><p>Only work that may require human judgment or intervention.</p></div></div>
        {attention.length ? <div className='home-list'>{attention.map((item) => <ActivityRow key={item.id} item={item} />)}</div> : <div className='home-empty'>Nothing urgent is waiting on you.</div>}
      </div>
      <div className='home-section'>
        <div className='home-section-head'><div><h2>Recent activity</h2><p>Latest workforce and governance events.</p></div></div>
        {recent.length ? <div className='home-list'>{recent.map((item) => <ActivityRow key={item.id} item={item} />)}</div> : <div className='home-empty'>Activity will appear as your workers begin running jobs.</div>}
      </div>
    </section>
    <section className='home-context'><span>{integrationCount} connected tools</span><span>{agentCount} agent identities</span><span>{organization.role.replace('_', ' ')} access</span></section>
  </div>;
}

function ActivityRow({ item }: { item: ActivityItem }) {
  return <div className='home-activity-row'><div><strong>{item.workerName || item.label}</strong><span>{item.label}</span></div><div><span className={`home-activity-status ${item.status}`}>{item.status.replace('_', ' ')}</span><time>{formatTime(item.occurredAt)}</time></div></div>;
}

function attentionStatus(item: ActivityItem) {
  const status = item.status.toLowerCase();
  return status.includes('fail') || status.includes('held') || status.includes('waiting') || status.includes('open') || status.includes('critical');
}

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const diff = Date.now() - date.getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
