import { api } from '../platform/client';
import type { ApiVersion } from './systemApi';

export type RunStatus='planning'|'running'|'waiting_approval'|'waiting_ai'|'waiting_configuration'|'completed'|'failed'|'cancelled';
export type RunStepStatus='pending'|'running'|'waiting_approval'|'completed'|'failed';

export interface RuntimeRun {
  id:string;organizationId:string;workItemId:string;jobId:string;workerId:string;agentId:string;
  correlationId:string;attempt:number;status:RunStatus;currentStep:number;stepIds:string[];
  contextMemoryIds:string[];artifactIds:string[];planSummary:string|null;resultSummary:string|null;
  failure:string|null;createdAt:string;startedAt:string|null;completedAt:string|null;updatedAt:string;
}
export interface RuntimeStep {
  id:string;organizationId:string;runId:string;index:number;kind:'reasoning'|'action'|'finish';
  title:string;instruction:string;resourceId:string;scope:string;status:RunStepStatus;output:string|null;
  actionId:string|null;approvalId:string|null;error:string|null;startedAt:string|null;completedAt:string|null;
}
export interface RuntimeWorkspace {
  runs:RuntimeRun[];
  summary:{total:number;planning:number;running:number;waitingApproval:number;completed:number;failed:number};
  window:{limit:number;truncated:boolean};
  runtime:{autonomousCadence:string;maxAiRunsPerCron:number;maxPlanSteps:number;maxRetries:number};
}

function displayText(value: unknown): string | null {
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
    ].filter(Boolean);
    if (parts.length) return parts.join(' · ');
    try {
      return JSON.stringify(record, null, 2);
    } catch {
      return 'Runtime returned structured diagnostic data.';
    }
  }
  return String(value);
}

function runV2(raw:any):RuntimeRun {
  return {
    id:raw.identity.id,
    organizationId:raw.organizationId,
    workItemId:raw.assignment.workItemId,
    jobId:raw.assignment.jobId,
    workerId:raw.assignment.workerId,
    agentId:raw.assignment.agentId,
    correlationId:raw.trace.correlationId,
    attempt:raw.identity.attempt,
    status:raw.identity.status,
    currentStep:raw.execution.currentStep,
    stepIds:raw.execution.stepIds,
    contextMemoryIds:raw.execution.contextMemoryIds??[],
    artifactIds:raw.execution.artifactIds??[],
    planSummary:displayText(raw.execution.planSummary),
    resultSummary:displayText(raw.execution.resultSummary),
    failure:displayText(raw.execution.failure),
    createdAt:raw.identity.createdAt,
    startedAt:raw.identity.startedAt??null,
    completedAt:raw.identity.completedAt??null,
    updatedAt:raw.identity.updatedAt,
  };
}

function stepV2(raw:any):RuntimeStep {
  return {
    id:raw.identity.id,
    organizationId:'',
    runId:'',
    index:raw.identity.index,
    kind:raw.identity.kind,
    title:raw.request.title,
    instruction:raw.request.instruction,
    resourceId:raw.request.resourceId??'',
    scope:raw.request.scope??'',
    status:raw.identity.status,
    output:displayText(raw.execution.output),
    actionId:raw.execution.actionId??null,
    approvalId:raw.execution.approvalId??null,
    error:displayText(raw.execution.error),
    startedAt:raw.execution.startedAt??null,
    completedAt:raw.execution.completedAt??null,
  };
}

function runV1(raw:any):RuntimeRun {
  return {
    ...raw,
    planSummary:displayText(raw.planSummary),
    resultSummary:displayText(raw.resultSummary),
    failure:displayText(raw.failure),
    startedAt:raw.startedAt??null,
    completedAt:raw.completedAt??null,
  } as RuntimeRun;
}

function stepV1(raw:any):RuntimeStep {
  return {
    ...raw,
    output:displayText(raw.output),
    error:displayText(raw.error),
    startedAt:raw.startedAt??null,
    completedAt:raw.completedAt??null,
  } as RuntimeStep;
}

export async function loadRuntime(version:ApiVersion,organizationId:string):Promise<RuntimeWorkspace>{
  const response=await api.get(`/api/${version}/organizations/${organizationId}/runtime`);
  if(version==='v1'){
    const data=response.data;
    return {...data,runs:(data.runs??[]).map(runV1)} as RuntimeWorkspace;
  }
  const data=response.data.data;
  return {runs:data.runs.map(runV2),summary:data.summary,window:data.window,runtime:data.runtime};
}

export async function getRun(version:ApiVersion,organizationId:string,runId:string){
  const response=await api.get(`/api/${version}/organizations/${organizationId}/runs/${runId}`);
  if(version==='v1'){
    return {run:runV1(response.data.run),steps:(response.data.steps??[]).map(stepV1)};
  }
  return {run:runV2(response.data.data.run),steps:response.data.data.steps.map(stepV2)};
}
export async function processWorkItem(version:ApiVersion,organizationId:string,workItemId:string){const response=await api.post(`/api/${version}/organizations/${organizationId}/work-items/${workItemId}/process`,{});return version==='v1'?response.data:response.data.data}
export async function continueRun(version:ApiVersion,organizationId:string,runId:string){const response=await api.post(`/api/${version}/organizations/${organizationId}/runs/${runId}/continue`,{});return version==='v1'?response.data:response.data.data}
export async function retryWorkItem(version:ApiVersion,organizationId:string,workItemId:string){const response=await api.post(`/api/${version}/organizations/${organizationId}/work-items/${workItemId}/retry`,{});return version==='v1'?response.data.workItem:response.data.data.workItem}
