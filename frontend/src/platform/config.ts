function trimTrailingSlashes(value: string) {
  return value.replace(/\/+$/, '');
}

const configuredApiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() ?? '';
const developmentApiBase = import.meta.env.DEV ? 'http://localhost:8000' : '';

export const platformConfig = Object.freeze({
  apiBaseUrl: trimTrailingSlashes(configuredApiBase || developmentApiBase),
});

export function apiUrl(path: string) {
  if (!platformConfig.apiBaseUrl) {
    throw new Error('VITE_API_BASE_URL is required outside local development.');
  }
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${platformConfig.apiBaseUrl}${normalizedPath}`;
}

export function authUrl(path: string) {
  return apiUrl(path);
}

export function realtimeUrl(path: string) {
  const base = apiUrl('/').replace(/\/$/, '');
  const socketBase = base.replace(/^https:/, 'wss:').replace(/^http:/, 'ws:');
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${socketBase}${normalizedPath}`;
}
