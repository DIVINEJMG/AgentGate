import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import type { OrganizationAccess } from '../lib/identityApi';
import { decideApproval, listApprovals, type ApprovalRecord } from '../lib/approvalApi';
import type { ApiVersion } from '../lib/systemApi';
import { loadWorkforce, type ManagedWorker } from '../lib/workforceApi';
import { confirmConversationCommand, createConversation, getConversation, listConversations, sendConversationMessage, uploadConversationAttachment, type ConversationMessage, type ConversationReceipt, type ConversationThread } from '../lib/conversationApi';
import type { AppView } from '../navigation';
import { subscribeOrganizationRealtime } from '../platform/realtimeClient';
import { approvalInConversation, latestThreadId, threadsForWorker, type LiveResultReference } from './workerChatModel';
import ConversationField from './ConversationField';
import { navigate } from '../workspace/routes';
import { usePageTitle } from '../workspace/history';

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

/** Worker and thread live in the URL (`/conversations/:workerId/:threadId`, `new` for a fresh chat). */
export default function WorkerChatPage({ organization, apiVersion, initialWorkerId, initialThreadId = null, onNavigate }: { organization: OrganizationAccess; apiVersion: ApiVersion; initialWorkerId: string | null; initialThreadId?: string | null; onNavigate: (view: AppView) => void }) {
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
      setError(null);
    }).catch((caught) => { if (active) setError(errorText(caught)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [apiVersion, organization.id]);

  // Follow the URL: Back, Forward, refresh and shared links all land on the same worker and thread.
  useEffect(() => {
    if (loading) return;
    const resolvedWorkerId = initialWorkerId && workers.some((item) => item.id === initialWorkerId) ? initialWorkerId : null;
    if (resolvedWorkerId !== workerId) { setMessages([]); setReceipt(null); setDraft(''); setFiles([]); }
    setWorkerId(resolvedWorkerId);
    if (!resolvedWorkerId) {
      setThreadId(null);
      if (initialWorkerId) navigate({ page: 'conversations' }, { replace: true });
      return;
    }
    if (initialThreadId === 'new') { setThreadId(null); setMessages([]); setReceipt(null); return; }
    const known = initialThreadId && threads.some((item) => item.id === initialThreadId && item.workerId === resolvedWorkerId) ? initialThreadId : null;
    const next = known ?? latestThreadId(threads, resolvedWorkerId);
    if (next !== threadId) setReceipt(null);
    setThreadId(next);
    if ((next ?? undefined) !== (initialThreadId ?? undefined)) navigate({ page: 'conversations', workerId: resolvedWorkerId, threadId: next ?? undefined }, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, initialWorkerId, initialThreadId]);

  const worker = workers.find((item) => item.id === workerId) ?? null;
  usePageTitle(threads.find((item) => item.id === threadId)?.title);
  const workerThreads = useMemo(() => threadsForWorker(threads, workerId), [threads, workerId]);
  const pendingApprovals = threadLoading ? [] : approvals.filter(item =>
    item.agentId === worker?.agentIdentityId && item.status === 'pending' && approvalInConversation(item, threadId, messages));

  useEffect(() => {
    if (!organization.permissions.includes('approvals.review')) return;
    let active = true;
    const refresh = () => { void listApprovals(apiVersion, organization.id).then((rows) => { if (active) setApprovals(rows); }).catch(() => { if (active) setApprovals([]); }); };
    refresh();
    const unsubscribe = subscribeOrganizationRealtime({ organizationId: organization.id, eventTypes: ['approval.created', 'approval.decided', 'run.waiting_approval'], onEvent: refresh, poll: refresh, pollingIntervalMs: 60000 });
    return () => { active = false; unsubscribe(); };
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

  useEffect(() => {
    if (!threadId) return;
    let active = true;
    const refresh = async () => {
      try {
        const result = await getConversation(apiVersion, organization.id, threadId);
        if (!active) return;
        setMessages(result.messages);
        setThreads((current) => current.map((item) => item.id === threadId ? result.thread : item));
      } catch {
        // Realtime refresh is best-effort; normal conversation errors remain user-visible.
      }
    };
    const unsubscribe = subscribeOrganizationRealtime({
      organizationId: organization.id,
      eventTypes: ['result.created', 'conversation.response.created', 'run.completed'],
      onEvent: () => { void refresh(); },
      poll: () => refresh(),
      pollingIntervalMs: 30000,
    });
    return () => { active = false; unsubscribe(); };
  }, [apiVersion, organization.id, threadId]);

  useEffect(() => { timeline.current?.scrollTo({ top: timeline.current.scrollHeight, behavior: 'smooth' }); }, [messages, threadLoading, responsePhase]);

  function chooseWorker(id: string) {
    if (busy || id === workerId) return;
    navigate({ page: 'conversations', workerId: id });
  }
  function back() {
    if (workerId) { navigate({ page: 'conversations' }); return; }
    onNavigate('workforce');
  }
  function chooseThread(id: string) { if (busy || !workerId) return; navigate({ page: 'conversations', workerId, threadId: id }); }
  function newThread() { if (busy || !workerId) return; navigate({ page: 'conversations', workerId, threadId: 'new' }); }

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
      if (createdThreadId) { setThreadId(createdThreadId); navigate({ page: 'conversations', workerId: worker.id, threadId: createdThreadId }, { replace: true }); }
      setMessages(result.messages);
      setReceipt(turn.receipt);
      setThreads((current) => current.map((item) => item.id === activeThreadId ? result.thread : item));
      setDraft(''); setFiles([]);
      if (fileInput.current) fileInput.current.value = '';
    } catch (caught) { if (createdThreadId) { setThreadId(createdThreadId); navigate({ page: 'conversations', workerId: worker.id, threadId: createdThreadId }, { replace: true }); } setError(errorText(caught)); } finally { setBusy(false); setResponsePhase(null); }
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

  function openResult(reference: LiveResultReference) {
    navigate({ page: 'results', resultId: reference.id });
  }

  async function explainResult(reference: LiveResultReference) {
    if (!threadId || busy) return;
    setBusy(true); setError(null); setReceipt(null); setResponsePhase('responding');
    try {
      const turn = await sendConversationMessage(
        apiVersion,
        organization.id,
        threadId,
        `Explain this specific task result to me: "${reference.name}" (result ID: ${reference.id}). Tell me what was completed, what remains blocked or unverified, what the result means, and anything I should pay attention to.`,
      );
      const result = await getConversation(apiVersion, organization.id, threadId);
      setMessages(result.messages);
      setReceipt(turn.receipt);
      setThreads((current) => current.map((item) => item.id === threadId ? result.thread : item));
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
      setResponsePhase(null);
    }
  }

  return <ConversationField
    organizationId={organization.id}
    apiVersion={apiVersion}
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
    onOpenResult={openResult}
    onExplainResult={(reference) => void explainResult(reference)}
    onNavigate={onNavigate}
    onBack={back}
  />;
}
