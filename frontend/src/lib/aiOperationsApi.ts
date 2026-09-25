import { api } from '../platform/client';
import type { ApiVersion } from './systemApi';

export interface AIOperationsSnapshot {
  configuration: {
    enabled: boolean;
    configured: boolean;
    provider: string;
    providerHealth: string;
    coordinatorModel: string;
    visionModel: string;
  };
  invocations: {
    sampleSize: number;
    successes: number;
    failures: number;
    successRate: number | null;
    averageLatencyMs: number | null;
    errorsByCategory: Record<string, number>;
    roleRouting: Record<string, { provider: string; model: string; invocations: number }>;
    window: { limit: number; truncated: boolean };
  };
  planner: {
    waitingCount: number;
    waitingConfiguration: number;
    waitingProvider: number;
    window: { limit: number; truncated: boolean };
  };
}

export async function loadAIOperations(
  version: ApiVersion,
  organizationId: string,
): Promise<AIOperationsSnapshot> {
  const response = await api.get(`/api/${version}/organizations/${organizationId}/ai/operations`);
  return (version === 'v1' ? response.data : response.data.data) as AIOperationsSnapshot;
}
