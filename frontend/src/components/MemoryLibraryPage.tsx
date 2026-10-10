import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Archive, ArrowUpRight, BookOpen, BrainCircuit, FileText, Plus, Search, ShieldCheck, Sparkles, Upload } from 'lucide-react';
import type { Artifact, MemoryPolicy, MemoryRecord } from '../lib/memoryApi';
import type { ManagedWorker } from '../lib/workforceApi';
import type { ApiVersion } from '../lib/systemApi';
import { filterMemories } from './workspaceListModel';
import { linkProps, navigate, queryParam, setQuery } from '../workspace/routes';
import { Ago, EmptyState, PageHeader, SkeletonLines, State } from '../workspace/ui';

export type MemoryScene = 'worker' | 'organization' | 'run' | 'artifacts' | 'policy';

const SCENES: Array<{ id: MemoryScene; label: string; description: string }> = [
  { id: 'worker', label: 'Worker memory', description: 'Knowledge assigned to one worker, written by people.' },
  { id: 'organization', label: 'Organization', description: 'Shared knowledge every worker can draw on.' },
  { id: 'run', label: 'Run memory', description: 'Short-lived context captured automatically from finished work.' },
  { id: 'artifacts', label: 'Artifacts', description: 'Files and outputs kept from managed runs.' },
  { id: 'policy', label: 'Retention', description: 'How long context stays eligible, and who may write it.' },
];
const SOURCE: Record<string, string> = { human: 'Written by a person', promoted: 'Promoted from run memory', runtime: 'Captured by the runtime' };

function day(value: string | null) {
  if (!value) return 'No expiry';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export default function MemoryLibraryPage({ memories, artifacts, workers, policy, loading, saving, canManage, onSceneChange, onPolicyChange, onCreate, onArchive, onPromote, onOpenArtifact, onSavePolicy }: {
  memories: MemoryRecord[];
  artifacts: Artifact[];
  workers: ManagedWorker[];
  policy: MemoryPolicy;
  scene: MemoryScene;
  loading: boolean;
  saving: boolean;
  canManage: boolean;
  apiVersion: ApiVersion;
  onSceneChange: (scene: MemoryScene) => void;
  onApiVersionChange?: (version: ApiVersion) => void;
  onPolicyChange: (policy: MemoryPolicy) => void;
  onCreate: () => void;
  onArchive: (memory: MemoryRecord) => void;
  onPromote: (memory: MemoryRecord) => void;
  onOpenArtifact: (artifact: Artifact) => void;
  onSavePolicy: (event: FormEvent) => void;
}) {
  const scene = (SCENES.some((item) => item.id === queryParam('view')) ? queryParam('view') : 'worker') as MemoryScene;
  const itemId = queryParam('item');
  const [search, setSearch] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const workerMap = useMemo(() => new Map(workers.map((worker) => [worker.id, worker.name])), [workers]);
  const counts: Record<MemoryScene, number | null> = {
    worker: memories.filter((item) => item.scope === 'worker' && item.status === 'active').length,
    organization: memories.filter((item) => item.scope === 'organization' && item.status === 'active').length,
    run: memories.filter((item) => item.scope === 'run' && item.status === 'active').length,
    artifacts: artifacts.length,
    policy: null,
  };
  const isMemory = scene === 'worker' || scene === 'organization' || scene === 'run';
  const visible = isMemory ? filterMemories(memories, workerMap, scene, search, showArchived) : [];
  const visibleArtifacts = artifacts.filter((item) => `${item.name} ${item.contentType} ${workerMap.get(item.workerId) ?? ''}`.toLowerCase().includes(search.trim().toLowerCase())).sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
  const selected = visible.find((item) => item.id === itemId) ?? null;
  const selectedArtifact = visibleArtifacts.find((item) => item.id === itemId) ?? null;
  const base = linkProps({ page: 'memory' }).href;
  const sceneHref = (id: MemoryScene) => base + (id === 'worker' ? '' : `?view=${id}`);

  useEffect(() => { onSceneChange(scene); setSearch(''); }, [scene]);
  // Open the first entry on wide screens so the reading pane is never blank.
  const first = isMemory ? visible[0]?.id : scene === 'artifacts' ? visibleArtifacts[0]?.id : undefined;
  useEffect(() => {
    if (loading || itemId || !first || window.matchMedia('(max-width: 860px)').matches) return;
    setQuery({ item: first });
  }, [loading, itemId, first, scene]);

  const choose = (id: string) => setQuery({ item: id }, { push: true });
  const description = SCENES.find((item) => item.id === scene)!.description;

  return <div className='ws-page ws-page-wide'>
    <PageHeader title='Memory' description='Context workers can use, always with its source, scope and lifetime. Memory informs work; it never grants permission.'
      actions={canManage && <button type='button' className='ws-button ws-button-primary' onClick={onCreate}><Plus size={15} />Add context</button>} />
    <nav className='ws-tabs' aria-label='Memory areas'>{SCENES.map((item) => <a key={item.id} href={sceneHref(item.id)} aria-current={scene === item.id ? 'page' : undefined} onClick={(event) => { if (event.metaKey || event.ctrlKey) return; event.preventDefault(); navigate(sceneHref(item.id), { keepScroll: true }); }}>{item.label}{counts[item.id] !== null && <span className='ws-tab-count'>{counts[item.id]}</span>}</a>)}</nav>

    {scene === 'policy' ? <form className='ws-retention' onSubmit={onSavePolicy}>
      <p className='ws-muted'>{description} When context expires it is no longer offered to workers; the record stays auditable.</p>
      <ul className='ws-rows'>
        <Retention label='Run memory' description='Captured automatically after successful runs' value={policy.runRetentionDays} min={1} max={90} disabled={!canManage} onChange={(value) => onPolicyChange({ ...policy, runRetentionDays: value })} />
        <Retention label='Worker memory' description='Long-term context for one worker' value={policy.workerRetentionDays} min={30} max={730} disabled={!canManage} onChange={(value) => onPolicyChange({ ...policy, workerRetentionDays: value })} />
        <Retention label='Organization knowledge' description='Shared context for the workspace' value={policy.organizationRetentionDays} min={30} max={3650} disabled={!canManage} onChange={(value) => onPolicyChange({ ...policy, organizationRetentionDays: value })} />
      </ul>
      <dl className='ws-facts'>
        <div><dt>Runtime writes</dt><dd>Run memory only, captured automatically</dd></div>
        <div><dt>Long-term writes</dt><dd>People only</dd></div>
        {policy.updatedAt && <div><dt>Last changed</dt><dd><Ago value={policy.updatedAt} /></dd></div>}
      </dl>
      {canManage && <div className='ws-form-actions'><button type='submit' className='ws-button ws-button-primary' disabled={saving}>{saving ? 'Saving…' : 'Save retention'}</button></div>}
    </form> : <div className='ws-inbox' data-detail={(selected || selectedArtifact) ? 'true' : undefined}>
      <div className='ws-inbox-list'>
        <p className='ws-muted ws-small'>{description}</p>
        <div className='ws-list-tools'>
          <label className='ws-search-field ws-search-full'><Search size={15} aria-hidden='true' /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={scene === 'artifacts' ? 'Search artifacts' : 'Search titles, content and tags'} aria-label='Search memory' /></label>
          {isMemory && <label className='ws-check'><input type='checkbox' checked={showArchived} onChange={(event) => setShowArchived(event.target.checked)} />Archived</label>}
        </div>
        {loading ? <SkeletonLines rows={5} /> : scene === 'artifacts'
          ? visibleArtifacts.length ? <ul className='ws-rows'>{visibleArtifacts.map((artifact) => <li key={artifact.id} className='ws-row ws-inbox-row' aria-current={artifact.id === itemId ? 'true' : undefined}>
              <span className='ws-doc-icon' aria-hidden='true'><FileText size={15} /></span>
              <button type='button' className='ws-row-main ws-row-link ws-row-button' onClick={() => choose(artifact.id)}><strong>{artifact.name}</strong><small>{workerMap.get(artifact.workerId) ?? 'Unknown worker'} · {artifact.contentType}</small></button>
              <div className='ws-inbox-row-meta'><Ago value={artifact.createdAt} /></div>
            </li>)}</ul> : <EmptyState icon={<FileText size={18} />} title='No artifacts'>Run outputs appear here after managed work completes.</EmptyState>
          : visible.length ? <ul className='ws-rows'>{visible.map((item) => <li key={item.id} className='ws-row ws-inbox-row' aria-current={item.id === itemId ? 'true' : undefined}>
              <span className='ws-doc-icon' aria-hidden='true'>{item.scope === 'run' ? <Upload size={15} /> : item.scope === 'organization' ? <BookOpen size={15} /> : <BrainCircuit size={15} />}</span>
              <button type='button' className='ws-row-main ws-row-link ws-row-button' onClick={() => choose(item.id)}><strong>{item.title}</strong><small>{item.workerId ? workerMap.get(item.workerId) ?? 'Unknown worker' : SOURCE[item.source]}</small></button>
              <div className='ws-inbox-row-meta'>{item.status === 'archived' && <State value='archived' tone='neutral' />}<Ago value={item.updatedAt} /></div>
            </li>)}</ul> : <EmptyState icon={<BrainCircuit size={18} />} title={search ? 'No matching context' : 'Nothing here yet'} action={!search && canManage && scene !== 'run' ? <button type='button' className='ws-button' onClick={onCreate}><Plus size={14} />Add context</button> : undefined}>{search ? 'Try another term, or include archived entries.' : scene === 'run' ? 'Run memory appears after successful managed work.' : 'Add context so workers start with what your team already knows.'}</EmptyState>}
      </div>
      <div className='ws-inbox-detail'>
        {selected ? <div className='ws-detail' key={selected.id}>
          <button type='button' className='ws-back ws-inbox-back' onClick={() => setQuery({ item: undefined })}>← Memory</button>
          <div><div className='ws-muted ws-small'>{SOURCE[selected.source]}{selected.workerId ? <> · <a className='ws-link' {...linkProps({ page: 'worker', workerId: selected.workerId, tab: 'memory' })}>{workerMap.get(selected.workerId) ?? 'Worker'}</a></> : null}</div><h2 className='ws-detail-title'>{selected.title}</h2></div>
          <div className='ws-memory-content'>{selected.content}</div>
          {selected.tags.length > 0 && <div className='ws-chip-list'>{selected.tags.map((tag) => <span key={tag} className='ws-tag'>{tag}</span>)}</div>}
          <dl className='ws-facts'>
            <div><dt>Status</dt><dd><State value={selected.status} tone={selected.status === 'active' ? 'ok' : 'neutral'} /></dd></div>
            <div><dt>Updated</dt><dd>{day(selected.updatedAt)}</dd></div>
            <div><dt>Offered to workers until</dt><dd>{day(selected.expiresAt)}</dd></div>
            {selected.runId && <div><dt>From run</dt><dd><a className='ws-link' {...linkProps({ page: 'runs', runId: selected.runId })}>Open run</a></dd></div>}
          </dl>
          {canManage && selected.status === 'active' && <div className='ws-form-actions ws-form-actions-start'>
            {selected.scope === 'run' && <button type='button' className='ws-button ws-button-primary' disabled={saving} onClick={() => onPromote(selected)}><Sparkles size={14} />Keep as worker memory</button>}
            <button type='button' className='ws-button' disabled={saving} onClick={() => onArchive(selected)}><Archive size={14} />Archive</button>
          </div>}
        </div> : selectedArtifact ? <div className='ws-detail' key={selectedArtifact.id}>
          <button type='button' className='ws-back ws-inbox-back' onClick={() => setQuery({ item: undefined })}>← Artifacts</button>
          <div><div className='ws-muted ws-small'>Run output</div><h2 className='ws-detail-title'>{selectedArtifact.name}</h2></div>
          <dl className='ws-facts'>
            <div><dt>Worker</dt><dd>{workerMap.get(selectedArtifact.workerId) ?? selectedArtifact.workerId}</dd></div>
            <div><dt>Format</dt><dd>{selectedArtifact.contentType}</dd></div>
            <div><dt>Size</dt><dd>{Math.max(1, Math.round(selectedArtifact.bytes / 1024))} KB</dd></div>
            <div><dt>Created</dt><dd>{day(selectedArtifact.createdAt)}</dd></div>
            <div><dt>Run</dt><dd><a className='ws-link' {...linkProps({ page: 'runs', runId: selectedArtifact.runId })}>Open run</a></dd></div>
          </dl>
          <div className='ws-form-actions ws-form-actions-start'><button type='button' className='ws-button ws-button-primary' onClick={() => onOpenArtifact(selectedArtifact)}>Open artifact<ArrowUpRight size={14} /></button></div>
        </div> : <div className='ws-inbox-placeholder'><ShieldCheck size={20} /><span>{loading ? 'Loading memory…' : 'Select an entry to read it.'}</span></div>}
      </div>
    </div>}
  </div>;
}

function Retention({ label, description, value, min, max, disabled, onChange }: { label: string; description: string; value: number; min: number; max: number; disabled: boolean; onChange: (value: number) => void }) {
  return <li className='ws-row ws-retention-row'>
    <span className='ws-row-main'><strong>{label}</strong><small>{description}</small></span>
    <label className='ws-days'><input type='number' min={min} max={max} required disabled={disabled} value={value} onChange={(event) => onChange(Number(event.target.value))} aria-label={`${label} retention in days`} /><span>days</span></label>
  </li>;
}
