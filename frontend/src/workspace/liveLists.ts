// Which workspace lists a live event can change, so the workspace reloads only those.
// Reloading all four lists on every event (several per second during a run) was the main
// source of database transfer.

export type LiveList = 'workforce' | 'runs' | 'approvals' | 'escalations';

export const ALL_LISTS: ReadonlySet<LiveList> = new Set<LiveList>(['workforce', 'runs', 'approvals', 'escalations']);

/** Bursts of live events collapse into one reload of the affected lists, at most this often. */
export const LIVE_BATCH_MS = 1000;

/** The lists an event can change. Unknown events reload everything, as before. */
export function listsFor(eventType: string): ReadonlySet<LiveList> {
  if (eventType === 'run.failed' || eventType === 'run.waiting_approval') return new Set<LiveList>(['runs', 'escalations']);
  if (eventType.startsWith('run.') || eventType === 'result.created') return new Set<LiveList>(['runs']);
  if (eventType.startsWith('approval.')) return new Set<LiveList>(['approvals', 'escalations']);
  if (eventType === 'worker.status.changed') return new Set<LiveList>(['workforce']);
  if (eventType === 'incident.created') return new Set<LiveList>(['escalations']);
  return ALL_LISTS;
}
