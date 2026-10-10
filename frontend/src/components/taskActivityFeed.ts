// Progress delivery is independent of task success: failures preserve the last snapshot.
export function startActivityFeed<T>(options: {
  load: (signal: AbortSignal) => Promise<T>;
  onSnapshot: (value: T) => void;
  onConnectivity: (connected: boolean) => void;
  intervalMs?: number; timeoutMs?: number;
  nextIntervalMs?: (value: T) => number;
}) {
  let active = true, inFlight = false;
  let interval = options.intervalMs ?? 10000;
  let poll: ReturnType<typeof setTimeout>;
  let controller: AbortController | null = null;
  const refresh = async () => {
    if (!active || inFlight || (typeof document !== 'undefined' && document.hidden)) return;
    clearTimeout(poll);
    inFlight = true;
    const requestController = new AbortController();
    controller = requestController;
    const deadline = setTimeout(() => requestController.abort(), options.timeoutMs ?? 20000);
    try {
      const result = await options.load(requestController.signal);
      if (active) {
        interval = options.nextIntervalMs?.(result) ?? interval;
        options.onSnapshot(result); options.onConnectivity(true);
      }
    } catch {
      if (active) options.onConnectivity(false);
    } finally {
      clearTimeout(deadline); inFlight = false;
      if (active) poll = setTimeout(() => void refresh(), interval);
    }
  };
  const visible = () => { if (!document.hidden) void refresh(); };
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', visible);
  void refresh();
  return {refresh, stop: () => {
    active = false; controller?.abort(); clearTimeout(poll);
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', visible);
  }};
}
