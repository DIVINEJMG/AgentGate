import type { Job, JobStatus } from '../lib/jobsApi';
import type { MemoryRecord, MemoryScope } from '../lib/memoryApi';

export function filterJobs(jobs: Job[], workerNames: Map<string, string>, query: string, status: 'all' | JobStatus): Job[] {
  const term = query.trim().toLocaleLowerCase();
  return jobs.filter((job) => (status === 'all' || job.status === status) &&
    `${job.name} ${job.objective} ${workerNames.get(job.workerId) ?? ''}`.toLocaleLowerCase().includes(term));
}

export function filterMemories(memories: MemoryRecord[], workerNames: Map<string, string>, scope: MemoryScope, query: string, includeArchived: boolean): MemoryRecord[] {
  const term = query.trim().toLocaleLowerCase();
  return memories.filter((item) => item.scope === scope && (includeArchived || item.status === 'active') &&
    `${item.title} ${item.content} ${item.tags.join(' ')} ${item.workerId ? workerNames.get(item.workerId) ?? '' : ''}`.toLocaleLowerCase().includes(term))
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
}
