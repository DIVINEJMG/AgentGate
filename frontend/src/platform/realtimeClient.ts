import { platformConfig } from './config';

export function createRealtimeSocket(path = '/api/v2/realtime') {
  if (!platformConfig.realtimeUrl) throw new Error('VITE_REALTIME_URL is not configured.');
  const base = platformConfig.realtimeUrl.replace(/\/+$/, '');
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return new WebSocket(`${base}${normalizedPath}`);
}
