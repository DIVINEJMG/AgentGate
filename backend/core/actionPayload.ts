const MAX_ACTION_PAYLOAD_BYTES = 16_384;

function sorted(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sorted);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => [key, sorted(item)]));
  }
  return value;
}

export function actionPayload(raw: unknown): Record<string, unknown> {
  if (raw === undefined || raw === null) return {};
  if (typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Action input must be a JSON object.');
  let serialized: string;
  try { serialized = JSON.stringify(raw); } catch { throw new Error('Action input must be JSON serializable.'); }
  if (!serialized) throw new Error('Action input must be JSON serializable.');
  if (Buffer.byteLength(serialized, 'utf8') > MAX_ACTION_PAYLOAD_BYTES) throw new Error('Action input must be 16,384 bytes or fewer.');
  return JSON.parse(serialized) as Record<string, unknown>;
}

export function canonicalActionPayload(payload: Record<string, unknown>) {
  return JSON.stringify(sorted(payload));
}
