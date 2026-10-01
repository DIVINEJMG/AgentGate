import test from 'node:test';
import assert from 'node:assert/strict';
import { filterJobs, filterMemories } from '../src/components/workspaceListModel.ts';

const names = new Map([['worker-1', 'Research analyst']]);

test('job registry combines state and worker-name search without altering source order', () => {
  const jobs = [
    { id: '1', workerId: 'worker-1', name: 'Weekly brief', objective: 'Summarize evidence', status: 'active' },
    { id: '2', workerId: 'worker-1', name: 'Daily review', objective: 'Review exceptions', status: 'paused' },
    { id: '3', workerId: 'worker-2', name: 'Operations update', objective: 'Track work', status: 'active' },
  ];
  assert.deepEqual(filterJobs(jobs, names, 'RESEARCH', 'active').map((job) => job.id), ['1']);
  assert.deepEqual(filterJobs(jobs, names, '', 'all').map((job) => job.id), ['1', '2', '3']);
});

test('memory library isolates scope, hides archived context, and orders recent updates first', () => {
  const memories = [
    { id: 'old', workerId: 'worker-1', scope: 'worker', status: 'active', title: 'Briefing', content: 'Sources', tags: [], updatedAt: '2026-09-01T00:00:00Z' },
    { id: 'org', workerId: null, scope: 'organization', status: 'active', title: 'Briefing', content: 'Sources', tags: [], updatedAt: '2026-09-30T00:00:00Z' },
    { id: 'archived', workerId: 'worker-1', scope: 'worker', status: 'archived', title: 'Briefing', content: 'Sources', tags: [], updatedAt: '2026-09-29T00:00:00Z' },
    { id: 'new', workerId: 'worker-1', scope: 'worker', status: 'active', title: 'Briefing', content: 'Sources', tags: [], updatedAt: '2026-09-30T00:00:00Z' },
  ];
  assert.deepEqual(filterMemories(memories, names, 'worker', 'research', false).map((item) => item.id), ['new', 'old']);
  assert.deepEqual(filterMemories(memories, names, 'worker', '', true).map((item) => item.id), ['new', 'archived', 'old']);
  assert.deepEqual(memories.map((item) => item.id), ['old', 'org', 'archived', 'new']);
});
