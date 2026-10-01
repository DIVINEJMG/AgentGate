import type { PolicyRecord } from '../lib/policyApi';

const effectPrecedence = { deny: 0, require_approval: 1, allow: 2 } as const;

export function policyOrder(policies: PolicyRecord[]): PolicyRecord[] {
  return [...policies].sort((a, b) => b.priority - a.priority || effectPrecedence[a.effect] - effectPrecedence[b.effect] || a.name.localeCompare(b.name));
}
