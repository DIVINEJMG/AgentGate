import test from 'node:test';
import assert from 'node:assert/strict';
import { attentionResults, featuredResult, visibleActions } from '../src/components/operations/operationModel.ts';
import { previewResponse } from '../src/preview/previewResponses.ts';

test('action ledger filters status without changing the original record order', () => {
  const actions = [{ id: 'held', status: 'held' }, { id: 'done', status: 'executed' }, { id: 'held-2', status: 'held' }];
  assert.deepEqual(visibleActions(actions, 'held').map((item) => item.id), ['held', 'held-2']);
  assert.deepEqual(visibleActions(actions, 'all').map((item) => item.id), ['held', 'done', 'held-2']);
});

test('results feature new work while keeping attention separate', () => {
  const items = [{ id: 'old', status: 'completed', isNew: false }, { id: 'failed', status: 'failed', isNew: true }, { id: 'new', status: 'completed', isNew: true }];
  assert.equal(featuredResult(items)?.id, 'failed');
  assert.deepEqual(attentionResults(items).map((item) => item.id), ['failed']);
  assert.equal(featuredResult([]), null);
});

test('operations preview supplies inspectable records and blocks decisions', async () => {
  const base = '/api/v1/organizations/preview-organization';
  const runtime = await previewResponse('GET', `${base}/runtime`);
  const run = await previewResponse('GET', `${base}/runs/preview-run`);
  const supervision = await previewResponse('GET', `${base}/supervision`);
  const actions = await previewResponse('GET', `${base}/actions`);
  assert.equal(runtime.data.runs.length, 1);
  assert.equal(run.data.steps.length, 3);
  assert.equal(supervision.data.escalations.length, 1);
  assert.equal(actions.data.actions.length, 3);
  await assert.rejects(previewResponse('POST', `${base}/approvals/preview-approval-1/decision`, {}), /read-only/);
});
