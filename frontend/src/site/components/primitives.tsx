// Building blocks shared by every section: the framed section, headings, actions, labels.
import type {ReactNode} from 'react';
import {ArrowRight, ArrowUpRight, Check, Pause, X} from 'lucide-react';
import type {Decision, Image, Link} from '../contract/contract.generated';
import {useMedia, useSite} from '../content/SiteContent';
import {isExternal, SiteLink} from '../navigation';
import './primitives.css';

/** Section key → anchor id: controlPath → control-path. */
export const anchorOf = (key: string) => key.replace(/[A-Z]/g, c => '-' + c.toLowerCase());

export type Tone = 'cream' | 'sage' | 'night' | 'forest';

/** A full-width band whose content sits inside the blueprint frame. */
export function Section({id, tone = 'cream', label, className, children}: {id: string; tone?: Tone; label?: string; className?: string; children: ReactNode}) {
  return (
    <section id={anchorOf(id)} aria-label={label} className={`site-section is-${tone}${className ? ' ' + className : ''}`}>
      <div className='site-frame'>
        <span className='site-cross is-left' aria-hidden='true' />
        <span className='site-cross is-right' aria-hidden='true' />
        {children}
      </div>
    </section>
  );
}

export function Kicker({children}: {children?: string}) {
  return children ? <p className='site-kicker'>{children}</p> : null;
}

/** Headline with an optional quieter second clause; one element for assistive technology. */
export function Headline({as: Tag = 'h2', headline, detail, size = 'h2'}: {as?: 'h1' | 'h2' | 'h3' | 'p'; headline: string; detail?: string; size?: 'hero' | 'h2' | 'statement'}) {
  return (
    <Tag className={`site-headline is-${size}`}>
      {headline}
      {detail && <> <span className='site-headline-detail'>{detail}</span></>}
    </Tag>
  );
}

type Heading = {kicker?: string; headline: string; headlineDetail?: string; body?: string};
/** Section opening: kicker and headline, introduction beside (split) or below (stacked). */
export function SectionHeader({heading, layout = 'split'}: {heading: Heading; layout?: 'split' | 'stacked' | 'center'}) {
  return (
    <header className={`site-section-header is-${layout}`}>
      <div>
        <Kicker>{heading.kicker}</Kicker>
        <Headline headline={heading.headline} detail={heading.headlineDetail} />
      </div>
      {heading.body && <p className='site-lead'>{heading.body}</p>}
    </header>
  );
}

type Variant = 'primary' | 'secondary' | 'on-night' | 'on-night-secondary' | 'text';
export function ActionLink({link, variant = 'primary'}: {link: Link; variant?: Variant}) {
  const Arrow = isExternal(link.href) ? ArrowUpRight : ArrowRight;
  return (
    <SiteLink href={link.href} className={`site-action is-${variant}`}>
      <span>{link.label}</span>
      <Arrow size={16} strokeWidth={1.75} aria-hidden='true' />
    </SiteLink>
  );
}

export function Actions({primary, secondary, night}: {primary?: Link; secondary?: Link; night?: boolean}) {
  if (!primary && !secondary) return null;
  return (
    <div className='site-actions'>
      {primary && <ActionLink link={primary} variant={night ? 'on-night' : 'primary'} />}
      {secondary && <ActionLink link={secondary} variant={night ? 'on-night-secondary' : 'secondary'} />}
    </div>
  );
}

/** Document-style label above a visual: "FIG. 03 · The gateway". */
export function FigureLabel({number, label}: {number: number; label: string}) {
  return (
    <p className='site-fig' aria-hidden='true'>
      <span>FIG. {String(number).padStart(2, '0')}</span>
      <span>{label}</span>
    </p>
  );
}

/** "Illustrative" badge followed by the disclosure text from content. */
export function Disclosure({children}: {children: string}) {
  const site = useSite();
  return (
    <p className='site-disclosure'>
      <span className='site-badge'>{site.labels.illustrative}</span>
      <span>{children}</span>
    </p>
  );
}

const DECISION_ICON = {allow: Check, review: Pause, deny: X} as const;
/** A decision shown as a coloured mono tag: colour, icon and word together. */
export function DecisionTag({decision, label}: {decision: Decision; label: string}) {
  const Mark = DECISION_ICON[decision];
  return <span className={`site-tag is-${decision}`}><Mark size={12} strokeWidth={2.4} aria-hidden='true' />{label}</span>;
}

export function Media({image, sizes = '(min-width: 64rem) 50vw, 100vw', className, eager}: {image?: Image; sizes?: string; className?: string; eager?: boolean}) {
  const resolve = useMedia();
  const resolved = image ? resolve(image) : null;
  if (!resolved) return null;
  return <img className={`site-media${className ? ' ' + className : ''}`} src={resolved.src} alt={resolved.alt} width={resolved.width} height={resolved.height} sizes={sizes} loading={eager ? 'eager' : 'lazy'} decoding='async' />;
}
