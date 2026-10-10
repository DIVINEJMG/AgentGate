import { useState, type ReactNode } from 'react';
import { ArrowRight, CirclePause, FileSearch, Fingerprint, Loader2, Plus, RotateCcw, Scale, Search, ShieldAlert, ShieldCheck, Siren, X } from 'lucide-react';
import type { AgentIdentity } from '../lib/agentApi';
import type { CapabilityCatalog } from '../lib/capabilityApi';
import type { AuditCategory, AuditEvent, AuditResult, AuditSeverity } from '../lib/auditApi';
import type { ExecutionControl, IncidentRecord } from '../lib/incidentApi';
import type { PolicyDecision, PolicyRecord } from '../lib/policyApi';
import type { RiskAssessment, RiskOverview, RiskOverviewItem } from '../lib/riskApi';
import type { ApiVersion } from '../lib/systemApi';
import { policyOrder } from './governanceOrder';
import { Ago, EmptyState, Notice, PageHeader, Section, Sheet, SkeletonLines, State, humanize, sentence } from '../workspace/ui';

const when = (value: string | null) => value ? new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—';
const EFFECT: Record<string, { label: string; tone: 'ok' | 'warn' | 'danger' }> = { allow: { label: 'Allow', tone: 'ok' }, require_approval: { label: 'Needs approval', tone: 'warn' }, deny: { label: 'Block', tone: 'danger' } };
const OUTCOME: Record<string, { label: string; tone: 'ok' | 'warn' | 'danger' }> = { ALLOW: { label: 'Allowed', tone: 'ok' }, REQUIRE_APPROVAL: { label: 'Needs approval', tone: 'warn' }, DENY: { label: 'Blocked', tone: 'danger' } };
const RISK_TONE: Record<string, 'ok' | 'info' | 'warn' | 'danger'> = { low: 'ok', medium: 'info', high: 'warn', critical: 'danger' };

function Split({ list, detail, open }: { list: ReactNode; detail: ReactNode; open: boolean }) {
  return <div className='ws-inbox' data-detail={open ? 'true' : undefined}><div className='ws-inbox-list'>{list}</div><div className='ws-inbox-detail'>{detail}</div></div>;
}
function Placeholder({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return <div className='ws-inbox-placeholder'>{icon}<span>{children}</span></div>;
}

/* ---------- Policies ---------- */

export function PoliciesStage(props: {
  apiVersion: ApiVersion; onApiVersionChange: (value: ApiVersion) => void; policies: PolicyRecord[]; agents: AgentIdentity[]; catalog: CapabilityCatalog | null;
  selected: PolicyRecord | null; select: (id: string) => void; canManage: boolean; loading: boolean; error: string | null;
  openCreate: () => void; openEdit: (policy: PolicyRecord) => void; toggleStatus: () => void;
  evalAgentId: string; setEvalAgentId: (value: string) => void; evalResourceId: string; setEvalResourceId: (value: string) => void;
  evalScope: string; setEvalScope: (value: string) => void; scopeOptions: Array<{ scope: string }>;
  runEvaluation: () => void; evaluating: boolean; decision: PolicyDecision | null; clearDecision: () => void;
}) {
  const p = props;
  const [labOpen, setLabOpen] = useState(false);
  const ranked = policyOrder(p.policies);
  const enabled = p.policies.filter((item) => item.status === 'enabled');
  const tally = (effect: string) => enabled.filter((item) => item.effect === effect).length;
  const names = (ids: string[], choices: Array<{ id: string; name: string }>, fallback: string) => ids.length ? ids.map((id) => choices.find((item) => item.id === id)?.name ?? id).join(', ') : fallback;
  const outcome = p.decision ? OUTCOME[p.decision.outcome] ?? { label: humanize(p.decision.outcome), tone: 'warn' as const } : null;

  return <div className='ws-page ws-page-wide'>
    <PageHeader title='Policies' description='The rules that decide what workers may do. Rules are checked from the top; anything no rule allows is blocked.'
      actions={<><button type='button' className='ws-button' onClick={() => setLabOpen(true)}><Scale size={15} />Test a request</button>{p.canManage && <button type='button' className='ws-button ws-button-primary' onClick={p.openCreate}><Plus size={15} />New policy</button>}</>} />
    {p.error && <Notice tone='danger'>{p.error}</Notice>}
    <p className='ws-summary-line'><strong>{enabled.length}</strong> active rule{enabled.length === 1 ? '' : 's'}: <span data-tone='ok'>{tally('allow')} allow</span>, <span data-tone='warn'>{tally('require_approval')} need approval</span>, <span data-tone='danger'>{tally('deny')} block</span>. When rules tie, block wins, then approval, then allow.</p>
    <Split open={Boolean(p.selected)} list={<>
      {p.loading ? <SkeletonLines rows={5} /> : ranked.length ? <ol className='ws-rows ws-rank'>{ranked.map((item, index) => {
        const effect = EFFECT[item.effect] ?? { label: humanize(item.effect), tone: 'warn' as const };
        return <li key={item.id} className='ws-row ws-inbox-row' aria-current={p.selected?.id === item.id ? 'true' : undefined} data-disabled={item.status === 'disabled' ? 'true' : undefined}>
          <span className='ws-rank-number'>{index + 1}</span>
          <button type='button' className='ws-row-main ws-row-link ws-row-button' onClick={() => p.select(item.id)}><strong>{item.name}</strong><small>Priority {item.priority}{item.status === 'disabled' ? ' · turned off' : ''}</small></button>
          <div className='ws-inbox-row-meta'><span className='ws-state' data-tone={item.status === 'disabled' ? 'neutral' : effect.tone}>{effect.label}</span></div>
        </li>;
      })}
        <li className='ws-row ws-rank-end'><span className='ws-rank-number'>·</span><span className='ws-row-main'><strong>Anything else</strong><small>No rule matched</small></span><div className='ws-inbox-row-meta'><span className='ws-state' data-tone='danger'>Block</span></div></li>
      </ol> : <EmptyState icon={<ShieldCheck size={18} />} title='No rules yet' action={p.canManage ? <button type='button' className='ws-button ws-button-primary' onClick={p.openCreate}><Plus size={14} />Create a rule</button> : undefined}>Until a rule allows something, every request is blocked.</EmptyState>}
    </>} detail={p.selected ? <div className='ws-detail' key={p.selected.id}>
      <button type='button' className='ws-back ws-inbox-back' onClick={() => p.select('')}>← Policies</button>
      <div><div className='ws-muted ws-small'>Priority {p.selected.priority} · revision {p.selected.revision}</div><h2 className='ws-detail-title'>{p.selected.name}</h2>
        <div className='ws-detail-sub'><span className='ws-state' data-tone={EFFECT[p.selected.effect]?.tone}>{EFFECT[p.selected.effect]?.label ?? humanize(p.selected.effect)}</span><State value={p.selected.status} tone={p.selected.status === 'enabled' ? 'ok' : 'neutral'} label={p.selected.status === 'enabled' ? 'On' : 'Off'} /></div></div>
      <p className='ws-detail-why'>{p.selected.description || 'No description.'}</p>
      <div className='ws-sentence'>When <strong>{names(p.selected.selectors.agentIds, p.agents, 'any agent')}</strong> tries <strong>{p.selected.selectors.actions.join(', ') || 'any action'}</strong> on <strong>{names(p.selected.selectors.resourceIds, p.catalog?.resources.map((item) => ({ id: item.id, name: item.displayName })) ?? [], 'any resource')}</strong>{p.selected.selectors.risks.length ? <> at <strong>{p.selected.selectors.risks.join(' or ')}</strong> risk</> : null}, the request is <strong>{(EFFECT[p.selected.effect]?.label ?? p.selected.effect).toLowerCase() === 'block' ? 'blocked' : p.selected.effect === 'allow' ? 'allowed' : 'held for approval'}</strong>.</div>
      <dl className='ws-facts'>
        <div><dt>Scopes</dt><dd>{p.selected.selectors.scopes.length ? <span className='ws-chip-list'>{p.selected.selectors.scopes.map((scope) => <code key={scope}>{scope}</code>)}</span> : 'Any scope'}</dd></div>
        <div><dt>Last changed</dt><dd><Ago value={p.selected.updatedAt} /></dd></div>
      </dl>
      {p.canManage && <div className='ws-form-actions ws-form-actions-start'><button type='button' className='ws-button' onClick={() => p.openEdit(p.selected!)}>Edit rule</button><button type='button' className='ws-button' onClick={p.toggleStatus}>{p.selected.status === 'enabled' ? 'Turn off' : 'Turn on'}</button></div>}
    </div> : <Placeholder icon={<FileSearch size={20} />}>Select a rule to read exactly what it matches.</Placeholder>} />

    <Sheet open={labOpen} onClose={() => setLabOpen(false)} title='Test a request' subtitle='See how the current rules would decide. Nothing is sent to any tool.'
      footer={<><button type='button' className='ws-button' onClick={() => setLabOpen(false)}>Close</button><button type='button' className='ws-button ws-button-primary' onClick={p.runEvaluation} disabled={p.evaluating || !p.evalAgentId || !p.evalResourceId || !p.evalScope}>{p.evaluating ? <Loader2 size={15} className='cf-spin' /> : <ArrowRight size={15} />}Evaluate</button></>}>
      <div className='ws-form'>
        <label className='ws-field'>Agent<select value={p.evalAgentId} onChange={(event) => { p.setEvalAgentId(event.target.value); p.clearDecision(); }}><option value=''>Choose an agent</option>{p.agents.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className='ws-field'>Resource<select value={p.evalResourceId} onChange={(event) => { p.setEvalResourceId(event.target.value); p.clearDecision(); }}><option value=''>Choose a resource</option>{p.catalog?.resources.map((item) => <option key={item.id} value={item.id}>{item.displayName}</option>)}</select></label>
        <label className='ws-field'>Scope<select value={p.evalScope} onChange={(event) => { p.setEvalScope(event.target.value); p.clearDecision(); }}><option value=''>Choose a scope</option>{p.scopeOptions.map((item) => <option key={item.scope} value={item.scope}>{item.scope}</option>)}</select></label>
        {p.decision && outcome && <div className='ws-verdict' data-tone={outcome.tone} key={p.decision.evaluatedAt} role='status'>
          <strong>{outcome.label}</strong>
          <p>{p.decision.reason}</p>
          <dl className='ws-facts ws-facts-stacked'>
            <div><dt>Deciding rule</dt><dd>{p.decision.winningPolicy ? `${p.decision.winningPolicy.name} · priority ${p.decision.winningPolicy.priority}` : 'No rule matched, or a precondition failed'}</dd></div>
            <div><dt>Risk</dt><dd>{p.decision.riskAssessment ? `${sentence(p.decision.riskAssessment.baselineRisk)} → ${p.decision.riskAssessment.effectiveRisk}` : 'Unavailable'}</dd></div>
            <div><dt>Preconditions</dt><dd>{Object.entries(p.decision.preconditions).filter(([, good]) => !good).map(([name]) => humanize(name)).join(', ') || 'All satisfied'}</dd></div>
          </dl>
        </div>}
      </div>
    </Sheet>
  </div>;
}

/* ---------- Risk ---------- */

const levels = ['low', 'medium', 'high', 'critical'] as const;
function RiskInstrument({ assessment, correlation }: { assessment: RiskAssessment; correlation?: string }) {
  const baseline = levels.indexOf(assessment.baselineRisk), effective = levels.indexOf(assessment.effectiveRisk);
  const active = assessment.signals.filter((signal) => signal.active);
  return <div className='ws-risk' key={`${assessment.context.agentId}-${assessment.context.scope}-${assessment.evaluatedAt}`}>
    <p className='ws-detail-why'>{assessment.elevationSteps > 0
      ? <>Risk rose from <strong>{assessment.baselineRisk}</strong> to <strong>{assessment.effectiveRisk}</strong> because {active.map((signal) => humanize(signal.code)).join(' and ') || 'of recent behaviour'}.</>
      : <>Risk stayed at its baseline of <strong>{assessment.baselineRisk}</strong>. No behaviour signal was active.</>}</p>
    <div className='ws-risk-scale' aria-label={`Baseline ${assessment.baselineRisk}, effective ${assessment.effectiveRisk}`}>{levels.map((level, index) => <span key={level} data-reached={index <= effective ? 'true' : undefined} data-raised={index > baseline && index <= effective ? 'true' : undefined} data-tone={RISK_TONE[level]}><i /><small>{sentence(level)}</small></span>)}</div>
    <div className='ws-muted ws-small'>{assessment.context.agentName} → {assessment.context.resourceName} → <code>{assessment.context.scope}</code>{correlation && <> · <code>{correlation.slice(0, 18)}</code></>}</div>
    <Section title='Signals' count={`${active.length} of ${assessment.signals.length}`}>
      <ul className='ws-rows'>{assessment.signals.map((signal) => <li key={signal.code} className='ws-row ws-compact-row ws-signal' data-active={signal.active ? 'true' : undefined}>
        <span className='ws-state' data-tone={signal.active ? 'warn' : 'neutral'}>{signal.active ? 'Active' : 'Quiet'}</span>
        <span className='ws-row-main'><strong>{sentence(signal.code)}</strong><small>{signal.reason}</small></span>
        <div className='ws-row-meta ws-mono'>{signal.count}/{signal.threshold}{signal.windowMinutes ? ` · ${signal.windowMinutes}m` : ''}</div>
      </li>)}</ul>
    </Section>
  </div>;
}

export function RiskStage(p: { apiVersion: ApiVersion; onApiVersionChange: (value: ApiVersion) => void; organizationName: string; overview: RiskOverview; selected: RiskOverviewItem | null; select: (item: RiskOverviewItem) => void; agents: AgentIdentity[]; catalog: CapabilityCatalog | null; resourceId: string; setResourceId: (value: string) => void; agentId: string; setAgentId: (value: string) => void; scope: string; setScope: (value: string) => void; result: RiskAssessment | null; clearResult: () => void; evaluate: () => void; evaluating: boolean; loading: boolean; error: string | null }) {
  const [labOpen, setLabOpen] = useState(false);
  const resource = p.catalog?.resources.find((item) => item.id === p.resourceId);
  const s = p.overview.summary;
  const t = p.overview.thresholds;
  return <div className='ws-page ws-page-wide'>
    <PageHeader title='Risk' description={`How risky recent behaviour in ${p.organizationName} is. Behaviour can raise a capability’s risk, never lower it, and a higher risk can require approval.`}
      actions={<button type='button' className='ws-button' onClick={() => setLabOpen(true)}><ShieldAlert size={15} />Check current risk</button>} />
    {p.error && <Notice tone='danger'>{p.error}</Notice>}
    <p className='ws-summary-line'>Of <strong>{s.assessedActions}</strong> recent actions, <span data-tone={s.elevated ? 'warn' : 'ok'}>{s.elevated} had their risk raised</span> and <span data-tone={s.highOrCritical ? 'danger' : 'ok'}>{s.highOrCritical} were high or critical</span>. {s.activeSignalHits} behaviour signal{s.activeSignalHits === 1 ? '' : 's'} fired.</p>
    <Split open={Boolean(p.selected)} list={p.loading ? <SkeletonLines rows={5} /> : p.overview.assessments.length ? <ul className='ws-rows'>{p.overview.assessments.map((item) => <li key={item.actionId} className='ws-row ws-inbox-row' aria-current={p.selected?.actionId === item.actionId ? 'true' : undefined}>
      <span className='ws-risk-dot' data-tone={RISK_TONE[item.assessment.effectiveRisk]} aria-hidden='true' />
      <button type='button' className='ws-row-main ws-row-link ws-row-button' onClick={() => p.select(item)}><strong>{item.agent.name}</strong><small>{item.resource.name} · {item.scope}</small></button>
      <div className='ws-inbox-row-meta'><span className='ws-state' data-tone={RISK_TONE[item.assessment.effectiveRisk]}>{sentence(item.assessment.effectiveRisk)}</span>{item.assessment.elevationSteps > 0 ? <small className='ws-raised'>raised from {item.assessment.baselineRisk}</small> : <Ago value={item.assessment.evaluatedAt} />}</div>
    </li>)}</ul> : <EmptyState icon={<ShieldCheck size={18} />} title='No assessments yet'>Every gateway action records its risk here. Older actions are not backfilled.</EmptyState>}
      detail={p.selected ? <div className='ws-detail' key={p.selected.actionId}>
        <div><div className='ws-muted ws-small'>{when(p.selected.assessment.evaluatedAt)}</div><h2 className='ws-detail-title'>{p.selected.agent.name} on {p.selected.resource.name}</h2><div className='ws-detail-sub'><span className='ws-state' data-tone={RISK_TONE[p.selected.assessment.effectiveRisk]}>{sentence(p.selected.assessment.effectiveRisk)} risk</span><State value={p.selected.status} /></div></div>
        <RiskInstrument assessment={p.selected.assessment} correlation={p.selected.correlationId} />
      </div> : <Placeholder icon={<ShieldAlert size={20} />}>Select an action to see what moved its risk.</Placeholder>} />
    <p className='ws-muted ws-small'>Thresholds: bursts over {t.burst.count} in {t.burst.minutes}m · blocks over {t.blocks.count} in {t.blocks.minutes}m · failures over {t.failures.count} in {t.failures.minutes}m · approvals over {t.approvals.count} in {t.approvals.minutes}m{t.truncatedHistoryElevation ? ' · incomplete history raises risk one level' : ''}.</p>

    <Sheet open={labOpen} onClose={() => setLabOpen(false)} title='Check current risk' subtitle='Reads recent behaviour. It neither authorizes nor runs anything.' wide={Boolean(p.result)}
      footer={<><button type='button' className='ws-button' onClick={() => setLabOpen(false)}>Close</button><button type='button' className='ws-button ws-button-primary' onClick={p.evaluate} disabled={p.evaluating || !p.agentId || !p.resourceId || !p.scope}>{p.evaluating ? <Loader2 size={15} className='cf-spin' /> : <ArrowRight size={15} />}Assess</button></>}>
      <div className='ws-form'>
        <label className='ws-field'>Agent<select value={p.agentId} onChange={(event) => { p.setAgentId(event.target.value); p.clearResult(); }}><option value=''>Choose an agent</option>{p.agents.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className='ws-field'>Resource<select value={p.resourceId} onChange={(event) => { p.setResourceId(event.target.value); p.clearResult(); }}><option value=''>Choose a resource</option>{p.catalog?.resources.map((item) => <option key={item.id} value={item.id}>{item.displayName}</option>)}</select></label>
        <label className='ws-field'>Scope<select value={p.scope} onChange={(event) => { p.setScope(event.target.value); p.clearResult(); }}><option value=''>Choose a scope</option>{resource?.actions.map((item) => <option key={item.scope} value={item.scope}>{item.scope}</option>)}</select></label>
        {p.result && <RiskInstrument assessment={p.result} />}
      </div>
    </Sheet>
  </div>;
}

/* ---------- Incidents ---------- */

export function IncidentsStage(p: { apiVersion: ApiVersion; onApiVersionChange: (value: ApiVersion) => void; incidents: IncidentRecord[]; selected: IncidentRecord | null; select: (id: string) => void; summary: { open: number; acknowledged: number; resolved: number; critical: number }; orgControl: ExecutionControl | null; targetControl: ExecutionControl | null; canManage: boolean; loading: boolean; busy: boolean; error: string | null; create: () => void; emergencyToggle: () => void; targetToggle: () => void; transition: (status: 'acknowledged' | 'resolved') => void }) {
  const stopped = p.orgControl?.state === 'suspended';
  const sevTone = (severity: string) => severity === 'critical' ? 'danger' : severity === 'high' ? 'warn' : 'neutral';
  return <div className='ws-page ws-page-wide'>
    <PageHeader title='Incidents' description='Stop switches and incident records. Closing an incident never restarts anything by itself; execution is restored separately.'
      actions={p.canManage && <button type='button' className='ws-button ws-button-primary' onClick={p.create}><Plus size={15} />Open incident</button>} />
    {p.error && <Notice tone='danger'>{p.error}</Notice>}
    <div className='ws-killswitch' data-stopped={stopped ? 'true' : undefined}>
      <span className='ws-killswitch-icon'>{stopped ? <CirclePause size={20} /> : <ShieldCheck size={20} />}</span>
      <div><strong>{!p.orgControl ? 'Checking organization state…' : stopped ? 'All workers are stopped' : 'Workers can run'}</strong><span>{p.orgControl?.reason || (stopped ? 'An emergency stop is active for the whole organization.' : 'No emergency stop is active. Use it to halt every worker at once.')}</span></div>
      {p.canManage && <button type='button' className={stopped ? 'ws-button' : 'ws-button ws-button-danger'} disabled={p.busy || !p.orgControl} onClick={p.emergencyToggle}>{stopped ? <><RotateCcw size={15} />Restore</> : <><CirclePause size={15} />Emergency stop</>}</button>}
    </div>
    <p className='ws-summary-line'><span data-tone={p.summary.open ? 'danger' : 'ok'}>{p.summary.open} open</span>, {p.summary.acknowledged} being investigated, {p.summary.resolved} resolved{p.summary.critical ? <>, <span data-tone='danger'>{p.summary.critical} critical</span></> : null}.</p>
    <Split open={Boolean(p.selected)} list={p.loading ? <SkeletonLines rows={4} /> : p.incidents.length ? <ul className='ws-rows'>{p.incidents.map((item) => <li key={item.id} className='ws-row ws-inbox-row' aria-current={p.selected?.id === item.id ? 'true' : undefined}>
      <span className='ws-risk-dot' data-tone={sevTone(item.severity)} aria-hidden='true' />
      <button type='button' className='ws-row-main ws-row-link ws-row-button' onClick={() => p.select(item.id)}><strong>{item.title}</strong><small>{sentence(item.target.type)} · {item.target.name}</small></button>
      <div className='ws-inbox-row-meta'><State value={item.status} tone={item.status === 'open' ? 'danger' : item.status === 'acknowledged' ? 'warn' : 'ok'} label={item.status === 'acknowledged' ? 'Investigating' : sentence(item.status)} /><Ago value={item.createdAt} /></div>
    </li>)}</ul> : <EmptyState icon={<Siren size={18} />} title='No incidents'>When something needs investigating, open an incident to track it here.</EmptyState>}
      detail={p.selected ? <div className='ws-detail' key={p.selected.id}>
        <button type='button' className='ws-back ws-inbox-back' onClick={() => p.select('')}>← Incidents</button>
        <div><div className='ws-muted ws-small'>Opened {when(p.selected.createdAt)}</div><h2 className='ws-detail-title'>{p.selected.title}</h2><div className='ws-detail-sub'><span className='ws-state' data-tone={sevTone(p.selected.severity)}>{sentence(p.selected.severity)} severity</span><State value={p.selected.status} label={p.selected.status === 'acknowledged' ? 'Investigating' : sentence(p.selected.status)} /></div></div>
        <p className='ws-detail-why'>{p.selected.description || 'No description.'}</p>
        <div className='ws-killswitch ws-killswitch-sm' data-stopped={p.targetControl?.state === 'suspended' ? 'true' : undefined}>
          <span className='ws-killswitch-icon'>{p.targetControl?.state === 'suspended' ? <CirclePause size={16} /> : <ShieldCheck size={16} />}</span>
          <div><strong>{p.selected.target.name} is {p.targetControl ? p.targetControl.state === 'suspended' ? 'stopped' : 'running normally' : '…'}</strong><span>{p.targetControl?.reason || 'This stop is independent of the incident status.'}</span></div>
          {p.canManage && <button type='button' className={p.targetControl?.state === 'suspended' ? 'ws-button ws-button-sm' : 'ws-button ws-button-sm ws-button-danger'} disabled={p.busy || !p.targetControl} onClick={p.targetToggle}>{p.targetControl?.state === 'suspended' ? 'Restore' : 'Stop'} {humanize(p.selected.target.type)}</button>}
        </div>
        <ol className='ws-progress-steps' aria-label='Incident progress'>
          <li data-done='true'><strong>Opened</strong><small>{when(p.selected.createdAt)}</small></li>
          <li data-done={p.selected.acknowledgedAt ? 'true' : undefined}><strong>Investigating</strong><small>{when(p.selected.acknowledgedAt)}</small></li>
          <li data-done={p.selected.resolvedAt ? 'true' : undefined}><strong>Resolved</strong><small>{when(p.selected.resolvedAt)}</small></li>
        </ol>
        {p.selected.status === 'resolved' && <Notice tone='ok'>{p.selected.resolutionNote || 'Resolved without a note.'} Execution stays as it is until someone restores it.</Notice>}
        {p.canManage && p.selected.status !== 'resolved' && <div className='ws-form-actions ws-form-actions-start'>
          {p.selected.status === 'open' && <button type='button' className='ws-button' disabled={p.busy} onClick={() => p.transition('acknowledged')}>Start investigating</button>}
          <button type='button' className='ws-button ws-button-primary' disabled={p.busy} onClick={() => p.transition('resolved')}>Resolve</button>
        </div>}
        <details className='ws-technical'><summary>Technical detail</summary><dl className='ws-facts'><div><dt>Correlation</dt><dd><code>{p.selected.correlationId}</code></dd></div><div><dt>Target</dt><dd><code>{p.selected.target.id}</code></dd></div></dl></details>
      </div> : <Placeholder icon={<Siren size={20} />}>Select an incident to see its state and controls.</Placeholder>} />
  </div>;
}

/* ---------- Audit ---------- */

const categories: AuditCategory[] = ['identity', 'workforce', 'integration', 'policy', 'action', 'approval', 'risk', 'incident', 'security', 'system'];
const SEV_TONE: Record<string, 'neutral' | 'warn' | 'danger'> = { info: 'neutral', warning: 'warn', critical: 'danger' };

export function AuditStage(p: { apiVersion: ApiVersion; onApiVersionChange: (value: ApiVersion) => void; organizationName: string; result: AuditResult; selected: AuditEvent | null; select: (event: AuditEvent) => void; q: string; setQ: (value: string) => void; category: AuditCategory | ''; setCategory: (value: AuditCategory | '') => void; severity: AuditSeverity | ''; setSeverity: (value: AuditSeverity | '') => void; correlationId: string; loading: boolean; error: string | null; refresh: () => void; clear: () => void; trace: (event: AuditEvent) => void }) {
  const s = p.result.summary;
  const filtered = Boolean(p.q || p.category || p.severity || p.correlationId);
  return <div className='ws-page ws-page-wide'>
    <PageHeader title='Audit' description={`A permanent record of who did what in ${p.organizationName}, and why it was allowed. Follow a correlation to see every connected decision.`} />
    {p.error && <Notice tone='danger'>{p.error}</Notice>}
    <p className='ws-summary-line'><strong>{s.visible}</strong> events{s.latestAt ? <>, the latest <Ago value={s.latestAt} /></> : null}. <span data-tone={s.bySeverity.critical ? 'danger' : 'ok'}>{s.bySeverity.critical} critical</span>, <span data-tone={s.bySeverity.warning ? 'warn' : 'ok'}>{s.bySeverity.warning} warnings</span> across {s.uniqueCorrelations} traces.</p>
    <form className='ws-filter-row' onSubmit={(event) => { event.preventDefault(); p.refresh(); }}>
      <label className='ws-search-field'><Search size={15} aria-hidden='true' /><input value={p.q} onChange={(event) => p.setQ(event.target.value)} placeholder='Search events, people, resources' aria-label='Search audit events' /></label>
      <select aria-label='Category' value={p.category} onChange={(event) => p.setCategory(event.target.value as AuditCategory | '')}><option value=''>Any category</option>{categories.map((item) => <option key={item} value={item}>{sentence(item)}</option>)}</select>
      <select aria-label='Severity' value={p.severity} onChange={(event) => p.setSeverity(event.target.value as AuditSeverity | '')}><option value=''>Any severity</option><option value='info'>Info</option><option value='warning'>Warning</option><option value='critical'>Critical</option></select>
      <button type='submit' className='ws-button ws-button-sm'>Apply</button>
      {filtered && <button type='button' className='ws-button ws-button-sm ws-button-quiet' onClick={p.clear}><X size={14} />Clear</button>}
    </form>
    {p.correlationId && <Notice tone='info' action={<button type='button' className='ws-button ws-button-sm' onClick={p.clear}>Exit trace</button>}><Fingerprint size={15} /> Showing every event in trace <code>{p.correlationId}</code></Notice>}
    <Split open={Boolean(p.selected)} list={p.loading ? <SkeletonLines rows={6} /> : p.result.events.length ? <ul className='ws-rows ws-audit-stream'>{p.result.events.map((event) => <li key={event.id} className='ws-row ws-inbox-row' aria-current={p.selected?.id === event.id ? 'true' : undefined}>
      <time className='ws-audit-time ws-mono' dateTime={event.occurredAt} title={new Date(event.occurredAt).toLocaleString()}>{new Date(event.occurredAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}<small>{new Date(event.occurredAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</small></time>
      <button type='button' className='ws-row-main ws-row-link ws-row-button' onClick={() => p.select(event)}><strong>{event.summary}</strong><small>{event.actor.label || event.actor.id} → {event.resource.name || event.resource.id}</small></button>
      <div className='ws-inbox-row-meta'><span className='ws-state' data-tone={SEV_TONE[event.severity]}>{sentence(event.category)}</span></div>
    </li>)}</ul> : <EmptyState icon={<ShieldCheck size={18} />} title='No matching events'>Adjust the filters, or wait for new activity.</EmptyState>}
      detail={p.selected ? <div className='ws-detail' key={p.selected.id}>
        <div><div className='ws-muted ws-small'>{when(p.selected.occurredAt)}</div><h2 className='ws-detail-title'>{p.selected.summary}</h2><div className='ws-detail-sub'><span className='ws-state' data-tone={SEV_TONE[p.selected.severity]}>{sentence(p.selected.severity)}</span><code className='ws-muted'>{p.selected.eventType}</code></div></div>
        <dl className='ws-facts'>
          <div><dt>Who</dt><dd>{p.selected.actor.label || p.selected.actor.id}<small>{sentence(p.selected.actor.type)}</small></dd></div>
          <div><dt>On what</dt><dd>{p.selected.resource.name || p.selected.resource.id}<small>{sentence(p.selected.resource.type)}</small></dd></div>
          <div><dt>Outcome</dt><dd>{p.selected.outcome ? sentence(p.selected.outcome.toLowerCase()) : '—'}</dd></div>
        </dl>
        {p.selected.correlationId && <div className='ws-form-actions ws-form-actions-start'><button type='button' className='ws-button' onClick={() => p.trace(p.selected!)}><Fingerprint size={14} />Follow this trace</button></div>}
        <Section title='Recorded detail'><pre className='ws-code'>{JSON.stringify(p.selected.metadata, null, 2)}</pre></Section>
        {p.selected.correlationId && <p className='ws-muted ws-small'>Correlation <code>{p.selected.correlationId}</code></p>}
      </div> : <Placeholder icon={<FileSearch size={20} />}>Select an event to see who, what and why.</Placeholder>} />
    {p.result.window.truncated && <p className='ws-muted ws-small'>Searched the latest {p.result.window.limit} events. Narrow the filters to reach older records.</p>}
  </div>;
}
