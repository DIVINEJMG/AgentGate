import { createHash } from 'node:crypto';
import { db } from '@appdeploy/sdk';

interface RuntimeCancellationRecord {
  organizationId: string;
  correlationId: string;
  runId: string;
  reason: string;
  cancelledBy: string;
  cancelledAt: string;
}

function table(organizationId: string, correlationId: string) {
  const digest = createHash('sha256').update(correlationId).digest('hex').slice(0, 32);
  return `runtime_cancellation:${organizationId}:${digest}`;
}

export async function markRuntimeCorrelationCancelled(organizationId: string, correlationId: string, runId: string, cancelledBy: string, reason: string) {
  const { items, nextToken } = await db.list<RuntimeCancellationRecord>(table(organizationId, correlationId), { limit: 2 });
  if (nextToken || items.length > 1) throw new Error('Runtime cancellation state is ambiguous. Audoryn fails closed.');
  if (items[0]) return items[0];
  const record: RuntimeCancellationRecord = { organizationId, correlationId, runId, reason: reason.slice(0, 500), cancelledBy, cancelledAt: new Date().toISOString() };
  const [id] = await db.add(table(organizationId, correlationId), [record]);
  if (!id) throw new Error('Runtime cancellation guard could not be persisted.');
  return { id, ...record };
}

export async function assertRuntimeCorrelationAllowed(organizationId: string, correlationId: string) {
  const { items, nextToken } = await db.list<RuntimeCancellationRecord>(table(organizationId, correlationId), { limit: 2 });
  if (nextToken || items.length > 1) throw new Error('Runtime cancellation state is ambiguous. Execution is denied.');
  if (items[0]) throw new Error(`Managed Run was cancelled: ${items[0].reason}`);
}

