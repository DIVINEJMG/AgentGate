export interface NotificationEnvironment {
  supported: boolean;
  isSupported: boolean;
  permission: NotificationPermission | 'unsupported';
  subscribed: boolean;
}

type NotificationPayload = { data: Record<string, unknown> };

function environment(): NotificationEnvironment {
  const supported = typeof window !== 'undefined' && 'Notification' in window;
  return {
    supported,
    isSupported: supported,
    permission: supported ? Notification.permission : 'unsupported',
    subscribed: supported && Notification.permission === 'granted',
  };
}

export const notifications = Object.freeze({
  async getEnvironment() {
    return environment();
  },

  async subscribe() {
    if ('Notification' in window && Notification.permission === 'default') {
      await Notification.requestPermission();
    }
    return environment();
  },

  onMessage(callback: (payload: NotificationPayload) => void) {
    const handler = (event: Event) => callback((event as CustomEvent<NotificationPayload>).detail ?? { data: {} });
    window.addEventListener('audoryn:notification', handler);
    return () => window.removeEventListener('audoryn:notification', handler);
  },
});
