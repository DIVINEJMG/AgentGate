import { useEffect, useMemo } from 'react';
import { File, FileCode, FileImage, FileSpreadsheet, FileText, LoaderCircle, X, type LucideIcon } from 'lucide-react';

/** What the composer accepts: at most this many files, each at most this size (the server's
 * conversation_attachment_max_bytes). */
export const MAX_ATTACHMENTS = 5;
export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

type Kind = { label: string; icon: LucideIcon; tone: string };
const KINDS: Array<[RegExp, Kind]> = [
  [/^image\/|\.(png|jpe?g|webp|gif)$/i, { label: 'Image', icon: FileImage, tone: 'image' }],
  [/pdf$/i, { label: 'PDF', icon: FileText, tone: 'pdf' }],
  [/spreadsheet|\.xlsx$/i, { label: 'Spreadsheet', icon: FileSpreadsheet, tone: 'sheet' }],
  [/csv$/i, { label: 'CSV', icon: FileSpreadsheet, tone: 'sheet' }],
  [/json$/i, { label: 'JSON', icon: FileCode, tone: 'code' }],
  [/python|typescript|javascript|\.(py|js|ts|tsx)$/i, { label: 'Code', icon: FileCode, tone: 'code' }],
  [/markdown|\.md$/i, { label: 'Markdown', icon: FileText, tone: 'text' }],
  [/^text\/|\.txt$/i, { label: 'Text', icon: FileText, tone: 'text' }],
];

export function fileKind(name: string, mediaType?: string): Kind {
  const match = KINDS.find(([pattern]) => (mediaType && pattern.test(mediaType)) || pattern.test(name));
  return match ? match[1] : { label: (name.split('.').pop() || 'File').toUpperCase(), icon: File, tone: 'other' };
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

/** The files to keep after adding `picked`: duplicates (same name and size) and files over the size
 * limit are left out, and at most MAX_ATTACHMENTS are kept. Returns why any were left out. */
export function addFiles(current: File[], picked: File[]): { files: File[]; problem: string | null } {
  const files = [...current];
  const problems: string[] = [];
  for (const file of picked) {
    if (files.some(item => item.name === file.name && item.size === file.size)) continue;
    if (file.size > MAX_ATTACHMENT_BYTES) { problems.push(`${file.name} is over ${formatBytes(MAX_ATTACHMENT_BYTES)}.`); continue; }
    if (files.length >= MAX_ATTACHMENTS) { problems.push(`Up to ${MAX_ATTACHMENTS} files per message.`); break; }
    files.push(file);
  }
  return { files, problem: problems.join(' ') || null };
}

type Props = {
  name: string; mediaType?: string; size?: number;
  /** A local file: images show a thumbnail. */
  file?: File;
  uploading?: boolean;
  onRemove?: () => void;
  onOpen?: () => void;
};

/** One file, as a card: its type, name and size; removable in the composer, openable in a message. */
export function AttachmentCard({ name, mediaType, size, file, uploading, onRemove, onOpen }: Props) {
  const kind = fileKind(name, mediaType ?? file?.type);
  const preview = useMemo(() => (file && kind.tone === 'image' ? URL.createObjectURL(file) : null), [file, kind.tone]);
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);
  const Icon = kind.icon;
  const content = <>
    <span className="cf-file-icon" aria-hidden="true">{preview ? <img src={preview} alt=""/> : <Icon size={18} strokeWidth={1.7}/>}</span>
    <span className="cf-file-text">
      <strong title={name}>{name}</strong>
      <small>{uploading ? 'Uploading…' : [kind.label, size ? formatBytes(size) : null].filter(Boolean).join(' · ')}</small>
    </span>
  </>;
  return <div className={'cf-file' + (onOpen ? ' is-openable' : '')} data-tone={kind.tone}>
    {onOpen ? <button type="button" className="cf-file-open" onClick={onOpen} title={`Open ${name}`}>{content}</button> : content}
    {uploading ? <LoaderCircle className="cf-file-spinner" size={16} aria-label="Uploading"/>
      : onRemove && <button type="button" className="cf-file-remove" onClick={onRemove} aria-label={`Remove ${name}`} title="Remove"><X size={14} strokeWidth={2}/></button>}
  </div>;
}
