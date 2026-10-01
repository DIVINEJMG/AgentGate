import { ArrowRight, ArrowUpRight, CheckCircle2, CircleAlert, Clock3, Play, ShieldCheck } from 'lucide-react';
import type { OrganizationAccess } from '../lib/identityApi';
import type { ActivityItem, PerformanceWorkspace } from '../lib/performanceApi';
import type { AppView } from '../navigation';

type Props = {
  organization: OrganizationAccess;
  operational: boolean;
  statusError: boolean;
  performance: PerformanceWorkspace | null;
  integrationCount: number;
  agentCount: number;
  onNavigate: (view: AppView) => void;
};

const routeForActivity: Record<ActivityItem['kind'], AppView> = {
  run: 'runtime', action: 'actions', escalation: 'supervision', incident: 'incidents',
};

export default function WorkspaceOverview({ organization, operational, statusError, performance, integrationCount, agentCount, onNavigate }: Props) {
  const summary = performance?.summary;
  const review = performance?.activity.filter(needsReview).slice(0, 4) ?? [];
  const recent = performance?.activity.slice(0, 6) ?? [];
  const trends = performance?.trends.slice(-14) ?? [];
  const maxTrend = Math.max(1, ...trends.map((day) => day.completed + day.failed + day.cancelled));
  const signals = summary ? summary.failed + summary.escalations + summary.incidents : null;

  return <div className='workspace-dashboard'>
    <header className='dashboard-heading'>
      <div><p className='dashboard-eyebrow'>{organization.name} / WORKSPACE</p><h1>Overview</h1><p>Workforce activity and decisions that need your attention.</p></div>
      <div className={`dashboard-health ${operational ? 'is-ok' : 'is-error'}`}><span className='dashboard-health-dot' /><span>{operational ? 'Operational' : 'Unavailable'}</span></div>
    </header>

    {statusError && <div className='dashboard-alert' role='alert'><CircleAlert size={17} aria-hidden='true' /><div><strong>Control plane unavailable</strong><span>Audoryn is failing closed until system state can be verified.</span></div></div>}

    <section className='dashboard-metrics' aria-label='Workspace snapshot'>
      <Metric label='Active workers' value={summary ? `${summary.activeWorkers} / ${summary.workers}` : '—'} detail='Configured workforce' onClick={() => onNavigate('workforce')} />
      <Metric label='Runs' value={summary?.runs ?? '—'} detail='In reporting window' onClick={() => onNavigate('runtime')} />
      <Metric label='Success rate' value={summary?.terminalRuns ? `${summary.successRate}%` : '—'} detail='Of terminal runs' onClick={() => onNavigate('performance')} />
      <Metric label='Review signals' value={signals ?? '—'} detail='Failures, escalations, incidents' onClick={() => onNavigate('supervision')} attention={Boolean(signals)} />
    </section>

    <div className='dashboard-primary-grid'>
      <section className='dashboard-panel dashboard-review' aria-labelledby='dashboard-review-title'>
        <PanelHeading title='Review signals' id='dashboard-review-title' description='Recent events that may need a decision or investigation.' action='Open supervision' onClick={() => onNavigate('supervision')} />
        {review.length ? <div className='dashboard-event-list'>{review.map((item) => <EventRow key={item.id} item={item} onNavigate={onNavigate} />)}</div> : <div className='dashboard-empty'><CheckCircle2 size={17} aria-hidden='true' /><div><strong>{performance ? 'No signals in recent activity' : 'Activity snapshot unavailable'}</strong><span>{performance ? 'The recent event window has no exceptions.' : 'Open the dedicated views for current work.'}</span></div></div>}
        <p className='dashboard-footnote'>This is a recent event sample. Open each queue to see its full contents.</p>
      </section>

      <section className='dashboard-panel dashboard-runs' aria-labelledby='dashboard-runs-title'>
        <PanelHeading title='Run outcomes' id='dashboard-runs-title' description='Completed, failed, and cancelled runs.' action='Performance' onClick={() => onNavigate('performance')} />
        <div className='dashboard-outcome-counts'><div><span>Completed</span><strong>{summary?.completed ?? '—'}</strong></div><div><span>Failed</span><strong>{summary?.failed ?? '—'}</strong></div><div><span>Cancelled</span><strong>{summary?.cancelled ?? '—'}</strong></div></div>
        <div className='dashboard-chart-heading'><span>RECENT RUNS</span><span>{trends.length ? `${trends.length} days` : 'No trend data'}</span></div>
        <div className='dashboard-chart' role='img' aria-label={trends.length ? `Run activity over ${trends.length} days. Completed ${summary?.completed ?? 0}, failed ${summary?.failed ?? 0}, cancelled ${summary?.cancelled ?? 0} in the reporting window.` : 'No run trend data available'}>
          {trends.length ? trends.map((day) => <div key={day.date} className='dashboard-chart-day' title={`${day.date}: ${day.completed} completed, ${day.failed} failed, ${day.cancelled} cancelled`}><span className='dashboard-chart-bar'><i className='is-completed' style={{ height: `${day.completed / maxTrend * 100}%` }} /><i className='is-failed' style={{ height: `${day.failed / maxTrend * 100}%` }} /><i className='is-cancelled' style={{ height: `${day.cancelled / maxTrend * 100}%` }} /></span></div>) : <span className='dashboard-chart-empty'>Run history will appear here.</span>}
        </div>
        <div className='dashboard-chart-legend'><span><i className='is-completed' /> Completed</span><span><i className='is-failed' /> Failed</span><span><i className='is-cancelled' /> Cancelled</span></div>
      </section>
    </div>

    <section className='dashboard-panel dashboard-activity' aria-labelledby='dashboard-activity-title'>
      <PanelHeading title='Recent activity' id='dashboard-activity-title' description='The latest workforce and governance events.' action='All activity' onClick={() => onNavigate('actions')} />
      <div className='dashboard-table-head'><span>EVENT</span><span>SOURCE</span><span>STATE</span><span>WHEN</span><span className='sr-only'>Open</span></div>
      {recent.length ? <div className='dashboard-event-list'>{recent.map((item) => <EventRow key={item.id} item={item} onNavigate={onNavigate} table />)}</div> : <div className='dashboard-empty'><Clock3 size={17} aria-hidden='true' /><div><strong>{performance ? 'No activity yet' : 'Activity snapshot unavailable'}</strong><span>{performance ? 'Events will appear when your workers start running jobs.' : 'Open Activity to inspect current events.'}</span></div></div>}
    </section>

    <footer className='dashboard-footer'><div><ShieldCheck size={14} aria-hidden='true' /><span>{integrationCount} connected tools</span><span aria-hidden='true'>·</span><span>{agentCount} agent identities</span></div><button type='button' onClick={() => onNavigate('approvals')}>Open approvals <ArrowRight size={14} aria-hidden='true' /></button></footer>
  </div>;
}

function Metric({ label, value, detail, onClick, attention = false }: { label: string; value: string | number; detail: string; onClick: () => void; attention?: boolean }) {
  return <button type='button' className={`dashboard-metric${attention ? ' is-attention' : ''}`} onClick={onClick}><span>{label}</span><strong>{value}</strong><small>{detail}</small><ArrowUpRight size={14} aria-hidden='true' /></button>;
}

function PanelHeading({ title, id, description, action, onClick }: { title: string; id: string; description: string; action: string; onClick: () => void }) {
  return <div className='dashboard-panel-head'><div><h2 id={id}>{title}</h2><p>{description}</p></div><button type='button' onClick={onClick}>{action} <ArrowUpRight size={14} aria-hidden='true' /></button></div>;
}

function EventRow({ item, onNavigate, table = false }: { item: ActivityItem; onNavigate: (view: AppView) => void; table?: boolean }) {
  const route = routeForActivity[item.kind];
  return <button type='button' className={`dashboard-event${table ? ' is-table' : ''}`} onClick={() => onNavigate(route)} aria-label={`${item.label}. ${item.status.replaceAll('_', ' ')}. Open ${route}.`}>
    <span className='dashboard-event-title'><span className={`dashboard-event-kind is-${item.kind}`}>{item.kind === 'run' ? <Play size={12} aria-hidden='true' /> : item.kind === 'incident' ? <CircleAlert size={13} aria-hidden='true' /> : item.kind === 'escalation' ? <ShieldCheck size={13} aria-hidden='true' /> : <ArrowRight size={13} aria-hidden='true' />}</span><span><strong>{item.label}</strong>{!table && <small>{item.workerName || item.department || item.kind}</small>}</span></span>
    {table && <span className='dashboard-event-source'>{item.workerName || item.department || '—'}</span>}
    <span className={`dashboard-event-state${needsReview(item) ? ' is-attention' : ''}`}>{item.status.replaceAll('_', ' ')}</span>
    <time dateTime={item.occurredAt}>{formatTime(item.occurredAt)}</time>
    <ArrowUpRight className='dashboard-event-open' size={14} aria-hidden='true' />
  </button>;
}

function needsReview(item: ActivityItem) {
  return ['fail', 'held', 'waiting', 'open', 'critical', 'blocked'].some((term) => item.status.toLowerCase().includes(term));
}

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const minutes = Math.floor((Date.now() - date.getTime()) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
