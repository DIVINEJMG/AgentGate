import { api } from '../platform/client';
import type { ApiVersion } from './systemApi';

export type ConversationStatus =
  | 'completed'
  | 'accepted'
  | 'waiting_confirmation'
  | 'clarification_required'
  | 'waiting_integration'
  | 'unavailable';

export interface ConversationReference {
  type: string;
  id: string;
  name?: string | null;
}

export interface ConversationActionHint {
  kind: 'connect_integration' | 'confirm_command' | string;
  provider?: string;
  label?: string;
  [key: string]: string | undefined;
}

export interface ConversationReceipt {
  status: ConversationStatus;
  message: string;
  references: ConversationReference[];
  result_references: ConversationReference[];
  command_id: string | null;
  failure_category?: string | null;
  action_hints: ConversationActionHint[];
}

export interface ConversationThread {
  id: string;
  organizationId: string;
  workerId: string | null;
  title: string;
  status: string;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
}

export interface ConversationTurn {
  thread?: ConversationThread;
  response: {
    id: string;
    role: string;
    content: string;
    createdAt: string;
  };
  receipt: ConversationReceipt;
}

function unwrap<T>(version: ApiVersion, response: { data: any }): T {
  return (version === 'v1' ? response.data : response.data.data) as T;
}

function normalizeReceipt(raw: any): ConversationReceipt {
  return {
    status: raw.status,
    message: raw.message ?? '',
    references: raw.references ?? [],
    result_references: raw.result_references ?? raw.resultReferences ?? [],
    command_id: raw.command_id ?? raw.commandId ?? null,
    failure_category: raw.failure_category ?? raw.failureCategory ?? null,
    action_hints: raw.action_hints ?? raw.actionHints ?? [],
  };
}

function normalizeTurn(version: ApiVersion, response: { data: any }): ConversationTurn {
  const raw = unwrap<any>(version, response);
  return {
    thread: raw.thread,
    response: raw.response,
    receipt: normalizeReceipt(raw.receipt),
  };
}

export async function createConversation(
  version: ApiVersion,
  organizationId: string,
  workerId?: string,
  title?: string,
): Promise<ConversationThread> {
  const response = await api.post(
    `/api/${version}/organizations/${organizationId}/conversations`,
    { workerId: workerId || undefined, title: title || undefined },
  );
  return unwrap<{ thread: ConversationThread }>(version, response).thread;
}

export async function sendConversationMessage(
  version: ApiVersion,
  organizationId: string,
  threadId: string,
  content: string,
  artifactReferences: Array<Record<string, unknown>> = [],
): Promise<ConversationTurn> {
  const response = await api.post(
    `/api/${version}/organizations/${organizationId}/conversations/${threadId}/messages`,
    { content, artifactReferences },
  );
  return normalizeTurn(version, response);
}

export async function askWorker(
  version: ApiVersion,
  organizationId: string,
  workerId: string,
  content: string,
): Promise<ConversationTurn> {
  const response = await api.post(
    `/api/${version}/organizations/${organizationId}/workforce/workers/${workerId}/ask`,
    { content, artifactReferences: [] },
  );
  return normalizeTurn(version, response);
}

export async function uploadConversationAttachment(
  version: ApiVersion,
  organizationId: string,
  threadId: string,
  input: { name: string; mediaType: string; contentBase64: string; question?: string },
) {
  const response = await api.post(
    `/api/${version}/organizations/${organizationId}/conversations/${threadId}/attachments`,
    input,
  );
  return unwrap<{
    artifact: { id: string; name: string; mediaType: string; category: string | null };
    analysis: { id: string; status: string; findings: Record<string, unknown> };
  }>(version, response);
}

export async function confirmConversationCommand(
  version: ApiVersion,
  organizationId: string,
  threadId: string,
  commandId: string,
): Promise<ConversationTurn> {
  const response = await api.post(
    `/api/${version}/organizations/${organizationId}/conversations/${threadId}/commands/${commandId}/confirm`,
    {},
  );
  return normalizeTurn(version, response);
}
