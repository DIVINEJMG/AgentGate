import { api } from '@appdeploy/client';

export type ApiVersion = 'v1' | 'v2';
export interface SystemStatus { service: string; apiVersion: ApiVersion; state: 'operational'; securityMode: 'fail-closed'; architecture: 'modular-monolith'; currentApiVersion: 'v2'; supportedApiVersions: ApiVersion[]; checkedAt: string; }

export async function loadSystemStatus(version: ApiVersion): Promise<SystemStatus> {
  const response = await api.get(`/api/${version}/system/status`);
  if (version === 'v1') { const d = response.data; return { service:d.service, apiVersion:'v1', state:d.status, securityMode:d.securityMode, architecture:d.architecture, currentApiVersion:d.currentVersion, supportedApiVersions:d.supportedVersions, checkedAt:d.checkedAt }; }
  const d = response.data; return { service:d.service, apiVersion:'v2', state:d.controlPlane.state, securityMode:d.controlPlane.securityMode, architecture:d.architecture.style, currentApiVersion:d.lifecycle.current, supportedApiVersions:d.lifecycle.supported, checkedAt:d.checkedAt };
}
