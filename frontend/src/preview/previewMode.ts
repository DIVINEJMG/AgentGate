export function isWorkspacePreview(): boolean {
  const route = window.location.hash.replace(/^#\/?/, '').split('?')[0].replace(/\/+$/, '');
  return import.meta.env.DEV && (route === 'workspace-preview' || window.location.pathname === '/workspace-preview' || window.location.pathname.startsWith('/workspace-preview/'));
}
