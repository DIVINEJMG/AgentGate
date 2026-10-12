// Runtime settings for loading published content, read from the frontend host environment.
// Same variables as the current site; both renderers read the same Console release.
export type SiteConfig = {
  enabled: boolean;
  /** Console public storage origin (may include a path prefix). */
  origin: string;
  /** This site's public origin, used for canonical links and the sitemap. */
  siteOrigin: string;
  locale: string;
  cacheSeconds: number;
  timeoutSeconds: number;
  /** The Console origin allowed to send preview content. */
  previewOrigin: string;
};

function checkOrigin(value: string, allowPath: boolean) {
  const url = new URL(value);
  const localHttp = url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname);
  if (url.username || url.password || url.search || url.hash || (!allowPath && url.pathname !== '/') || /%2f|%5c|%2e/i.test(url.pathname) || !(url.protocol === 'https:' || localHttp)) {
    throw new Error('invalid_public_origin');
  }
  return url;
}

function seconds(env: Record<string, string | undefined>, name: string, fallback: number, max: number) {
  const value = Number(env[name] ?? fallback);
  if (!Number.isFinite(value) || value < 1 || value > max) throw new Error(`invalid_${name.toLowerCase()}`);
  return value;
}

export function siteConfigFromEnvironment(env: Record<string, string | undefined>): SiteConfig {
  const origin = env.PUBLIC_CONTENT_ORIGIN ? checkOrigin(env.PUBLIC_CONTENT_ORIGIN, true).href.replace(/\/$/, '') : '';
  const siteOrigin = env.PUBLIC_SITE_ORIGIN ? checkOrigin(env.PUBLIC_SITE_ORIGIN, false).origin : '';
  const previewOrigin = env.PUBLIC_CONTENT_PREVIEW_ORIGIN ? checkOrigin(env.PUBLIC_CONTENT_PREVIEW_ORIGIN, false).origin : '';
  return {
    enabled: env.PUBLIC_CONTENT_ENABLED === 'true' && Boolean(origin),
    origin,
    siteOrigin,
    locale: env.PUBLIC_CONTENT_DEFAULT_LOCALE || 'en',
    cacheSeconds: seconds(env, 'PUBLIC_CONTENT_POINTER_CACHE_SECONDS', 60, 3600),
    timeoutSeconds: seconds(env, 'PUBLIC_CONTENT_FETCH_TIMEOUT_SECONDS', 5, 60),
    previewOrigin,
  };
}
