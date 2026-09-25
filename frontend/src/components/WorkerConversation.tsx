import { useRef, useState } from 'react';
import { Check, MessageCircle, Paperclip, Send, X } from 'lucide-react';
import type { ApprovalRecord } from '../lib/approvalApi';
import { decideApproval } from '../lib/approvalApi';
import {
  confirmConversationCommand,
  createConversation,
  sendConversationMessage,
  uploadConversationAttachment,
  type ConversationReceipt,
} from '../lib/conversationApi';
import type { ApiVersion } from '../lib/systemApi';
import type { AppView } from '../navigation';

async function fileBase64(file: File) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  const chunk = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunk) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunk));
  }
  return btoa(binary);
}

function errorText(value: unknown) {
  const data = value as { response?: { data?: { detail?: unknown; error?: string } }; message?: string };
  const detail = data.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  return data.response?.data?.error || data.message || 'Conversation request failed.';
}

export default function WorkerConversation({
  organizationId,
  workerId,
  workerName,
  apiVersion,
  pendingApprovals,
  onApprovalsChanged,
  onNavigate,
}: {
  organizationId: string;
  workerId: string;
  workerName: string;
  apiVersion: ApiVersion;
  pendingApprovals: ApprovalRecord[];
  onApprovalsChanged: () => void;
  onNavigate: (view: AppView) => void;
}) {
  const [threadId, setThreadId] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [receipt, setReceipt] = useState<ConversationReceipt | null>(null);
  const [response, setResponse] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  async function ensureThread() {
    if (threadId) return threadId;
    const thread = await createConversation(
      apiVersion,
      organizationId,
      workerId,
      `Conversation with ${workerName}`,
    );
    setThreadId(thread.id);
    return thread.id;
  }

  async function send(event: React.FormEvent) {
    event.preventDefault();
    if (!input.trim() && files.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const activeThread = await ensureThread();
      const references: Array<Record<string, unknown>> = [];
      for (const file of files) {
        const uploaded = await uploadConversationAttachment(apiVersion, organizationId, activeThread, {
          name: file.name,
          mediaType: file.type || 'application/octet-stream',
          contentBase64: await fileBase64(file),
          question: input.trim() || 'Analyze this upload for the task I am discussing with this worker.',
        });
        references.push({
          id: uploaded.artifact.id,
          name: uploaded.artifact.name,
          mediaType: uploaded.artifact.mediaType,
          analysisId: uploaded.analysis.id,
        });
      }
      const message = input.trim() || 'Look at the uploaded file and tell me what it means.';
      const turn = await sendConversationMessage(
        apiVersion,
        organizationId,
        activeThread,
        message,
        references,
      );
      setResponse(turn.response.content);
      setReceipt(turn.receipt);
      setInput('');
      setFiles([]);
      if (fileInput.current) fileInput.current.value = '';
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!threadId || !receipt?.command_id) return;
    setBusy(true);
    setError(null);
    try {
      const turn = await confirmConversationCommand(
        apiVersion,
        organizationId,
        threadId,
        receipt.command_id,
      );
      setResponse(turn.response.content);
      setReceipt(turn.receipt);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function decide(record: ApprovalRecord, decision: 'approve' | 'reject') {
    setBusy(true);
    setError(null);
    try {
      await decideApproval(apiVersion, organizationId, record.id, decision, 'Decided inline from worker conversation.');
      onApprovalsChanged();
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="worker-conversation">
      <div className="worker-conversation-title">
        <MessageCircle size={16} />
        <div>
          <strong>Message {workerName}</strong>
          <span>Ask for status, change work, add instructions, or attach a file.</span>
        </div>
      </div>

      {pendingApprovals.slice(0, 2).map((approval) => (
        <div className="inline-approval" key={approval.id}>
          <div>
            <strong>Approval required</strong>
            <p>{approval.request.action} · {approval.request.target || approval.request.resourceName}</p>
          </div>
          <div>
            <button className="secondary-button" type="button" disabled={busy} onClick={() => void decide(approval, 'approve')}>
              <Check size={13} /> Approve
            </button>
            <button className="ghost-button" type="button" disabled={busy} onClick={() => void decide(approval, 'reject')}>
              <X size={13} /> Reject
            </button>
          </div>
        </div>
      ))}

      {response && (
        <div className="worker-response">
          <strong>{workerName}</strong>
          <p>{response}</p>
          {receipt?.failure_category && <small>{receipt.failure_category.replaceAll('_', ' ')}</small>}
          <div className="receipt-actions">
            {receipt?.action_hints.map((hint, index) => {
              if (hint.kind === 'connect_integration') {
                return (
                  <button key={`connect-${hint.provider ?? index}`} type="button" className="secondary-button" onClick={() => onNavigate('integrations')}>
                    {hint.label || `Connect ${hint.provider ?? 'integration'}`}
                  </button>
                );
              }
              if (hint.kind === 'confirm_command' && receipt.command_id) {
                return (
                  <button key={`confirm-${index}`} type="button" className="danger-button solid" disabled={busy} onClick={() => void confirm()}>
                    {hint.label || 'Confirm'}
                  </button>
                );
              }
              return null;
            })}
          </div>
        </div>
      )}

      {error && <div className="inline-error">{error}</div>}
      {files.length > 0 && (
        <div className="conversation-files">
          {files.map((file) => <span key={`${file.name}-${file.size}`}>{file.name}</span>)}
        </div>
      )}

      <form className="worker-message-form" onSubmit={send}>
        <input
          ref={fileInput}
          hidden
          multiple
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif,application/pdf,text/plain,text/markdown,text/csv,.xlsx,.json,.py,.js,.ts,.tsx,.md"
          onChange={(event) => setFiles(Array.from(event.target.files ?? []).slice(0, 5))}
        />
        <button type="button" className="icon-button" aria-label="Attach files" onClick={() => fileInput.current?.click()}>
          <Paperclip size={15} />
        </button>
        <textarea
          value={input}
          maxLength={12000}
          rows={2}
          onChange={(event) => setInput(event.target.value)}
          placeholder={`Ask something or give ${workerName} a task`}
        />
        <button className="primary-button compact" disabled={busy || (!input.trim() && files.length === 0)}>
          <Send size={14} /> {busy ? 'Working…' : 'Send'}
        </button>
      </form>
    </section>
  );
}
