import test from 'node:test';
import assert from 'node:assert/strict';
import { approvalInConversation, conversationFrames, discoveryWorkers, isHumanMessage, latestThreadId, liveResultLabel, liveResultReference, threadsForWorker } from '../src/components/workerChatModel.ts';

test('approval ownership stays in its conversation, including legacy references and thread switches', () => {
  const messages = [{ threadId: 'old', resultReferences: [{ type: 'approval', id: 'approval' }] }];
  assert.equal(approvalInConversation({ id: 'approval', conversationThreadId: 'old' }, 'new', messages), false);
  assert.equal(approvalInConversation({ id: 'approval' }, 'new', messages), false);
  assert.equal(approvalInConversation({ id: 'approval' }, 'old', messages), true);
  assert.equal(approvalInConversation({ id: 'approval', conversationThreadId: 'new' }, 'new', []), true);
  assert.equal(approvalInConversation({ id: 'approval' }, null, messages), false);
});

const threads = [
  { id: 'a-old', workerId: 'a', updatedAt: '2026-09-01T10:00:00Z' },
  { id: 'b-new', workerId: 'b', updatedAt: '2026-10-01T10:00:00Z' },
  { id: 'a-new', workerId: 'a', updatedAt: '2026-09-30T10:00:00Z' },
];

test('worker switching shows only that worker’s threads, newest first', () => {
  assert.deepEqual(threadsForWorker(threads, 'a').map((item) => item.id), ['a-new', 'a-old']);
  assert.deepEqual(threadsForWorker(threads, 'b').map((item) => item.id), ['b-new']);
  assert.deepEqual(threads.map((item) => item.id), ['a-old', 'b-new', 'a-new']);
});

test('new workers open an empty conversation', () => {
  assert.equal(latestThreadId(threads, 'missing'), null);
  assert.equal(latestThreadId(threads, null), null);
});

test('discovery filters workers and keeps a six-avatar window within the results', () => {
  const workers = Array.from({ length: 9 }, (_, index) => ({ name: `Worker ${index + 1}`, department: index < 3 ? 'Research' : 'Operations' }));
  assert.deepEqual(discoveryWorkers(workers, '', 0).visible.map((worker) => worker.name), workers.slice(0, 6).map((worker) => worker.name));
  assert.deepEqual(discoveryWorkers(workers, '', 4).visible.map((worker) => worker.name), workers.slice(3).map((worker) => worker.name));
  const search = discoveryWorkers(workers, 'research', 4);
  assert.equal(search.start, 0);
  assert.deepEqual(search.visible.map((worker) => worker.name), ['Worker 1', 'Worker 2', 'Worker 3']);
});

test('exchange frames preserve message order including worker-first and unanswered turns', () => {
  const messages = [
    { id: 'opening', role: 'assistant' },
    { id: 'question', role: 'user' },
    { id: 'answer', role: 'assistant' },
    { id: 'follow-up', role: 'assistant' },
    { id: 'unanswered', role: 'user' },
  ];
  const frames = conversationFrames(messages);
  assert.deepEqual(frames.map((frame) => [frame.direction?.id ?? null, frame.responses.map((message) => message.id)]), [
    [null, ['opening']],
    ['question', ['answer', 'follow-up']],
    ['unanswered', []],
  ]);
  assert.deepEqual(messages.map((message) => message.id), ['opening', 'question', 'answer', 'follow-up', 'unanswered']);
});

test('backend human messages are identified separately from worker replies', () => {
  assert.equal(isHumanMessage('human'), true);
  assert.equal(isHumanMessage('user'), true);
  assert.equal(isHumanMessage('worker'), false);
  assert.equal(isHumanMessage('system'), false);
  const frames = conversationFrames([
    { id: 'sent', role: 'human' },
    { id: 'reply', role: 'worker' },
  ]);
  assert.deepEqual(frames.map((frame) => [frame.direction?.id, frame.responses.map((message) => message.id)]), [
    ['sent', ['reply']],
  ]);
});


test('live result UI is reserved for runtime-backed completion references', () => {
  const live = liveResultReference({
    resultReferences: [{
      type: 'result',
      id: 'result-1',
      name: 'Catalog check result',
      presentation: 'live_result',
      status: 'completed',
      summary: 'Catalog check completed.',
      runId: 'run-1',
    }],
  });
  assert.equal(live?.id, 'result-1');
  assert.equal(live?.presentation, 'live_result');
  assert.equal(live?.runId, 'run-1');
});

test('ordinary result references stay normal chat messages', () => {
  assert.equal(liveResultReference({
    resultReferences: [{ type: 'result', id: 'result-2', name: 'Earlier result' }],
  }), null);
  assert.equal(liveResultReference({ resultReferences: [] }), null);
});


test('blocked and partial outcomes remain live results with accurate labels', () => {
  for (const [outcome, label] of [['blocked', 'NEEDS ATTENTION'], ['partial', 'PARTIALLY COMPLETED']]) {
    const result = liveResultReference({ resultReferences: [{ type: 'result', id: 'r',
      presentation: 'live_result', status: 'attention', executionOutcome: outcome }] });
    assert.equal(result.status, 'attention');
    assert.equal(liveResultLabel(result), label);
  }
  const completed = liveResultReference({ resultReferences: [{ type: 'result', id: 'r',
    presentation: 'live_result', status: 'completed' }] });
  assert.equal(liveResultLabel(completed), 'COMPLETED');
});
