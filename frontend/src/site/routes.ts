// The public site's paths: the nine fixed pages, and which paths may hold published content.
import type {DefaultPage} from './content/defaults';

/** The fixed pages and their paths. They always render: published content or built-in content. */
export const FIXED_PAGES: Record<string, DefaultPage> = {
  '/': 'home', '/product': 'product', '/solutions': 'solutions', '/security': 'security', '/pricing': 'pricing',
  '/resources': 'resources', '/company': 'company', '/privacy': 'privacy', '/terms': 'terms',
};

export const normalizePath = (path: string) => path.replace(/\/+$/, '') || '/';

/** Paths the application or the platform own; never public content. */
const RESERVED = /^\/(api|app|login|signup|workspace-preview|invite|auth|_public|_cms)(\/|$)/;

/** A content path is a local path without query, fragment, control characters or reserved prefixes. */
export function isContentPath(value: string) {
  return value.startsWith('/') && !value.startsWith('//') && !/[\\\u0000-\u001f?#]/.test(value) && !/%2f|%5c|%2e/i.test(value) && !RESERVED.test(value);
}
