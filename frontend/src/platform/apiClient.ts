import { apiUrl } from './config';
import { isWorkspacePreview } from '../preview/previewMode';

export class ApiClientError extends Error {
  status: number;
  payload: unknown;

  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = 'ApiClientError';
    this.status = status;
    this.payload = payload;
  }
}

type ApiResponse<T = any> = { data: T; status: number; headers: Headers };

function readAccessToken() {
  return window.localStorage.getItem('audoryn.access_token');
}

async function request<T = any>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<ApiResponse<T>> {
  if (import.meta.env.DEV && isWorkspacePreview()) {
    const { previewResponse } = await import('../preview/previewResponses');
    // The sample workspace emits the same request events, and can simulate a slow API
    // (sessionStorage 'audoryn.preview.latency' in ms) to show the loading states.
    window.dispatchEvent(new CustomEvent('audoryn:request', { detail: { phase: 'start', method } }));
    try {
      const latency = Number(window.sessionStorage.getItem('audoryn.preview.latency') ?? 0);
      if (latency > 0) await new Promise((resolve) => window.setTimeout(resolve, Math.min(latency, 15_000)));
      return await previewResponse(method, path, body) as ApiResponse<T>;
    } finally {
      window.dispatchEvent(new CustomEvent('audoryn:request', { detail: { phase: 'end', method } }));
    }
  }
  const headers = new Headers({ Accept: 'application/json' });
  const token = readAccessToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (body !== undefined) headers.set('Content-Type', 'application/json');

  // Request lifecycle events drive the workspace progress bar and outage notices.
  window.dispatchEvent(new CustomEvent('audoryn:request', { detail: { phase: 'start', method } }));
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      method,
      headers,
      credentials: 'include',
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    const aborted = error instanceof DOMException && error.name === 'AbortError';
    window.dispatchEvent(new CustomEvent('audoryn:request', { detail: { phase: 'end', method, failure: aborted ? undefined : 'network' } }));
    throw error;
  }
  window.dispatchEvent(new CustomEvent('audoryn:request', { detail: { phase: 'end', method, status: response.status, failure: response.status >= 500 ? 'server' : undefined } }));

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = text; }
  }

  if (!response.ok) {
    const message =
      typeof payload === 'object' && payload !== null && 'message' in payload &&
      typeof (payload as { message?: unknown }).message === 'string'
        ? (payload as { message: string }).message
        : typeof payload === 'object' && payload !== null && 'detail' in payload &&
          typeof (payload as { detail?: unknown }).detail === 'string'
          ? (payload as { detail: string }).detail
          : `Request failed with status ${response.status}.`;
    throw new ApiClientError(message, response.status, payload);
  }

  return { data: payload as T, status: response.status, headers: response.headers };
}

export const api = Object.freeze({
  get: <T = any>(path: string, options?: {signal?: AbortSignal}) => request<T>('GET', path, undefined, options?.signal),
  post: <T = any>(path: string, body?: unknown) => request<T>('POST', path, body),
  put: <T = any>(path: string, body?: unknown) => request<T>('PUT', path, body),
  delete: <T = any>(path: string, body?: unknown) => request<T>('DELETE', path, body),
});
