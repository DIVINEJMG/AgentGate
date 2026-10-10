import { useEffect, useMemo, useState } from 'react';
import { Bell, CheckCircle2, FileText, ShieldCheck, SlidersHorizontal } from 'lucide-react';
import type { AuthUser } from '../../platform/client';
import { approvalExplanation, decideApproval, enableApprovalNotifications, type ApprovalRecord } from '../../lib/approvalApi';
import type { OrganizationAccess } from '../../lib/identityApi';
import { loadResults, type ResultSummary } from '../../lib/resultsApi';
import { addEscalationNote, interveneEscalation, listSupervision, reassignEscalation, setEscalationStatus, updateRule, type Escalation, type EscalationRule } from '../../lib/supervisionApi';
import type { ApiVersion } from '../../lib/systemApi';
import { notify } from '../feedback';
import { usePageTitle } from '../history';
import { useLive } from '../live';
import { linkProps, navigate, type WorkspaceRoute } from '../routes';
import { Ago, Avatar, Confirm, EmptyState, PageHeader, Pills, Sheet, SkeletonLines, State, humanize } from '../ui';

type Filter = 'all' | 'approvals' | 'escalations' | 'results' | 'done';
type Item =
  | { kind: 'approval'; id: string; at: string; done: boolean; approval: ApprovalRecord }
  | { kind: 'escalation'; id: string; at: string; done: boolean; escalation: Escalation }
  | { kind: 'result'; id: string; at: string; done: boolean; result: ResultSummary };

const message = (cause: unknown, fallback: string) => (cause instanceof Error ? cause.message : fallback);

export default function InboxPage({ organization, user, apiVersion, route }: { organization: OrganizationAccess; user: AuthUser; apiVersion: ApiVersion; route: Extract<WorkspaceRoute, { page: 'inbox' }> }) {
  const live = useLive();
  const [results, setResults] = useState<ResultSummary[]>([]);
  const [rules, setRules] = useState<EscalationRule[]>([]);
  const [rulesOpen, setRulesOpen] = useState(false);
  const filter: Filter = (['approvals', 'escalations', 'results', 'done'] as Filter[]).includes(route.filter as Filter) ? route.filter as Filter : 'all';

  useEffect(() => {
    let active = true;
    void loadResults(apiVersion, organization.id).then((workspace) => { if (active) setResults(workspace.items); }).catch(() => {});
    void listSupervision(apiVersion, organization.id).then((workspace) => { if (active) setRules(workspace.rules); }).catch(() => {});
    return () => { active = false; };
  }, [apiVersion, organization.id]);

  const items = useMemo<Item[]>(() => [
    ...live.approvals.map((approval) => ({ kind: 'approval' as const, id: approval.id, at: approval.requestedAt, done: approval.status !== 'pending', approval })),
    ...live.escalations.map((escalation) => ({ kind: 'escalation' as const, id: escalation.id, at: escalation.createdAt, done: escalation.status === 'resolved', escalation })),
    ...results.filter((result) => result.status === 'attention').map((result) => ({ kind: 'result' as const, id: result.id, at: result.completedAt, done: false, result })),
  ].sort((a, b) => Number(a.done) - Number(b.done) || new Date(b.at).getTime() - new Date(a.at).getTime()), [live.approvals, live.escalations, results]);

  const visible = items.filter((item) => filter === 'done' ? item.done : !item.done && (filter === 'all' || filter === item.kind + 's'));
  const selected = (route.id && items.find((item) => item.id === route.id && item.kind === route.kind)) || null;
  usePageTitle(!selected ? null : selected.kind === 'approval' ? `Approve ${selected.approval.request.action || selected.approval.request.scope}` : selected.kind === 'escalation' ? selected.escalation.title : selected.result.title);
  const count = (kind: Item['kind']) => items.filter((item) => item.kind === kind && !item.done).length;
  const at = (target: Partial<Extract<WorkspaceRoute, { page: 'inbox' }>>) => ({ page: 'inbox' as const, filter: filter === 'all' ? undefined : filter, ...target });

  // Desktop opens the first item automatically; the URL then names what is shown.
  useEffect(() => {
    if (!route.id && visible[0] && window.matchMedia('(min-width: 861px)').matches) navigate(at({ kind: visible[0].kind, id: visible[0].id }), { replace: true });
  }, [route.id, visible[0]?.id, filter]); // eslint-disable-line react-hooks/exhaustive-deps

  async function enableAlerts() {
    try {
      const environment = await enableApprovalNotifications();
      notify({ key: 'alerts', tone: environment.permission === 'granted' ? 'ok' : 'warn', title: environment.permission === 'granted' ? 'Alerts are on' : 'Alerts need browser permission', body: environment.permission === 'granted' ? 'You’ll be notified when a worker needs a decision.' : 'Allow notifications for this site, then try again.' });
    } catch (cause) { notify({ key: 'alerts', tone: 'danger', title: 'Alerts could not be enabled', body: message(cause, 'Try again later.') }); }
  }

  return <div className='ws-page ws-page-wide ws-inbox-page'>
    <PageHeader title='Inbox' description='Decisions, escalations and results that need a person. Workers wait here instead of guessing.'
      actions={<><button type='button' className='ws-button' onClick={() => void enableAlerts()}><Bell size={15} />Alerts</button><button type='button' className='ws-button' onClick={() => setRulesOpen(true)}><SlidersHorizontal size={15} />Escalation rules</button></>} />
    <div className='ws-inbox' data-detail={selected ? 'true' : undefined}>
      <div className='ws-inbox-list'>
        <Pills label='Inbox filter' value={filter} onChange={(next) => navigate({ page: 'inbox', filter: next === 'all' ? undefined : next })} options={[
          { value: 'all', label: 'All', count: items.filter((item) => !item.done).length },
          { value: 'approvals', label: 'Approvals', count: count('approval') },
          { value: 'escalations', label: 'Escalations', count: count('escalation') },
          { value: 'results', label: 'Results', count: count('result') },
          { value: 'done', label: 'Done' },
        ]} />
        {!live.loaded ? <SkeletonLines rows={4} avatar /> : visible.length === 0 ? <EmptyState icon={<CheckCircle2 size={18} />} title={filter === 'done' ? 'Nothing decided yet' : 'Inbox zero'}>{filter === 'done' ? 'Decided approvals and resolved escalations appear here.' : 'When a worker needs a decision, it lands here first.'}</EmptyState> : <ul className='ws-rows'>
          {visible.map((item) => <InboxRow key={item.kind + item.id} item={item} current={selected?.id === item.id && selected.kind === item.kind} to={at({ kind: item.kind, id: item.id })} />)}
        </ul>}
      </div>
      <div className='ws-inbox-detail'>
        {selected ? <>
          <a className='ws-back ws-inbox-back' {...linkProps(at({}))}>← Inbox</a>
          {selected.kind === 'approval' ? <ApprovalDetail key={selected.id} approval={selected.approval} organization={organization} apiVersion={apiVersion} canReview={live.canReview} />
            : selected.kind === 'escalation' ? <EscalationDetail key={selected.id} escalation={selected.escalation} organization={organization} apiVersion={apiVersion} user={user} />
              : <ResultDetail result={selected.result} />}
        </> : <div className='ws-inbox-placeholder'><ShieldCheck size={20} /><p>Select an item to review it.</p></div>}
      </div>
    </div>
    <Sheet open={rulesOpen} onClose={() => setRulesOpen(false)} title='Escalation rules' subtitle='When Audoryn raises something to a supervisor'>
      <RulesEditor rules={rules} organization={organization} apiVersion={apiVersion} onChange={(rule) => setRules((current) => current.map((item) => item.trigger === rule.trigger ? rule : item))} />
    </Sheet>
  </div>;
}

function InboxRow({ item, current, to }: { item: Item; current: boolean; to: WorkspaceRoute }) {
  const content = item.kind === 'approval'
    ? { avatar: <Avatar name={item.approval.agentName} seed={item.approval.agentId} size={32} />, title: `${item.approval.request.action || item.approval.request.scope} on ${item.approval.request.resourceName}`, sub: `${item.approval.agentName} · approval`, state: item.approval.status === 'pending' ? <State tone='warn' label='Needs decision' /> : <State value={item.approval.status} /> }
    : item.kind === 'escalation'
      ? { avatar: <Avatar name={item.escalation.workerName} seed={item.escalation.workerId} size={32} />, title: item.escalation.title, sub: `${item.escalation.workerName} · ${humanize(item.escalation.trigger)}`, state: <State value={item.escalation.status === 'open' ? item.escalation.severity : item.escalation.status} label={item.escalation.status === 'open' ? `${item.escalation.severity}` : item.escalation.status} /> }
      : { avatar: <span className='ws-doc-icon' aria-hidden='true'><FileText size={16} /></span>, title: item.result.title, sub: `${item.result.workerName} · result needs review`, state: <State tone='warn' label='Review' /> };
  return <li className='ws-row ws-inbox-row' aria-current={current ? 'true' : undefined}>
    {content.avatar}
    <a className='ws-row-main ws-row-link' {...linkProps(to)}><strong>{content.title}</strong><small>{content.sub}</small></a>
    <div className='ws-inbox-row-meta'>{content.state}<Ago value={item.at} /></div>
  </li>;
}

function Facts({ items }: { items: Array<[string, React.ReactNode]> }) {
  return <dl className='ws-facts'>{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

function ApprovalDetail({ approval, organization, apiVersion, canReview }: { approval: ApprovalRecord; organization: OrganizationAccess; apiVersion: ApiVersion; canReview: boolean }) {
  const live = useLive();
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  async function decide(decision: 'approve' | 'reject') {
    setBusy(true);
    live.replaceApproval({ ...approval, status: decision === 'approve' ? 'approved' : 'rejected' });
    try {
      live.replaceApproval(await decideApproval(apiVersion, organization.id, approval.id, decision, note));
      setNote('');
      notify({ key: 'decision-' + approval.id, tone: 'ok', title: decision === 'approve' ? 'Approved' : 'Declined', body: 'The worker continues with the current authority checks.' });
    } catch (cause) {
      live.replaceApproval(approval);
      notify({ key: 'decision-' + approval.id, tone: 'danger', title: 'Decision not recorded', body: message(cause, 'Try again.') });
    } finally { setBusy(false); }
  }
  const request = approval.request;
  return <article className='ws-detail'>
    <header className='ws-detail-head'>
      <Avatar name={approval.agentName} seed={approval.agentId} size={40} />
      <div><p className='ws-eyebrow'>{approval.agentName} wants to</p><h2>{request.action || request.scope} on {request.resourceName}</h2><p className='ws-muted'>Requested <Ago value={approval.requestedAt} /></p></div>
    </header>
    <p className='ws-detail-why'>{approvalExplanation(approval)}</p>
    <Facts items={[['Target', request.target || '—'], ['Tool', request.providerOperation || request.integrationId], ['Risk', <State key='risk' value={request.risk} />], ['Policy', approval.policy.winningPolicy?.name ?? 'No matching rule']]} />
    {approval.status === 'pending' ? canReview ? <div className='ws-decision-box'>
      <label className='ws-field'><span>Note for the record <small>(optional)</small></span><textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} placeholder='Why you’re approving or declining' /></label>
      <div className='ws-decision-actions'><span className='ws-muted'>Approval re-checks the worker’s identity, scope and current policy before anything runs.</span><button type='button' className='ws-button' disabled={busy} onClick={() => void decide('reject')}>Decline</button><button type='button' className='ws-button ws-button-primary' disabled={busy} onClick={() => void decide('approve')}>Approve</button></div>
    </div> : <p className='ws-muted'>Your role can see this request but not decide it.</p>
      : <div className='ws-outcome'><State value={approval.status} /><span>{approval.decision ? <>by {approval.decision.decidedBy} · <Ago value={approval.decision.decidedAt} />{approval.decision.note && <> — “{approval.decision.note}”</>}</> : 'Decision recorded'}</span>{approval.execution.state !== 'not_started' && <span className='ws-muted'>Then: {humanize(approval.execution.state)}{approval.execution.summary ? ` · ${approval.execution.summary}` : ''}</span>}</div>}
    <details className='ws-technical'><summary>Technical detail</summary>
      <Facts items={[['Scope', <code key='s'>{request.scope}</code>], ['Resource', <code key='r'>{request.resourceId}</code>], ['Outcome', approval.policy.outcome], ['Reason', approval.policy.reason || '—'], ['Matched rules', approval.policy.matchedPolicies.map((rule) => `${rule.name} (P${rule.priority})`).join(', ') || '—'], ['Approval ID', <code key='id'>{approval.id}</code>], ['Action ID', <code key='a'>{approval.actionId}</code>]]} />
      {Object.keys(request.input ?? {}).length > 0 && <pre className='ws-code'>{JSON.stringify(request.input, null, 2)}</pre>}
    </details>
  </article>;
}

function EscalationDetail({ escalation, organization, apiVersion, user }: { escalation: Escalation; organization: OrganizationAccess; apiVersion: ApiVersion; user: AuthUser }) {
  const live = useLive();
  const canManage = organization.permissions.includes('supervision.manage');
  const canIncident = organization.permissions.includes('incidents.manage');
  const [note, setNote] = useState('');
  const [assignee, setAssignee] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<null | 'resolve' | 'cancel_run' | 'pause_worker' | 'create_incident'>(null);
  async function run(task: () => Promise<Escalation>, success: string) {
    setBusy(true);
    try { live.replaceEscalation(await task()); notify({ key: 'escalation-' + escalation.id, tone: 'ok', title: success }); }
    catch (cause) { notify({ key: 'escalation-' + escalation.id, tone: 'danger', title: 'That didn’t go through', body: message(cause, 'Try again.') }); }
    finally { setBusy(false); }
  }
  const labels = { cancel_run: 'Cancel the run', pause_worker: 'Pause the worker', create_incident: 'Open an incident' } as const;
  return <article className='ws-detail'>
    <header className='ws-detail-head'>
      <Avatar name={escalation.workerName} seed={escalation.workerId} size={40} />
      <div><p className='ws-eyebrow'>{escalation.workerName} · {humanize(escalation.trigger)}</p><h2>{escalation.title}</h2><p className='ws-muted'><State value={escalation.severity} label={escalation.severity} /> · {escalation.status} · raised <Ago value={escalation.createdAt} /></p></div>
    </header>
    <p className='ws-detail-why'>{escalation.summary}</p>
    {escalation.reason && <p className='ws-muted'>Why it surfaced: {escalation.reason}</p>}
    <Facts items={[['Assigned to', escalation.assignedToUserId === user.userId ? 'You' : escalation.assignedToUserId], ['Run', escalation.runId ? <code key='r'>{escalation.runId}</code> : '—'], ['Approval', escalation.approvalId ? <a key='a' className='ws-link' {...linkProps({ page: 'inbox', kind: 'approval', id: escalation.approvalId })}>Open approval</a> : '—'], ['Incident', escalation.incidentId ? <code key='i'>{escalation.incidentId}</code> : '—']]} />
    {canManage && escalation.status !== 'resolved' && <div className='ws-decision-box'>
      <div className='ws-decision-actions' style={{ borderTop: 0, paddingTop: 0 }}>
        {escalation.status === 'open' && <button type='button' className='ws-button' disabled={busy} onClick={() => void run(() => setEscalationStatus(apiVersion, organization.id, escalation.id, 'acknowledged'), 'Acknowledged')}>Acknowledge</button>}
        <button type='button' className='ws-button ws-button-primary' disabled={busy} onClick={() => setConfirm('resolve')}>Resolve</button>
      </div>
      <div className='ws-intervene'>
        <span className='ws-muted'>Step in</span>
        {escalation.runId && <button type='button' className='ws-button ws-button-sm ws-button-danger' disabled={busy} onClick={() => setConfirm('cancel_run')}>Cancel run</button>}
        <button type='button' className='ws-button ws-button-sm' disabled={busy} onClick={() => setConfirm('pause_worker')}>Pause worker</button>
        {canIncident && <button type='button' className='ws-button ws-button-sm' disabled={busy} onClick={() => setConfirm('create_incident')}>Open incident</button>}
      </div>
    </div>}
    <section className='ws-notes'>
      <h3>Notes</h3>
      {escalation.notes.length ? <ol>{escalation.notes.map((entry, index) => <li key={index}><Avatar name={entry.authorUserId === user.userId ? (user.name || 'You') : entry.authorUserId} size={24} /><div><strong>{entry.authorUserId === user.userId ? 'You' : entry.authorUserId}</strong> <Ago value={entry.createdAt} /><p>{entry.body}</p></div></li>)}</ol> : <p className='ws-muted'>No notes yet.</p>}
      {escalation.interventions.length > 0 && <ol>{escalation.interventions.map((entry, index) => <li key={'i' + index}><span className='ws-doc-icon' aria-hidden='true'><ShieldCheck size={14} /></span><div><strong>{humanize(entry.action)}</strong> <Ago value={entry.occurredAt} /><p>{entry.reason} · {entry.result}</p></div></li>)}</ol>}
      {canManage && <form className='ws-note-form' onSubmit={(event) => { event.preventDefault(); if (note.trim().length < 2) return; void run(() => addEscalationNote(apiVersion, organization.id, escalation.id, note.trim()), 'Note added').then(() => setNote('')); }}>
        <input value={note} onChange={(event) => setNote(event.target.value)} placeholder='Add a note…' aria-label='Add a note' /><button className='ws-button' disabled={busy || note.trim().length < 2}>Add</button>
      </form>}
      {canManage && <form className='ws-note-form' onSubmit={(event) => { event.preventDefault(); if (!assignee.trim()) return; void run(() => reassignEscalation(apiVersion, organization.id, escalation.id, assignee.trim()), 'Reassigned').then(() => setAssignee('')); }}>
        <input value={assignee} onChange={(event) => setAssignee(event.target.value)} placeholder='Reassign to user ID' aria-label='Reassign to user ID' /><button className='ws-button' disabled={busy || !assignee.trim()}>Reassign</button>
      </form>}
    </section>
    <Confirm open={confirm === 'resolve'} title='Resolve this escalation' confirmLabel='Resolve' note={{ label: 'Resolution note', required: true, minLength: 3, placeholder: 'What was decided or fixed' }} onCancel={() => setConfirm(null)}
      onConfirm={(text) => { setConfirm(null); void run(() => setEscalationStatus(apiVersion, organization.id, escalation.id, 'resolved', text), 'Resolved'); }} />
    {confirm && confirm !== 'resolve' && <Confirm open title={labels[confirm]} tone={confirm === 'create_incident' ? 'default' : 'danger'} confirmLabel={labels[confirm]}
      body={confirm === 'cancel_run' ? 'The run stops and any action waiting on approval can no longer reach a tool.' : confirm === 'pause_worker' ? 'The worker stops picking up new work until someone resumes it.' : 'An incident record is created and linked to this escalation.'}
      note={{ label: 'Reason', required: true, minLength: 5, placeholder: 'Recorded in the audit trail' }} onCancel={() => setConfirm(null)}
      onConfirm={(text) => { const action = confirm; setConfirm(null); void run(async () => (await interveneEscalation(apiVersion, organization.id, escalation.id, action, text)).escalation, 'Done'); }} />}
  </article>;
}

function ResultDetail({ result }: { result: ResultSummary }) {
  return <article className='ws-detail'>
    <header className='ws-detail-head'><span className='ws-doc-icon ws-doc-icon-lg' aria-hidden='true'><FileText size={20} /></span><div><p className='ws-eyebrow'>{result.workerName} · {result.jobName}</p><h2>{result.title}</h2><p className='ws-muted'>Finished <Ago value={result.completedAt} /></p></div></header>
    <p className='ws-detail-why'>{result.summary || 'This result was marked as needing a person to look at it.'}</p>
    <div><a className='ws-button ws-button-primary' {...linkProps({ page: 'results', resultId: result.id })}>Open result</a></div>
  </article>;
}

function RulesEditor({ rules, organization, apiVersion, onChange }: { rules: EscalationRule[]; organization: OrganizationAccess; apiVersion: ApiVersion; onChange: (rule: EscalationRule) => void }) {
  const canManage = organization.permissions.includes('supervision.manage');
  async function patch(rule: EscalationRule, change: Partial<EscalationRule>) {
    try { onChange(await updateRule(apiVersion, organization.id, rule.trigger, change)); }
    catch (cause) { notify({ key: 'rule', tone: 'danger', title: 'Rule not saved', body: message(cause, 'Try again.') }); }
  }
  if (!rules.length) return <p className='ws-muted'>No escalation rules are configured.</p>;
  return <ul className='ws-rows'>{rules.map((rule) => <li key={rule.trigger} className='ws-row' style={{ gridTemplateColumns: 'minmax(0,1fr) auto' }}>
    <span className='ws-row-main'><strong style={{ textTransform: 'capitalize' }}>{humanize(rule.trigger)}</strong><small>{rule.notifySupervisor ? 'Alerts the supervisor' : 'Recorded without an alert'}</small></span>
    <div className='ws-row-meta'>
      <select aria-label={`${humanize(rule.trigger)} severity`} value={rule.severity} disabled={!canManage} onChange={(event) => void patch(rule, { severity: event.target.value as EscalationRule['severity'] })} style={{ width: 110 }}>{['low', 'medium', 'high', 'critical'].map((value) => <option key={value}>{value}</option>)}</select>
      <label className='ws-switch'><input type='checkbox' checked={rule.notifySupervisor} disabled={!canManage} onChange={(event) => void patch(rule, { notifySupervisor: event.target.checked })} /><span>Alert</span></label>
      <label className='ws-switch'><input type='checkbox' checked={rule.enabled} disabled={!canManage} onChange={(event) => void patch(rule, { enabled: event.target.checked })} /><span>{rule.enabled ? 'On' : 'Off'}</span></label>
    </div>
  </li>)}</ul>;
}
