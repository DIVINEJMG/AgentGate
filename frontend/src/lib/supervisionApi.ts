import { api, notifications } from '@appdeploy/client';
import type { ApiVersion } from './systemApi';

export type EscalationTrigger = 'job_failure' | 'policy_decision' | 'risk_high' | 'risk_critical';
export type EscalationSeverity = 'low' | 'medium' | 'high' | 'critical';
export type EscalationStatus = 'open' | 'acknowledged' | 'resolved';

export interface EscalationRule {
  id: string | null;
  organizationId: string;
  trigger: EscalationTrigger;
  enabled: boolean;
  severity: EscalationSeverity;
  notifySupervisor: boolean;
  updatedBy: string;
  updatedAt: string;
}

export interface Escalation {
  id: string;
  organizationId: string;
  trigger: EscalationTrigger;
  severity: EscalationSeverity;
  status: EscalationStatus;
  workerId: string;
  workerName: string;
  supervisorUserId: string;
  assignedToUserId: string;
  jobId: string | null;
  workItemId: string | null;
  runId: string | null;
  actionId: string | null;
  approvalId: string | null;
  incidentId: string | null;
  correlationId: string | null;
  sourceType: string;
  sourceId: string;
  title: string;
  summary: string;
  reason: string;
  notes: Array<{ body: string; authorUserId: string; createdAt: string }>;
  interventions: Array<{ action: 'cancel_run' | 'pause_worker' | 'create_incident'; reason: string; actorUserId: string; occurredAt: string; result: string }>;
  notification: { state: 'not_attempted' | 'sent' | 'unavailable' | 'failed'; attemptedAt: string | null; sent: number; failed: number };
  createdAt: string;
  updatedAt: string;
  acknowledgedBy: string | null;
  acknowledgedAt: string | null;
  resolvedBy: string | null;
  resolvedAt: string | null;
}

export interface SupervisionWorkspace {
  escalations: Escalation[];
  rules: EscalationRule[];
  summary: { total: number; open: number; acknowledged: number; resolved: number; critical: number; assignedToMe: number };
  window: { limit: number; truncated: boolean };
}

type V2Escalation = {
  identity: { id: string; trigger: EscalationTrigger; status: EscalationStatus; severity: EscalationSeverity; createdAt: string; updatedAt: string };
  organizationId: string;
  assignment: { workerId: string; workerName: string; supervisorUserId: string; assignedToUserId: string };
  source: { type: string; id: string; jobId: string | null; workItemId: string | null; runId: string | null; actionId: string | null; approvalId: string | null; correlationId: string | null };
  details: { title: string; summary: string; reason: string };
  notes: Escalation['notes'];
  interventions: Escalation['interventions'];
  incidentId: string | null;
  notification: Escalation['notification'];
  lifecycle: { acknowledgedBy: string | null; acknowledgedAt: string | null; resolvedBy: string | null; resolvedAt: string | null };
};

type V2Rule = {
  identity: { id: string | null; trigger: EscalationTrigger };
  behavior: { enabled: boolean; severity: EscalationSeverity; notifySupervisor: boolean };
  audit: { updatedBy: string; updatedAt: string | null };
  organizationId: string;
};

function normalizeEscalation(value: V2Escalation): Escalation {
  return {
    id: value.identity.id,
    organizationId: value.organizationId,
    trigger: value.identity.trigger,
    severity: value.identity.severity,
    status: value.identity.status,
    workerId: value.assignment.workerId,
    workerName: value.assignment.workerName,
    supervisorUserId: value.assignment.supervisorUserId,
    assignedToUserId: value.assignment.assignedToUserId,
    jobId: value.source.jobId,
    workItemId: value.source.workItemId,
    runId: value.source.runId,
    actionId: value.source.actionId,
    approvalId: value.source.approvalId,
    incidentId: value.incidentId,
    correlationId: value.source.correlationId,
    sourceType: value.source.type,
    sourceId: value.source.id,
    title: value.details.title,
    summary: value.details.summary,
    reason: value.details.reason,
    notes: value.notes,
    interventions: value.interventions,
    notification: value.notification,
    createdAt: value.identity.createdAt,
    updatedAt: value.identity.updatedAt,
    acknowledgedBy: value.lifecycle.acknowledgedBy,
    acknowledgedAt: value.lifecycle.acknowledgedAt,
    resolvedBy: value.lifecycle.resolvedBy,
    resolvedAt: value.lifecycle.resolvedAt,
  };
}

function normalizeRule(value: V2Rule): EscalationRule {
  return { id: value.identity.id, organizationId: value.organizationId, trigger: value.identity.trigger, enabled: value.behavior.enabled, severity: value.behavior.severity, notifySupervisor: value.behavior.notifySupervisor, updatedBy: value.audit.updatedBy, updatedAt: value.audit.updatedAt ?? '' };
}

export async function listSupervision(version: ApiVersion, organizationId: string): Promise<SupervisionWorkspace> {
  const response = await api.get(`/api/${version}/organizations/${organizationId}/supervision`);
  if (version === 'v1') return response.data as SupervisionWorkspace;
  const data = response.data.data;
  return { escalations: (data.items as V2Escalation[]).map(normalizeEscalation), rules: (data.rules as V2Rule[]).map(normalizeRule), summary: data.summary, window: data.window };
}

export async function updateRule(version: ApiVersion, organizationId: string, trigger: EscalationTrigger, patch: Partial<Pick<EscalationRule, 'enabled' | 'severity' | 'notifySupervisor'>>) {
  const response = await api.put(`/api/${version}/organizations/${organizationId}/supervision/rules/${trigger}`, patch);
  return version === 'v1' ? response.data.rule as EscalationRule : normalizeRule(response.data.data.rule as V2Rule);
}

export async function setEscalationStatus(version: ApiVersion, organizationId: string, id: string, status: 'acknowledged' | 'resolved', note = '') {
  const response = await api.post(`/api/${version}/organizations/${organizationId}/escalations/${id}/status`, { status, note });
  return version === 'v1' ? response.data.escalation as Escalation : normalizeEscalation(response.data.data.escalation as V2Escalation);
}

export async function addEscalationNote(version: ApiVersion, organizationId: string, id: string, note: string) {
  const response = await api.post(`/api/${version}/organizations/${organizationId}/escalations/${id}/notes`, { note });
  return version === 'v1' ? response.data.escalation as Escalation : normalizeEscalation(response.data.data.escalation as V2Escalation);
}

export async function reassignEscalation(version: ApiVersion, organizationId: string, id: string, assignedToUserId: string) {
  const response = await api.post(`/api/${version}/organizations/${organizationId}/escalations/${id}/reassign`, { assignedToUserId });
  return version === 'v1' ? response.data.escalation as Escalation : normalizeEscalation(response.data.data.escalation as V2Escalation);
}

export async function interveneEscalation(version: ApiVersion, organizationId: string, id: string, action: 'cancel_run' | 'pause_worker' | 'create_incident', reason: string) {
  const response = await api.post(`/api/${version}/organizations/${organizationId}/escalations/${id}/intervene`, { action, reason });
  return version === 'v1' ? { escalation: response.data.escalation as Escalation, result: response.data.result } : { escalation: normalizeEscalation(response.data.data.escalation as V2Escalation), result: response.data.data.result };
}

export async function enableSupervisionNotifications() {
  await notifications.subscribe();
  return notifications.getEnvironment();
}
