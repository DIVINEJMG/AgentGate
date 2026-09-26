import { api } from '../platform/client';
import type { ApiVersion } from './systemApi';

export type Weekday='monday'|'tuesday'|'wednesday'|'thursday'|'friday'|'saturday'|'sunday';

export interface ScheduleDefinition {
  enabled:boolean;
  cadence:'daily'|'weekly'|'interval';
  timezone:string;
  localTime:string;
  weekdays:Weekday[];
  intervalMinutes:number;
  missedRunPolicy:'skip'|'queue_once';
  outsideWorkingHoursPolicy:'queue_anyway'|'next_open';
  nextDueAt:string|null;
}

export interface TriggerConfig {
  id:string;
  organizationId:string;
  jobId:string;
  workerId:string;
  revision:number;
  schedule:ScheduleDefinition;
  apiEnabled:boolean;
  internalEventKeys:string[];
  dependencyJobIds:string[];
  createdBy:string;
  createdAt:string;
  updatedBy:string;
  updatedAt:string;
}

export interface TriggerHistory {
  id:string;
  organizationId:string;
  jobId:string;
  workerId:string;
  triggerType:'manual'|'schedule'|'internal_event'|'api'|'dependency';
  outcome:'queued'|'skipped'|'blocked';
  key:string|null;
  eventId:string|null;
  scheduledFor:string|null;
  workItemId:string|null;
  correlationId:string|null;
  reason:string|null;
  actorType:'human'|'agent'|'system';
  actorId:string;
  occurredAt:string;
}

export interface TriggerWorkspace {
  configs:TriggerConfig[];
  history:TriggerHistory[];
  summary:{
    configs:number;
    scheduled:number;
    apiEnabled:number;
    eventDriven:number;
    dependencyDriven:number;
    history:{total:number;queued:number;skipped:number;blocked:number};
  };
  window:{configsTruncated:boolean;historyTruncated:boolean};
  scheduler:{cadenceMinutes:number;mode:'queue_only'};
}

export interface TriggerConfigInput {
  schedule:Omit<ScheduleDefinition,'nextDueAt'>;
  apiEnabled:boolean;
  internalEventKeys:string[];
  dependencyJobIds:string[];
}

function displayReason(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    if (typeof record.message === 'string') return record.message;
    const parts = [
      typeof record.kind === 'string' ? record.kind.replaceAll('_', ' ') : null,
      typeof record.category === 'string' ? record.category.replaceAll('_', ' ') : null,
      typeof record.retryable === 'boolean'
        ? (record.retryable ? 'retryable' : 'not retryable')
        : null,
    ].filter((item): item is string => Boolean(item));
    if (parts.length) return parts.join(' · ');
    try {
      return JSON.stringify(record);
    } catch {
      return 'Trigger returned structured diagnostic data.';
    }
  }
  return String(value);
}

function configV2(raw:any):TriggerConfig {
  return {
    id:raw.identity.id,
    organizationId:raw.organizationId,
    jobId:raw.identity.jobId,
    workerId:raw.assignment.workerId,
    revision:raw.identity.revision,
    schedule:raw.schedule,
    apiEnabled:raw.triggers.apiEnabled,
    internalEventKeys:raw.triggers.internalEventKeys,
    dependencyJobIds:raw.triggers.dependencyJobIds,
    createdBy:raw.ownership.createdBy,
    createdAt:raw.identity.createdAt,
    updatedBy:raw.ownership.updatedBy,
    updatedAt:raw.identity.updatedAt,
  };
}

function historyEntry(raw:any):TriggerHistory {
  return {
    id:raw.id,
    organizationId:raw.organizationId,
    jobId:raw.jobId,
    workerId:raw.workerId,
    triggerType:raw.triggerType,
    outcome:raw.outcome,
    key:raw.key??null,
    eventId:raw.eventId??null,
    scheduledFor:raw.scheduledFor??null,
    workItemId:raw.workItemId??null,
    correlationId:raw.correlationId??null,
    reason:displayReason(raw.reason),
    actorType:raw.actorType,
    actorId:raw.actorId,
    occurredAt:raw.occurredAt,
  };
}

export async function loadTriggerWorkspace(version:ApiVersion,organizationId:string):Promise<TriggerWorkspace>{
  const response=await api.get(`/api/${version}/organizations/${organizationId}/triggers`);
  if(version==='v1'){
    const data=response.data;
    return {
      configs:data.configs,
      history:(data.history??[]).map(historyEntry),
      summary:data.summary,
      window:data.window,
      scheduler:data.scheduler,
    };
  }
  const data=response.data.data;
  return {
    configs:data.configs.map(configV2),
    history:(data.history??[]).map(historyEntry),
    summary:data.summary,
    window:data.window,
    scheduler:data.scheduler,
  };
}

export async function saveTriggerConfig(version:ApiVersion,organizationId:string,jobId:string,input:TriggerConfigInput){
  const response=await api.put(`/api/${version}/organizations/${organizationId}/jobs/${jobId}/triggers`,input);
  return version==='v1'?response.data.config as TriggerConfig:configV2(response.data.data.config);
}

export async function emitInternalEvent(
  version:ApiVersion,
  organizationId:string,
  input:{key:string;payload?:Record<string,unknown>|null;sourceJobId?:string|null;sourceWorkItemId?:string|null},
){
  const response=await api.post(`/api/${version}/organizations/${organizationId}/triggers/events`,input);
  return version==='v1'?response.data:response.data.data;
}
