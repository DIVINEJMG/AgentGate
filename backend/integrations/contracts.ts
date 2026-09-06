export type IntegrationProvider='github'|'gmail'|'google_drive'|'slack'|'google_calendar'|'generic_mcp';
export type IntegrationCredentialMode='none'|'encrypted_secret';
export type CapabilityRisk='low'|'medium'|'high'|'critical';
export interface ProviderCapabilityDescriptor{providerOperation:string;resourceType:string;action:string;target:string;scope:string;description:string;risk:CapabilityRisk}
export interface ProviderConnectionResult{resourceKey:string;displayName:string;webUrl:string;config:Record<string,string>;metadata:Record<string,string|number|boolean|null>}
export interface ProviderHealthResult{state:'healthy'|'degraded';message:string;checkedAt:string;metadata?:Record<string,string|number|boolean|null>}
export interface ProviderExecutionResult{providerOperation:string;summary:string;executedAt:string;providerRequestId:string|null;data:unknown}
export interface IntegrationAdapter{provider:IntegrationProvider;displayName:string;credentialPolicy:'optional'|'required'|'disabled';capabilities:ProviderCapabilityDescriptor[];supportedOperations:string[];validateConnection(config:Record<string,unknown>,credential?:string):Promise<ProviderConnectionResult>;checkHealth(config:Record<string,string>,credential?:string):Promise<ProviderHealthResult>;executeOperation(config:Record<string,string>,providerOperation:string,credential?:string):Promise<ProviderExecutionResult>}
