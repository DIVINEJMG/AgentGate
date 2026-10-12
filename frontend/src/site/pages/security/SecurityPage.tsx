// The security page, below the navigation.
import type {DecisionSection, FeatureSection, PageOf, RecordSection, StepSection} from '../../contract/contract.generated';
import {useVocabulary} from '../../content/SiteContent';
import {Icon} from '../../components/Icon';
import {DecisionTag, FigureLabel, Section, SectionHeader} from '../../components/primitives';
import {ControlPlane} from '../home/ControlPlane';
import {Closing, RecordConsole} from '../home/HomeSections';
import {PageHero} from '../shared/PageParts';
import './security.css';

export function SecurityPage({page}: {page: PageOf<'security'>}) {
  const s = page.sections;
  return (
    <>
      <PageHero section={s.hero} tone='night' align='center' />
      <Decision section={s.decision} record={s.record} />
      <ControlPath section={s.controlPath} />
      <Safeguards section={s.safeguards} />
      <RecordConsole section={s.record} id='record' figure={4} />
      <Closing section={s.closing} />
    </>
  );
}

/* The gateway, interactive: the same request resolving three ways. */
function Decision({section, record}: {section: DecisionSection; record: RecordSection}) {
  return (
    <Section id='decision' tone='night'>
      <SectionHeader heading={section} layout='center' />
      <FigureLabel number={1} label={section.kicker ?? section.headline} />
      <ControlPlane demo={section.demo} records={record.records} streamLabel={record.kicker ?? record.headline} />
    </Section>
  );
}

/* The control path: four gates on one line, ending in three possible decisions. */
function ControlPath({section}: {section: StepSection}) {
  const words = useVocabulary();
  return (
    <Section id='controlPath'>
      <SectionHeader heading={section} />
      <FigureLabel number={2} label={section.kicker ?? section.headline} />
      <div className='sec-path'>
        <ol className='sec-gates' style={{['--gates' as string]: section.steps.length}}>
          {section.steps.map((step, index) => (
            <li key={step.id}>
              <span className='sec-gate' aria-hidden='true'><span>{String(index + 1).padStart(2, '0')}</span></span>
              <p className='sec-gate-label'>{step.label}</p>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </li>
          ))}
        </ol>
        <ul className='sec-outcomes'>
          {(['allow', 'review', 'deny'] as const).map(decision => <li key={decision}><DecisionTag decision={decision} label={words.decisions[decision]} /></li>)}
        </ul>
      </div>
    </Section>
  );
}

function Safeguards({section}: {section: FeatureSection}) {
  return (
    <Section id='safeguards' tone='sage'>
      <SectionHeader heading={section} />
      <ul className='sec-safeguards'>
        {section.items.map((item, index) => (
          <li key={item.id}>
            <p className='sec-safeguard-top'><Icon name={item.icon} size={20} /><span>{String(index + 1).padStart(2, '0')}</span></p>
            <h3>{item.title}</h3>
            <p>{item.body}</p>
          </li>
        ))}
      </ul>
    </Section>
  );
}
