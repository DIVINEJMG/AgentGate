import { githubAdapter } from './github';
import type { IntegrationAdapter, IntegrationProvider } from './contracts';
const adapters: Record<IntegrationProvider, IntegrationAdapter> = { github: githubAdapter };
export function getIntegrationAdapter(provider: IntegrationProvider) { return adapters[provider]; }
