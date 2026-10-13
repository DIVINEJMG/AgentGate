import test from 'node:test';
import assert from 'node:assert/strict';
import {ALL_LISTS, LIVE_BATCH_MS, listsFor} from '../src/workspace/liveLists.ts';

const lists = type => [...listsFor(type)].sort();

test('run progress reloads only the runs list', () => {
  for (const type of ['run.created', 'run.started', 'run.progress', 'run.step.started', 'run.step.completed', 'run.completed', 'result.created']) assert.deepEqual(lists(type), ['runs'], type);
});

test('events that can raise an escalation also reload escalations', () => {
  assert.deepEqual(lists('run.failed'), ['escalations', 'runs']);
  assert.deepEqual(lists('run.waiting_approval'), ['escalations', 'runs']);
  assert.deepEqual(lists('incident.created'), ['escalations']);
  assert.deepEqual(lists('approval.created'), ['approvals', 'escalations']);
  assert.deepEqual(lists('approval.decided'), ['approvals', 'escalations']);
});

test('worker changes reload the workforce; unknown events reload everything', () => {
  assert.deepEqual(lists('worker.status.changed'), ['workforce']);
  assert.equal(listsFor('something.new'), ALL_LISTS);
});

test('bursts are batched for a second', () => {
  assert.equal(LIVE_BATCH_MS, 1000);
});
