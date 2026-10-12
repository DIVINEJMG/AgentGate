// The solutions page, below the navigation: an index of areas and one section per solution.
import {useEffect} from 'react';
import {ArrowDown, Info} from 'lucide-react';
import type {PageOf, Solution, SolutionSection} from '../../contract/contract.generated';
import {useSite} from '../../content/SiteContent';
import {Icon} from '../../components/Icon';
import {FigureLabel, Kicker, Media, Section, SectionHeader} from '../../components/primitives';
import {SiteLink} from '../../navigation';
import {Closing} from '../home/HomeSections';
import {PageHero, ToolChip} from '../shared/PageParts';
import './solutions.css';

/** Links from the previous site used ?selected=<record id>; they now lead to the same solution. */
const LEGACY_SELECTION: Record<string, string> = {
  '3ddf2c2c-1a9a-5205-8a89-b8f25b4f672a': 'operations', '44ab54c3-56e8-5e9e-8e41-4d5bbce48483': 'research',
  'a705b4b2-cbd5-52d4-86e8-315c399b7c42': 'customer-support', 'ca955ec0-2da5-5e81-851a-b4eaa56c5d2a': 'sales',
  'bd083f9b-6750-59f2-8232-ef69a0b5b8b8': 'marketing', 'dbba8259-a688-589c-8e6a-799e018c5c0f': 'engineering',
};

export function SolutionsPage({page}: {page: PageOf<'solutions'>}) {
  const s = page.sections;
  useEffect(() => {
    const selected = new URLSearchParams(window.location.search).get('selected');
    const target = selected ? LEGACY_SELECTION[selected.toLowerCase()] ?? selected : null;
    if (target) requestAnimationFrame(() => document.getElementById(target)?.scrollIntoView());
  }, []);
  return (
    <>
      <PageHero section={s.hero} align='center' meta={<AreaIndex items={s.solutions.items} />} />
      <Solutions section={s.solutions} />
      <Closing section={s.closing} />
    </>
  );
}

function AreaIndex({items}: {items: Solution[]}) {
  return (
    <ul className='sol-index'>
      {items.map((item, index) => (
        <li key={item.id}>
          <SiteLink href={`#${item.id}`}><span className='sol-index-number'>{String(index + 1).padStart(2, '0')}</span><Icon name={item.icon} size={16} /><span>{item.area}</span><ArrowDown size={14} aria-hidden='true' /></SiteLink>
        </li>
      ))}
    </ul>
  );
}

function Solutions({section}: {section: SolutionSection}) {
  return (
    <Section id='solutions'>
      <SectionHeader heading={section} />
      <div className='sol-list'>
        {section.items.map((item, index) => <SolutionRow key={item.id} item={item} index={index} total={section.items.length} />)}
      </div>
      <p className='sol-note'><Info size={16} aria-hidden='true' />{section.note}</p>
    </Section>
  );
}

function SolutionRow({item, index, total}: {item: Solution; index: number; total: number}) {
  const site = useSite();
  const words = site.vocabulary;
  return (
    <article id={item.id} className={`sol-row${index % 2 ? ' is-reversed' : ''}`} aria-labelledby={`${item.id}-title`}>
      <div className='sol-copy'>
        <p className='sol-count'>{String(index + 1).padStart(2, '0')} / {String(total).padStart(2, '0')}</p>
        <Kicker>{item.area}</Kicker>
        <h3 id={`${item.id}-title`}>{item.title}</h3>
        <p className='sol-body'>{item.body}</p>
      </div>
      <div className='sol-visual'>
        <FigureLabel number={index + 1} label={words.exampleJob} />
        {item.image && <Media image={item.image} className='sol-image' sizes='(min-width: 64rem) 45vw, 100vw' />}
        <div className={`page-card sol-card${item.image ? ' is-over-image' : ''}`}>
          <div className='page-card-bar'><span className='cp-dot' aria-hidden='true' /><span>{item.worker}</span><span className='site-badge'>{site.labels.illustrative}</span></div>
          <div className='page-card-body'>
            <dl className='sol-facts'>
              <div><dt>{words.worker}</dt><dd>{item.worker}</dd></div>
              <div><dt>{words.exampleJob}</dt><dd>{item.job}</dd></div>
              <div><dt>{words.connectedTools}</dt><dd className='sol-tools'>{item.tools.map(tool => <ToolChip key={tool} name={tool} />)}</dd></div>
              <div><dt>{words.deliverable}</dt><dd className='sol-deliverable'>{item.deliverable}</dd></div>
            </dl>
            <ol className='sol-path' aria-hidden='true'>
              <li>{item.tools[0]}</li>
              <li>{item.worker}</li>
              <li className='is-end'>{item.deliverable}</li>
            </ol>
          </div>
        </div>
      </div>
    </article>
  );
}
