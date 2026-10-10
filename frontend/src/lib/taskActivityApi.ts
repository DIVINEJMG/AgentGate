import { api } from '../platform/client';
import type { ApiVersion } from './systemApi';

export interface TaskActivity {
  id: string; requestMessageId?: string | null; status: string; phase: string; detail: string;
  startedAt: string; updatedAt: string; elapsedSeconds: number;
  phaseStartedAt?: string | null; retryAt?: string | null; retryScheduled?: boolean; responseState?: string | null;
  deadlineAt?: string | null;
  isActive?: boolean; executionState?: 'running' | 'retry_scheduled' | 'inactive' | 'unknown';
  responseReceivedAt?: string | null; responseBytes?: number;
  completedActions: number; preservedWork: boolean;
  events: {id: string; state: string; label: string; at: string; verified?: boolean}[];
  commands: {id: string; command: string; status: string; exitCode: number | null; stdout: string; stderr: string; observedAt: string}[];
  changedFiles: string[]; links: {title: string; url: string}[];
}
export interface ActivitySnapshot { observedAt: string; tasks: TaskActivity[] }
export async function loadTaskActivity(version: ApiVersion, organizationId: string, threadId: string, signal?: AbortSignal): Promise<ActivitySnapshot> {
  const result = await api.get(`/api/${version}/organizations/${organizationId}/conversations/${threadId}/activity`, {signal});
  return result.data as ActivitySnapshot;
}
