export type IntegrationProvider = 'github';
export type IntegrationCredentialMode = 'none' | 'encrypted_secret';
export interface ProviderConnectionResult { resourceKey: string; displayName: string; webUrl: string; config: Record<string, string>; metadata: Record<string, string | number | boolean | null>; }
export interface ProviderHealthResult { state: 'healthy' | 'degraded'; message: string; checkedAt: string; metadata?: Record<string, string | number | boolean | null>; }
export interface IntegrationAdapter { provider: IntegrationProvider; displayName: string; credentialPolicy: 'optional'; supportedOperations: string[]; validateConnection(config: Record<string, unknown>, credential?: string): Promise<ProviderConnectionResult>; checkHealth(config: Record<string, string>, credential?: string): Promise<ProviderHealthResult>; }
