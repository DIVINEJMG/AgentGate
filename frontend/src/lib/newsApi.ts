import { api } from '../platform/client';
import { isWorkspacePreview } from '../preview/previewMode';
import type { ApiVersion } from './systemApi';

/** "What's new": the Console's announcements (via AgentGate) and the CMS changelog (via the website server). */
export interface NewsAnnouncement {
  id: string; kind: string; title: string; body: string; severity: 'info' | 'success' | 'warning' | 'critical';
  publishedAt: string; endsAt: string | null; cta: { label: string; url: string } | null;
  dismissible: boolean; major: boolean; upgrade: boolean; media: NewsMedia | null;
}
/** Published media for an announcement's window (from the Console); none means the app's own cover. */
export interface NewsMedia { image?: string; imageDark?: string; video?: string; alt: string; width?: number; height?: number }
export interface ChangelogEntry { id: string; path: string; title: string; summary: string; published: string; tag: string | null; image: string | null }
export interface NewsFeed { announcements: NewsAnnouncement[]; state: Record<string, { readAt: string | null; dismissedAt: string | null }>; canUpgrade: boolean }

const unwrap = <T>(version: ApiVersion, data: any): T => (version === 'v1' ? data : data.data) as T;

export async function loadNews(v: ApiVersion, o: string) { return unwrap<NewsFeed>(v, (await api.get(`/api/${v}/organizations/${o}/news`)).data); }
export async function markNews(v: ApiVersion, o: string, keys: string[], mark: 'read' | 'dismissed') {
  if (keys.length) await api.post(`/api/${v}/organizations/${o}/news/state`, { keys: keys.slice(0, 50), mark });
}

/** Published changelog entries; none when the website server has no content to offer. */
export async function loadChangelog(): Promise<ChangelogEntry[]> {
  if (isWorkspacePreview()) return (await import('../preview/previewResponses')).previewChangelog;
  try {
    const response = await fetch('/_public/changelog', { credentials: 'omit', headers: { Accept: 'application/json' } });
    const payload = response.ok ? await response.json() : null;
    return Array.isArray(payload?.entries) ? payload.entries : [];
  } catch { return []; }
}
