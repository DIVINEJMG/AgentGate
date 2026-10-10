const terminal = new Set(['completed', 'failed', 'cancelled', 'partial_completion', 'policy_denied']);
export function isActivityTerminal(status: string): boolean { return terminal.has(status); }
export function isActivityActive(task: {status: string; phase: string; retryAt?: string | null; retryScheduled?: boolean; isActive?: boolean; executionState?: string}): boolean {
  if (isActivityTerminal(task.status) || ['paused', 'uncertain_outcome'].includes(task.status)) return false;
  if (task.isActive !== undefined) return task.isActive;
  if (task.executionState === 'inactive' || task.executionState === 'unknown') return false;
  // Automatically scheduled recovery is still ongoing; human/setup waits belong to their request.
  if (task.status.startsWith('waiting') || task.phase === 'recovery') return task.retryScheduled === true;
  return true;
}
export function activityElapsed(task: {status: string; elapsedSeconds: number; isActive?: boolean}, observedAt: string, now: number): string {
  const seconds = Math.max(0, task.elapsedSeconds + (task.isActive === false || isActivityTerminal(task.status) || task.status.startsWith('waiting') || task.status === 'paused' ? 0 : Math.floor((now - Date.parse(observedAt)) / 1000)));
  return seconds >= 3600 ? `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}
export function phaseLabel(phase: string): string {
  return ({preparing: 'Preparing', awaiting_planner: 'Awaiting planner', executing: 'Executing action', testing: 'Command / tests', publishing: 'Publishing', recovery: 'Needs attention / recovery', completed: 'Completed', attention: 'Needs attention'} as Record<string, string>)[phase] ?? 'Working';
}
