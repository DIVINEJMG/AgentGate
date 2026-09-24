import { apiUrl, realtimeUrl } from './config';

export interface RealtimeEvent {
  stream_id?: string;
  event_id?: string;
  event_type: string;
  organization_id?: string;
  worker_id?: string | null;
  job_id?: string | null;
  run_id?: string | null;
  resource_id?: string | null;
  correlation_id?: string | null;
  timestamp?: string;
  payload?: Record<string, unknown>;
}

export type RealtimeTransport = 'connecting' | 'websocket' | 'sse' | 'polling';

const NORMALIZED_EVENT_TYPES = [
  'run.created',
  'run.started',
  'run.progress',
  'run.step.started',
  'run.step.completed',
  'run.waiting_approval',
  'run.failed',
  'run.completed',
  'result.created',
  'result.updated',
  'worker.status.changed',
  'action.proposed',
  'action.approved',
  'action.blocked',
  'action.executed',
  'approval.created',
  'approval.decided',
  'incident.created',
  'incident.updated',
  'integration.health.changed',
] as const;

interface SubscriptionOptions {
  organizationId: string;
  eventTypes?: Iterable<string>;
  onEvent: (event: RealtimeEvent) => void;
  onTransportChange?: (transport: RealtimeTransport) => void;
  poll?: () => void | Promise<void>;
  pollingIntervalMs?: number;
}

function accessToken() {
  return window.localStorage.getItem('audoryn.access_token');
}

function cursorKey(organizationId: string) {
  return `audoryn.realtime.cursor.${organizationId}`;
}

function readCursor(organizationId: string) {
  return window.localStorage.getItem(cursorKey(organizationId));
}

function writeCursor(organizationId: string, cursor: string | undefined) {
  if (cursor) window.localStorage.setItem(cursorKey(organizationId), cursor);
}

function parseEvent(value: string): RealtimeEvent | null {
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>;
    const eventType =
      typeof parsed.event_type === 'string'
        ? parsed.event_type
        : typeof parsed.type === 'string'
          ? parsed.type
          : '';
    if (!eventType) return null;
    return { ...parsed, event_type: eventType } as RealtimeEvent;
  } catch {
    return null;
  }
}

function accepts(event: RealtimeEvent, allowed: Set<string> | null) {
  return allowed === null || allowed.has(event.event_type);
}

export function subscribeOrganizationRealtime({
  organizationId,
  eventTypes,
  onEvent,
  onTransportChange,
  poll,
  pollingIntervalMs = 15000,
}: SubscriptionOptions) {
  let closed = false;
  let socket: WebSocket | null = null;
  let source: EventSource | null = null;
  let pollTimer: number | null = null;
  let fallbackTimer: number | null = null;
  const allowed = eventTypes ? new Set(eventTypes) : null;
  const token = accessToken();
  const seen = new Set<string>();

  const changeTransport = (transport: RealtimeTransport) => {
    if (!closed) onTransportChange?.(transport);
  };

  const deliver = (event: RealtimeEvent) => {
    if (event.organization_id && event.organization_id !== organizationId) return;
    if (event.event_id && seen.has(event.event_id)) return;
    if (event.event_id) {
      seen.add(event.event_id);
      if (seen.size > 512) {
        const oldest = seen.values().next().value;
        if (typeof oldest === 'string') seen.delete(oldest);
      }
    }
    writeCursor(organizationId, event.stream_id);
    if (accepts(event, allowed)) onEvent(event);
  };

  const stopPolling = () => {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const startPolling = () => {
    if (closed || pollTimer !== null || !poll) return;
    changeTransport('polling');
    void poll();
    pollTimer = window.setInterval(() => void poll(), pollingIntervalMs);
  };

  const startSse = () => {
    if (closed || source || !token) {
      if (!token) startPolling();
      return;
    }
    const query = new URLSearchParams({
      access_token: token,
      cursor: readCursor(organizationId) ?? '$',
    });
    source = new EventSource(
      apiUrl(`/api/v2/organizations/${organizationId}/events/stream?${query.toString()}`),
    );
    source.onopen = () => {
      stopPolling();
      changeTransport('sse');
    };
    const receive = (message: Event) => {
      const realtimeMessage = message as MessageEvent<string>;
      const event = parseEvent(realtimeMessage.data);
      if (!event) return;
      if (!event.stream_id && realtimeMessage.lastEventId) {
        event.stream_id = realtimeMessage.lastEventId;
      }
      deliver(event);
    };
    source.onmessage = receive;
    for (const eventType of NORMALIZED_EVENT_TYPES) {
      source.addEventListener(eventType, receive);
    }
    source.onerror = () => {
      source?.close();
      source = null;
      startPolling();
    };
  };

  const startWebSocket = () => {
    if (closed || !token) {
      if (!token) startPolling();
      return;
    }
    const query = new URLSearchParams({ access_token: token });
    socket = new WebSocket(
      realtimeUrl(`/ws/v1/organizations/${organizationId}?${query.toString()}`),
    );
    socket.onopen = () => {
      if (fallbackTimer !== null) window.clearTimeout(fallbackTimer);
      fallbackTimer = null;
      stopPolling();
      changeTransport('websocket');
    };
    socket.onmessage = (message) => {
      const event = parseEvent(String(message.data));
      if (event) deliver(event);
    };
    socket.onerror = () => {
      if (socket?.readyState !== WebSocket.OPEN) startSse();
    };
    socket.onclose = () => {
      if (!closed) startSse();
    };
    fallbackTimer = window.setTimeout(() => {
      if (socket?.readyState !== WebSocket.OPEN) startSse();
    }, 2500);
  };

  const replayThenConnect = async () => {
    if (!token) {
      startPolling();
      return;
    }
    const cursor = readCursor(organizationId);
    if (cursor) {
      try {
        const query = new URLSearchParams({ cursor, limit: '500' });
        const response = await fetch(
          apiUrl(`/api/v2/organizations/${organizationId}/events?${query.toString()}`),
          { headers: { Authorization: `Bearer ${token}` } },
        );
        if (response.ok) {
          const payload = (await response.json()) as {
            events?: RealtimeEvent[];
            cursor?: string;
          };
          for (const event of payload.events ?? []) deliver(event);
          writeCursor(organizationId, payload.cursor);
        }
      } catch {
        // WebSocket/SSE/polling still provide bounded fallback below.
      }
    }
    startWebSocket();
  };

  changeTransport('connecting');
  void replayThenConnect();

  return () => {
    closed = true;
    if (fallbackTimer !== null) window.clearTimeout(fallbackTimer);
    stopPolling();
    source?.close();
    socket?.close();
  };
}
