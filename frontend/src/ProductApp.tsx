import { useEffect, useState } from 'react';
import '@fontsource-variable/geist';
import '@fontsource-variable/geist-mono';
import { auth } from './platform/authClient';
import GitHubSignupConnection from './components/GitHubSignupConnection';
import type { AuthCredentials, AuthUser } from './platform/client';
import AgentsPanel from './components/AgentsPanel';
import AuditPanel from './components/AuditPanel';
import PoliciesPanel from './components/PoliciesPanel';
import RiskPanel from './components/RiskPanel';
import IncidentsPanel from './components/IncidentsPanel';
import SettingsPanel from './components/SettingsPanel';
import WorkforcePanel from './components/WorkforcePanel';
import WorkerChatPage from './components/WorkerChatPage';
import JobsPanel from './components/JobsPanel';
import CommercialPanel from './components/CommercialPanel';
import InvitationGate from './components/InvitationGate';
import MemoryPanel from './components/MemoryPanel';
import { IdentityLoading, OrganizationOnboarding, SignInGate } from './components/IdentityGate';
import { createOrganization, currentUser, listOrganizations, signIn, signOut, signUp, type OrganizationAccess } from './lib/identityApi';
import { loadSystemStatus, type ApiVersion, type SystemStatus } from './lib/systemApi';
import { clearPendingInvitationCode, getPendingInvitationCode } from './lib/commercialApi';
import type { PerformanceWorkspace } from './lib/performanceApi';
import type { AppView } from './navigation';
import { LiveProvider } from './workspace/live';
import { formatRoute, navigate, routeForView, useRoute, type WorkspaceRoute } from './workspace/routes';
import { Shell } from './workspace/Shell';
import { useTheme } from './workspace/theme';
import { RequestProgress, Toaster } from './workspace/feedback';
import HomePage from './workspace/pages/HomePage';
import InboxPage from './workspace/pages/InboxPage';
import ResultsPage from './workspace/pages/ResultsPage';
import ActivityPage from './workspace/pages/ActivityPage';
import ConnectionsHome from './workspace/connections/ConnectionsHome';
import { DirectoryPage, ToolPage } from './workspace/connections/Directory';
import AccountPage from './workspace/connections/AccountPage';
import AccessMap from './workspace/connections/AccessMap';
import RunsPage from './workspace/pages/RunsPage';
import PerformancePage from './workspace/pages/PerformancePage';
import { DeveloperSettings } from './workspace/pages/LegacyPage';
import './audit.css';
import './incidents.css';
import './product.css';
import './workforce.css';
import './jobs.css';
import './memory.css';
import './commercial.css';
import './r1.css';
import './r3.css';
import './auth-page.css';
import './conversation-field.css';
import './connections-stage.css';
import './workspace/workspace.css';
import './workspace/pages.css';
import './workspace/conversation.css';
import './workspace/legacy.css';
import './workspace/connections/connections.css';
import './workspace/navigation.css';

type EntryMode = 'signin' | 'signup' | 'app';
export type WorkspacePreviewContext = {
  user: AuthUser;
  organization: OrganizationAccess;
  status: SystemStatus;
  performance: PerformanceWorkspace;
};

const API_VERSION_KEY = 'audoryn.workspace.apiVersion';
function storedApiVersion(): ApiVersion {
  try { return window.localStorage.getItem(API_VERSION_KEY) === 'v2' ? 'v2' : 'v1'; } catch { return 'v1'; }
}

export default function ProductApp({ entryMode, onBack, onSignedIn, onModeChange, previewContext }: { entryMode: EntryMode; onBack: () => void; onSignedIn: () => void; onModeChange: (mode: 'signin' | 'signup') => void; previewContext?: WorkspacePreviewContext }) {
  const route = useRoute();
  const { theme, choose: chooseTheme } = useTheme();
  const [apiVersion, setApiVersionState] = useState<ApiVersion>(storedApiVersion);
  const [status, setStatus] = useState<SystemStatus | null>(previewContext?.status ?? null);
  const [statusError, setStatusError] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(previewContext?.user ?? null);
  const [organizations, setOrganizations] = useState<OrganizationAccess[]>(previewContext ? [previewContext.organization] : []);
  const [identityLoading, setIdentityLoading] = useState(!previewContext);
  const [authError, setAuthError] = useState<string | null>(null);
  const [githubSetup, setGitHubSetup] = useState<string | null>(() => window.sessionStorage.getItem('audoryn.github.setup'));
  const [pendingInvite, setPendingInvite] = useState<string | null>(() => getPendingInvitationCode());

  function setApiVersion(version: ApiVersion) {
    setApiVersionState(version);
    try { window.localStorage.setItem(API_VERSION_KEY, version); } catch { /* Session-only preference. */ }
  }

  // Existing GitHub setup links open the integrations destination.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has('github_setup') && route.page === 'home') navigate(formatRoute({ page: 'connections', view: 'tool', id: 'github' }) + window.location.search, { replace: true });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function hydrateIdentity(version: ApiVersion, knownUser?: AuthUser | null) {
    const resolvedUser = knownUser === undefined ? (await auth.completeGitHub()) || await currentUser() : knownUser;
    setGitHubSetup(window.sessionStorage.getItem('audoryn.github.setup'));
    setUser(resolvedUser);
    if (!resolvedUser) {
      setOrganizations([]);
      return;
    }
    if (entryMode !== 'app') onSignedIn();
    const nextOrganizations = await listOrganizations(version);
    setOrganizations(nextOrganizations);
  }

  useEffect(() => {
    if (previewContext) return;
    let active = true;
    Promise.all([
      loadSystemStatus(apiVersion).then((result) => {
        if (active) { setStatus(result); setStatusError(false); }
      }).catch(() => {
        if (active) { setStatus(null); setStatusError(true); }
      }),
      hydrateIdentity(apiVersion).catch((cause) => {
        if (active) setAuthError(cause instanceof Error ? cause.message : 'Audoryn could not verify your identity context.');
      }),
    ]).finally(() => {
      if (active) setIdentityLoading(false);
    });
    return () => { active = false; };
  }, [apiVersion, previewContext]); // eslint-disable-line react-hooks/exhaustive-deps

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
    onSignedIn();
  }

  async function handleSignOut() {
    await signOut();
    setUser(null);
    setOrganizations([]);
    onBack();
  }

  function handleInviteJoined(organization: OrganizationAccess) {
    clearPendingInvitationCode();
    setPendingInvite(null);
    setOrganizations((current) => [organization, ...current.filter((item) => item.id !== organization.id)]);
    navigate({ page: 'settings', section: 'billing' });
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

  if (githubSetup) return <GitHubSignupConnection flowId={githubSetup} organizations={organizations} onFinished={(chosen, onboardingId) => {
    window.sessionStorage.removeItem('audoryn.github.setup');
    window.sessionStorage.removeItem('audoryn.github.installation'); setGitHubSetup(null);
    if (chosen) setOrganizations((current) => [chosen, ...current.filter((item) => item.id !== chosen.id)]);
    const url = new URL(window.location.href); url.searchParams.delete('github_setup');
    if (onboardingId) url.searchParams.set('github_setup', onboardingId);
    window.history.replaceState({}, '', url);
    if (chosen || onboardingId) navigate(formatRoute({ page: 'connections', view: onboardingId ? 'tool' : 'home', id: onboardingId ? 'github' : undefined }) + url.search);
  }} />;

  const organization = organizations[0];
  const signedInUser = user;
  const operational = status?.state === 'operational' && !statusError;
  const accountLabel = signedInUser.name || signedInUser.email?.split('@')[0] || 'Account';
  const go = (view: AppView) => navigate(routeForView(view));
  const common = { organization, apiVersion, onApiVersionChange: setApiVersion };

  function page(current: WorkspaceRoute) {
    switch (current.page) {
      case 'home': return <HomePage organization={organization} user={signedInUser} apiVersion={apiVersion} operational={operational || Boolean(previewContext)} statusError={statusError} />;
      case 'inbox': return <InboxPage organization={organization} user={signedInUser} apiVersion={apiVersion} route={current} />;
      case 'workers': case 'worker': return <WorkforcePanel {...common} user={signedInUser} route={current} onNavigate={go} onOpenChat={(workerId) => navigate({ page: 'conversations', workerId })} />;
      case 'conversations': return <WorkerChatPage organization={organization} apiVersion={apiVersion} initialWorkerId={current.workerId ?? null} initialThreadId={current.threadId ?? null} onNavigate={go} />;
      case 'jobs': return <JobsPanel {...common} onNavigate={go} />;
      case 'results': return <ResultsPage organization={organization} apiVersion={apiVersion} resultId={current.resultId} />;
      case 'memory': return <MemoryPanel {...common} />;
      case 'activity': return <ActivityPage organization={organization} apiVersion={apiVersion} actionId={current.actionId} />;
      case 'runs': return <RunsPage organization={organization} apiVersion={apiVersion} runId={current.runId} onWorkChanged={() => { if (!previewContext) void hydrateIdentity(apiVersion, signedInUser); }} />;
      case 'performance': return <PerformancePage organization={organization} apiVersion={apiVersion} />;
      case 'policies': return <PoliciesPanel {...common} />;
      case 'risk': return <RiskPanel {...common} />;
      case 'incidents': return <IncidentsPanel {...common} />;
      case 'audit': return <AuditPanel {...common} />;
      case 'connections':
        if (current.view === 'directory') return <DirectoryPage organization={organization} apiVersion={apiVersion} />;
        if (current.view === 'tool') return <ToolPage organization={organization} apiVersion={apiVersion} slug={current.id} />;
        if (current.view === 'account' && current.id) return <AccountPage organization={organization} apiVersion={apiVersion} id={current.id} />;
        if (current.view === 'identities') return <AgentsPanel {...common} user={signedInUser} identityId={current.id} onCountChange={() => {}} />;
        if (current.view === 'access') return <AccessMap organization={organization} apiVersion={apiVersion} />;
        return <ConnectionsHome organization={organization} apiVersion={apiVersion} />;
      case 'settings': return current.section === 'billing' ? <CommercialPanel {...common} /> : current.section === 'developer' ? <DeveloperSettings apiVersion={apiVersion} onApiVersionChange={setApiVersion} /> : <SettingsPanel {...common} onWorkspaceUpdated={(name) => setOrganizations((items) => items.map((item) => item.id === organization.id ? { ...item, name } : item))} />;
    }
  }

  return <LiveProvider organization={organization} apiVersion={apiVersion}>
    <RequestProgress />
    <Shell route={route} organization={organization} organizations={organizations} accountName={accountLabel} accountEmail={signedInUser.email} theme={theme} onTheme={chooseTheme}
      onSwitchOrganization={(chosen) => { setOrganizations((items) => [chosen, ...items.filter((item) => item.id !== chosen.id)]); navigate({ page: 'home' }); }}
      onSignOut={previewContext ? undefined : () => void handleSignOut()} preview={previewContext ? { onExit: onBack } : undefined}>
      {page(route)}
    </Shell>
    <Toaster />
  </LiveProvider>;
}
