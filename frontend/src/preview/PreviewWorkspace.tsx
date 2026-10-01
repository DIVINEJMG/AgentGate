import ProductApp, { type WorkspacePreviewContext } from '../ProductApp';
import { previewPerformance } from './previewResponses';
import './preview.css';

const previewContext: WorkspacePreviewContext = {
  user: { userId: 'preview-user', name: 'Preview operator', email: 'preview@example.invalid' },
  organization: {
    id: 'preview-organization',
    name: 'Sample workspace',
    role: 'owner',
    createdAt: '2026-09-01T10:00:00Z',
    permissions: [
      'actions.manage', 'agents.manage', 'approvals.review', 'capabilities.manage',
      'incidents.manage', 'integrations.manage', 'jobs.manage', 'jobs.run',
      'memory.manage', 'organizations.manage', 'policies.manage', 'results.export',
      'supervision.manage', 'workforce.manage',
    ],
  },
  status: {
    service: 'Audoryn preview', apiVersion: 'v1', state: 'operational',
    securityMode: 'fail-closed', architecture: 'modular-monolith',
    currentApiVersion: 'v2', supportedApiVersions: ['v1', 'v2'],
    checkedAt: '2026-09-30T10:00:00Z',
  },
  performance: previewPerformance,
};

export default function PreviewWorkspace({ onExit }: { onExit: () => void }) {
  return <ProductApp entryMode='app' onBack={onExit} onSignedIn={() => {}} onModeChange={() => {}} previewContext={previewContext} />;
}
