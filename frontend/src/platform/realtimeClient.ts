import { realtimeUrl } from './config';

export function createRealtimeSocket(path = '/api/v2/realtime') {
  return new WebSocket(realtimeUrl(path));
}
