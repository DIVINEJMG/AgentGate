import { ShieldCheck } from 'lucide-react';

export interface BrowserConnectionDraft {
  displayName: string;
  startUrl: string;
  allowedOrigins: string;
  deniedOrigins: string;
  allowedPaths: string;
  deniedPaths: string;
  enableFileTransfer: boolean;
  allowPrivateNetwork: boolean;
}

export const EMPTY_BROWSER_DRAFT: BrowserConnectionDraft = {
  displayName: '',
  startUrl: '',
  allowedOrigins: '',
  deniedOrigins: '',
  allowedPaths: '',
  deniedPaths: '',
  enableFileTransfer: false,
  allowPrivateNetwork: false,
};

function csv(value: string) {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
    .join(',');
}

export function browserDraftValid(draft: BrowserConnectionDraft) {
  const raw = draft.startUrl.trim();
  if (!raw) return false;
  try {
    const url = new URL(raw);
    return (url.protocol === 'https:' || url.protocol === 'http:') && Boolean(url.hostname);
  } catch {
    return false;
  }
}

export function browserConfigFromDraft(draft: BrowserConnectionDraft): Record<string, string> {
  const startUrl = draft.startUrl.trim();
  const parsed = new URL(startUrl);
  if (!['https:', 'http:'].includes(parsed.protocol) || !parsed.hostname) {
    throw new Error('Browser start URL must be an absolute HTTP or HTTPS URL.');
  }

  const allowedOrigins = csv(draft.allowedOrigins) || parsed.origin;
  return {
    displayName: draft.displayName.trim() || parsed.hostname,
    startUrl,
    allowedOrigins,
    deniedOrigins: csv(draft.deniedOrigins),
    allowedPaths: csv(draft.allowedPaths),
    deniedPaths: csv(draft.deniedPaths),
    enableFileTransfer: String(draft.enableFileTransfer),
    allowPrivateNetwork: String(draft.allowPrivateNetwork),
  };
}

export default function BrowserConnectionFields({
  value,
  onChange,
}: {
  value: BrowserConnectionDraft;
  onChange: (next: BrowserConnectionDraft) => void;
}) {
  const set = <K extends keyof BrowserConnectionDraft>(
    key: K,
    next: BrowserConnectionDraft[K],
  ) => onChange({ ...value, [key]: next });

  return (
    <>
      <div className="credential-note">
        <ShieldCheck size={17} />
        <div>
          <strong>Governed Browser boundary</strong>
          <p>
            Audoryn runs this site inside an isolated Chromium context. Every navigation,
            form action, popup, download and upload remains subject to capability, policy,
            risk, approval, verification and audit controls.
          </p>
        </div>
      </div>

      <label>Connection name <span className="optional-label">optional</span></label>
      <input
        value={value.displayName}
        onChange={(event) => set('displayName', event.target.value)}
        placeholder="Customer portal"
        maxLength={100}
      />

      <label>Start URL</label>
      <input
        type="url"
        required
        value={value.startUrl}
        onChange={(event) => set('startUrl', event.target.value)}
        placeholder="https://portal.example.com/dashboard"
      />
      <p className="field-help">
        The URL must use HTTP or HTTPS. Its origin becomes the default allowed origin.
      </p>

      <label>Allowed origins <span className="optional-label">one per line</span></label>
      <textarea
        value={value.allowedOrigins}
        onChange={(event) => set('allowedOrigins', event.target.value)}
        placeholder={'https://portal.example.com\nhttps://login.example.com'}
      />
      <p className="field-help">
        Leave blank to allow only the Start URL origin. Cross-origin navigation fails closed.
      </p>

      <details className="quick-start-details">
        <summary>Destination policy <span>optional restrictions</span></summary>

        <label>Denied origins <span className="optional-label">one per line</span></label>
        <textarea
          value={value.deniedOrigins}
          onChange={(event) => set('deniedOrigins', event.target.value)}
          placeholder="https://admin.example.com"
        />

        <label>Allowed paths <span className="optional-label">one per line</span></label>
        <textarea
          value={value.allowedPaths}
          onChange={(event) => set('allowedPaths', event.target.value)}
          placeholder={'/dashboard\n/reports\n/login'}
        />

        <label>Denied paths <span className="optional-label">one per line</span></label>
        <textarea
          value={value.deniedPaths}
          onChange={(event) => set('deniedPaths', event.target.value)}
          placeholder={'/admin\n/billing/delete'}
        />
      </details>

      <label className="quick-identity-options">
        <span>
          <input
            type="checkbox"
            checked={value.enableFileTransfer}
            onChange={(event) => set('enableFileTransfer', event.target.checked)}
          />
          <strong> Enable governed file upload/download</strong>
          <small>
            Adds browser.file.upload and browser.file.download. File actions remain
            artifact-scoped and approval-governed.
          </small>
        </span>
      </label>

      <details className="quick-start-details">
        <summary>Advanced network access <span>keep off for normal websites</span></summary>
        <label className="quick-identity-options">
          <span>
            <input
              type="checkbox"
              checked={value.allowPrivateNetwork}
              onChange={(event) => set('allowPrivateNetwork', event.target.checked)}
            />
            <strong> Allow private/internal network destinations</strong>
            <small>
              Off by default. When off, localhost, loopback, private/link-local addresses,
              cloud metadata endpoints and DNS names resolving to internal IPs are blocked.
            </small>
          </span>
        </label>
      </details>
    </>
  );
}
