export type ApiVersion = 'v1' | 'v2';
export interface CanonicalSystemStatus { service:'Audoryn'; state:'operational'; securityMode:'fail-closed'; architecture:'modular-monolith'; currentApiVersion:'v2'; supportedApiVersions:ApiVersion[]; checkedAt:string; }
export function readCanonicalSystemStatus(): CanonicalSystemStatus { return { service:'Audoryn', state:'operational', securityMode:'fail-closed', architecture:'modular-monolith', currentApiVersion:'v2', supportedApiVersions:['v1','v2'], checkedAt:new Date().toISOString() }; }
export function serializeV1(s:CanonicalSystemStatus){return{service:s.service,apiVersion:'v1',status:s.state,securityMode:s.securityMode,architecture:s.architecture,currentVersion:s.currentApiVersion,supportedVersions:s.supportedApiVersions,checkedAt:s.checkedAt};}
export function serializeV2(s:CanonicalSystemStatus){return{service:s.service,version:'v2',controlPlane:{state:s.state,securityMode:s.securityMode},architecture:{style:s.architecture},lifecycle:{current:s.currentApiVersion,supported:s.supportedApiVersions},checkedAt:s.checkedAt};}

