// Page frame: skip link, announcement, navigation, main content and footer.
import type {ReactNode} from 'react';
import '@fontsource-variable/geist';
import '@fontsource-variable/geist-mono';
import '@fontsource-variable/newsreader/opsz.css';
import '../styles/tokens.css';
import '../styles/base.css';
import {useSite} from '../content/SiteContent';
import {Footer} from './Footer';
import {Header} from './Header';

export function SiteShell({children}: {children: ReactNode}) {
  const site = useSite();
  return (
    <div className='site'>
      <a className='site-skip' href='#main'>{site.labels.skipToContent}</a>
      <Header />
      <main id='main' className='site-main' tabIndex={-1}>{children}</main>
      <Footer />
    </div>
  );
}
