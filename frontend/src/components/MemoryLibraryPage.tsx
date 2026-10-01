import { useMemo, useState, type FormEvent } from 'react';
import { Archive, ArrowUpRight, BookOpen, BrainCircuit, Clock3, FileText, Filter, Plus, Search, ShieldCheck, Sparkles, Upload } from 'lucide-react';
import type { Artifact, MemoryPolicy, MemoryRecord } from '../lib/memoryApi';
import type { ManagedWorker } from '../lib/workforceApi';
import type { ApiVersion } from '../lib/systemApi';
import { filterMemories } from './workspaceListModel';

export type MemoryScene = 'worker' | 'organization' | 'run' | 'artifacts' | 'policy';

const scopes: Array<{ id: MemoryScene; name: string; label: string; icon: typeof BrainCircuit }> = [
  { id: 'worker', name: 'Worker memory', label: 'Context assigned to one worker', icon: BrainCircuit },
  { id: 'organization', name: 'Organization', label: 'Shared knowledge', icon: BookOpen },
  { id: 'run', name: 'Run memory', label: 'Automatic execution context', icon: Clock3 },
  { id: 'artifacts', name: 'Artifacts', label: 'Evidence from work', icon: FileText },
  { id: 'policy', name: 'Retention', label: 'Context lifetime and writes', icon: ShieldCheck },
];

function date(value: string | null) {
  if (!value) return 'No expiry';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export default function MemoryLibraryPage({ memories, artifacts, workers, policy, scene, loading, saving, canManage, apiVersion, onSceneChange, onApiVersionChange, onPolicyChange, onCreate, onArchive, onPromote, onOpenArtifact, onSavePolicy }: {
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
  onApiVersionChange: (version: ApiVersion) => void;
  onPolicyChange: (policy: MemoryPolicy) => void;
  onCreate: () => void;
  onArchive: (memory: MemoryRecord) => void;
  onPromote: (memory: MemoryRecord) => void;
  onOpenArtifact: (artifact: Artifact) => void;
  onSavePolicy: (event: FormEvent) => void;
}) {
  const [query, setQuery] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const workerMap = useMemo(() => new Map(workers.map((worker) => [worker.id, worker.name])), [workers]);
  const counts = {
    worker: memories.filter((item) => item.scope === 'worker' && item.status === 'active').length,
    organization: memories.filter((item) => item.scope === 'organization' && item.status === 'active').length,
    run: memories.filter((item) => item.scope === 'run' && item.status === 'active').length,
    artifacts: artifacts.length,
  };
  const visible = scene === 'worker' || scene === 'organization' || scene === 'run' ? filterMemories(memories, workerMap, scene, query, showArchived) : [];
  const selected = visible.find((item) => item.id === selectedId) ?? visible[0] ?? null;
  const visibleArtifacts = artifacts.filter((item) => `${item.name} ${item.contentType} ${workerMap.get(item.workerId) ?? ''}`.toLowerCase().includes(query.trim().toLowerCase())).sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
  const selectedArtifact = visibleArtifacts.find((item) => item.id === selectedArtifactId) ?? visibleArtifacts[0] ?? null;

  return <div className='memory-library-page'>
    <header className='memory-library-header'><div><p className='eyebrow'>WORKFORCE / MEMORY</p><h1>Memory</h1><p>Context workers can use, with its source, scope, and lifetime always visible.</p></div>{canManage && <button type='button' className='memory-add-button' onClick={onCreate}><Plus size={16} /> Add context</button>}</header>
    <div className='memory-context-map' aria-label='Memory scope summary'><div><span>CONTEXT LANDSCAPE</span><strong>{counts.worker + counts.organization + counts.run}</strong><small>Active memories</small></div><i aria-hidden='true' />{([['worker', 'Worker', counts.worker], ['organization', 'Organization', counts.organization], ['run', 'Run', counts.run], ['artifacts', 'Artifacts', counts.artifacts]] as const).map(([id, label, count]) => <button key={id} type='button' className={scene === id ? 'is-current' : ''} onClick={() => onSceneChange(id)}><span>{label}</span><strong>{count}</strong></button>)}</div>
    <div className='memory-library-layout'>
      <nav className='memory-scope-nav' aria-label='Memory areas'><span className='memory-nav-title'>BROWSE BY SCOPE</span>{scopes.map((scope) => { const Icon = scope.icon; return <button key={scope.id} type='button' className={scene === scope.id ? 'is-current' : ''} aria-current={scene === scope.id ? 'page' : undefined} onClick={() => { onSceneChange(scope.id); setQuery(''); setSelectedId(null); setSelectedArtifactId(null); }}><Icon size={16} /><span><strong>{scope.name}</strong><small>{scope.label}</small></span></button>; })}<div className='memory-trust-note'><ShieldCheck size={17} /><p>Memory supplies context. It never grants permission or replaces approval.</p></div></nav>
      <main className='memory-library-main' key={scene}>
        {scene === 'policy' ? <section className='memory-retention-view'><div className='memory-section-heading'><span>05 / GOVERNANCE</span><h2>Retention & write policy</h2><p>Choose how long context remains eligible for future work.</p></div><div className='memory-policy-principle'><ShieldCheck size={19} /><p>Expiry removes memory from context selection. Historical records remain auditable until a separate deletion policy removes them.</p></div><form onSubmit={onSavePolicy}><div className='memory-retention-rows'><RetentionRow label='Run memory' description='Automatically captured after successful runs' value={policy.runRetentionDays} min={1} max={90} onChange={(value) => onPolicyChange({ ...policy, runRetentionDays: value })} /><RetentionRow label='Worker memory' description='Long-term context scoped to a worker' value={policy.workerRetentionDays} min={30} max={730} onChange={(value) => onPolicyChange({ ...policy, workerRetentionDays: value })} /><RetentionRow label='Organization knowledge' description='Shared context for the workspace' value={policy.organizationRetentionDays} min={30} max={3650} onChange={(value) => onPolicyChange({ ...policy, organizationRetentionDays: value })} /></div><div className='memory-write-rules'><div><span>RUNTIME WRITES</span><strong>Automatic run context only</strong></div><div><span>LONG-TERM WRITES</span><strong>Human controlled</strong></div></div><div className='memory-policy-footer'><div className='api-switcher'><span>API</span><div className='segmented'><button type='button' className={apiVersion === 'v1' ? 'active' : ''} onClick={() => onApiVersionChange('v1')}>v1</button><button type='button' className={apiVersion === 'v2' ? 'active' : ''} onClick={() => onApiVersionChange('v2')}>v2</button></div></div>{canManage && <button type='submit' disabled={saving}>Save policy</button>}</div></form></section> : <>
          <div className='memory-section-heading'><span>{scene === 'worker' ? '01 / PERSONAL CONTEXT' : scene === 'organization' ? '02 / SHARED KNOWLEDGE' : scene === 'run' ? '03 / EXECUTION CONTEXT' : '04 / EVIDENCE'}</span><h2>{scopes.find((item) => item.id === scene)?.name}</h2><p>{scene === 'worker' ? 'Knowledge assigned to a specific worker.' : scene === 'organization' ? 'Shared knowledge curated for the organization.' : scene === 'run' ? 'Short-lived context captured from completed work.' : 'Files and outputs retained from managed runs.'}</p></div>
          <div className='memory-browse-toolbar'><label><Search size={16} /><span className='sr-only'>Search memory</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={scene === 'artifacts' ? 'Search artifacts' : 'Search titles, content, tags'} /></label>{scene !== 'artifacts' && <button type='button' className={showArchived ? 'is-active' : ''} onClick={() => setShowArchived((value) => !value)} aria-pressed={showArchived}><Filter size={15} /> {showArchived ? 'Including archived' : 'Active only'}</button>}</div>
          <div className='memory-browse-grid'><div className='memory-index' aria-label={scene === 'artifacts' ? 'Artifacts' : 'Memory entries'}>{loading ? <div className='memory-index-empty'>Loading context…</div> : scene === 'artifacts' ? visibleArtifacts.length ? visibleArtifacts.map((artifact, index) => <button type='button' key={artifact.id} className={`memory-index-row${selectedArtifact?.id === artifact.id ? ' is-selected' : ''}`} style={{ animationDelay: (Math.min(index, 8) * 25) + 'ms' }} onClick={() => setSelectedArtifactId(artifact.id)}><span className='memory-index-icon'><FileText size={16} /></span><span><strong>{artifact.name}</strong><small>{workerMap.get(artifact.workerId) ?? 'Unknown worker'} · {date(artifact.createdAt)}</small></span><ArrowUpRight size={15} /></button>) : <Empty title='No artifacts found' text='Run outputs appear here after managed work completes.' /> : visible.length ? visible.map((item, index) => <button type='button' key={item.id} className={`memory-index-row${selected?.id === item.id ? ' is-selected' : ''}`} style={{ animationDelay: (Math.min(index, 8) * 25) + 'ms' }} onClick={() => setSelectedId(item.id)}><span className='memory-index-icon'>{item.scope === 'run' ? <Upload size={16} /> : item.scope === 'organization' ? <BookOpen size={16} /> : <BrainCircuit size={16} />}</span><span><strong>{item.title}</strong><small>{item.workerId ? workerMap.get(item.workerId) ?? 'Unknown worker' : item.source === 'human' ? 'Human-authored' : item.source} · {date(item.updatedAt)}</small></span><span className={`memory-index-status ${item.status}`} /></button>) : <Empty title={query ? 'No matching context' : 'This scope is empty'} text={query ? 'Try another search term or include archived entries.' : scene === 'run' ? 'Run memory appears after successful managed work.' : 'Add context to make this scope useful to workers.'} />}</div>
            <article className='memory-reading-pane' key={scene === 'artifacts' ? selectedArtifact?.id ?? 'empty' : selected?.id ?? 'empty'}>{scene === 'artifacts' ? selectedArtifact ? <><span className='memory-reading-overline'>ARTIFACT / RUN OUTPUT</span><div className='memory-reading-title'><FileText size={23} /><h3>{selectedArtifact.name}</h3></div><p className='memory-reading-intro'>This output belongs to a managed run. Open it to inspect the stored artifact.</p><dl><div><dt>Worker</dt><dd>{workerMap.get(selectedArtifact.workerId) ?? selectedArtifact.workerId}</dd></div><div><dt>Run</dt><dd>{selectedArtifact.runId.slice(0, 12)}</dd></div><div><dt>Created</dt><dd>{date(selectedArtifact.createdAt)}</dd></div><div><dt>Format</dt><dd>{selectedArtifact.contentType}</dd></div><div><dt>Size</dt><dd>{Math.max(1, Math.round(selectedArtifact.bytes / 1024))} KB</dd></div></dl><button type='button' className='memory-open-artifact' onClick={() => onOpenArtifact(selectedArtifact)}>Open artifact <ArrowUpRight size={16} /></button></> : <ReadingEmpty /> : selected ? <><span className='memory-reading-overline'>{selected.scope.toUpperCase()} / {selected.source.toUpperCase()}</span><h3>{selected.title}</h3><div className='memory-reading-content'>{selected.content}</div>{selected.tags.length > 0 && <div className='memory-reading-tags'>{selected.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}<dl><div><dt>Status</dt><dd>{selected.status}</dd></div><div><dt>Source</dt><dd>{selected.source === 'human' ? 'Human authored' : selected.source === 'promoted' ? 'Promoted from run memory' : 'Runtime generated'}</dd></div>{selected.workerId && <div><dt>Worker</dt><dd>{workerMap.get(selected.workerId) ?? selected.workerId}</dd></div>}<div><dt>Updated</dt><dd>{date(selected.updatedAt)}</dd></div><div><dt>Eligible until</dt><dd>{date(selected.expiresAt)}</dd></div></dl><div className='memory-reading-actions'>{canManage && selected.scope === 'run' && selected.status === 'active' && <button type='button' disabled={saving} onClick={() => onPromote(selected)}><Sparkles size={15} /> Promote to worker memory</button>}{canManage && selected.status === 'active' && <button type='button' disabled={saving} onClick={() => onArchive(selected)}><Archive size={15} /> Archive</button>}</div></> : <ReadingEmpty />}</article>
          </div>
        </>}
      </main>
    </div>
  </div>;
}

function RetentionRow({ label, description, value, min, max, onChange }: { label: string; description: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  return <label className='memory-retention-row'><span><strong>{label}</strong><small>{description}</small></span><span><input type='number' min={min} max={max} required value={value} onChange={(event) => onChange(Number(event.target.value))} /><small>days</small></span></label>;
}

function Empty({ title, text }: { title: string; text: string }) {
  return <div className='memory-index-empty'><BrainCircuit size={23} /><strong>{title}</strong><p>{text}</p></div>;
}

function ReadingEmpty() {
  return <div className='memory-reading-empty'><BookOpen size={25} /><strong>Select an entry</strong><p>Its content, source, and lifetime will appear here.</p></div>;
}
