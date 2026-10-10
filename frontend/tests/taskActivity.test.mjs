import test from 'node:test';
import assert from 'node:assert/strict';
import { startActivityFeed } from '../src/components/taskActivityFeed.ts';
import { activityElapsed, isActivityTerminal } from '../src/components/taskActivityModel.ts';

test('idle polling slows down, while visibility and explicit refresh resume immediately', async t => {
  t.mock.timers.enable({apis: ['setTimeout']});
  const previousDocument = globalThis.document;
  const listeners = new Map();
  globalThis.document = {hidden:false,
    addEventListener: (name, callback) => listeners.set(name, callback),
    removeEventListener: name => listeners.delete(name)};
  let calls = 0;
  const feed = startActivityFeed({load: async () => (++calls, {active:false}),
    nextIntervalMs: value => value.active ? 10000 : 60000,
    onSnapshot: () => {}, onConnectivity: () => {}});
  try {
    await new Promise(resolve => setImmediate(resolve));
    t.mock.timers.tick(10000);
    assert.equal(calls, 1);
    t.mock.timers.tick(50000);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls, 2);
    globalThis.document.hidden = true;
    t.mock.timers.tick(60000);
    await feed.refresh();
    assert.equal(calls, 2);
    globalThis.document.hidden = false;
    listeners.get('visibilitychange')();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls, 3);
  } finally {
    feed.stop();
    assert.equal(listeners.size, 0);
    globalThis.document = previousDocument;
  }
});

test('failed progress requests retain the last snapshot and recover on the next refresh', async () => {
  const snapshots = [], connectivity = [];
  let calls = 0;
  const feed = startActivityFeed({intervalMs: 100000,
    load: async () => { if (++calls === 2) throw Error('offline'); return {status: calls === 1 ? 'running' : 'failed'}; },
    onSnapshot: value => snapshots.push(value), onConnectivity: value => connectivity.push(value),
  });
  try {
    await new Promise(resolve => setImmediate(resolve));
    await feed.refresh();
    assert.deepEqual(snapshots, [{status: 'running'}]);
    await feed.refresh();
    assert.deepEqual(snapshots, [{status: 'running'}, {status: 'failed'}]);
    assert.deepEqual(connectivity, [true, false, true]);
  } finally { feed.stop(); }
});

test('switching conversations discards late responses and overlapping refreshes', async () => {
  let resolve, calls = 0;
  const snapshots = [];
  const feed = startActivityFeed({intervalMs: 100000,
    load: () => { calls++; return new Promise(done => {resolve = done;}); },
    onSnapshot: value => snapshots.push(value), onConnectivity: () => {},
  });
  await feed.refresh();
  assert.equal(calls, 1);
  feed.stop();
  resolve('old conversation');
  await new Promise(done => setImmediate(done));
  assert.deepEqual(snapshots, []);
});

test('an unresponsive progress request times out without stopping the feed', async () => {
  const connectivity = [], snapshots = [];
  let calls = 0;
  const feed = startActivityFeed({intervalMs: 100000, timeoutMs: 5,
    load: signal => ++calls === 1 ? new Promise((_, reject) => signal.addEventListener('abort', () => reject(Error('timeout')))) : Promise.resolve('recovered'),
    onSnapshot: value => snapshots.push(value), onConnectivity: value => connectivity.push(value),
  });
  try {
    await new Promise(done => setTimeout(done, 20));
    await feed.refresh();
    assert.deepEqual(connectivity, [false, true]);
    assert.deepEqual(snapshots, ['recovered']);
  } finally {feed.stop();}
});

test('terminal elapsed time stays frozen while waits remain visible', () => {
  assert.equal(isActivityTerminal('failed'), true);
  assert.equal(isActivityTerminal('waiting_ai'), false);
  assert.equal(activityElapsed({status: 'completed', elapsedSeconds: 65}, '2026-10-07T10:00:00Z', Date.parse('2026-10-07T11:00:00Z')), '1m 5s');
  assert.equal(activityElapsed({status: 'running', elapsedSeconds: 65}, '2026-10-07T10:00:00Z', Date.parse('2026-10-07T10:00:10Z')), '1m 15s');
});

test('ongoing work stays live; stopped work attaches only to its initiating request', async () => {
  const { isActivityActive } = await import('../src/components/taskActivityModel.ts');
  const tasks = [
    {id:'run', requestMessageId:'request-a', status:'running', phase:'awaiting_planner'},
    {id:'done', requestMessageId:'request-a', status:'completed', phase:'completed'},
    {id:'paused', requestMessageId:'request-b', status:'waiting_ai', phase:'recovery', retryAt:'stale'},
    {id:'retry', requestMessageId:'request-b', status:'waiting_ai', phase:'recovery', retryScheduled:true},
    {id:'failed', requestMessageId:'request-b', status:'failed', phase:'attention'},
  ];
  assert.deepEqual(tasks.filter(isActivityActive).map(task => task.id), ['run', 'retry']);
  assert.deepEqual(tasks.filter(task => !isActivityActive(task) && task.requestMessageId === 'request-a').map(task => task.id), ['done']);
  assert.equal(tasks.some(task => task.requestMessageId === 'follow-up'), false);
});


test('saved running labels cannot override confirmed inactive execution', async () => {
  const { isActivityActive } = await import('../src/components/taskActivityModel.ts');
  assert.equal(isActivityActive({status:'running', phase:'executing', isActive:false, executionState:'inactive'}), false);
  assert.equal(isActivityActive({status:'accepted', phase:'preparing', isActive:false}), false);
  assert.equal(isActivityActive({status:'running', phase:'executing', executionState:'unknown'}), false);
  assert.equal(isActivityActive({status:'running', phase:'executing', isActive:true}), true);
  assert.equal(isActivityActive({status:'completed', phase:'completed', isActive:true}), false);
});

test('inactive process clocks freeze and long durations stay compact', () => {
  const observed = '2026-10-08T10:00:00Z';
  assert.equal(activityElapsed({status:'running', elapsedSeconds:65, isActive:false}, observed, Date.parse('2026-10-08T11:00:00Z')), '1m 5s');
  assert.equal(activityElapsed({status:'running', elapsedSeconds:39065, isActive:false}, observed, Date.parse(observed)), '10h 51m');
});
