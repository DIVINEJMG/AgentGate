import { api } from '../platform/client';
import type { ApiVersion } from './systemApi';

export type Goal = 'engineering' | 'support' | 'research' | 'operations' | 'general';
export type Care = 'visible' | 'changes' | 'risky';
export type ToolChoice = 'github' | 'slack' | 'gmail' | 'google_calendar' | 'google_drive' | 'web_research';
export type ChecklistStepId = 'connect' | 'say_hi' | 'first_task' | 'approval' | 'schedule';

export interface OnboardingAnswers { goal?: Goal; tools?: ToolChoice[]; care?: Care; timezone?: string }
export interface ChecklistStep { id: ChecklistStepId; label: string; description: string; complete: boolean; destination: string }
export interface Onboarding {
  /** none: a workspace created before onboarding existed; welcome: questions not answered yet. */
  state: 'none' | 'welcome' | 'ready';
  answers?: OnboardingAnswers;
  setup?: { workerId?: string; workerName?: string; threadId?: string; policyId?: string | null };
  checklistDismissed?: boolean;
  checklist?: { steps: ChecklistStep[]; complete: number; total: number };
  examples?: string[];
}

const path = (version: ApiVersion, org: string) => `/api/${version}/organizations/${org}/onboarding`;

export async function loadOnboarding(version: ApiVersion, org: string) {
  return (await api.get(path(version, org))).data as Onboarding;
}
export async function setUpWorkspace(version: ApiVersion, org: string, answers: OnboardingAnswers) {
  return (await api.post(`${path(version, org)}/setup`, answers)).data as Onboarding;
}
export async function setChecklistDismissed(version: ApiVersion, org: string, dismissed: boolean) {
  return (await api.post(`${path(version, org)}/checklist`, { dismissed })).data as { checklistDismissed: boolean };
}
