// Built-in content: the complete site in the audoryn.public.v2 format. Used whenever Console has
// not published a valid v2 document for a page, and the source for the Console seed.
import type {PageDocument, PageName, PageOf, Site} from '../../contract/contract.generated';
import site from './site.json';
import home from './home.json';
import product from './product.json';
import solutions from './solutions.json';
import security from './security.json';
import pricing from './pricing.json';
import resources from './resources.json';
import company from './company.json';
import privacy from './privacy.json';
import terms from './terms.json';

export type DefaultPage = Exclude<PageName, 'article'>;
export const defaultSite = site as Site;
export const defaultPages = {home, product, solutions, security, pricing, resources, company, privacy, terms} as unknown as {[P in DefaultPage]: PageOf<P>};
export const defaultPage = (page: DefaultPage): PageDocument => defaultPages[page];

/** Seed media: stable IDs shared with the Console seed, mapped to the bundled files. */
export const defaultMedia: Record<string, {file: string; width: number; height: number}> = {
  'de5cb793-7e09-5703-8021-c31ecd15ab0c': {file: 'human-oversight.webp', width: 1774, height: 887},
  '262bf108-0e0b-58ab-835f-9bd73d8c353c': {file: 'operations-work.webp', width: 1672, height: 941},
  'a201f4ad-5d40-51eb-8948-a5c1b09319d1': {file: 'research-work.webp', width: 1774, height: 887},
};

/** Built-in page metadata, used when Console has not published SEO fields for a page. */
const description = 'Audoryn is an SOT product for creating, operating and governing an AI workforce with explicit human control.';
export const defaultSeo: Record<DefaultPage, {title: string; description: string}> = {
  home: {title: 'Audoryn · AI workforce, under control', description},
  product: {title: 'Product · Audoryn', description},
  solutions: {title: 'Solutions · Audoryn', description},
  security: {title: 'Security · Audoryn', description},
  pricing: {title: 'Pricing · Audoryn', description},
  resources: {title: 'Resources · Audoryn', description},
  company: {title: 'Company · Audoryn', description},
  privacy: {title: 'Privacy · Audoryn', description},
  terms: {title: 'Terms · Audoryn', description},
};
