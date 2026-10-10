import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, CheckCircle2, FileText, Inbox, MessageSquare } from 'lucide-react';
import type { AuthUser } from '../../platform/client';
import { approvalExplanation, decideApproval, type ApprovalRecord } from '../../lib/approvalApi';
import type { OrganizationAccess } from '../../lib/identityApi';
import { loadJobsWorkspace, type Job } from '../../lib/jobsApi';
import { loadResults, type ResultSummary } from '../../lib/resultsApi';
import type { RuntimeRun } from '../../lib/runtimeApi';
import type { ApiVersion } from '../../lib/systemApi';
import { notify } from '../feedback';
import { useLive } from '../live';
import { linkProps } from '../routes';
import { Ago, Avatar, EmptyState, Notice, Section, SkeletonLines, State, humanize } from '../ui';

function greeting(name: string) {
  const hour = new Date().getHours();
  const part = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  return `${part}, ${name.split(/\s+/)[0]}`;
}

export default function HomePage({ organization, user, apiVersion, operational, statusError }: { organization: OrganizationAccess; user: AuthUser; apiVersion: ApiVersion; operational: boolean; statusError: boolean }) {
  const live = useLive();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [results, setResults] = useState<ResultSummary[] | null>(null);
  const [deciding, setDeciding] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void loadJobsWorkspace(apiVersion, organization.id).then((workspace) => { if (active) setJobs(workspace.jobs); }).catch(() => {});
    void loadResults(apiVersion, organization.id).then((workspace) => { if (active) setResults(workspace.items); }).catch(() => { if (active) setResults([]); });
    return () => { active = false; };
  }, [apiVersion, organization.id]);

  const pending = live.approvals.filter((item) => item.status === 'pending');
  const openEscalations = live.escalations.filter((item) => item.status !== 'resolved');
  const working = useMemo(() => live.runs
    .filter((run) => ['planning', 'running'].includes(run.status) || run.status.startsWith('waiting'))
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
    .slice(0, 5), [live.runs]);
  const workerName = (id: string) => live.workers.find((worker) => worker.id === id)?.name ?? 'Worker';
  const jobName = (id: string) => jobs.find((job) => job.id === id)?.name;
  const needsYou = pending.length + openEscalations.length;
  const workingCount = live.workers.filter((worker) => live.liveness(worker) === 'working').length;

  async function decide(approval: ApprovalRecord, decision: 'approve' | 'reject') {
    setDeciding(approval.id);
    // Show the decision immediately; restore the request if the server refuses it.
    live.replaceApproval({ ...approval, status: decision === 'approve' ? 'approved' : 'rejected' });
    try {
      live.replaceApproval(await decideApproval(apiVersion, organization.id, approval.id, decision, ''));
      notify({ key: 'decision-' + approval.id, tone: 'ok', title: decision === 'approve' ? 'Approved' : 'Declined', body: `${approval.agentName} · ${approval.request.action || approval.request.scope}` });
    } catch (cause) {
      live.replaceApproval(approval);
      notify({ key: 'decision-' + approval.id, tone: 'danger', title: 'Decision not recorded', body: cause instanceof Error ? cause.message : 'Try again from the inbox.' });
    } finally { setDeciding(null); }
  }

  return <div className='ws-page'>
    <header className='ws-home-head'>
      <p className='ws-eyebrow'>{new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}</p>
      <h1>{greeting(user.name || user.email || 'there')}</h1>
      <p className='ws-home-summary'>
        {!live.loaded ? 'Checking on your team…' : <>
          <span>{needsYou ? <a {...linkProps({ page: 'inbox' })}><strong>{needsYou} {needsYou === 1 ? 'thing needs' : 'things need'} you</strong></a> : 'Nothing needs you right now'}</span>
          <span aria-hidden='true'>·</span>
          <span>{workingCount ? <><strong>{workingCount}</strong> {workingCount === 1 ? 'worker is' : 'workers are'} working</> : `${live.workers.filter((worker) => worker.status === 'active').length} workers ready`}</span>
        </>}
      </p>
    </header>

    {statusError && !operational && <Notice tone='danger'><strong>Audoryn can’t confirm its own status.</strong> Actions stay blocked until it can. Your workers’ records are still shown.</Notice>}

    <div className='ws-cols'>
      <div style={{ display: 'grid', gap: 40, alignContent: 'start' }}>
        <Section title='Needs you' count={live.loaded ? needsYou : undefined} action={<a className='ws-link' {...linkProps({ page: 'inbox' })}>Open inbox</a>}>
          {!live.loaded ? <SkeletonLines rows={2} avatar /> : needsYou === 0 ? <EmptyState icon={<CheckCircle2 size={18} />} title='You’re all caught up'>Approvals and escalations will appear here the moment a worker needs a decision.</EmptyState> : <ul className='ws-rows'>
            {pending.slice(0, 4).map((approval) => <li key={approval.id} className='ws-row ws-decision'>
              <Avatar name={approval.agentName} seed={approval.agentId} size={32} />
              <a className='ws-row-main ws-row-link' {...linkProps({ page: 'inbox', kind: 'approval', id: approval.id })}><strong>{approval.request.action || approval.request.scope} on {approval.request.resourceName}</strong><small>{approvalExplanation(approval)}</small></a>
              <div className='ws-row-meta'>
                <span className='ws-hide-sm'><Ago value={approval.requestedAt} /></span>
                <button type='button' className='ws-button ws-button-sm' disabled={deciding === approval.id} onClick={() => void decide(approval, 'reject')}>Decline</button>
                <button type='button' className='ws-button ws-button-sm ws-button-primary' disabled={deciding === approval.id} onClick={() => void decide(approval, 'approve')}>Approve</button>
              </div>
            </li>)}
            {openEscalations.slice(0, 3).map((escalation) => <li key={escalation.id} className='ws-row'>
              <Avatar name={escalation.workerName} seed={escalation.workerId} size={32} />
              <a className='ws-row-main ws-row-link' {...linkProps({ page: 'inbox', kind: 'escalation', id: escalation.id })}><strong>{escalation.title}</strong><small>{escalation.workerName} · {escalation.summary}</small></a>
              <div className='ws-row-meta'><State value={escalation.severity} label={`${escalation.severity} · ${escalation.status}`} /><span className='ws-hide-sm'><Ago value={escalation.createdAt} /></span></div>
            </li>)}
          </ul>}
        </Section>

        <Section title='Working now' count={live.loaded ? working.length : undefined} action={<a className='ws-link' {...linkProps({ page: 'runs' })}>All runs</a>}>
          {!live.loaded ? <SkeletonLines rows={2} avatar /> : working.length === 0 ? <EmptyState title='No work in progress'>When a worker picks up a job, you’ll see its plan move step by step here.</EmptyState> : <ul className='ws-rows'>
            {working.map((run) => <WorkingRow key={run.id} run={run} worker={workerName(run.workerId)} job={jobName(run.jobId)} />)}
          </ul>}
        </Section>

        <Section title='Recent results' action={<a className='ws-link' {...linkProps({ page: 'results' })}>All results</a>}>
          {results === null ? <SkeletonLines rows={3} /> : results.length === 0 ? <EmptyState icon={<FileText size={18} />} title='No results yet'>Finished work arrives here as readable documents.</EmptyState> : <ul className='ws-rows'>
            {results.slice(0, 5).map((result) => <li key={result.id} className='ws-row' style={{ gridTemplateColumns: 'auto minmax(0,1fr) auto' }}>
              <span className='ws-doc-icon' aria-hidden='true'><FileText size={16} /></span>
              <a className='ws-row-main ws-row-link' {...linkProps({ page: 'results', resultId: result.id })}><strong>{result.title}{result.isNew && <span className='ws-tag' data-tone='info' style={{ marginLeft: 8 }}>New</span>}</strong><small>{result.workerName} · {result.jobName}</small></a>
              <div className='ws-row-meta'><State value={result.status} /><span className='ws-hide-sm'><Ago value={result.completedAt} /></span></div>
            </li>)}
          </ul>}
        </Section>
      </div>

      <aside style={{ display: 'grid', gap: 40, alignContent: 'start' }}>
        <Section title='Your team' count={live.loaded ? live.workers.length : undefined} action={<a className='ws-link' {...linkProps({ page: 'workers' })}>All workers</a>}>
          {!live.loaded ? <SkeletonLines rows={3} avatar /> : live.workers.length === 0 ? <EmptyState title='No workers yet' action={<a className='ws-button ws-button-primary' {...linkProps({ page: 'workers' })}>Create a worker</a>}>Describe an outcome and Audoryn sets up a worker for it.</EmptyState> : <ul className='ws-team'>
            {live.workers.slice(0, 8).map((worker) => {
              const state = live.liveness(worker);
              const run = live.activeRun(worker.id);
              return <li key={worker.id}>
                <a className='ws-team-link' {...linkProps({ page: 'worker', workerId: worker.id, tab: 'overview' })}>
                  <Avatar name={worker.name} seed={worker.id} size={36} live={state} />
                  <span><strong>{worker.name}</strong><small>{state === 'working' ? (jobName(run?.jobId ?? '') ?? 'Working') : state === 'waiting' ? `Waiting · ${humanize(run?.status)}` : state === 'ready' ? `Ready · ${worker.department || 'No department'}` : humanize(worker.status)}</small></span>
                </a>
                <a className='ws-icon-button' aria-label={`Message ${worker.name}`} title='Message' {...linkProps({ page: 'conversations', workerId: worker.id })}><MessageSquare size={16} /></a>
              </li>;
            })}
          </ul>}
        </Section>
        <div className='ws-quiet-links'>
          <a {...linkProps({ page: 'inbox' })}><Inbox size={15} />Inbox<ArrowRight size={14} /></a>
          <a {...linkProps({ page: 'activity' })}>Activity<ArrowRight size={14} /></a>
          <a {...linkProps({ page: 'performance' })}>Performance<ArrowRight size={14} /></a>
        </div>
      </aside>
    </div>
  </div>;
}

function WorkingRow({ run, worker, job }: { run: RuntimeRun; worker: string; job?: string }) {
  const total = Math.max(run.stepIds.length, 1);
  const done = Math.min(run.currentStep, total);
  const waiting = run.status.startsWith('waiting');
  return <li className='ws-row ws-working'>
    <Avatar name={worker} seed={run.workerId} size={32} live={waiting ? 'waiting' : 'working'} />
    <a className='ws-row-main ws-row-link' {...linkProps({ page: 'runs' })}>
      <strong>{job ?? run.planSummary ?? 'Run in progress'}</strong>
      <small>{worker} · {waiting ? humanize(run.waitingReason || run.status) : run.plannerPhase ? humanize(run.plannerPhase) : `Step ${done + 1} of ${total}`}</small>
      <span className='ws-steps' aria-label={`${done} of ${total} steps complete`}>{Array.from({ length: total }, (_, index) => <i key={index} data-state={index < done ? 'done' : index === done ? (waiting ? 'waiting' : 'active') : undefined} />)}</span>
    </a>
    <div className='ws-row-meta'><State value={run.status} /><span className='ws-hide-sm'><Ago value={run.updatedAt} /></span></div>
  </li>;
}
