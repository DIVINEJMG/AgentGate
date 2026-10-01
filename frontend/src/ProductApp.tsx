import { useEffect, useState } from 'react';
import { Activity, Bot, ChevronRight, House, LogOut, Menu } from 'lucide-react';
import type { AuthCredentials, AuthUser } from './platform/client';
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
import WorkerChatPage from './components/WorkerChatPage';
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
import WorkspaceOverview from './components/WorkspaceOverview';
import { listAgents } from './lib/agentApi';
import { createOrganization, currentUser, listOrganizations, signIn, signOut, signUp, type OrganizationAccess } from './lib/identityApi';
import { listIntegrations } from './lib/integrationApi';
import { loadSystemStatus, type ApiVersion, type SystemStatus } from './lib/systemApi';
import { clearPendingInvitationCode, getPendingInvitationCode } from './lib/commercialApi';
import { loadPerformance, type PerformanceWorkspace } from './lib/performanceApi';
import { labelForView, PRIMARY_SECTIONS, SECTION_ITEMS, sectionForView, type AppView } from './navigation';
import './gateway.css';
import './audit.css';
import './risk.css';
import './incidents.css';
import './product.css';
import './workforce.css';
import './jobs.css';
import './memory.css';
import './jobs-memory-redesign.css';
import './supervision.css';
import './performance.css';
import './results.css';
import './commercial.css';
import './r1.css';
import './r3.css';
import './auth-page.css';
import './workspace-overview.css';
import './workspace-shell.css';
import './worker-pages.css';
import './conversation-field.css';
import './operations-stage.css';
import './governance-stage.css';
import './connections-stage.css';
import './organization-stage.css';

type EntryMode = 'signin' | 'signup' | 'app';
export type WorkspacePreviewContext = {
  user: AuthUser;
  organization: OrganizationAccess;
  status: SystemStatus;
  performance: PerformanceWorkspace;
};

export default function ProductApp({ entryMode, onBack, onSignedIn, onModeChange, previewContext }: { entryMode: EntryMode; onBack: () => void; onSignedIn: () => void; onModeChange: (mode: 'signin' | 'signup') => void; previewContext?: WorkspacePreviewContext }) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try { return window.localStorage.getItem('audoryn.sidebar.collapsed') === 'true'; } catch { return false; }
  });
  const [conversationSidebarOpen, setConversationSidebarOpen] = useState(false);
  const [view, setView] = useState<AppView>('overview');
  const [chatWorkerId, setChatWorkerId] = useState<string | null>(null);
  const [apiVersion, setApiVersion] = useState<ApiVersion>('v1');
  const [status, setStatus] = useState<SystemStatus | null>(previewContext?.status ?? null);
  const [statusError, setStatusError] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(previewContext?.user ?? null);
  const [organizations, setOrganizations] = useState<OrganizationAccess[]>(previewContext ? [previewContext.organization] : []);
  const [agentCount, setAgentCount] = useState(previewContext ? 2 : 0);
  const [integrationCount, setIntegrationCount] = useState(previewContext ? 2 : 0);
  const [performance, setPerformance] = useState<PerformanceWorkspace | null>(previewContext?.performance ?? null);
  const [identityLoading, setIdentityLoading] = useState(!previewContext);
  const [authError, setAuthError] = useState<string | null>(null);
  const [pendingInvite, setPendingInvite] = useState<string | null>(() => getPendingInvitationCode());

  useEffect(() => {
    if (!mobileNavOpen) return;
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setMobileNavOpen(false);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [mobileNavOpen]);

  useEffect(() => {
    if (view !== 'conversations' || !conversationSidebarOpen) return;
    const timer = window.setTimeout(() => setConversationSidebarOpen(false), 5000);
    return () => window.clearTimeout(timer);
  }, [view, conversationSidebarOpen]);

  useEffect(() => {
    if (view !== 'conversations') setConversationSidebarOpen(false);
  }, [view]);

  useEffect(() => {
    if (view !== 'conversations' || !mobileNavOpen) return;
    const timer = window.setTimeout(() => setMobileNavOpen(false), 5000);
    return () => window.clearTimeout(timer);
  }, [view, mobileNavOpen]);

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
    if (previewContext) return;
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
  }, [apiVersion, previewContext]);

  async function handleAuthenticate(credentials: AuthCredentials, mode: 'signin' | 'signup') {
    setAuthError(null);
    try {
      const authenticated = mode === 'signup' ? await signUp(credentials) : await signIn(credentials);
      setIdentityLoading(true);
      await hydrateIdentity(apiVersion, authenticated);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : 'Secure authentication failed.';
      setAuthError(message);
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
  if (!user) return <SignInGate key={entryMode} onAuthenticate={handleAuthenticate} error={authError} mode={entryMode === 'signup' ? 'signup' : 'signin'} onBack={onBack} onModeChange={(mode) => { setAuthError(null); onModeChange(mode); }} pendingInvitation={Boolean(pendingInvite)} />;
  if (pendingInvite) return <InvitationGate code={pendingInvite} apiVersion={apiVersion} onJoined={handleInviteJoined} onAbandon={handleInviteAbandon} />;
  if (organizations.length === 0) return <OrganizationOnboarding user={user} apiVersion={apiVersion} onCreate={handleCreateOrganization} />;

  const organization = organizations[0];
  const operational = status?.state === 'operational' && !statusError;
  const section = sectionForView(view);
  const tabs = SECTION_ITEMS[section];
  const sectionLabel = PRIMARY_SECTIONS.find((item) => item.id === section)?.label || 'Workspace';
  const accountLabel = user.name || user.email?.split('@')[0] || 'Account';

  function toggleSidebar() {
    if (view === 'conversations') {
      setConversationSidebarOpen((current) => !current);
      return;
    }
    setSidebarCollapsed((current) => {
      try { window.localStorage.setItem('audoryn.sidebar.collapsed', String(!current)); } catch { /* Session-only preference when storage is unavailable. */ }
      return !current;
    });
  }

  function renderView() {
    if (view === 'workforce') return <WorkforcePanel organization={organization} user={user!} apiVersion={apiVersion} onApiVersionChange={setApiVersion} onNavigate={setView} onOpenChat={(workerId) => { setChatWorkerId(workerId); setView('conversations'); }} />;
    if (view === 'conversations') return <WorkerChatPage organization={organization} apiVersion={apiVersion} initialWorkerId={chatWorkerId} onNavigate={setView} />;
    if (view === 'jobs') return <JobsPanel organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} onNavigate={setView} />;
    if (view === 'results') return <ResultsPanel organization={organization} apiVersion={apiVersion} />;
    if (view === 'supervision') return <SupervisionPanel organization={organization} user={user!} apiVersion={apiVersion} onApiVersionChange={setApiVersion} />;
    if (view === 'performance') return <PerformancePanel organization={organization} apiVersion={apiVersion} onApiVersionChange={setApiVersion} />;
    if (view === 'runtime') return <RuntimePanel organization={organization} apiVersion={apiVersion} onWorkChanged={() => { if (!previewContext) void hydrateIdentity(apiVersion, user); }} />;
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
    return <WorkspaceOverview organization={organization} operational={operational} statusError={statusError} performance={performance} integrationCount={integrationCount} agentCount={agentCount} onNavigate={setView} />;
  }

  return <div className={`app-shell workspace-shell${sidebarCollapsed || view === 'conversations' ? ' sidebar-collapsed' : ''}${view === 'conversations' ? ' conversation-mode' : ''}${conversationSidebarOpen && view === 'conversations' ? ' conversation-sidebar-open' : ''}${previewContext ? ' workspace-preview' : ''}`}>
    {previewContext && <div className='workspace-preview-banner'><strong>WORKSPACE PREVIEW</strong><span>Sample data · no backend connection · changes are not saved</span><button type='button' onClick={onBack}>Exit preview</button></div>}
    <Sidebar open={mobileNavOpen} collapsed={view === 'conversations' ? !conversationSidebarOpen : sidebarCollapsed} onClose={() => { setMobileNavOpen(false); setConversationSidebarOpen(false); }} onToggleCollapse={toggleSidebar} activeView={view} onNavigate={setView} organizationName={organization.name} accountName={accountLabel} onSignOut={previewContext ? undefined : handleSignOut} />
    {view === 'conversations' && conversationSidebarOpen && <button type='button' className='conversation-sidebar-dismiss' aria-label='Close navigation' onClick={() => setConversationSidebarOpen(false)} />}
    {view === 'conversations' && <button type='button' className='conversation-mobile-menu' aria-label='Open workspace navigation' aria-controls='workspace-primary-sidebar' aria-expanded={mobileNavOpen} onClick={() => setMobileNavOpen(true)}><Menu size={20} aria-hidden='true' /></button>}
    <main className='main-shell'>
      <header className='topbar workspace-topbar'>
        <div className='workspace-topbar-mobile'>
          <button type='button' className='workspace-mobile-menu-button' aria-label='Open workspace navigation' aria-controls='workspace-primary-sidebar' aria-expanded={mobileNavOpen} onClick={() => setMobileNavOpen(true)}><Menu size={20} aria-hidden='true' /></button>
          <div className='workspace-mobile-page'><span>{sectionLabel}</span><strong>{view === 'overview' ? 'Overview' : labelForView(view)}</strong></div>
          <span className={`workspace-mobile-status${operational ? ' is-ok' : statusError ? ' is-error' : ''}`} role='img' aria-label={previewContext ? 'Preview data' : operational ? 'Systems operational' : 'System unavailable'} />
        </div>
        <div className='workspace-topbar-location'><span>{organization.name}</span><ChevronRight size={13} aria-hidden='true' /><span>{sectionLabel}</span><ChevronRight size={13} aria-hidden='true' /><strong>{view === 'overview' ? 'Overview' : labelForView(view)}</strong></div>
        <div className='workspace-topbar-actions'>
          <span className={`workspace-topbar-status${operational ? ' is-ok' : statusError ? ' is-error' : ''}`}><span className='workspace-topbar-dot' />{previewContext ? 'Preview data' : operational ? 'Systems operational' : 'System unavailable'}</span>
          {previewContext ? <div className='workspace-topbar-user' aria-label={`Preview account: ${accountLabel}`}><span className='workspace-topbar-avatar'>{accountLabel.slice(0, 1).toUpperCase()}</span><span>{accountLabel}</span></div> : <button type='button' className='workspace-topbar-user' onClick={handleSignOut} aria-label={`Sign out as ${accountLabel}`} title='Sign out'><span className='workspace-topbar-avatar'>{accountLabel.slice(0, 1).toUpperCase()}</span><span>{accountLabel}</span><LogOut size={14} aria-hidden='true' /></button>}
        </div>
      </header>
      {view !== 'conversations' && tabs.length > 1 && <nav className='section-nav' aria-label={`${section} navigation`}>{tabs.map((tab) => <button key={tab.view} className={view === tab.view ? 'active' : ''} onClick={() => { if (tab.view === 'conversations') setChatWorkerId(null); setView(tab.view); }}>{tab.label}</button>)}</nav>}
      <div className={`content-wrap${view === 'conversations' ? ' conversation-content' : ''}`}>{renderView()}</div>
    </main>
    <nav className='mobile-primary-nav' aria-label='Mobile primary navigation'>
      <button type='button' className={section === 'home' ? 'active' : ''} aria-current={section === 'home' ? 'page' : undefined} onClick={() => setView('overview')}><House size={19} aria-hidden='true' /><span>Home</span></button>
      <button type='button' className={section === 'workforce' ? 'active' : ''} aria-current={section === 'workforce' ? 'page' : undefined} onClick={() => setView('workforce')}><Bot size={19} aria-hidden='true' /><span>Workforce</span></button>
      <button type='button' className={section === 'operations' ? 'active' : ''} aria-current={section === 'operations' ? 'page' : undefined} onClick={() => setView('results')}><Activity size={19} aria-hidden='true' /><span>Operations</span></button>
      <button type='button' className={mobileNavOpen || section === 'governance' || section === 'connections' || section === 'organization' ? 'active' : ''} aria-controls='workspace-primary-sidebar' aria-expanded={mobileNavOpen} onClick={() => setMobileNavOpen(true)}><Menu size={19} aria-hidden='true' /><span>More</span></button>
    </nav>
  </div>;
}
