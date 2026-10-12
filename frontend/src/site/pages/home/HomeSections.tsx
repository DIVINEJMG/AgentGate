// Home page sections. Each renders one section of the home page document; visuals that are
// drawn by the site (diagrams, small product fragments) take their words from content.
import {useRef, useState, type KeyboardEvent} from 'react';
import {ArrowRight, Check} from 'lucide-react';
import type {CardSection, ClosingSection, CodeSection, DecisionDemo, DecisionSection, FeatureSection, FigureSection, HeroSection, IntegrationSection, LayerSection, RecordSection, StepSection} from '../../contract/contract.generated';
import {fill, useSite, useVocabulary} from '../../content/SiteContent';
import {Icon} from '../../components/Icon';
import {useTypedWords} from '../../components/motion';
import {Actions, DecisionTag, Disclosure, FigureLabel, Headline, Kicker, Media, Section, SectionHeader} from '../../components/primitives';
import {SiteLink} from '../../navigation';
import {ControlPlane} from './ControlPlane';
import './home.css';

/* 1. Hero: headline, typing line, actions, and the control-plane window over the night band. */
export function Hero({section, demo, activity, connections}: {section: HeroSection; demo: DecisionDemo; activity: RecordSection; connections: IntegrationSection}) {
  const words = useVocabulary();
  return (
    <Section id='hero' className='home-hero'>
      <div className='home-hero-copy'>
        <Kicker>{section.kicker}</Kicker>
        <Headline as='h1' size='hero' headline={section.headline} detail={section.headlineDetail} />
        {section.typing && <TypingLine template={section.typing.template} places={section.typing.places} />}
        <p className='site-lead home-hero-lead'>{section.body}</p>
        <Actions primary={section.primaryAction} secondary={section.secondaryAction} />
      </div>
      <div className='home-stage'>
        <FigureLabel number={1} label={words.gateway} />
        <ControlPlane demo={demo} records={activity.records} streamLabel={activity.kicker ?? activity.headline} />
        <div className='home-works'>
          <span className='home-works-label'>{words.worksWith}</span>
          <ul>
            {connections.items.map(item => <li key={item.id}><ProviderMark provider={item.provider} />{item.name}</li>)}
          </ul>
        </div>
      </div>
    </Section>
  );
}

function TypingLine({template, places}: {template: string; places: string[]}) {
  const slot = useRef<HTMLParagraphElement>(null);
  const typed = useTypedWords(slot, places);
  const [before, after] = template.split('{place}');
  return (
    <p ref={slot} className='home-typing'>
      <span className='site-sr'>{fill(template, {place: places.join(', ')})}</span>
      <span aria-hidden='true'>{before}<span className='home-typing-word'>{typed}<i /></span>{after}</span>
    </p>
  );
}

const MARKS = new Set(['github', 'gmail', 'google-drive', 'slack', 'google-calendar']);
export function ProviderMark({provider}: {provider: string}) {
  return (
    <span className='home-mark' aria-hidden='true'>
      {MARKS.has(provider) ? <img src={`/provider-marks/${provider}.svg`} alt='' width={18} height={18} /> : <Icon name={provider === 'browser' ? 'browser' : 'research'} size={16} />}
    </span>
  );
}

/* 2. Product: three framed columns. */
export function Product({section}: {section: FeatureSection}) {
  return (
    <Section id='product'>
      <SectionHeader heading={section} />
      <ul className='home-columns'>
        {section.items.map((item, index) => (
          <li key={item.id}>
            <span className='home-columns-top'><span className='home-index'>{String(index + 1).padStart(2, '0')}</span><Icon name={item.icon} size={20} /></span>
            <h3>{item.title}</h3>
            <p>{item.body}</p>
          </li>
        ))}
      </ul>
    </Section>
  );
}

/* 3. Principle: one serif statement and the three decisions. */
export function Principle({section}: {section: DecisionSection}) {
  const words = useVocabulary();
  return (
    <Section id='decision' tone='sage' className='home-principle'>
      <Kicker>{section.kicker}</Kicker>
      <Headline size='statement' headline={section.headline} detail={section.headlineDetail} />
      {section.body && <p className='site-lead'>{section.body}</p>}
      <ul className='home-principle-decisions'>
        {(['allow', 'review', 'deny'] as const).map(decision => (
          <li key={decision}>
            <DecisionTag decision={decision} label={words.decisions[decision]} />
            <p>{section.demo.outcomes[decision].title}</p>
          </li>
        ))}
      </ul>
    </Section>
  );
}

/* 4. How it works: four stages on the signal path, each with a small product fragment. */
export function HowItWorks({section, demo}: {section: StepSection; demo: DecisionDemo}) {
  const words = useVocabulary();
  const fragments = [
    <div className='home-frag' key='work'><span className='home-frag-label'>{words.job}</span><strong>{demo.action}</strong><span>{demo.worker}</span></div>,
    <div className='home-frag is-code' key='policy'><span className='home-frag-label'>{words.policy}</span><code>{demo.checks[demo.checks.length - 1].detail ?? demo.checks[demo.checks.length - 1].label}</code></div>,
    <div className='home-frag' key='approval'><DecisionTag decision='review' label={words.decisions.review} /><span>{demo.outcomes.review.note ?? demo.outcomes.review.title}</span></div>,
    <div className='home-frag is-code' key='record'><span className='home-frag-label'>{words.recordedAs}</span><code>{demo.record.reference}</code><span>{demo.record.summary}</span></div>,
  ];
  return (
    <Section id='controlPath'>
      <SectionHeader heading={section} />
      <FigureLabel number={2} label={section.kicker ?? section.headline} />
      <ol className='home-steps' style={{['--steps' as string]: section.steps.length}}>
        {section.steps.map((step, index) => (
          <li key={step.id}>
            <span className='home-step-node' aria-hidden='true'>{String(index + 1).padStart(2, '0')}</span>
            <p className='home-step-label'>{step.label}</p>
            <h3>{step.title}</h3>
            <p>{step.body}</p>
            {fragments[index]}
          </li>
        ))}
      </ol>
    </Section>
  );
}

/* 5. Policy as code: tabs of illustrative code; the AI proposal in violet, the decision in green. */
export function PolicyCode({section}: {section: CodeSection}) {
  const words = useVocabulary();
  const [active, setActive] = useState(0);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    const step = ({ArrowRight: 1, ArrowLeft: -1} as Record<string, number>)[event.key];
    if (!step) return;
    event.preventDefault();
    const next = (active + step + section.tabs.length) % section.tabs.length;
    setActive(next);
    tabs.current[next]?.focus();
  }
  const tab = section.tabs[active];
  return (
    <Section id='policy' tone='night'>
      <div className='home-code'>
        <div className='home-code-copy'>
          <SectionHeader heading={section} layout='stacked' />
          <div className='home-proposal'>
            <p className='home-proposal-label'>{words.proposedBy}</p>
            <p>{section.proposal}</p>
          </div>
          <div className={`home-verdict is-${section.decision.decision}`}>
            <DecisionTag decision={section.decision.decision} label={words.decisions[section.decision.decision]} />
            <p>{section.decision.text}</p>
          </div>
          <Disclosure>{section.disclosure}</Disclosure>
        </div>
        <div className='home-code-window'>
          <FigureLabel number={3} label={section.kicker ?? section.headline} />
          <div className='home-editor'>
            <div className='home-editor-tabs' role='tablist' aria-label={section.headline} onKeyDown={onKey}>
              {section.tabs.map((item, index) => (
                <button key={item.id} ref={element => {tabs.current[index] = element;}} type='button' role='tab' id={`code-tab-${item.id}`}
                  aria-selected={index === active} aria-controls={`code-panel-${item.id}`} tabIndex={index === active ? 0 : -1} onClick={() => setActive(index)}>
                  {item.label}
                </button>
              ))}
            </div>
            <pre id={`code-panel-${tab.id}`} role='tabpanel' aria-labelledby={`code-tab-${tab.id}`} tabIndex={0} className={`home-editor-code is-${tab.language}`}>
              {tab.lines.map((line, index) => <span key={index} className='home-line'><span className='home-line-number' aria-hidden='true'>{index + 1}</span><CodeLine line={line} /></span>)}
            </pre>
          </div>
        </div>
      </div>
    </Section>
  );
}

/** Minimal colouring for YAML and JSON: keys, strings and decision values. */
function CodeLine({line}: {line: string}) {
  const match = line.match(/^(\s*-?\s*)("?[\w.-]+"?)(\s*:\s*)(.*)$/);
  if (!match) return <span className='tok-punct'>{line || ' '}</span>;
  const [, indent, key, colon, value] = match;
  const decisionValue = /require_approval|held_for_review|deny|allow/.test(value);
  return (
    <span>
      {indent}<span className='tok-key'>{key}</span><span className='tok-punct'>{colon}</span>
      <span className={decisionValue ? 'tok-decision' : /^"/.test(value) ? 'tok-string' : 'tok-value'}>{value}</span>
    </span>
  );
}

/* 6. Operating model: four framed panels, each with its path. */
export function OperatingModel({section}: {section: LayerSection}) {
  return (
    <Section id='operatingModel'>
      <SectionHeader heading={section} />
      <FigureLabel number={4} label={section.kicker ?? section.headline} />
      <ul className='home-bento'>
        {section.layers.map(layer => (
          <li key={layer.id}>
            <p className='home-bento-top'><Icon name={layer.icon} size={18} /><span>{layer.title}</span><small>{layer.subtitle}</small></p>
            <h3>{layer.headline}</h3>
            <p className='home-bento-body'>{layer.body}</p>
            <ol className='home-flow' aria-label={layer.title}>
              {layer.path.map((stage, index) => <li key={stage} className={index === layer.path.length - 1 ? 'is-end' : undefined}>{stage}</li>)}
            </ol>
            <p className='home-bento-signal'>{layer.signal}</p>
          </li>
        ))}
      </ul>
    </Section>
  );
}

/* 7. Human oversight: photograph with an approval card laid over it. */
export function Oversight({section, demo}: {section: FigureSection; demo: DecisionDemo}) {
  const words = useVocabulary();
  return (
    <Section id='oversight' tone='sage'>
      <div className='home-oversight'>
        <div className='home-oversight-media'>
          <Media image={section.image} sizes='(min-width: 64rem) 55vw, 100vw' />
          <div className='home-approval site-window' aria-hidden='true'>
            <p className='home-approval-top'><DecisionTag decision='review' label={words.decisions.review} /><span>{demo.record.reference}</span></p>
            <strong>{demo.action}</strong>
            <span>{demo.worker} · {demo.target}</span>
            <p className='home-approval-note'>{demo.outcomes.review.note}</p>
          </div>
        </div>
        <div>
          <Kicker>{section.kicker}</Kicker>
          <Headline headline={section.headline} detail={section.headlineDetail} />
          {section.body && <p className='site-lead home-oversight-lead'>{section.body}</p>}
          <p className='home-oversight-label'>{section.figureLabel}</p>
          <ul className='home-chips'>{section.points.map(point => <li key={point}>{point}</li>)}</ul>
        </div>
      </div>
    </Section>
  );
}

/* 8. The record: illustrative counters and the full console. */
export function RecordConsole({section, id = 'activity', figure = 5}: {section: RecordSection; id?: string; figure?: number}) {
  const site = useSite();
  const words = site.vocabulary;
  return (
    <Section id={id} tone='night'>
      <SectionHeader heading={section} />
      {section.metrics && section.metrics.length > 0 && (
        <dl className='home-metrics'>
          {section.metrics.map((metric, index) => (
            <div key={metric.id} className={`is-${['all', 'allow', 'review', 'deny'][index] ?? 'all'}`}><dt>{metric.label}</dt><dd>{metric.value}</dd></div>
          ))}
        </dl>
      )}
      <div className='home-console'>
        <FigureLabel number={figure} label={section.kicker ?? section.headline} />
        <div className='home-console-table' role='table' aria-label={section.headline}>
          <div role='row' className='home-console-row is-head'>
            {[words.time, words.reference, words.actor, words.action, words.policy, words.decision, words.outcome].map(label => <span role='columnheader' key={label}>{label}</span>)}
          </div>
          {section.records.map(record => (
            <div role='row' className='home-console-row' key={record.id}>
              <span role='cell'>{record.time}</span>
              <span role='cell'>{record.reference}</span>
              <span role='cell' className='is-strong'>{record.actor}</span>
              <span role='cell'>{record.action}</span>
              <span role='cell' className='is-muted'>{record.policy}</span>
              <span role='cell'><DecisionTag decision={record.decision} label={words.decisions[record.decision]} /></span>
              <span role='cell' className='is-muted'>{record.outcome}</span>
            </div>
          ))}
        </div>
        <p className='cp-prompt home-console-prompt' aria-hidden='true'>{site.brand.name.toLowerCase()} <span>›</span> <i /></p>
      </div>
      <Disclosure>{section.disclosure}</Disclosure>
    </Section>
  );
}

/* 9. Connections: providers → gateway → outcomes. */
export function Connections({section, id = 'connections', figure = 6}: {section: IntegrationSection; id?: string; figure?: number}) {
  const words = useVocabulary();
  return (
    <Section id={id}>
      <SectionHeader heading={section} />
      <FigureLabel number={figure} label={section.kicker ?? section.headline} />
      <div className='home-net'>
        <ul className='home-net-providers'>
          {section.items.map(item => <li key={item.id}><ProviderMark provider={item.provider} /><span><strong>{item.name}</strong><small>{item.use}</small></span></li>)}
        </ul>
        <Fan count={section.items.length} direction='in' />
        <div className='home-net-gateway'>
          <p className='home-net-gateway-label'>{words.gateway}</p>
          <ol>{section.boundary.stages.map(stage => <li key={stage}><Check size={12} strokeWidth={2.4} aria-hidden='true' />{stage}</li>)}</ol>
          <p className='home-net-gateway-title'>{section.boundary.title}</p>
        </div>
        <Fan count={3} direction='out' />
        <ul className='home-net-outcomes'>
          {(['allow', 'review', 'deny'] as const).map(decision => <li key={decision}><DecisionTag decision={decision} label={words.decisions[decision]} /></li>)}
        </ul>
      </div>
      <p className='home-net-note'>{section.note}</p>
    </Section>
  );
}

/** Curved lines between evenly spaced rows and the gateway's centre. */
function Fan({count, direction}: {count: number; direction: 'in' | 'out'}) {
  const ends = Array.from({length: count}, (_, index) => ((index + .5) / count) * 100);
  return (
    <svg className={`home-fan is-${direction}`} viewBox='0 0 100 100' preserveAspectRatio='none' aria-hidden='true'>
      {ends.map(y => <path key={y} d={direction === 'in' ? `M0 ${y} C 55 ${y}, 45 50, 100 50` : `M0 50 C 55 50, 45 ${y}, 100 ${y}`} />)}
    </svg>
  );
}

/* 10. Solutions: tabs by function. */
export function Solutions({section}: {section: CardSection}) {
  const words = useVocabulary();
  const [active, setActive] = useState(0);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    const step = ({ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1} as Record<string, number>)[event.key];
    if (!step) return;
    event.preventDefault();
    const next = (active + step + section.items.length) % section.items.length;
    setActive(next);
    tabs.current[next]?.focus();
  }
  const card = section.items[active];
  return (
    <Section id='solutions'>
      <SectionHeader heading={section} />
      <div className='home-solutions'>
        <div className='home-solutions-tabs' role='tablist' aria-label={words.chooseSolution} aria-orientation='vertical' onKeyDown={onKey}>
          {section.items.map((item, index) => (
            <button key={item.id} ref={element => {tabs.current[index] = element;}} type='button' role='tab' id={`solution-tab-${item.id}`}
              aria-selected={index === active} aria-controls='solution-panel' tabIndex={index === active ? 0 : -1} onClick={() => setActive(index)}>
              <Icon name={item.icon} size={18} /><span>{item.kicker ?? item.title}</span><ArrowRight size={14} aria-hidden='true' />
            </button>
          ))}
        </div>
        <div id='solution-panel' role='tabpanel' aria-labelledby={`solution-tab-${card.id}`} className='home-solutions-panel'>
          {card.image ? <Media image={card.image} className='home-solutions-image' eager /> : <div className='home-solutions-art' aria-hidden='true'><Icon name={card.icon} size={40} /></div>}
          <div className='home-solutions-copy'>
            <Kicker>{card.kicker}</Kicker>
            <h3>{card.title}</h3>
            <p>{card.body}</p>
            <SiteLink href={card.link.href} className='site-action is-text'><span>{card.link.label}</span><ArrowRight size={16} aria-hidden='true' /></SiteLink>
          </div>
        </div>
      </div>
    </Section>
  );
}

/* 11. Security: the controls on forest, with the link to the security page. */
export function Security({section}: {section: FeatureSection}) {
  return (
    <Section id='controls' tone='forest'>
      <SectionHeader heading={section} />
      <ul className='home-controls'>
        {section.items.map(item => (
          <li key={item.id}><Icon name={item.icon} size={20} /><h3>{item.title}</h3><p>{item.body}</p></li>
        ))}
      </ul>
      {section.link && <div className='site-actions'><SiteLink href={section.link.href} className='site-action is-on-night-secondary'><span>{section.link.label}</span><ArrowRight size={16} aria-hidden='true' /></SiteLink></div>}
    </Section>
  );
}

/* 12. Closing: the signal path ends in "allow". */
export function Closing({section}: {section: ClosingSection}) {
  const words = useVocabulary();
  return (
    <Section id='closing' tone='night' className='home-closing'>
      <div className='home-closing-path' aria-hidden='true'><span className='home-closing-line' /><DecisionTag decision='allow' label={words.decisions.allow} /></div>
      <Kicker>{section.kicker}</Kicker>
      <Headline headline={section.headline} detail={section.headlineDetail} size='hero' />
      {section.body && <p className='site-lead'>{section.body}</p>}
      <Actions primary={section.primaryAction} secondary={section.secondaryAction} night />
    </Section>
  );
}
