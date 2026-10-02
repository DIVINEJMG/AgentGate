import type { ConversationMessage, ConversationThread } from '../lib/conversationApi';

export type LiveResultReference = {
  type: 'result';
  id: string;
  name: string;
  presentation: 'live_result';
  status: 'completed';
  summary?: string;
  workerId?: string;
  jobId?: string;
  workItemId?: string;
  runId?: string;
};

export function liveResultReference(message: { resultReferences?: Array<Record<string, unknown>> | null }): LiveResultReference | null {
  for (const raw of message.resultReferences ?? []) {
    if (!raw || typeof raw !== 'object') continue;
    if (raw.type !== 'result' || raw.presentation !== 'live_result' || typeof raw.id !== 'string' || !raw.id) continue;
    return {
      type: 'result',
      id: raw.id,
      name: typeof raw.name === 'string' && raw.name ? raw.name : 'Completed result',
      presentation: 'live_result',
      status: 'completed',
      summary: typeof raw.summary === 'string' ? raw.summary : undefined,
      workerId: typeof raw.workerId === 'string' ? raw.workerId : undefined,
      jobId: typeof raw.jobId === 'string' ? raw.jobId : undefined,
      workItemId: typeof raw.workItemId === 'string' ? raw.workItemId : undefined,
      runId: typeof raw.runId === 'string' ? raw.runId : undefined,
    };
  }
  return null;
}

export function isHumanMessage(role: string) {
  return role === 'human' || role === 'user';
}

export function discoveryWorkers<T extends { name: string; department: string }>(workers: T[], query: string, offset: number) {
  const term = query.trim().toLowerCase();
  const filtered = workers.filter((worker) => `${worker.name} ${worker.department}`.toLowerCase().includes(term));
  const start = Math.max(0, Math.min(offset, Math.max(0, filtered.length - 6)));
  return { filtered, visible: filtered.slice(start, start + 6), start };
}

export function conversationFrames(messages: ConversationMessage[]) {
  const frames: Array<{ id: string; direction: ConversationMessage | null; responses: ConversationMessage[] }> = [];
  for (const message of messages) {
    if (isHumanMessage(message.role)) {
      frames.push({ id: message.id, direction: message, responses: [] });
    } else {
      if (!frames.length) frames.push({ id: message.id, direction: null, responses: [] });
      frames[frames.length - 1].responses.push(message);
    }
  }
  return frames;
}

export function threadsForWorker(threads: ConversationThread[], workerId: string | null) {
  if (!workerId) return [];
  return threads
    .filter((thread) => thread.workerId === workerId)
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
}

export function latestThreadId(threads: ConversationThread[], workerId: string | null) {
  return threadsForWorker(threads, workerId)[0]?.id ?? null;
}
