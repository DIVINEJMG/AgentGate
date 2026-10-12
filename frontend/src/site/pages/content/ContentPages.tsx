// Resources, company, legal documents and articles. Reading-first pages in the same design
// language: cream surfaces, the blueprint frame, signal colours only where something is live.
import {ArrowLeft, ArrowRight, ArrowUpRight} from 'lucide-react';
import type {ArticleSection, CardSection, LegalSection, PageOf, ProfileSection, ProseSection} from '../../contract/contract.generated';
import {useSite, useVocabulary} from '../../content/SiteContent';
import {Icon} from '../../components/Icon';
import {ActionLink, FigureLabel, Headline, Kicker, Media, Section, SectionHeader} from '../../components/primitives';
import {isExternal, SiteLink} from '../../navigation';
import {Closing} from '../home/HomeSections';
import {PageHero} from '../shared/PageParts';
import './content.css';

const formatDate = (value: string) => new Intl.DateTimeFormat('en-GB', {dateStyle: 'long', timeZone: 'UTC'}).format(new Date(value + 'T00:00:00Z'));

/* ---- Resources ------------------------------------------------------------------------------ */
export function ResourcesPage({page, articles}: {page: PageOf<'resources'>; articles: {path: string; title: string}[]}) {
  const s = page.sections;
  return (
    <>
      <PageHero section={s.hero} align='center' />
      <Guides section={s.guides} />
      {s.library && articles.length > 0 && (
        <Section id='library' tone='sage'>
          <SectionHeader heading={s.library} />
          <ul className='content-library'>
            {articles.map(article => (
              <li key={article.path}>
                <a href={article.path}><Icon name='document' size={18} /><span>{article.title}</span><ArrowRight size={16} aria-hidden='true' /></a>
              </li>
            ))}
          </ul>
        </Section>
      )}
      <Operators section={s.operators} />
    </>
  );
}

/** Linked cards in a framed grid; external links open in a new tab and say so with their arrow. */
function Guides({section, id = 'guides', figure = 1}: {section: CardSection; id?: string; figure?: number}) {
  return (
    <Section id={id}>
      <SectionHeader heading={section} />
      <FigureLabel number={figure} label={section.kicker ?? section.headline} />
      <ul className='content-cards' data-count={section.items.length}>
        {section.items.map((card, index) => {
          const external = isExternal(card.link.href);
          const Arrow = external ? ArrowUpRight : ArrowRight;
          return (
            <li key={card.id}>
              <SiteLink href={card.link.href} newTab={external} className='content-card'>
                <span className='content-card-top'><span className='content-card-index'>{String(index + 1).padStart(2, '0')}</span><Icon name={card.icon} size={20} /></span>
                {card.kicker && <span className='content-card-kicker'>{card.kicker}</span>}
                <strong>{card.title}</strong>
                <span className='content-card-body'>{card.body}</span>
                <span className='content-card-link'>{card.link.label}<Arrow size={15} aria-hidden='true' /></span>
              </SiteLink>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

function Operators({section}: {section: ProseSection}) {
  return (
    <Section id='operators' tone='night'>
      <div className='content-operators'>
        <div>
          <Kicker>{section.kicker}</Kicker>
          <Headline headline={section.headline} detail={section.headlineDetail} />
        </div>
        <div>
          {section.paragraphs.map(paragraph => <p key={paragraph} className='site-lead'>{paragraph}</p>)}
          {section.link && <div className='site-actions'><ActionLink link={section.link} variant='on-night' /></div>}
        </div>
      </div>
    </Section>
  );
}

/* ---- Company -------------------------------------------------------------------------------- */
export function CompanyPage({page}: {page: PageOf<'company'>}) {
  const s = page.sections;
  const words = useVocabulary();
  return (
    <>
      <PageHero section={s.hero} aside={<div className='content-mark'><img src='/sot-mark.png' alt={words.ownerLogo} width={160} height={160} /></div>} />
      <Prose section={s.about} id='about' />
      <Guides section={s.portfolio} id='portfolio' figure={1} />
      <Statement section={s.why} />
      <Founder section={s.founder} />
      <Closing section={s.closing} />
    </>
  );
}

function Prose({section, id}: {section: ProseSection; id: string}) {
  return (
    <Section id={id}>
      <div className='content-prose'>
        <div>
          <Kicker>{section.kicker}</Kicker>
          <Headline headline={section.headline} detail={section.headlineDetail} />
        </div>
        <div>
          {section.paragraphs.map(paragraph => <p key={paragraph}>{paragraph}</p>)}
          {section.link && <ActionLink link={section.link} variant='text' />}
        </div>
      </div>
    </Section>
  );
}

/** A full-width serif statement, as on the home page's principle section. */
function Statement({section}: {section: ProseSection}) {
  return (
    <Section id='why' tone='sage' className='home-principle'>
      <Kicker>{section.kicker}</Kicker>
      <Headline size='statement' headline={section.headline} detail={section.headlineDetail} />
      {section.paragraphs.map(paragraph => <p key={paragraph} className='site-lead'>{paragraph}</p>)}
      {section.link && <div className='site-actions'><ActionLink link={section.link} variant='secondary' /></div>}
    </Section>
  );
}

function Founder({section}: {section: ProfileSection}) {
  return (
    <Section id='founder'>
      <div className='content-founder'>
        <div className='content-founder-card page-card'>
          <div className='page-card-bar'><span className='cp-dot' aria-hidden='true' /><span>{section.kicker}</span>{section.location && <span>{section.location}</span>}</div>
          <div className='page-card-body'>
            <span className='content-founder-initials' aria-hidden='true'>{section.name.split(' ').map(word => word[0]).join('').slice(0, 2)}</span>
            <strong>{section.name}</strong>
            <span>{section.role}</span>
          </div>
        </div>
        <div>
          <Kicker>{section.kicker}</Kicker>
          <Headline headline={section.headline} detail={section.headlineDetail} />
          {section.paragraphs.map(paragraph => <p key={paragraph} className='content-founder-text'>{paragraph}</p>)}
          {section.link && <ActionLink link={section.link} variant='text' />}
        </div>
      </div>
    </Section>
  );
}

/* ---- Privacy and terms ---------------------------------------------------------------------- */
export function LegalPage({page}: {page: PageOf<'privacy'> | PageOf<'terms'>}) {
  const document: LegalSection = page.sections.document;
  const site = useSite();
  const words = site.vocabulary;
  return (
    <Section id='document' className='content-legal'>
      <header className='content-legal-head'>
        <Kicker>{site.brand.owner}</Kicker>
        <h1 className='site-headline is-h2'>{document.title}</h1>
        <p className='site-lead'>{document.summary}</p>
        <dl className='content-legal-meta'>
          <div><dt>{words.effective}</dt><dd><time dateTime={document.effectiveDate}>{formatDate(document.effectiveDate)}</time></dd></div>
          <div><dt>{words.version}</dt><dd>{document.version}</dd></div>
        </dl>
      </header>
      <div className='content-legal-body'>
        <nav className='content-legal-toc' aria-label={site.labels.onThisPage}>
          <p>{site.labels.onThisPage}</p>
          <ol>{document.clauses.map((clause, index) => <li key={clause.id}><a href={`#${clause.id}`}><span>{String(index + 1).padStart(2, '0')}</span>{clause.heading}</a></li>)}</ol>
        </nav>
        <div className='content-legal-text'>
          <p className='content-legal-scope'>{document.scopeNote}</p>
          {document.clauses.map((clause, index) => (
            <section key={clause.id} id={clause.id} aria-labelledby={`${clause.id}-heading`}>
              <h2 id={`${clause.id}-heading`}><span>{String(index + 1).padStart(2, '0')}</span>{clause.heading}</h2>
              {clause.paragraphs.map(paragraph => <p key={paragraph}>{paragraph}</p>)}
              {clause.points.length > 0 && <ul>{clause.points.map(point => <li key={point}>{point}</li>)}</ul>}
            </section>
          ))}
          <p className='content-legal-related'><SiteLink href={document.related.href}>{document.related.label}<ArrowRight size={15} aria-hidden='true' /></SiteLink></p>
        </div>
      </div>
    </Section>
  );
}

/* ---- Articles ------------------------------------------------------------------------------- */
export function ArticlePage({page}: {page: PageOf<'article'>}) {
  const article: ArticleSection = page.sections.article;
  const words = useVocabulary();
  return (
    <Section id='article' className='content-article'>
      <header className='content-article-head'>
        <a className='content-back' href='/resources'><ArrowLeft size={15} aria-hidden='true' />{words.backToResources}</a>
        <Kicker>{article.kicker}</Kicker>
        <h1 className='site-headline is-h2'>{article.title}</h1>
        <p className='site-lead'>{article.summary}</p>
        {(article.published || article.author) && (
          <p className='content-article-meta'>
            {article.published && <span>{words.published} <time dateTime={article.published}>{formatDate(article.published)}</time></span>}
            {article.author && <span>{words.by} {article.author}</span>}
          </p>
        )}
      </header>
      {article.image && <Media image={article.image} className='content-article-image' sizes='(min-width: 64rem) 60rem, 100vw' />}
      <div className='content-article-body'>
        {article.sections.map(part => (
          <section key={part.id} id={part.id} aria-labelledby={`${part.id}-heading`}>
            <h2 id={`${part.id}-heading`}>{part.heading}</h2>
            {part.paragraphs.map(paragraph => <p key={paragraph}>{paragraph}</p>)}
            {part.points.length > 0 && <ul>{part.points.map(point => <li key={point}>{point}</li>)}</ul>}
            {part.image && <Media image={part.image} sizes='(min-width: 64rem) 46rem, 100vw' />}
            {part.links.length > 0 && <p className='content-article-links'>{part.links.map(link => <ActionLink key={link.href} link={link} variant='text' />)}</p>}
          </section>
        ))}
      </div>
    </Section>
  );
}
