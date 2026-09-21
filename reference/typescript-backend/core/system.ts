export type ApiVersion = 'v1' | 'v2';
export interface CanonicalSystemStatus { service: 'Audoryn'; state: 'operational'; securityMode: 'fail-closed'; architecture: 'modular-monolith'; currentApiVersion: 'v2'; supportedApiVersions: ApiVersion[]; checkedAt: string; }

export function readCanonicalSystemStatus(): CanonicalSystemStatus {
    return { service: 'Audoryn', state: 'operational', securityMode: 'fail-closed', architecture: 'modular-monolith', currentApiVersion: 'v2', supportedApiVersions: ['v1', 'v2'], checkedAt: new Date().toISOString() };
}

export function serializeV1(status: CanonicalSystemStatus) {
    return { service: status.service, apiVersion: 'v1' as const, status: status.state, securityMode: status.securityMode, architecture: status.architecture, currentVersion: status.currentApiVersion, supportedVersions: status.supportedApiVersions, checkedAt: status.checkedAt };
}

export function serializeV2(status: CanonicalSystemStatus) {
    return { service: status.service, version: 'v2' as const, controlPlane: { state: status.state, securityMode: status.securityMode }, architecture: { style: status.architecture }, lifecycle: { current: status.currentApiVersion, supported: status.supportedApiVersions }, checkedAt: status.checkedAt };
}