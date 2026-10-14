import { useRef, useState } from 'react';
import { FilePlus2, Paperclip, Settings2, X } from 'lucide-react';
import type { OrganizationAccess } from '../lib/identityApi';
import type { ApiVersion } from '../lib/systemApi';
import type { AppView } from '../navigation';
import {
  createConversation,
  sendConversationMessage,
  uploadConversationAttachment,
  type ConversationReceipt,
} from '../lib/conversationApi';

function errorText(value: unknown) {
  const data = value as { response?: { data?: { detail?: unknown; error?: string } }; message?: string };
  const detail = data.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as any).message);
  return data.response?.data?.error || data.message || 'Worker creation could not be completed.';
}

function safeMediaType(file: File) {
  if (file.type) return file.type;
  const name = file.name.toLowerCase();
  if (name.endsWith('.json')) return 'application/json';
  if (name.endsWith('.md')) return 'text/markdown';
  if (name.endsWith('.csv')) return 'text/csv';
  if (name.endsWith('.xlsx')) return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  if (name.endsWith('.py')) return 'text/x-python';
  if (name.endsWith('.ts') || name.endsWith('.tsx')) return 'text/x-typescript';
  if (name.endsWith('.js')) return 'text/javascript';
  return 'text/plain';
}

async function fileBase64(file: File) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  const chunk = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunk) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunk));
  }
  return btoa(binary);
}

export default function NaturalWorkerCreate({
  organization,
  apiVersion,
  onClose,
  onAdvanced,
  onCreated,
  onNavigate,
}: {
  organization: OrganizationAccess;
  apiVersion: ApiVersion;
  onClose: () => void;
  onAdvanced: () => void;
  onCreated: (workerId?: string) => void;
  onNavigate: (view: AppView) => void;
}) {
  const [instruction, setInstruction] = useState('');
  const [context, setContext] = useState('');
  const [showContext, setShowContext] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [receipt, setReceipt] = useState<ConversationReceipt | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // When Audoryn asks a question, the answer goes to the same conversation and continues the request.
  const [threadId, setThreadId] = useState<string | null>(null);
  const [answer, setAnswer] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);
  const asking = receipt?.status === 'clarification_required' && Boolean(threadId && receipt.command_id);

  function finish(turnReceipt: ConversationReceipt) {
    setReceipt(turnReceipt);
    const worker = turnReceipt.references.find((item) => item.type === 'worker');
    if (turnReceipt.status === 'completed' || turnReceipt.status === 'waiting_integration') {
      onCreated(worker?.id);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (asking) {
      const reply = answer.trim();
      if (!reply || !threadId || !receipt?.command_id) return;
      setSaving(true);
      setError(null);
      try {
        const turn = await sendConversationMessage(apiVersion, organization.id, threadId, reply, [],
          { operation: 'worker.create', resumeCommandId: receipt.command_id });
        setAnswer('');
        finish(turn.receipt);
      } catch (caught) {
        setError(errorText(caught));
      } finally {
        setSaving(false);
      }
      return;
    }
    const trimmed = instruction.trim();
    if (!trimmed) return;
    setSaving(true);
    setError(null);
    setReceipt(null);
    try {
      const thread = await createConversation(apiVersion, organization.id, undefined, 'Create worker');
      setThreadId(thread.id);
      const references: Array<Record<string, unknown>> = [];
      for (const file of files) {
        const uploaded = await uploadConversationAttachment(apiVersion, organization.id, thread.id, {
          name: file.name,
          mediaType: safeMediaType(file),
          contentBase64: await fileBase64(file),
          question: 'Use this file only as context for the worker I am asking you to create.',
        });
        references.push({
          id: uploaded.artifact.id,
          name: uploaded.artifact.name,
          mediaType: uploaded.artifact.mediaType,
          analysisId: uploaded.analysis.id,
        });
      }
      // The form is worker creation: the request is sent as written, with no wording required.
      const message = context.trim()
        ? `${trimmed}\n\nAdditional context from me:\n${context.trim()}`
        : trimmed;
      const turn = await sendConversationMessage(
        apiVersion,
        organization.id,
        thread.id,
        message,
        references,
        { operation: 'worker.create' },
      );
      finish(turn.receipt);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="workforce-modal-backdrop">
      <form className="workforce-modal natural-worker-modal" onSubmit={submit}>
        <header>
          <div>
            <p className="panel-kicker">CREATE A WORKER</p>
            <h2>What should this worker do?</h2>
            <span>Describe the outcome in normal language. Audoryn will infer the worker, jobs, tools, schedule and approval boundaries.</span>
          </div>
          <button type="button" onClick={onClose} aria-label="Close create worker"><X size={18} /></button>
        </header>

        {error && <div className="inline-error">{error}</div>}
        <label className="natural-worker-prompt">
          <span className="sr-only">What should this worker do?</span>
          <textarea
            required
            minLength={4}
            maxLength={12000}
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            placeholder="e.g. Monitor my application status every morning, tell me if anything changes, and never submit or modify anything without asking me."
            autoFocus
          />
        </label>

        <div className="natural-worker-tools">
          <input
            ref={fileInput}
            hidden
            type="file"
            multiple
            accept="image/png,image/jpeg,image/webp,image/gif,application/pdf,text/plain,text/markdown,text/csv,.xlsx,.json,.py,.js,.ts,.tsx,.md"
            onChange={(event) => setFiles(Array.from(event.target.files ?? []).slice(0, 5))}
          />
          <button type="button" className="ghost-button" onClick={() => fileInput.current?.click()}>
            <Paperclip size={14} /> Attach files
          </button>
          <button type="button" className="ghost-button" onClick={() => setShowContext((value) => !value)}>
            <FilePlus2 size={14} /> Add context
          </button>
        </div>

        {files.length > 0 && (
          <div className="natural-worker-files">
            {files.map((file) => <span key={`${file.name}-${file.size}`}>{file.name}</span>)}
          </div>
        )}

        {showContext && (
          <label>
            Additional context <small>optional</small>
            <textarea
              maxLength={6000}
              value={context}
              onChange={(event) => setContext(event.target.value)}
              placeholder="Names, boundaries, preferences, or other context the worker should use."
            />
          </label>
        )}

        {receipt && (
          <section className={`conversation-receipt ${receipt.status}`}>
            <strong>{receipt.status === 'completed' ? 'Worker ready' : receipt.status.replaceAll('_', ' ')}</strong>
            <p>{receipt.message}</p>
            {asking && (
              <label className="natural-worker-answer">
                <span className="sr-only">Your answer</span>
                <textarea
                  maxLength={4000}
                  value={answer}
                  onChange={(event) => setAnswer(event.target.value)}
                  placeholder="Your answer"
                  autoFocus
                />
              </label>
            )}
            {receipt.failure_category && <small>Reason: {receipt.failure_category.replaceAll('_', ' ')}</small>}
            {receipt.action_hints.length > 0 && (
              <div className="receipt-actions">
                {receipt.action_hints.map((hint, index) => (
                  hint.kind === 'connect_integration' ? (
                    <button
                      type="button"
                      className="secondary-button"
                      key={`${hint.kind}-${hint.provider ?? index}`}
                      onClick={() => {
                        onClose();
                        onNavigate('integrations');
                      }}
                    >
                      {hint.label || `Connect ${hint.provider ?? 'integration'}`}
                    </button>
                  ) : null
                ))}
              </div>
            )}
          </section>
        )}

        <div className="natural-worker-boundary">
          <strong>Audoryn proposes. Governance decides.</strong>
          <p>AI cannot grant itself permissions, bypass approvals, or execute outside the resolved allowlist. Existing F29/F30 controls remain authoritative.</p>
        </div>

        <footer>
          <button type="button" className="ghost-button" onClick={onAdvanced}>
            <Settings2 size={14} /> Advanced setup
          </button>
          <button className="primary-button" disabled={saving || (asking ? !answer.trim() : !instruction.trim())}>
            {saving ? (asking ? 'Sending…' : 'Creating worker…') : asking ? 'Send answer' : 'Create worker'}
          </button>
        </footer>
      </form>
    </div>
  );
}
