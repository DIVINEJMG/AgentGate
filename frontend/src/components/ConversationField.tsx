import { useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent, type KeyboardEvent, type RefObject } from 'react';
import { ArrowLeft, Check, ChevronRight, FileUp, Paperclip, Plus, Search, Send, ShieldCheck, X } from 'lucide-react';
import type { ApprovalRecord } from '../lib/approvalApi';
import type { ConversationMessage, ConversationReceipt, ConversationThread } from '../lib/conversationApi';
import type { ManagedWorker } from '../lib/workforceApi';
import type { AppView } from '../navigation';
import { discoveryWorkers } from './workerChatModel';

type Props = {
  workers: ManagedWorker[]; worker: ManagedWorker | null; workerId: string | null;
  workerThreads: ConversationThread[]; threadId: string | null; messages: ConversationMessage[];
  receipt: ConversationReceipt | null; pendingApprovals: ApprovalRecord[];
  query: string; draft: string; files: File[]; loading: boolean; threadLoading: boolean;
  busy: boolean; responsePhase: 'preparing' | 'responding' | null; error: string | null;
  fileInput: RefObject<HTMLInputElement | null>; timeline: RefObject<HTMLDivElement | null>;
  onQuery: (value: string) => void; onDraft: (value: string) => void; onFiles: (files: File[]) => void;
  onWorker: (id: string) => void; onThread: (id: string) => void; onNewThread: () => void;
  onSend: (event: FormEvent) => void; onConfirm: () => void;
  onDecide: (approval: ApprovalRecord, decision: 'approve' | 'reject') => void;
  onNavigate: (view: AppView) => void; onBack: () => void;
};

const hues = ['sage', 'slate', 'mint', 'sand', 'lilac', 'blue'];
const exampleIntegrations = [
  { name: 'GitHub', mark: '/provider-marks/github.svg', result: 'Repository review prepared', worker: 'Development worker' },
  { name: 'Gmail', mark: '/provider-marks/gmail.svg', result: 'Support inbox triaged', worker: 'Support worker' },
  { name: 'Google Drive', mark: '/provider-marks/google-drive.svg', result: 'Research brief organized', worker: 'Research worker' },
  { name: 'Slack', mark: '/provider-marks/slack.svg', result: 'Operations handoff shared', worker: 'Operations worker' },
] as const;
function arcPoint(index: number, count: number) {
  if (count === 1) return { x: 50, y: 8 };
  const progress = index / (count - 1);
  const span = Math.min(1, (count - 1) / 5);
  return { x: 50 + 84 * (progress - .5) * span, y: 8 + 52 * Math.pow(Math.abs(2 * progress - 1), 1.5) * span };
}
function initials(name: string) { return name.split(/\s+/).slice(0, 2).map(part => part[0] || '').join('').toUpperCase(); }
function shortTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

export default function ConversationField(props: Props) {
  const { workers, worker, workerId, workerThreads, threadId, messages, receipt, pendingApprovals, query, draft, files, loading, threadLoading, busy, responsePhase, error, fileInput, timeline, onQuery, onDraft, onFiles, onWorker, onThread, onNewThread, onSend, onConfirm, onDecide, onNavigate, onBack } = props;
  const [arcOffset, setArcOffset] = useState(0);
  const [historyQuery, setHistoryQuery] = useState('');
  const [position, setPosition] = useState({ progress: 0, size: 1 });
  const historyRef = useRef<HTMLDivElement>(null);
  const railRef = useRef<HTMLDivElement>(null);
  const workerViewport = useRef<HTMLDivElement>(null);
  const composerInput = useRef<HTMLTextAreaElement>(null);
  const [workerFade, setWorkerFade] = useState({ before: false, after: false });
  const { filtered: filteredWorkers, visible, start } = useMemo(() => discoveryWorkers(workers, query, arcOffset), [workers, query, arcOffset]);
  const filteredThreads = useMemo(() => workerThreads.filter(item => item.title.toLowerCase().includes(historyQuery.trim().toLowerCase())), [workerThreads, historyQuery]);
  const chat = Boolean(workerId);
  const activeThread = workerThreads.find(item => item.id === threadId);

  useEffect(() => { setArcOffset(0); }, [query]);
  useEffect(() => { setHistoryQuery(''); }, [workerId]);
  useEffect(() => {
    if (chat && workerViewport.current) workerViewport.current.scrollTop = 0;
  }, [chat, query]);
  function syncWorkerFade() {
    const viewport = workerViewport.current;
    if (!viewport) return;
    const before = viewport.scrollTop > 2;
    const after = viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop > 2;
    setWorkerFade((current) => current.before === before && current.after === after ? current : { before, after });
  }
  useEffect(() => {
    const viewport = workerViewport.current;
    if (!chat || !viewport) return;
    const observer = new ResizeObserver(syncWorkerFade);
    observer.observe(viewport);
    if (viewport.firstElementChild) observer.observe(viewport.firstElementChild);
    syncWorkerFade();
    return () => observer.disconnect();
  }, [chat, filteredWorkers.length]);
  useEffect(() => {
    const input = composerInput.current;
    if (!input) return;
    input.style.height = '0px';
    input.style.height = `${Math.min(input.scrollHeight, 104)}px`;
  }, [draft]);

  function syncPosition() {
    const element = timeline.current;
    if (!element) return;
    const range = Math.max(0, element.scrollHeight - element.clientHeight);
    setPosition({ progress: range ? element.scrollTop / range : 0, size: Math.min(1, element.clientHeight / Math.max(element.scrollHeight, 1)) });
  }
  useEffect(() => {
    const element = timeline.current;
    if (!element || !chat) return;
    const observer = new ResizeObserver(syncPosition);
    observer.observe(element);
    if (element.firstElementChild) observer.observe(element.firstElementChild);
    syncPosition();
    return () => observer.disconnect();
  }, [chat, messages, threadLoading, receipt, pendingApprovals.length]);

  function moveTranscript(clientY: number) {
    const rail = railRef.current;
    const element = timeline.current;
    if (!rail || !element) return;
    const bounds = rail.getBoundingClientRect();
    const fraction = Math.max(0, Math.min(1, (clientY - bounds.top) / bounds.height));
    element.scrollTop = fraction * (element.scrollHeight - element.clientHeight);
  }
  function railKey(event: KeyboardEvent<HTMLDivElement>) {
    const element = timeline.current;
    if (!element) return;
    const step = event.key === 'PageDown' || event.key === 'PageUp' ? element.clientHeight * .8 : 72;
    if (event.key === 'ArrowDown' || event.key === 'PageDown') element.scrollBy({ top: step, behavior: 'smooth' });
    else if (event.key === 'ArrowUp' || event.key === 'PageUp') element.scrollBy({ top: -step, behavior: 'smooth' });
    else if (event.key === 'Home') element.scrollTo({ top: 0, behavior: 'smooth' });
    else if (event.key === 'End') element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' });
    else return;
    event.preventDefault();
  }

  return <section className={chat ? 'cf-scene is-chat' : 'cf-scene is-discovery'} aria-label="Worker conversations">
    <button type="button" className="cf-back" onClick={onBack} aria-label={chat ? 'Back to worker discovery' : 'Back to workforce'}><ArrowLeft size={20} strokeWidth={1.7}/></button>
    <h1 className="sr-only">Worker conversations</h1>
    <div className="cf-worker-map">
      <div className={'cf-worker-viewport' + (chat && workerFade.before ? ' has-before' : '') + (chat && workerFade.after ? ' has-after' : '')} ref={workerViewport} aria-label="Choose a worker" onScroll={syncWorkerFade}>
        <div className="cf-worker-inner" style={{ '--worker-count': filteredWorkers.length } as CSSProperties}>
          {filteredWorkers.map((item, index) => {
            const positionInArc = index - start;
            const point = arcPoint(positionInArc, visible.length);
            return <button type="button" key={item.id} className={'cf-avatar ' + hues[index % hues.length] + (workerId === item.id ? ' is-active' : '') + (positionInArc < 0 || positionInArc >= 6 ? ' is-outside-arc' : '')} style={{ '--i': index, '--arc-x': point.x + '%', '--arc-y': point.y + '%' } as CSSProperties} onClick={() => onWorker(item.id)} disabled={busy} tabIndex={chat || (positionInArc >= 0 && positionInArc < 6) ? 0 : -1} aria-label={'Open conversations with ' + item.name} aria-pressed={workerId === item.id} title={item.name}>
              <span className="cf-avatar-disc"><span>{initials(item.name)}</span></span><span className={'cf-avatar-status ' + item.status}/><span className="cf-avatar-name">{item.name}</span>
            </button>;
          })}
        </div>
      </div>
      {!chat && filteredWorkers.length > 6 && <>
        {start > 0 && <button type="button" className="cf-arc-more is-before" onClick={() => setArcOffset(Math.max(0, start - 1))} aria-label="Show previous workers"><span/></button>}
        {start + visible.length < filteredWorkers.length && <button type="button" className="cf-arc-more is-after" onClick={() => setArcOffset(start + 1)} aria-label="Show more workers"><span/></button>}
      </>}
      {!chat && visible.length > 1 && <div className="cf-arc-line" aria-hidden="true"/>}
      <label className="cf-worker-search"><Search size={19} strokeWidth={1.7}/><span className="sr-only">Search workers</span><input value={query} onChange={event => onQuery(event.target.value)} placeholder="Find a worker" autoComplete="off"/><span className="cf-search-count">{filteredWorkers.length}</span></label>
      {!chat && <div className="cf-discovery-note" role="status">{loading ? 'Loading workers…' : error ? error : filteredWorkers.length === 0 ? 'No workers match your search.' : 'Choose a worker to open a conversation.'}</div>}
      {!chat && <div className="cf-integration-preview" aria-label="Illustrative integration activity">
        <span className="cf-integration-heading">INTEGRATION EXAMPLES</span>
        <div className="cf-integration-row">{exampleIntegrations.map((item) => <div className="cf-integration-item" key={item.name}>
          <button type="button" aria-label={`${item.name}: example completed job, ${item.result}`} aria-describedby={`cf-example-${item.name.toLowerCase().replace(/\s+/g, '-')}`}><span className="cf-integration-mark"><img src={item.mark} alt=""/></span><span className="cf-integration-name">{item.name}</span></button>
          <div className="cf-integration-result" id={`cf-example-${item.name.toLowerCase().replace(/\s+/g, '-')}`} role="tooltip"><span>ILLUSTRATIVE RESULT</span><strong>{item.result}</strong><small>{item.worker} · Example completed job</small></div>
        </div>)}</div>
      </div>}
    </div>
    <div className="cf-chat-canvas" aria-hidden={!chat}>
      <header className="cf-chat-header">
        <div className="cf-identity"><span className={'cf-identity-disc ' + hues[Math.max(0, workers.findIndex(item => item.id === workerId)) % hues.length]}>{worker ? initials(worker.name) : ''}</span><div><strong>{worker?.name}</strong><small>{worker?.department || 'AI worker'} · {worker?.status}</small></div></div>
        <div className="cf-history">
          <label className="cf-history-search"><Search size={17} strokeWidth={1.7}/><span className="sr-only">Search chat history</span><input value={historyQuery} onChange={event => setHistoryQuery(event.target.value)} placeholder="Search chats"/></label>
          <div className="cf-history-row"><button type="button" className="cf-new-chat" onClick={onNewThread} disabled={!worker || busy} aria-label="Start a new chat" title="New chat"><Plus size={20} strokeWidth={1.6}/></button><div className="cf-history-strip" ref={historyRef} aria-label="Chat history">{filteredThreads.map(item => <button type="button" key={item.id} className={item.id === threadId ? 'is-active' : ''} onClick={() => onThread(item.id)} disabled={busy} title={item.title}>{item.title}</button>)}{filteredThreads.length === 0 && <span className="cf-no-history">{historyQuery ? 'No matching chats' : 'No earlier chats'}</span>}</div><button type="button" className="cf-history-next" onClick={() => historyRef.current?.scrollBy({ left: 230, behavior: 'smooth' })} aria-label="Scroll chat history right"><ChevronRight size={19}/></button></div>
        </div>
      </header>
      <div className="cf-transcript-wrap">
        <div id="cf-transcript" className="cf-transcript" ref={timeline} role="log" aria-label="Conversation messages" aria-live="polite" onScroll={syncPosition}>
          <div className="cf-transcript-inner">
            {error && <div className="cf-error" role="alert">{error}</div>}
            {threadLoading ? <div className="cf-chat-empty">Opening conversation…</div> : messages.length ? messages.map(message => {
              const isUser = message.role === 'user';
              return <article key={message.id} className={'cf-message ' + (isUser ? 'is-user' : 'is-worker')}><span className="cf-message-avatar">{isUser ? 'You' : initials(worker?.name ?? 'Worker')}</span><div className="cf-message-main"><div className="cf-message-body">{message.content}{message.artifactReferences?.length > 0 && <span className="cf-message-file"><FileUp size={13}/>{message.artifactReferences.length} attached file{message.artifactReferences.length === 1 ? '' : 's'}</span>}</div><time dateTime={message.createdAt}>{shortTime(message.createdAt)}</time></div></article>;
            }) : responsePhase ? null : <div className="cf-chat-empty"><span className="cf-empty-ring"><Send size={19}/></span><strong>{activeThread?.title || ('Start with ' + (worker?.name || 'a worker'))}</strong><p>Give a clear direction. The conversation remains connected to this worker's identity, capabilities, and approvals.</p></div>}
            {responsePhase && <div className="cf-response-pending" role="status" aria-live="polite"><span className="cf-message-avatar">{initials(worker?.name ?? 'Worker')}</span><div className="cf-response-pending-bubble"><span>{worker?.name ?? 'Worker'} · {responsePhase === 'preparing' ? 'Preparing context' : 'Working on your reply'}</span><span className="cf-response-dots" aria-hidden="true"><i/><i/><i/></span></div></div>}
            {receipt && <div className={'cf-receipt ' + receipt.status}><span className="cf-receipt-label">EXECUTION RECEIPT · {receipt.status.replaceAll('_', ' ')}</span><p>{receipt.message}</p><div>{receipt.action_hints.map((hint, index) => hint.kind === 'connect_integration' ? <button type="button" key={index} onClick={() => onNavigate('integrations')}>{hint.label || 'Connect integration'}</button> : hint.kind === 'confirm_command' && receipt.command_id ? <button type="button" key={index} onClick={onConfirm} disabled={busy}><Check size={14}/>{hint.label || 'Confirm action'}</button> : null)}</div></div>}
            {pendingApprovals.map(approval => <div className="cf-approval" key={approval.id}><span>HUMAN DECISION</span><strong>{approval.request.action}</strong><p>{approval.request.target || approval.request.resourceName}</p><div><button type="button" disabled={busy} onClick={() => onDecide(approval, 'approve')}>Approve</button><button type="button" disabled={busy} onClick={() => onDecide(approval, 'reject')}>Reject</button></div></div>)}
          </div>
        </div>
        <div className="cf-position" ref={railRef} role="scrollbar" tabIndex={chat ? 0 : -1} aria-label="Message position" aria-controls="cf-transcript" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(position.progress * 100)} onKeyDown={railKey} onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); moveTranscript(event.clientY); }} onPointerMove={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) moveTranscript(event.clientY); }}><div className="cf-position-ticks"/><span className="cf-position-thumb" style={{ top: position.progress * (100 - Math.max(8, position.size * 100)) + '%', height: Math.max(8, position.size * 100) + '%' }}/></div>
      </div>
      <form className="cf-composer" onSubmit={onSend}>
        <input ref={fileInput} hidden type="file" multiple accept="image/png,image/jpeg,image/webp,image/gif,application/pdf,text/plain,text/markdown,text/csv,.xlsx,.json,.py,.js,.ts,.tsx,.md" onChange={event => onFiles(Array.from(event.target.files ?? []).slice(0, 5))}/>
        {files.length > 0 && <div className="cf-composer-files">{files.map(file => <span key={file.name + '-' + file.size}><FileUp size={13}/>{file.name}<button type="button" aria-label={'Remove ' + file.name} onClick={() => onFiles(files.filter(item => item !== file))}><X size={13}/></button></span>)}</div>}
        <div className="cf-composer-line"><button type="button" className="cf-attach" disabled={!worker || busy} onClick={() => fileInput.current?.click()} aria-label="Attach files" title="Attach files"><Paperclip size={19}/></button><label className="sr-only" htmlFor="cf-message-input">Message {worker?.name}</label><textarea id="cf-message-input" ref={composerInput} rows={1} maxLength={12000} value={draft} onChange={event => onDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} disabled={!worker || busy} placeholder={'Message ' + (worker?.name || 'worker') + '…'}/><button type="submit" className="cf-send" disabled={!worker || busy || (!draft.trim() && files.length === 0)} aria-label="Send message" title="Send message"><Send size={18}/></button></div>
        <span className="cf-composer-caption"><ShieldCheck size={12}/> Governed by workspace policy and human approval</span>
      </form>
    </div>
  </section>;
}
