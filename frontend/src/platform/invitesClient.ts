const PENDING_INVITE_KEY = 'audoryn.pending_invitation';

function codeFromLocation() {
  const params = new URLSearchParams(window.location.search);
  return params.get('invite') ?? params.get('code');
}

export const invitesClient = Object.freeze({
  buildJoinUrl(code: string) {
    const url = new URL(window.location.origin);
    url.searchParams.set('invite', code);
    return url.toString();
  },

  getPendingCode() {
    const fromUrl = codeFromLocation();
    if (fromUrl) {
      window.sessionStorage.setItem(PENDING_INVITE_KEY, fromUrl);
      return fromUrl;
    }
    return window.sessionStorage.getItem(PENDING_INVITE_KEY);
  },

  clearPendingCode() {
    window.sessionStorage.removeItem(PENDING_INVITE_KEY);
  },
});
