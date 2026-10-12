// Development-only preview of the redesigned public pages with built-in content.
// Open /site-preview.html?page=product on the Vite dev server. Not part of the production build.
// Add &site=limits to use a site document with every navigation list at its contract maximum.
import {defaultPages, type DefaultPage} from '../content/defaults';
import {defaultBundle} from '../content/bundle';
import {FIXED_PAGES} from '../routes';
import {SiteDocument} from '../SiteApp';
import type {Site} from '../contract/contract.generated';
import limits from '../contract/examples/site.limits.json';

export function Preview({page = 'home', site}: {page?: DefaultPage; site?: 'limits'}) {
  const name = page in defaultPages ? page : 'home';
  const path = Object.entries(FIXED_PAGES).find(([, value]) => value === name)?.[0] ?? '/';
  const bundle = defaultBundle(path, name, 'en', []);
  return <SiteDocument bundle={site === 'limits' ? {...bundle, site: limits as Site} : bundle} navigate={null} />;
}
