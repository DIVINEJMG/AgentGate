import { api } from '@appdeploy/client';
import type { ApiVersion } from './systemApi';
import type { JobInput } from './jobsApi';
import type { WorkingHours } from './workforceApi';

export type QuickTimingMode='manual'|'start_now'|'once'|'interval'|'daily'|'weekly'|'custom'|'event'|'dependency';
export interface QuickTiming{mode:QuickTimingMode;timezone?:string;at?:string|null;intervalMinutes?:number;localTime?:string;weekdays?:string[];eventKey?:string|null;dependencyJobIds?:string[]}
export interface WorkerQuickStartInput{worker:{identityMode:'automatic'|'existing';agentIdentityId?:string;name:string;roleId:string;department:string;supervisorUserId:string;description:string;responsibilities:string[];instructions:string;workingHours:WorkingHours};jobs:Array<JobInput&{timing:QuickTiming}>}
export interface QuickStartResult{workerId:string;jobIds:string[];identity?:{agentId:string;createdAutomatically:boolean};readiness:{state:'started'|'needs_setup';blockers:string[];workerId:string;jobIds:string[]}}
export interface JobQuickStartResult{jobId:string;workerId:string;readiness:{state:'started'|'needs_setup';blockers:string[];workerId:string;jobIds:string[]}}

export async function createWorkerQuickStart(version:ApiVersion,organizationId:string,input:WorkerQuickStartInput):Promise<QuickStartResult>{const response=await api.post(`/api/${version}/organizations/${organizationId}/workforce/quick-start`,input);return version==='v1'?response.data as QuickStartResult:response.data.data as QuickStartResult}
export async function createJobQuickStart(version:ApiVersion,organizationId:string,job:JobInput,timing:QuickTiming):Promise<JobQuickStartResult>{const response=await api.post(`/api/${version}/organizations/${organizationId}/jobs/quick-start`,{job,timing});return version==='v1'?response.data as JobQuickStartResult:response.data.data as JobQuickStartResult}