import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { ChevronDown, Download, FileText, Paperclip, Printer, Search, X } from 'lucide-react';
import type { OrganizationAccess } from '../../lib/identityApi';
import type { ApiVersion } from '../../lib/systemApi';
import { backfillResults, getArtifactUrl, getResult, loadArtifactSummaries, loadResults, markResultsSeen, recordResultExport, type ArtifactSummary, type ResultDetail, type ResultExportFormat, type ResultSummary, type ResultsWorkspace } from '../../lib/resultsApi';
import { buildAndDownloadResult } from '../../lib/resultExport';
import { loadTriggerWorkspace } from '../../lib/schedulerApi';
import { subscribeOrganizationRealtime } from '../../platform/realtimeClient';
import { notify } from '../feedback';
import { usePageTitle } from '../history';
import { linkProps, queryParam, setQuery } from '../routes';
import { Ago, Avatar, EmptyState, Notice, PageHeader, Pills, SkeletonLines, State } from '../ui';

const EMPTY: ResultsWorkspace = { items: [], workers: [], jobs: [], recentExports: [], summary: { total: 0, new: 0, completed: 0, attention: 0, failed: 0, cancelled: 0 }, filters: { departments: [], lastSeenAt: null }, window: { limit: 150, truncated: false } };
type View = 'list' | 'workers' | 'jobs';
const STATUS_LABEL: Record<string, string> = { completed: 'Completed', attention: 'Needs attention', failed: 'Failed', cancelled: 'Cancelled' };
const FORMATS: Array<{ value: ResultExportFormat; label: string; tables?: boolean }> = [
  { value: 'pdf', label: 'PDF' }, { value: 'docx', label: 'Word (.docx)' }, { value: 'md', label: 'Markdown (.md)' }, { value: 'txt', label: 'Plain text (.txt)' },
  { value: 'json', label: 'JSON' }, { value: 'csv', label: 'CSV', tables: true }, { value: 'xlsx', label: 'Excel (.xlsx)', tables: true },
];

function errorText(value: unknown) {
  const data = value as { response?: { data?: { error?: string } }; message?: string };
  return data.response?.data?.error || data.message || 'Results request failed.';
}
function scheduleLabel(schedule: { enabled: boolean; cadence: string; intervalMinutes: number; localTime: string; weekdays: string[] }) {
  if (!schedule.enabled) return 'Manual or event driven';
  if (schedule.cadence === 'interval') return `Every ${schedule.intervalMinutes} minutes`;
  if (schedule.cadence === 'daily') return `Daily at ${schedule.localTime}`;
  return `${schedule.weekdays.map((day) => day.slice(0, 3)).join(', ')} at ${schedule.localTime}`;
}
function dayGroup(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Earlier';
  const start = new Date(); start.setHours(0, 0, 0, 0);
  const days = Math.floor((start.getTime() - new Date(date).setHours(0, 0, 0, 0)) / 86400000);
  if (days <= 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7) return 'Earlier this week';
  if (days < 31) return 'Earlier this month';
  return 'Older';
}
const statusTone = (status: string) => status === 'completed' ? 'ok' : status === 'attention' ? 'warn' : status === 'failed' ? 'danger' : 'neutral';

export default function ResultsPage({ organization, apiVersion, resultId }: { organization: OrganizationAccess; apiVersion: ApiVersion; resultId?: string }) {
  const [data, setData] = useState<ResultsWorkspace>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [schedules, setSchedules] = useState<Record<string, string>>({});
  const [newSince, setNewSince] = useState<number | null>(null);
  const view = (['workers', 'jobs'].includes(queryParam('view')) ? queryParam('view') : 'list') as View;
  const filters = { search: queryParam('q'), workerId: queryParam('worker'), jobId: queryParam('job'), status: queryParam('status'), department: queryParam('department'), range: queryParam('range') || '30' };
  const [search, setSearch] = useState(filters.search);
  const filterKey = JSON.stringify(filters);

  async function refresh() {
    setError(null);
    try {
      const from = filters.range === 'all' ? undefined : new Date(Date.now() - Number(filters.range) * 86400000).toISOString();
      const next = await loadResults(apiVersion, organization.id, { search: filters.search || undefined, workerId: filters.workerId || undefined, jobId: filters.jobId || undefined, status: filters.status || undefined, department: filters.department || undefined, from });
      setData(next);
      setNewSince((current) => current ?? next.summary.new);
    } catch (caught) { setError(errorText(caught)); } finally { setLoading(false); }
  }

  useEffect(() => {
    let active = true;
    (async () => {
      try { await backfillResults(apiVersion, organization.id); } catch { /* best effort */ }
      if (!active) return;
      try {
        const [items, triggers] = await Promise.all([loadArtifactSummaries(apiVersion, organization.id), loadTriggerWorkspace(apiVersion, organization.id)]);
        if (active) { setArtifacts(items); setSchedules(Object.fromEntries(triggers.configs.map((config) => [config.jobId, scheduleLabel(config.schedule)]))); }
      } catch { /* optional context */ }
      try { await markResultsSeen(apiVersion, organization.id); } catch { /* best effort */ }
    })();
    return () => { active = false; };
  }, [apiVersion, organization.id]);

  useEffect(() => { setLoading(true); void refresh(); }, [apiVersion, organization.id, filterKey]);
  useEffect(() => subscribeOrganizationRealtime({ organizationId: organization.id, eventTypes: ['result.created', 'result.updated', 'run.completed'], onEvent: () => void refresh(), poll: () => refresh() }), [apiVersion, organization.id, filterKey]);
  useEffect(() => { setSearch(filters.search); }, [filters.search]);

  const groups = useMemoGroups(data.items);
  if (resultId) return <ResultReader organization={organization} apiVersion={apiVersion} resultId={resultId} artifacts={artifacts} onExported={() => void refresh()} />;

  const attention = data.items.filter((item) => item.status !== 'completed').length;
  const worker = data.workers.find((item) => item.id === filters.workerId);
  const job = data.jobs.find((item) => item.id === filters.jobId);
  const filtered = Boolean(filters.search || filters.workerId || filters.jobId || filters.status || filters.department || filters.range !== '30');

  function submit(event: FormEvent) { event.preventDefault(); setQuery({ q: search.trim() || undefined }); }
  function clear() { setSearch(''); setQuery({ q: undefined, worker: undefined, job: undefined, status: undefined, department: undefined, range: undefined }); }

  return <div className='ws-page'>
    <PageHeader title='Results' description={<>Finished work from your workers, ready to read, download and review.{newSince ? <> <strong className='ws-new-count'>{newSince} new</strong> since your last visit.</> : null}</>} />
    {error && <Notice tone='danger' action={<button type='button' className='ws-button ws-button-sm' onClick={() => void refresh()}>Retry</button>}>{error}</Notice>}

    <div className='ws-results-bar'>
      <Pills label='Results view' value={view} onChange={(next) => setQuery({ view: next === 'list' ? undefined : next })} options={[
        { value: 'list', label: 'All results', count: data.summary.total },
        { value: 'workers', label: 'By worker', count: data.workers.length },
        { value: 'jobs', label: 'By job', count: data.jobs.length },
      ]} />
      <form className='ws-search-field' role='search' onSubmit={submit}><Search size={15} aria-hidden='true' /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder='Search results' aria-label='Search results' /></form>
    </div>

    {view === 'list' && <div className='ws-filter-row'>
      <select aria-label='Status' value={filters.status} onChange={(event) => setQuery({ status: event.target.value || undefined })}><option value=''>Any status</option><option value='completed'>Completed</option><option value='attention'>Needs attention</option><option value='failed'>Failed</option><option value='cancelled'>Cancelled</option></select>
      <select aria-label='Worker' value={filters.workerId} onChange={(event) => setQuery({ worker: event.target.value || undefined })}><option value=''>Any worker</option>{data.workers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
      <select aria-label='Job' value={filters.jobId} onChange={(event) => setQuery({ job: event.target.value || undefined })}><option value=''>Any job</option>{data.jobs.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
      {data.filters.departments.length > 0 && <select aria-label='Department' value={filters.department} onChange={(event) => setQuery({ department: event.target.value || undefined })}><option value=''>Any department</option>{data.filters.departments.map((item) => <option key={item} value={item}>{item}</option>)}</select>}
      <select aria-label='Date range' value={filters.range} onChange={(event) => setQuery({ range: event.target.value === '30' ? undefined : event.target.value })}><option value='7'>Last 7 days</option><option value='30'>Last 30 days</option><option value='90'>Last 90 days</option><option value='all'>All loaded history</option></select>
      {filtered && <button type='button' className='ws-button ws-button-quiet ws-button-sm' onClick={clear}><X size={14} />Clear</button>}
      {attention > 0 && !filters.status && <button type='button' className='ws-link ws-filter-hint' onClick={() => setQuery({ status: 'attention' })}>{attention} need{attention === 1 ? 's' : ''} a closer look</button>}
    </div>}

    {loading ? <SkeletonLines rows={6} avatar /> : view === 'list' ? (data.items.length === 0
      ? <EmptyState icon={<FileText size={18} />} title={filtered ? 'Nothing matches these filters' : 'No results yet'} action={filtered ? <button type='button' className='ws-button' onClick={clear}>Clear filters</button> : undefined}>{filtered ? `Try a wider date range${worker ? ` or another worker than ${worker.name}` : ''}${job ? ` or job` : ''}.` : 'When a worker finishes a job, the deliverable appears here automatically.'}</EmptyState>
      : <div className='ws-day-groups'>{groups.map(([label, items]) => <section key={label} className='ws-section' aria-label={label}>
        <div className='ws-section-head'><h2>{label}</h2><span className='ws-count'>{items.length}</span></div>
        <ul className='ws-rows'>{items.map((item) => <ResultRow key={item.id} item={item} />)}</ul>
      </section>)}</div>)
    : view === 'workers' ? <ul className='ws-rows'>{data.workers.map((item) => <li key={item.id} className='ws-row'>
        <Avatar name={item.name} seed={item.id} size={32} />
        <a className='ws-row-main ws-row-link' {...linkProps({ page: 'results' })} onClick={(event) => { event.preventDefault(); setQuery({ view: undefined, worker: item.id }); }}><strong>{item.name}</strong><small>{item.department || 'No department'}</small></a>
        <div className='ws-row-meta'><span>{item.resultCount} result{item.resultCount === 1 ? '' : 's'}</span><span className='ws-hide-sm'>{item.weekCount} this week</span><span className='ws-hide-sm'><Ago value={item.lastResultAt} /></span></div>
      </li>)}{!data.workers.length && <li className='ws-quiet'>No worker has delivered results in this range.</li>}</ul>
    : <ul className='ws-rows'>{data.jobs.map((item) => <li key={item.id} className='ws-row'>
        <span className='ws-doc-icon' aria-hidden='true'><FileText size={16} /></span>
        <a className='ws-row-main ws-row-link' {...linkProps({ page: 'results' })} onClick={(event) => { event.preventDefault(); setQuery({ view: undefined, job: item.id }); }}><strong>{item.name}</strong><small>{item.workerName} · {schedules[item.id] ?? 'Manual or event driven'}</small></a>
        <div className='ws-row-meta'><span>{item.resultCount} result{item.resultCount === 1 ? '' : 's'}</span><span className='ws-hide-sm'><Ago value={item.lastResultAt} /></span>{item.latestResultId && <a className='ws-button ws-button-sm' {...linkProps({ page: 'results', resultId: item.latestResultId })}>Latest</a>}</div>
      </li>)}{!data.jobs.length && <li className='ws-quiet'>No job has delivered results in this range.</li>}</ul>}

    {data.window.truncated && <p className='ws-muted'>Showing the most recent {data.window.limit} results. Narrow the filters to reach older work.</p>}
  </div>;
}

function useMemoGroups(items: ResultSummary[]) {
  return useMemo(() => {
    const groups = new Map<string, ResultSummary[]>();
    for (const item of [...items].sort((a, b) => Date.parse(b.completedAt) - Date.parse(a.completedAt))) {
      const key = dayGroup(item.completedAt);
      groups.set(key, [...(groups.get(key) ?? []), item]);
    }
    return [...groups.entries()];
  }, [items]);
}

function ResultRow({ item }: { item: ResultSummary }) {
  return <li className='ws-row ws-result-row'>
    <span className='ws-doc-icon' aria-hidden='true'><FileText size={16} /></span>
    <a className='ws-row-main ws-row-link' {...linkProps({ page: 'results', resultId: item.id })}>
      <strong>{item.title}{item.isNew && <span className='ws-tag' data-tone='info'>New</span>}</strong>
      <small>{item.summary || `${item.workerName} · ${item.jobName}`}</small>
    </a>
    <div className='ws-row-meta'>
      <span className='ws-hide-sm ws-result-who'>{item.workerName}</span>
      {item.status !== 'completed' && <State value={item.status} label={STATUS_LABEL[item.status]} tone={statusTone(item.status) as 'warn'} />}
      <Ago value={item.completedAt} />
    </div>
  </li>;
}

function ResultReader({ organization, apiVersion, resultId, artifacts, onExported }: { organization: OrganizationAccess; apiVersion: ApiVersion; resultId: string; artifacts: ArtifactSummary[]; onExported: () => void }) {
  const [result, setResult] = useState<ResultDetail | null>(null);
  usePageTitle(result?.title);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<ResultExportFormat | null>(null);
  const [menu, setMenu] = useState(false);
  const canExport = organization.permissions.includes('results.export');

  useEffect(() => {
    let active = true;
    setResult(null); setError(null);
    getResult(apiVersion, organization.id, resultId).then((value) => { if (active) setResult(value); }).catch((caught) => { if (active) setError(errorText(caught)); });
    return () => { active = false; };
  }, [apiVersion, organization.id, resultId]);
  useEffect(() => {
    if (!menu) return;
    const close = (event: PointerEvent) => { if (!(event.target as Element).closest('[data-ws-menu]')) setMenu(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenu(false); };
    document.addEventListener('pointerdown', close); document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape); };
  }, [menu]);

  async function exportAs(format: ResultExportFormat) {
    if (!result || !canExport) return;
    setMenu(false); setExporting(format);
    try {
      if (format === 'print') { await recordResultExport(apiVersion, organization.id, result.id, 'print'); window.print(); }
      else { await buildAndDownloadResult(result, organization.name, format); await recordResultExport(apiVersion, organization.id, result.id, format); notify({ key: 'result-export', tone: 'ok', title: `Downloaded ${format.toUpperCase()}` }); }
      onExported();
    } catch (caught) { notify({ key: 'result-export', tone: 'danger', title: 'Download failed', body: errorText(caught) }); } finally { setExporting(null); }
  }
  async function openArtifact(id: string) {
    try { const link = await getArtifactUrl(apiVersion, organization.id, id); window.open(link.url, '_blank', 'noopener,noreferrer'); }
    catch (caught) { notify({ key: 'result-artifact', tone: 'danger', title: 'Could not open the attachment', body: errorText(caught) }); }
  }

  if (error) return <div className='ws-page ws-reader'><EmptyState icon={<FileText size={18} />} title='This result could not be opened'>{error}</EmptyState></div>;
  if (!result) return <div className='ws-page ws-reader' aria-busy='true'><div className='ws-skeleton'><i style={{ width: '60%', height: 28 }} /></div><SkeletonLines rows={8} /></div>;

  const hasTables = result.blocks.some((block) => block.type === 'table');
  const seconds = result.durationMs == null ? null : Math.round(result.durationMs / 1000);
  return <div className='ws-page ws-reader'>
    <div className='ws-reader-bar'>

      {canExport && <div className='ws-page-actions'>
        <button type='button' className='ws-button ws-button-sm' onClick={() => void exportAs('print')} disabled={Boolean(exporting)}><Printer size={14} />Print</button>
        <div className='ws-menu-anchor' data-ws-menu>
          <button type='button' className='ws-button ws-button-sm' aria-haspopup='menu' aria-expanded={menu} disabled={Boolean(exporting)} onClick={() => setMenu(!menu)}><Download size={14} />{exporting ? `Preparing ${exporting.toUpperCase()}…` : 'Download'}<ChevronDown size={13} /></button>
          {menu && <div className='ws-menu ws-menu-right' role='menu'>{FORMATS.filter((format) => !format.tables || hasTables).map((format) => <button key={format.value} type='button' role='menuitem' onClick={() => void exportAs(format.value)}>{format.label}</button>)}</div>}
        </div>
      </div>}
    </div>
    <article className='ws-doc'>
      <header className='ws-doc-head'>
        <div className='ws-doc-meta'><State value={result.status} label={STATUS_LABEL[result.status]} tone={statusTone(result.status) as 'ok'} /><span>Version {result.version}</span></div>
        <h1>{result.title}</h1>
        {result.summary && <p className='ws-doc-summary'>{result.summary}</p>}
        <dl className='ws-doc-facts'>
          <div><dt>Worker</dt><dd><a className='ws-link' {...linkProps({ page: 'worker', workerId: result.workerId, tab: 'results' })}>{result.workerName}</a></dd></div>
          <div><dt>Job</dt><dd>{result.jobName}</dd></div>
          <div><dt>Completed</dt><dd>{new Date(result.completedAt).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}</dd></div>
          {seconds !== null && <div><dt>Took</dt><dd>{seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`}</dd></div>}
        </dl>
      </header>
      <div className='ws-doc-body'>{result.blocks.map((block, index) => {
        if (block.type === 'heading') return block.level === 2 ? <h2 key={index}>{block.text}</h2> : <h3 key={index}>{block.text}</h3>;
        if (block.type === 'paragraph') return <p key={index}>{block.text}</p>;
        if (block.type === 'list') return <ul key={index}>{block.items.map((item, i) => <li key={i}>{item}</li>)}</ul>;
        if (block.type === 'table') return <div className='ws-doc-table' key={index}><table><thead><tr>{block.headers.map((header) => <th key={header}>{header}</th>)}</tr></thead><tbody>{block.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>)}</tbody></table></div>;
        if (block.type === 'metric') return <div className='ws-doc-metric' key={index}><span>{block.label}</span><strong>{block.value}</strong></div>;
        if (block.type === 'quote') return <blockquote key={index}>{block.text}</blockquote>;
        if (block.type === 'code') return <pre key={index}><code>{block.text}</code></pre>;
        if (block.type === 'source') return <p key={index} className='ws-doc-source'><a href={block.url} target='_blank' rel='noreferrer'>{block.label}</a></p>;
        const artifact = artifacts.find((item) => item.id === block.artifactId);
        return <button type='button' className='ws-doc-attachment' key={index} onClick={() => void openArtifact(block.artifactId)}><Paperclip size={15} /><span><strong>{artifact?.name || block.label}</strong><small>{artifact ? `${formatBytes(artifact.bytes)} · ${artifact.contentType}` : 'Open attachment'}</small></span></button>;
      })}</div>
      {result.sourceReferences.length > 0 && <section className='ws-doc-sources'><h2>Sources</h2><ol>{result.sourceReferences.map((source) => <li key={source.url}><a href={source.url} target='_blank' rel='noreferrer'>{source.label}</a></li>)}</ol></section>}
      <details className='ws-technical ws-doc-technical'>
        <summary>How this was produced</summary>
        <dl className='ws-facts'>
          <div><dt>Run</dt><dd><code>{result.runId}</code></dd></div>
          <div><dt>Work item</dt><dd><code>{result.workItemId}</code></dd></div>
          <div><dt>Agent identity</dt><dd><code>{result.agentId}</code></dd></div>
          <div><dt>Audit correlation</dt><dd><code>{result.correlationId}</code></dd></div>
          <div><dt>Capabilities used</dt><dd>{result.capabilitiesUsed.join(', ') || 'None'}</dd></div>
          <div><dt>Actions</dt><dd>{result.actionIds.length ? result.actionIds.map((id) => <code key={id}>{id}</code>) : 'None'}</dd></div>
          <div><dt>Approvals</dt><dd>{result.approvalIds.length ? result.approvalIds.map((id) => <code key={id}>{id}</code>) : 'None'}</dd></div>
        </dl>
      </details>
      <footer className='ws-doc-foot'>Produced by Audoryn for {organization.name} · Result <code>{result.id}</code></footer>
    </article>
  </div>;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1048576) return `${Math.round(value / 1024)} KB`;
  return `${(value / 1048576).toFixed(1)} MB`;
}
