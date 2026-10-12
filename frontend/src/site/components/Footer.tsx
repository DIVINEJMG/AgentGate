import type {CSSProperties} from 'react';
import {useSite} from '../content/SiteContent';
import {SiteLink} from '../navigation';
import './footer.css';

export function Footer() {
  const site = useSite();
  const groups = site.navigation.footer;
  // The footer keeps its height: more groups narrow the columns, more links tighten the rows.
  const density = {'--footer-groups': groups.length, '--footer-links': Math.max(...groups.map(group => group.children.length))} as CSSProperties;
  return (
    <footer className='site-footer'>
      <div className='site-footer-frame'>
        <div className='site-footer-grid'>
          <div className='site-footer-brand'>
            <SiteLink href='/' className='site-wordmark' aria-label={site.labels.home}>
              <img src='/audoryn-mark.png' alt='' width={22} height={22} />
              <span>{site.brand.name}</span>
            </SiteLink>
            <p>{site.brand.tagline}</p>
            <p>{site.brand.owner}</p>
          </div>
          <nav aria-label={site.labels.footerNavigation} className='site-footer-nav' style={density}>
            {groups.map(group => (
              <div key={group.id}>
                <h2>{group.label}</h2>
                <ul>
                  {group.children.map(link => <li key={link.id}><SiteLink href={link.href} newTab={link.newTab}>{link.label}</SiteLink></li>)}
                </ul>
              </div>
            ))}
          </nav>
        </div>
        <div className='site-footer-bottom'>
          <span>{site.brand.legal}</span>
          <span>{site.brand.name} · {site.brand.owner}</span>
        </div>
      </div>
    </footer>
  );
}
