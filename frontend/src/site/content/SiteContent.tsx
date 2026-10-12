// Shared site content (brand, navigation, labels, vocabulary) and media resolution for the
// public site. Phase 3 fills this from verified published releases; until then, and whenever
// no valid published document exists, the built-in v2 content is used.
import {createContext, useContext, type ReactNode} from 'react';
import type {Image, Site} from '../contract/contract.generated';
import {defaultMedia, defaultSite} from './defaults';

export type ResolvedImage = {src: string; alt: string; width?: number; height?: number};
export type MediaResolver = (image: Image) => ResolvedImage | null;

/** Built-in images: the seed asset IDs map to files bundled with the site. */
const bundled = import.meta.glob<string>('../assets/*.webp', {eager: true, import: 'default'});
export const builtInMedia: MediaResolver = image => {
  const entry = defaultMedia[image.asset];
  if (!entry) return null;
  const src = Object.entries(bundled).find(([file]) => file.endsWith('/' + entry.file))?.[1];
  return src ? {src, alt: image.alt, width: entry.width, height: entry.height} : null;
};

type SiteContentValue = {site: Site; media: MediaResolver; path: string};
const Context = createContext<SiteContentValue>({site: defaultSite, media: builtInMedia, path: '/'});

export function SiteContentProvider({site = defaultSite, media = builtInMedia, path = '/', children}: Partial<SiteContentValue> & {children: ReactNode}) {
  return <Context.Provider value={{site, media, path}}>{children}</Context.Provider>;
}
export const useSite = () => useContext(Context).site;
export const useVocabulary = () => useContext(Context).site.vocabulary;
export const useCurrentPath = () => useContext(Context).path;
export const useMedia = () => useContext(Context).media;

/** Replaces {name} placeholders. Content guarantees the placeholder exists. */
export const fill = (template: string, values: Record<string, string | number>) =>
  template.replace(/\{(\w+)\}/g, (match, name: string) => (name in values ? String(values[name]) : match));
