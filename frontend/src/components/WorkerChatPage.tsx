import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import type { OrganizationAccess } from '../lib/identityApi';
import { decideApproval, listApprovals, type ApprovalRecord } from '../lib/approvalApi';
import type { ApiVersion } from '../lib/systemApi';
import { loadWorkforce, type ManagedWorker } from '../lib/workforceApi';
import { confirmConversationCommand, createConversation, getConversation, listConversations, sendConversationMessage, uploadConversationAttachment, type ConversationMessage, type ConversationReceipt, type ConversationThread } from '../lib/conversationApi';
import type { AppView } from '../navigation';
import { latestThreadId, threadsForWorker } from './workerChatModel';
import ConversationField from './ConversationField';

function errorText(value: unknown) {
  const data = value as { response?: { data?: { detail?: unknown; error?: string } }; message?: string };
  return typeof data.response?.data?.detail === 'string' ? data.response.data.detail : data.response?.data?.error || data.message || 'Conversation request failed.';
}

function mediaType(file: File) {
  if (file.type) return file.type;
  const suffix = file.name.split('.').pop()?.toLowerCase();
  return ({ json: 'application/json', md: 'text/markdown', csv: 'text/csv', xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', py: 'text/x-python', ts: 'text/x-typescript', tsx: 'text/x-typescript', js: 'text/javascript' } as Record<string, string>)[suffix || ''] || 'text/plain';
}

async function base64(file: File) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 0x8000) binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  return btoa(binary);
}

export default function WorkerChatPage({ organization, apiVersion, initialWorkerId, onNavigate }: { organization: OrganizationAccess; apiVersion: ApiVersion; initialWorkerId: string | null; onNavigate: (view: AppView) => void }) {
  const [workers, setWorkers] = useState<ManagedWorker[]>([]);
  const [threads, setThreads] = useState<ConversationThread[]>([]);
  const [workerId, setWorkerId] = useState<string | null>(initialWorkerId);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [receipt, setReceipt] = useState<ConversationReceipt | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [query, setQuery] = useState('');
  const [draft, setDraft] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [loading, setLoading] = useState(true);
  const [threadLoading, setThreadLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [responsePhase, setResponsePhase] = useState<'preparing' | 'responding' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const timeline = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([loadWorkforce(apiVersion, organization.id), listConversations(apiVersion, organization.id)]).then(([workforce, conversations]) => {
      if (!active) return;
      setWorkers(workforce.workers);
      setThreads(conversations);
      const resolvedWorkerId = initialWorkerId && workforce.workers.some((item) => item.id === initialWorkerId) ? initialWorkerId : null;
      setWorkerId(resolvedWorkerId);
      setThreadId(latestThreadId(conversations, resolvedWorkerId));
      setError(null);
    }).catch((caught) => { if (active) setError(errorText(caught)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [apiVersion, organization.id]);

  const worker = workers.find((item) => item.id === workerId) ?? null;
  const workerThreads = useMemo(() => threadsForWorker(threads, workerId), [threads, workerId]);
  const pendingApprovals = approvals.filter((item) => item.agentId === worker?.agentIdentityId && item.status === 'pending');

  useEffect(() => {
    if (!organization.permissions.includes('approvals.review')) return;
    let active = true;
    listApprovals(apiVersion, organization.id).then((rows) => { if (active) setApprovals(rows); }).catch(() => { if (active) setApprovals([]); });
    return () => { active = false; };
  }, [apiVersion, organization.id, organization.permissions]);

  useEffect(() => {
    if (!threadId) { setMessages([]); setReceipt(null); setThreadLoading(false); return; }
    let active = true;
    setThreadLoading(true);
    getConversation(apiVersion, organization.id, threadId).then((result) => {
      if (active) { setMessages(result.messages); setError(null); }
    }).catch((caught) => { if (active) setError(errorText(caught)); }).finally(() => { if (active) setThreadLoading(false); });
    return () => { active = false; };
  }, [apiVersion, organization.id, threadId]);

  useEffect(() => { timeline.current?.scrollTo({ top: timeline.current.scrollHeight, behavior: 'smooth' }); }, [messages, threadLoading, responsePhase]);

  function chooseWorker(id: string) {
    if (busy) return;
    setWorkerId(id);
    setThreadId(latestThreadId(threads, id));
    setMessages([]);
    setReceipt(null);
    setDraft('');
    setFiles([]);
  }
  function back() {
    if (workerId) { setWorkerId(null); setThreadId(null); setReceipt(null); setMessages([]); return; }
    onNavigate('workforce');
  }
  function chooseThread(id: string) { if (busy) return; setReceipt(null); setThreadId(id); }
  function newThread() { if (busy) return; setThreadId(null); setMessages([]); setReceipt(null); }

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!worker || (!draft.trim() && files.length === 0) || busy) return;
    setBusy(true); setError(null); setReceipt(null);
    setResponsePhase(!threadId || files.length > 0 ? 'preparing' : 'responding');
    let createdThreadId: string | null = null;
    try {
      let activeThreadId = threadId;
      if (!activeThreadId) {
        const created = await createConversation(apiVersion, organization.id, worker.id, `Conversation with ${worker.name}`);
        activeThreadId = created.id;
        createdThreadId = created.id;
        setThreads((current) => [created, ...current]);
      }
      const references: Array<Record<string, unknown>> = [];
      for (const file of files) {
        const uploaded = await uploadConversationAttachment(apiVersion, organization.id, activeThreadId, { name: file.name, mediaType: mediaType(file), contentBase64: await base64(file), question: draft.trim() || `Review this file for ${worker.name}.` });
        references.push({ id: uploaded.artifact.id, name: uploaded.artifact.name, mediaType: uploaded.artifact.mediaType, analysisId: uploaded.analysis.id });
      }
      const content = draft.trim() || 'Review the attached file and explain what matters.';
      setResponsePhase('responding');
      const turn = await sendConversationMessage(apiVersion, organization.id, activeThreadId, content, references);
      const result = await getConversation(apiVersion, organization.id, activeThreadId);
      if (createdThreadId) setThreadId(createdThreadId);
      setMessages(result.messages);
      setReceipt(turn.receipt);
      setThreads((current) => current.map((item) => item.id === activeThreadId ? result.thread : item));
      setDraft(''); setFiles([]);
      if (fileInput.current) fileInput.current.value = '';
    } catch (caught) { if (createdThreadId) setThreadId(createdThreadId); setError(errorText(caught)); } finally { setBusy(false); setResponsePhase(null); }
  }

  async function confirm() {
    if (!threadId || !receipt?.command_id) return;
    setBusy(true); setError(null);
    try {
      const turn = await confirmConversationCommand(apiVersion, organization.id, threadId, receipt.command_id);
      const result = await getConversation(apiVersion, organization.id, threadId);
      setMessages(result.messages);
      setReceipt(turn.receipt);
    } catch (caught) { setError(errorText(caught)); } finally { setBusy(false); }
  }

  async function decide(approval: ApprovalRecord, decision: 'approve' | 'reject') {
    setBusy(true); setError(null);
    try {
      await decideApproval(apiVersion, organization.id, approval.id, decision, 'Decided in the worker conversation.');
      setApprovals((current) => current.filter((item) => item.id !== approval.id));
    } catch (caught) { setError(errorText(caught)); } finally { setBusy(false); }
  }

  return <ConversationField
    workers={workers}
    worker={worker}
    workerId={workerId}
    workerThreads={workerThreads}
    threadId={threadId}
    messages={messages}
    receipt={receipt}
    pendingApprovals={pendingApprovals}
    query={query}
    draft={draft}
    files={files}
    loading={loading}
    threadLoading={threadLoading}
    busy={busy}
    responsePhase={responsePhase}
    error={error}
    fileInput={fileInput}
    timeline={timeline}
    onQuery={setQuery}
    onDraft={setDraft}
    onFiles={setFiles}
    onWorker={chooseWorker}
    onThread={chooseThread}
    onNewThread={newThread}
    onSend={send}
    onConfirm={() => void confirm()}
    onDecide={(approval, decision) => void decide(approval, decision)}
    onNavigate={onNavigate}
    onBack={back}
  />;
}
