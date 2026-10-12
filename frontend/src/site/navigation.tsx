// Links on the public site are real <a href> elements. When a navigation handler is provided,
// plain left-clicks on site paths are handled in the page (no full reload); modified clicks,
// new tabs and external links keep normal browser behaviour.
import {createContext, useContext, type ComponentPropsWithRef, type MouseEvent, type ReactNode} from 'react';

type Navigate = (href: string) => void;
const NavigationContext = createContext<Navigate | null>(null);
export function NavigationProvider({navigate, children}: {navigate: Navigate | null; children: ReactNode}) {
  return <NavigationContext.Provider value={navigate}>{children}</NavigationContext.Provider>;
}

/** Paths the authenticated application owns; always loaded with a full page request. */
const APPLICATION = /^\/(app|login|signup|workspace-preview|invite|auth)(\/|$|\?|#)/;
export const isExternal = (href: string) => /^https?:\/\//.test(href);

type Props = Omit<ComponentPropsWithRef<'a'>, 'href'> & {href: string; newTab?: boolean; children: ReactNode};
export function SiteLink({href, newTab, onClick, children, ...rest}: Props) {
  const navigate = useContext(NavigationContext);
  const external = isExternal(href);
  function handle(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event);
    if (event.defaultPrevented || !navigate || external || newTab || APPLICATION.test(href)) return;
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(href);
  }
  return (
    <a href={href} onClick={handle} {...(newTab || external ? {target: newTab ? '_blank' : undefined, rel: 'noopener noreferrer'} : {})} {...rest}>
      {children}
    </a>
  );
}
