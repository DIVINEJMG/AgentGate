// Parts shared by the inner pages (product, solutions, security, pricing): the page hero,
// check lists and tool marks. Same type, spacing and colour rules as the home page.
import type {ReactNode} from 'react';
import {Check} from 'lucide-react';
import type {HeroSection} from '../../contract/contract.generated';
import {Actions, Headline, Kicker, Section, type Tone} from '../../components/primitives';
import {ProviderMark} from '../home/HomeSections';
import './page-parts.css';

/** Opening of an inner page. The aside, when given, sits beside the copy on wide screens. */
export function PageHero({section, tone = 'cream', align = 'left', aside, meta}: {section: HeroSection; tone?: Tone; align?: 'left' | 'center'; aside?: ReactNode; meta?: ReactNode}) {
  const night = tone === 'night' || tone === 'forest';
  return (
    <Section id='hero' tone={tone} className={`page-hero is-${align}${aside ? ' has-aside' : ''}`}>
      <div className='page-hero-grid'>
        <div className='page-hero-copy'>
          <Kicker>{section.kicker}</Kicker>
          <Headline as='h1' size='hero' headline={section.headline} detail={section.headlineDetail} />
          <p className='site-lead page-hero-lead'>{section.body}</p>
          <Actions primary={section.primaryAction} secondary={section.secondaryAction} night={night} />
          {meta}
        </div>
        {aside && <div className='page-hero-aside'>{aside}</div>}
      </div>
    </Section>
  );
}

export function CheckList({items}: {items: string[]}) {
  if (!items.length) return null;
  return <ul className='page-checks'>{items.map(item => <li key={item}><Check size={14} strokeWidth={2.2} aria-hidden='true' />{item}</li>)}</ul>;
}

/** Tool names from content shown with their provider mark when there is one. */
const TOOL_MARKS: Record<string, string> = {github: 'github', gmail: 'gmail', 'google drive': 'google-drive', slack: 'slack', 'google calendar': 'google-calendar'};
export function ToolChip({name}: {name: string}) {
  return <span className='page-tool'><ProviderMark provider={TOOL_MARKS[name.toLowerCase()] ?? 'browser'} />{name}</span>;
}
