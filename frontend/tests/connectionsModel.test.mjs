import test from 'node:test';
import assert from 'node:assert/strict';
import { scopeChanges, visibleIdentities, visibleProviders } from '../src/components/connectionsModel.ts';
import { previewResponse } from '../src/preview/previewResponses.ts';

const base = '/api/v1/organizations/preview-organization';

test('provider search and status filters use current connections', async () => {
  const response = await previewResponse('GET', `${base}/integrations`);
  const providers = [
    { provider: 'github', name: 'GitHub', description: 'Repositories', credential: 'optional', state: 'available' },
    { provider: 'slack', name: 'Slack', description: 'Channels', credential: 'required', state: 'available' },
    { provider: 'browser', name: 'Governed Browser', description: 'Websites', credential: 'optional', state: 'available' },
  ];
  assert.deepEqual(visibleProviders(providers, response.data.integrations, '', 'connected').map(item => item.provider), ['github', 'browser']);
  assert.deepEqual(visibleProviders(providers, response.data.integrations, 'channel', 'not_connected').map(item => item.provider), ['slack']);
});

test('identity filter and scope changes remain tied to the selected agent', async () => {
  const response = await previewResponse('GET', `${base}/agents`);
  assert.deepEqual(visibleIdentities(response.data.agents, 'operations', 'active').map(item => item.id), ['preview-agent-2']);
  const research = await previewResponse('GET', `${base}/agents/preview-agent-1/capabilities`);
  const operations = await previewResponse('GET', `${base}/agents/preview-agent-2/capabilities`);
  assert.notDeepEqual(research.data.profile.declaredScopes, operations.data.profile.declaredScopes);
  assert.deepEqual(scopeChanges(research.data.profile.declaredScopes, ['browser.page.read', 'github.repository.write']), { added: ['github.repository.write'], removed: ['github.repository.read'] });
  await assert.rejects(previewResponse('POST', `${base}/integrations/connect/slack`, {}), /read-only/);
  await assert.rejects(previewResponse('PUT', `${base}/agents/preview-agent-1/capabilities`, {}), /read-only/);
});
