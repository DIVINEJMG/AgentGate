import assert from 'node:assert/strict';
import test from 'node:test';

import { githubAdapter, normalizeGitHubIssueInput } from '../backend/integrations/github.ts';
import { normalizeSlackMessageInput, slackAdapter } from '../backend/integrations/slack.ts';
import { actionPayload, canonicalActionPayload } from '../backend/core/actionPayload.ts';

test('GitHub issue creation is a high-risk write that always requires approval', () => {
  const capability = githubAdapter.capabilities.find((item) => item.scope === 'github.repository.issues.create');
  assert.deepEqual(capability && {
    operation: capability.providerOperation,
    action: capability.action,
    risk: capability.risk,
    requiresApproval: capability.requiresApproval,
    requiresCredential: capability.requiresCredential,
  }, {
    operation: 'repository.issue.create',
    action: 'create',
    risk: 'high',
    requiresApproval: true,
    requiresCredential: true,
  });
});

test('GitHub issue input is normalized to a bounded provider payload', () => {
  assert.deepEqual(normalizeGitHubIssueInput({
    title: '  Durable worker recovery  ',
    body: 'Capture the recovery evidence.',
    labels: [' reliability ', 'f22', 'reliability'],
  }), {
    title: 'Durable worker recovery',
    body: 'Capture the recovery evidence.',
    labels: ['reliability', 'f22'],
  });
});

test('GitHub issue input rejects missing titles, unknown fields, and oversized bodies', () => {
  assert.throws(() => normalizeGitHubIssueInput({ body: 'No title' }), /title/i);
  assert.throws(() => normalizeGitHubIssueInput({ title: 'Valid', assignees: ['someone'] }), /unsupported/i);
  assert.throws(() => normalizeGitHubIssueInput({ title: 'Valid', body: 'x'.repeat(10_001) }), /10,000/);
});

test('action payload snapshots are JSON-safe, bounded, and canonically fingerprintable', () => {
  const first = actionPayload({ labels: ['f22'], title: 'Create issue' });
  const second = actionPayload({ title: 'Create issue', labels: ['f22'] });
  assert.equal(canonicalActionPayload(first), canonicalActionPayload(second));
  assert.throws(() => actionPayload(['not-an-object']), /object/i);
  assert.throws(() => actionPayload({ body: 'x'.repeat(16_385) }), /16,384/);
});

test('Slack message creation is a credentialed, approved write with bounded input', () => {
  const capability = slackAdapter.capabilities.find((item) => item.scope === 'slack.messages.create');
  assert.equal(capability?.requiresApproval, true);
  assert.equal(capability?.requiresCredential, true);
  assert.equal(capability?.risk, 'high');
  assert.deepEqual(normalizeSlackMessageInput({ channel: '  C12345 ', text: '  Hello workers  ' }), { channel: 'C12345', text: 'Hello workers' });
  assert.throws(() => normalizeSlackMessageInput({ channel: 'general', text: '' }), /text/i);
  assert.throws(() => normalizeSlackMessageInput({ channel: 'C12345', text: 'x'.repeat(4_001) }), /4,000/);
});
