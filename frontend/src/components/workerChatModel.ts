import type { ConversationMessage, ConversationThread } from '../lib/conversationApi';

export function discoveryWorkers<T extends { name: string; department: string }>(workers: T[], query: string, offset: number) {
  const term = query.trim().toLowerCase();
  const filtered = workers.filter((worker) => `${worker.name} ${worker.department}`.toLowerCase().includes(term));
  const start = Math.max(0, Math.min(offset, Math.max(0, filtered.length - 6)));
  return { filtered, visible: filtered.slice(start, start + 6), start };
}

export function conversationFrames(messages: ConversationMessage[]) {
  const frames: Array<{ id: string; direction: ConversationMessage | null; responses: ConversationMessage[] }> = [];
  for (const message of messages) {
    if (message.role === 'user') {
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
