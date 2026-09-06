import { db } from '@appdeploy/sdk';

export type RuntimeDispatchState = 'queued' | 'active' | 'waiting_approval';
interface RuntimeDispatchRecord { organizationId: string; workItemId: string; state: RuntimeDispatchState; runId: string | null; notBefore: string; createdAt: string; updatedAt: string; }
interface RuntimeCursorRecord { cursor: string | null; updatedAt: string; }
export type RuntimeDispatch = RuntimeDispatchRecord & { id: string };

const TABLE = 'runtime_dispatch';
const STATE_TABLE = 'runtime_dispatch_state';

export async function registerRuntimeDispatch(organizationId: string, workItemId: string, notBefore: string, state: RuntimeDispatchState = 'queued', runId: string | null = null) {
  const now = new Date().toISOString();
  const [id] = await db.add(TABLE, [{ organizationId, workItemId, state, runId, notBefore, createdAt: now, updatedAt: now }]);
  if (!id) throw new Error('Managed Runtime dispatch registration failed.');
  return id;
}

export async function updateRuntimeDispatch(id: string, patch: Partial<Pick<RuntimeDispatchRecord, 'state' | 'runId' | 'notBefore'>>) {
  const [current] = await db.get<RuntimeDispatchRecord>(TABLE, [id]);
  if (!current) throw new Error('Managed Runtime dispatch entry is unavailable.');
  const record: RuntimeDispatchRecord = { ...current, ...patch, updatedAt: new Date().toISOString() };
  const [updated] = await db.update(TABLE, [{ id, record }]);
  if (!updated) throw new Error('Managed Runtime dispatch checkpoint could not be updated.');
  return { id, ...record };
}

export async function removeRuntimeDispatch(id: string | null | undefined) {
  if (!id) return;
  await db.delete(TABLE, [id]);
}

export async function readRuntimeDispatchPage(limit = 5) {
  const { items: states } = await db.list<RuntimeCursorRecord>(STATE_TABLE, { limit: 1 });
  const state = states[0];
  const page = await db.list<RuntimeDispatchRecord>(TABLE, { limit, nextToken: state?.cursor ?? undefined });
  return { items: page.items.map((item) => ({ ...item } as RuntimeDispatch)), nextToken: page.nextToken ?? null, stateId: state?.id ?? null };
}

export async function checkpointRuntimeDispatch(stateId: string | null, cursor: string | null) {
  const record: RuntimeCursorRecord = { cursor, updatedAt: new Date().toISOString() };
  if (stateId) {
    const [updated] = await db.update(STATE_TABLE, [{ id: stateId, record }]);
    if (!updated) console.warn('Managed Runtime dispatch cursor could not be updated.');
    return;
  }
  const [id] = await db.add(STATE_TABLE, [record]);
  if (!id) console.warn('Managed Runtime dispatch cursor could not be created.');
}
