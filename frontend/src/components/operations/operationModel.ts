import type { ActionRecord, ActionStatus } from '../../lib/actionApi';
import type { ResultSummary } from '../../lib/resultsApi';

export function visibleActions(actions: ActionRecord[], status: 'all' | ActionStatus): ActionRecord[] {
  return status === 'all' ? actions : actions.filter((item) => item.status === status);
}

export function featuredResult(items: ResultSummary[]): ResultSummary | null {
  return items.find((item) => item.isNew) ?? items[0] ?? null;
}

export function attentionResults(items: ResultSummary[]): ResultSummary[] {
  return items.filter((item) => item.status !== 'completed');
}
