import test from 'node:test';
import assert from 'node:assert/strict';
import { previewResponse } from '../src/preview/previewResponses.ts';
import { policyOrder } from '../src/components/governanceOrder.ts';

const base = '/api/v1/organizations/preview-organization';

test('policy track follows priority and deny-first precedence without mutating input', () => {
  const rules = [
    { id: 'allow', name: 'Allow', priority: 500, effect: 'allow' },
    { id: 'review', name: 'Review', priority: 500, effect: 'require_approval' },
    { id: 'deny', name: 'Deny', priority: 500, effect: 'deny' },
    { id: 'high', name: 'High', priority: 700, effect: 'allow' },
  ];
  assert.deepEqual(policyOrder(rules).map(rule => rule.id), ['high', 'deny', 'review', 'allow']);
  assert.equal(rules[0].id, 'allow');
});

test('governance preview exposes a connected control story without real writes', async () => {
  const [policies, risk, incidents, target, audit, trace] = await Promise.all([
    previewResponse('GET', `${base}/policies`),
    previewResponse('GET', `${base}/risk`),
    previewResponse('GET', `${base}/incidents`),
    previewResponse('GET', `${base}/execution-controls/agent/preview-agent-2`),
    previewResponse('GET', `${base}/audit`),
    previewResponse('GET', `${base}/audit?correlationId=preview-correlation-3`),
  ]);
  assert.deepEqual(policies.data.policies.map(item => item.effect), ['deny', 'require_approval', 'allow']);
  assert.equal(risk.data.assessments[0].assessment.effectiveRisk, 'critical');
  assert.equal(risk.data.assessments[0].correlationId, incidents.data.incidents[0].correlationId);
  assert.equal(incidents.data.incidents[0].status, 'acknowledged');
  assert.equal(target.data.control.state, 'suspended');
  assert.equal(audit.data.events.length, 4);
  assert.equal(trace.data.events.length, 3);
  assert.ok(trace.data.events.every(event => event.correlationId === 'preview-correlation-3'));
  await assert.rejects(previewResponse('POST', `${base}/risk-assessments`, {}), /read-only/);
  await assert.rejects(previewResponse('POST', `${base}/execution-controls/agent/preview-agent-2`, {}), /read-only/);
});
