// The pricing page, below the navigation. Plans are informational: nothing here grants or
// implies an entitlement, and paid plans are shown as not yet available.
import {ArrowRight, Pause, Users} from 'lucide-react';
import type {FaqSection, FeatureSection, PageOf, Plan, PlanSection} from '../../contract/contract.generated';
import {fill, useVocabulary} from '../../content/SiteContent';
import {Icon} from '../../components/Icon';
import {Section, SectionHeader} from '../../components/primitives';
import {SiteLink} from '../../navigation';
import {Closing} from '../home/HomeSections';
import {CheckList, PageHero} from '../shared/PageParts';
import './pricing.css';

export function PricingPage({page}: {page: PageOf<'pricing'>}) {
  const s = page.sections;
  return (
    <>
      <PageHero section={s.hero} align='center' />
      <Plans section={s.plans} />
      <SameModel section={s.sameModel} />
      <Questions section={s.questions} />
      <Closing section={s.closing} />
    </>
  );
}

export function formatPrice(plan: Plan) {
  return new Intl.NumberFormat('en', {style: 'currency', currency: plan.currency, minimumFractionDigits: plan.priceMinor % 100 ? 2 : 0}).format(plan.priceMinor / 100);
}

function Plans({section}: {section: PlanSection}) {
  return (
    <Section id='plans'>
      <SectionHeader heading={section} />
      <div className='price-grid'>{section.plans.map(plan => <PlanCard key={plan.key} plan={plan} />)}</div>
      <div className='price-notice' role='note'>
        <Pause size={16} aria-hidden='true' />
        <div><strong>{section.notice.title}</strong><p>{section.notice.body}</p></div>
      </div>
    </Section>
  );
}

function PlanCard({plan}: {plan: Plan}) {
  const words = useVocabulary();
  const available = plan.availability !== 'unavailable';
  return (
    <article className={`price-card${plan.availability === 'free-signup' ? ' is-featured' : ''}`} aria-labelledby={`plan-${plan.key}`}>
      <header>
        <h3 id={`plan-${plan.key}`}>{plan.name}</h3>
        <span className={`price-status${available ? ' is-available' : ''}`}>{plan.status}</span>
      </header>
      <p className='price-amount'><strong>{formatPrice(plan)}</strong><span>{plan.period === 'year' ? words.perYear : words.perMonth}</span></p>
      <p className='price-audience'>{plan.audience}</p>
      <p className='price-limit'><Users size={16} aria-hidden='true' />{fill(words.workerLimit, {count: plan.workerLimit})}</p>
      <CheckList items={plan.features} />
      {plan.action && available && (
        <SiteLink href={plan.action.href} className={`site-action ${plan.availability === 'free-signup' ? 'is-primary' : 'is-secondary'} price-action`}>
          <span>{plan.action.label}</span><ArrowRight size={16} aria-hidden='true' />
        </SiteLink>
      )}
    </article>
  );
}

function SameModel({section}: {section: FeatureSection}) {
  return (
    <Section id='sameModel' tone='forest'>
      <SectionHeader heading={section} />
      <ul className='price-model'>
        {section.items.map(item => <li key={item.id}><Icon name={item.icon} size={20} /><h3>{item.title}</h3><p>{item.body}</p></li>)}
      </ul>
    </Section>
  );
}

function Questions({section}: {section: FaqSection}) {
  return (
    <Section id='questions'>
      <div className='price-faq'>
        <SectionHeader heading={section} layout='stacked' />
        <div className='price-faq-list'>
          {section.items.map(item => (
            <details key={item.id}>
              <summary><span>{item.question}</span><span className='price-faq-toggle' aria-hidden='true' /></summary>
              <p>{item.answer}</p>
            </details>
          ))}
        </div>
      </div>
    </Section>
  );
}
