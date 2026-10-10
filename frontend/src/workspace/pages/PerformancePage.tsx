import { useEffect, useState } from 'react';
import { Gauge, RefreshCw } from 'lucide-react';
import type { OrganizationAccess } from '../../lib/identityApi';
import type { ApiVersion } from '../../lib/systemApi';
import { loadPerformance, type PerformanceWorkspace } from '../../lib/performanceApi';
import { linkProps, queryParam, setQuery } from '../routes';
import { Ago, Avatar, EmptyState, Notice, PageHeader, Section, SkeletonLines, sentence } from '../ui';

function elapsed(ms: number) { if (!ms) return '—'; return ms < 60000 ? `${Math.max(1, Math.round(ms / 1000))}s` : ms < 3600000 ? `${Math.round(ms / 60000)}m` : `${(ms / 3600000).toFixed(1)}h`; }
function errorText(value: unknown) {
  const data = value as { response?: { data?: { error?: string } }; message?: string };
  return data.response?.data?.error || data.message || 'Performance analytics request failed.';
}
const KIND_TONE: Record<string, string> = { run: 'info', action: 'neutral', escalation: 'warn', incident: 'danger' };

function Rate({ value, runs }: { value: number; runs?: number }) {
  if (runs === 0) return <span className='ws-muted'>—</span>;
  const tone = value >= 80 ? 'ok' : value >= 60 ? 'warn' : 'danger';
  return <span className='ws-rate' data-tone={tone}><span className='ws-rate-track'><i style={{ width: `${Math.max(2, Math.min(100, value))}%` }} /></span><b>{value}%</b></span>;
}

export default function PerformancePage({ organization, apiVersion }: { organization: OrganizationAccess; apiVersion: ApiVersion }) {
  const [data, setData] = useState<PerformanceWorkspace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const worker = queryParam('worker');

  async function hydrate() {
    setError(null);
    try { setData(await loadPerformance(apiVersion, organization.id)); } catch (caught) { setError(errorText(caught)); } finally { setLoading(false); }
  }
  useEffect(() => { setLoading(true); void hydrate(); }, [apiVersion, organization.id]);

  const summary = data?.summary;
  const trends = data?.trends ?? [];
  const max = Math.max(1, ...trends.map((point) => point.completed + point.failed + point.cancelled));
  const truncated = Object.values(data?.window ?? {}).some((item) => item.truncated);
  const activity = (data?.activity ?? []).filter((item) => !worker || item.workerId === worker);
  const totals = trends.reduce((sum, point) => ({ completed: sum.completed + point.completed, failed: sum.failed + point.failed, cancelled: sum.cancelled + point.cancelled }), { completed: 0, failed: 0, cancelled: 0 });

  return <div className='ws-page'>
    <PageHeader title='Performance' description={<>How work is going across workers, departments and tools.{data ? <> Updated <Ago value={data.generatedAt} />.</> : null}</>}
      actions={<button type='button' className='ws-button' disabled={loading} onClick={() => { setLoading(true); void hydrate(); }}><RefreshCw size={15} />Refresh</button>} />
    {error && <Notice tone='danger' action={<button type='button' className='ws-button ws-button-sm' onClick={() => void hydrate()}>Retry</button>}>{error}</Notice>}
    {truncated && <Notice tone='warn'>Some sources hit their scan limit. Rates describe the work that was scanned, not all history.</Notice>}

    {loading && !data ? <SkeletonLines rows={6} /> : !summary ? null : <>
      <dl className='ws-stats'>
        <div><dt>Runs</dt><dd>{summary.runs}</dd><small>{summary.completed} completed · {summary.failed} failed</small></div>
        <div><dt>Run success</dt><dd>{summary.terminalRuns ? `${summary.successRate}%` : '—'}</dd><small>of {summary.terminalRuns} finished runs</small></div>
        <div><dt>Median run</dt><dd>{elapsed(summary.medianRunDurationMs)}</dd><small>average {elapsed(summary.averageRunDurationMs)}</small></div>
        <div><dt>Approval rate</dt><dd>{summary.approvalRate}%</dd><small>of decided approvals</small></div>
        <div><dt>Blocked actions</dt><dd>{summary.blockedActions}</dd><small>{summary.escalationsPer100Runs} escalations per 100 runs</small></div>
      </dl>

      <Section title='Last 14 days' description={`${totals.completed} completed · ${totals.failed} failed · ${totals.cancelled} cancelled`}>
        <div className='ws-chart' role='img' aria-label={`Daily run outcomes for the last ${trends.length} days`}>
          {trends.map((point) => {
            const total = point.completed + point.failed + point.cancelled;
            return <div key={point.date} className='ws-chart-day' title={`${new Date(point.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}: ${point.completed} completed, ${point.failed} failed, ${point.cancelled} cancelled`}>
              <div className='ws-chart-bar' style={{ height: `${total / max * 100}%` }}>
                {point.cancelled > 0 && <i data-kind='cancelled' style={{ flexGrow: point.cancelled }} />}
                {point.failed > 0 && <i data-kind='failed' style={{ flexGrow: point.failed }} />}
                {point.completed > 0 && <i data-kind='completed' style={{ flexGrow: point.completed }} />}
              </div>
              <small>{new Date(point.date).toLocaleDateString(undefined, { day: 'numeric' })}</small>
            </div>;
          })}
          {!trends.length && <p className='ws-quiet'>The trend appears after the first managed runs.</p>}
        </div>
        <div className='ws-legend'><span data-kind='completed'>Completed</span><span data-kind='failed'>Failed</span><span data-kind='cancelled'>Cancelled</span></div>
      </Section>

      <Section title='Workers' count={data!.workers.length}>
        {data!.workers.length ? <div className='ws-table-wrap'><table className='ws-table'>
          <thead><tr><th>Worker</th><th className='ws-num'>Runs</th><th>Success</th><th className='ws-num'>Median run</th><th className='ws-num'>Waiting</th><th className='ws-num'>Blocked</th><th className='ws-num'>Escalations</th></tr></thead>
          <tbody>{data!.workers.map((item) => <tr key={item.id}>
            <td><a className='ws-cell-link' {...linkProps({ page: 'worker', workerId: item.id, tab: 'overview' })}><Avatar name={item.name} seed={item.id} size={26} /><span><strong>{item.name}</strong><small>{item.department || 'No department'} · {sentence(item.status)}</small></span></a></td>
            <td className='ws-num'>{item.runs}</td>
            <td><Rate value={item.successRate} runs={item.completed + item.failed + item.cancelled} /></td>
            <td className='ws-num'>{elapsed(item.medianRunDurationMs)}</td>
            <td className='ws-num'>{item.waitingApproval}</td>
            <td className='ws-num'>{item.blockedActions}</td>
            <td className='ws-num'>{item.escalations}</td>
          </tr>)}</tbody>
        </table></div> : <EmptyState icon={<Gauge size={18} />} title='No worker activity yet'>Worker figures appear after managed runs.</EmptyState>}
      </Section>

      <div className='ws-cols'>
        <Section title='Departments' count={data!.departments.length}>
          {data!.departments.length ? <div className='ws-table-wrap'><table className='ws-table'>
            <thead><tr><th>Department</th><th className='ws-num'>Workers</th><th className='ws-num'>Runs</th><th>Success</th></tr></thead>
            <tbody>{data!.departments.map((item) => <tr key={item.name}><td><strong>{item.name || 'Unassigned'}</strong></td><td className='ws-num'>{item.workers}</td><td className='ws-num'>{item.runs}</td><td><Rate value={item.successRate} runs={item.completed + item.failed + item.cancelled} /></td></tr>)}</tbody>
          </table></div> : <p className='ws-quiet'>No department data yet.</p>}
        </Section>
        <Section title='Tools' count={data!.tools.length} description='Provider attempts only'>
          {data!.tools.length ? <div className='ws-table-wrap'><table className='ws-table'>
            <thead><tr><th>Tool</th><th>Reliability</th><th className='ws-num'>Held</th><th className='ws-num'>Blocked</th></tr></thead>
            <tbody>{data!.tools.map((tool) => <tr key={tool.id}><td><strong>{tool.name}</strong><small className='ws-block'>{tool.provider} · {tool.executed} executed · {tool.failed} failed</small></td><td><Rate value={tool.reliabilityRate} runs={tool.providerAttempts} /></td><td className='ws-num'>{tool.held}</td><td className='ws-num'>{tool.blocked}</td></tr>)}</tbody>
          </table></div> : <p className='ws-quiet'>Tool reliability appears after workers use a connected tool.</p>}
        </Section>
      </div>

      <Section title='Recent signals' count={activity.length} action={<select aria-label='Filter signals by worker' className='ws-select-sm' value={worker} onChange={(event) => setQuery({ worker: event.target.value || undefined })}><option value=''>All workers</option>{data!.workers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}>
        {activity.length ? <ul className='ws-rows'>{activity.slice(0, 12).map((item) => <li key={item.id} className='ws-row ws-signal-row'>
          <span className='ws-signal-kind' data-tone={KIND_TONE[item.kind]}>{sentence(item.kind)}</span>
          <span className='ws-row-main'><strong>{item.label}</strong><small>{item.workerName}{item.department ? ` · ${item.department}` : ''} · {sentence(item.status)}</small></span>
          <div className='ws-row-meta'><Ago value={item.occurredAt} /></div>
        </li>)}</ul> : <p className='ws-quiet'>No signals in this view.</p>}
      </Section>

      <details className='ws-technical'>
        <summary>How these figures are measured</summary>
        <div className='ws-prose'>
          <p>These are signals about observable work and governance outcomes. They do not grade people and never authorize an action. Blocks and approval holds are governance decisions, not tool failures.</p>
          <dl className='ws-facts'>
            <div><dt>Run success</dt><dd>{data!.methodology.successRate}</dd></div>
            <div><dt>Approval rate</dt><dd>{data!.methodology.approvalRate}</dd></div>
            <div><dt>Tool reliability</dt><dd>{data!.methodology.toolReliability}</dd></div>
            <div><dt>Escalations</dt><dd>{data!.methodology.escalationRate}</dd></div>
            <div><dt>Incidents</dt><dd>{data!.methodology.incidentRate}</dd></div>
          </dl>
        </div>
      </details>
    </>}
  </div>;
}
