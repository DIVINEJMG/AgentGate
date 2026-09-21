function trimTrailingSlashes(value: string) {
  return value.replace(/\/+$/, '');
}

const configuredApiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() ?? '';
const configuredAuthBase = (import.meta.env.VITE_AUTH_BASE_URL as string | undefined)?.trim() ?? '';
const configuredRealtimeUrl = (import.meta.env.VITE_REALTIME_URL as string | undefined)?.trim() ?? '';

export const platformConfig = Object.freeze({
  apiBaseUrl: trimTrailingSlashes(configuredApiBase),
  authBaseUrl: trimTrailingSlashes(configuredAuthBase || configuredApiBase),
  realtimeUrl: configuredRealtimeUrl,
});

export function apiUrl(path: string) {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${platformConfig.apiBaseUrl}${normalizedPath}`;
}

export function authUrl(path: string) {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${platformConfig.authBaseUrl}${normalizedPath}`;
}
