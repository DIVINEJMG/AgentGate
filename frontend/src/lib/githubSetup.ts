import {api} from '../platform/apiClient';

export interface GitHubSetupResult {
  status: 'connected' | 'installation_required' | 'select_installation';
  onboardingId?: string;
  connectionId?: string;
  installationUrl?: string | null;
  installations?: {id: string; account: string; suspended: boolean}[];
}

// Share an in-flight check, including StrictMode remounts. Never cache completed
// access checks: permission and installation state may change during setup.
const pending = new Map<string, Promise<GitHubSetupResult>>();
export function continueGitHubSetup(organizationId: string, flowId: string, installationId?: string) {
  const key = JSON.stringify([organizationId, flowId, installationId]);
  const previous = pending.get(key);
  if (previous) return previous;
  const request = api.post<GitHubSetupResult>(
    `/api/v2/organizations/${encodeURIComponent(organizationId)}/github/signup-connection/${encodeURIComponent(flowId)}`,
    installationId ? {installation_id: installationId} : {},
  ).then(({data}) => data);
  pending.set(key, request);
  void request.then(() => {if (pending.get(key) === request) pending.delete(key);},
    () => {if (pending.get(key) === request) pending.delete(key);});
  return request;
}

export function installationReturn(flowId: string): string | undefined {
  try {
    const hint = JSON.parse(window.sessionStorage.getItem('audoryn.github.installation') || 'null');
    return hint?.flowId === flowId && typeof hint.installationId === 'string' && /^[0-9]{1,20}$/.test(hint.installationId)
      ? hint.installationId : undefined;
  } catch {return undefined;}
}
