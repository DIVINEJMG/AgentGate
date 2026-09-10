import { api } from '@appdeploy/client';

export type ApiVersion = 'v1' | 'v2';
export interface SystemStatus { service: string; apiVersion: ApiVersion; state: 'operational'; securityMode: 'fail-closed'; architecture: 'modular-monolith'; currentApiVersion: 'v2'; supportedApiVersions: ApiVersion[]; checkedAt: string; }

interface V1Payload { service: string; apiVersion: 'v1'; status: 'operational'; securityMode: 'fail-closed'; architecture: 'modular-monolith'; currentVersion: 'v2'; supportedVersions: ApiVersion[]; checkedAt: string; }
interface V2Payload { service: string; version: 'v2'; controlPlane: { state: 'operational'; securityMode: 'fail-closed' }; architecture: { style: 'modular-monolith' }; lifecycle: { current: 'v2'; supported: ApiVersion[] }; checkedAt: string; }

export async function loadSystemStatus(version: ApiVersion): Promise<SystemStatus> {
    const response = await api.get(`/api/${version}/system/status`);
    if (version === 'v1') {
        const data = response.data as V1Payload;
        return { service: data.service, apiVersion: 'v1', state: data.status, securityMode: data.securityMode, architecture: data.architecture, currentApiVersion: data.currentVersion, supportedApiVersions: data.supportedVersions, checkedAt: data.checkedAt };
    }
    const data = response.data as V2Payload;
    return { service: data.service, apiVersion: 'v2', state: data.controlPlane.state, securityMode: data.controlPlane.securityMode, architecture: data.architecture.style, currentApiVersion: data.lifecycle.current, supportedApiVersions: data.lifecycle.supported, checkedAt: data.checkedAt };
}