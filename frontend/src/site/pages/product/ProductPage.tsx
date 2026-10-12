// The product page, below the navigation. Same design language as the home page.
import {ArrowRight, Check} from 'lucide-react';
import type {FeatureSection, FigureSection, PageOf, RunSection, WorkerProfileSection, WorkspaceDemoSection} from '../../contract/contract.generated';
import {useSite, useVocabulary} from '../../content/SiteContent';
import {Icon} from '../../components/Icon';
import {ActionLink, DecisionTag, Disclosure, FigureLabel, Media, Section, SectionHeader} from '../../components/primitives';
import {Closing, Connections} from '../home/HomeSections';
import {CheckList, PageHero} from '../shared/PageParts';
import './product.css';

export function ProductPage({page}: {page: PageOf<'product'>}) {
  const s = page.sections;
  return (
    <>
      <PageHero section={s.hero} align='center' />
      <Workspace section={s.workspace} />
      <Workforce section={s.workforce} />
      <Runtime section={s.runtime} />
      <Intelligence section={s.intelligence} />
      <Supervision section={s.supervision} />
      <Connections section={s.integrations} id='integrations' figure={6} />
      <Closing section={s.closing} />
    </>
  );
}

const JOB_TAG = {completed: 'allow', running: 'allow', queued: 'queued', held: 'review'} as const;

/* The workspace: workers, jobs and an action held for review, in one window. */
function Workspace({section}: {section: WorkspaceDemoSection}) {
  const site = useSite();
  const words = site.vocabulary;
  const held = section.jobs.filter(job => job.status === 'held').length;
  return (
    <Section id='workspace' tone='night'>
      <SectionHeader heading={section} layout='center' />
      <FigureLabel number={1} label={section.kicker ?? section.headline} />
      <div className='prod-window site-window'>
        <div className='cp-bar'>
          <span className='cp-dot' aria-hidden='true' />
          <span className='cp-path'>{section.organization}</span>
          <span className='site-badge'>{site.labels.illustrative}</span>
          <span className='cp-ref'>{section.kicker}</span>
        </div>
        <div className='prod-body'>
          <nav className='prod-side' aria-label={section.organization}>
            <p className='prod-side-item is-active'><Icon name='workers' size={15} />{words.worker}<span>{section.workers.length}</span></p>
            <p className='prod-side-item'><Icon name='jobs' size={15} />{words.job}<span>{section.jobs.length}</span></p>
            <p className='prod-side-item'><Icon name='approval' size={15} />{words.decisions.review}<span className='is-hold'>{held}</span></p>
          </nav>
          <div className='prod-main'>
            <div className='prod-table' role='table' aria-label={words.worker}>
              <div role='row' className='prod-row is-head'>
                {[words.worker, words.team, words.supervisor, words.status].map(label => <span role='columnheader' key={label}>{label}</span>)}
              </div>
              {section.workers.map(worker => (
                <div role='row' className='prod-row' key={worker.id}>
                  <span role='cell' className='prod-worker'><span className='prod-avatar' aria-hidden='true'>{worker.name.split(' ').map(word => word[0]).join('').slice(0, 2).toUpperCase()}</span>{worker.name}</span>
                  <span role='cell'>{worker.team}</span>
                  <span role='cell'>{worker.supervisor}</span>
                  <span role='cell'><span className='prod-status'>{worker.status}</span></span>
                </div>
              ))}
            </div>
            <p className='page-label prod-jobs-label'>{words.job}</p>
            <ul className='prod-jobs'>
              {section.jobs.map(job => (
                <li key={job.id} className={`is-${job.status}`}>
                  <span className='prod-job-mark' aria-hidden='true' />
                  <span className='prod-job-copy'><strong>{job.title}</strong><small>{job.worker} · {job.trigger}</small></span>
                  {JOB_TAG[job.status] === 'queued'
                    ? <span className='site-tag is-queued'>{words.jobStatus[job.status]}</span>
                    : <DecisionTag decision={JOB_TAG[job.status] as 'allow' | 'review'} label={words.jobStatus[job.status]} />}
                </li>
              ))}
            </ul>
          </div>
          <aside className='prod-approval' aria-label={words.decisions.review}>
            <DecisionTag decision='review' label={words.decisions.review} />
            <strong>{section.approval.title}</strong>
            <p>{section.approval.body}</p>
            <dl>
              <div><dt>{words.requestedBy}</dt><dd>{section.approval.requestedBy}</dd></div>
              <div><dt>{words.reviewer}</dt><dd>{section.approval.reviewer}</dd></div>
            </dl>
          </aside>
        </div>
      </div>
      <div className='prod-disclosure'><Disclosure>{section.disclosure}</Disclosure></div>
    </Section>
  );
}

/* Workforce: text and the worker profile card. */
function Workforce({section}: {section: WorkerProfileSection}) {
  const site = useSite();
  const words = site.vocabulary;
  const profile = section.profile;
  return (
    <Section id='workforce'>
      <div className='page-split'>
        <div>
          <SectionHeader heading={section} layout='stacked' />
          <CheckList items={section.points} />
        </div>
        <div>
          <FigureLabel number={2} label={section.figureLabel} />
          <div className='page-card'>
            <div className='page-card-bar'><span className='cp-dot' aria-hidden='true' /><span>{section.figureLabel}</span><span className='site-badge'>{site.labels.illustrative}</span></div>
            <div className='page-card-body prod-profile'>
              <div className='prod-profile-top'>
                <span className='prod-avatar is-large' aria-hidden='true'>{profile.role.split(' ').map(word => word[0]).join('').slice(0, 2).toUpperCase()}</span>
                <div><strong>{profile.role}</strong><small>{profile.team} · {words.supervisor}: {profile.supervisor}</small></div>
                <span className='prod-status'>{profile.status}</span>
              </div>
              <div className='prod-profile-job'>
                <p className='page-label'>{words.assignedJob}</p>
                <p>{profile.job}</p>
              </div>
              <dl className='prod-profile-facts'>
                <div><dt>{words.schedule}</dt><dd>{profile.schedule}</dd></div>
                <div><dt>{words.policy}</dt><dd>{profile.checkpoint}</dd></div>
              </dl>
              <ol className='home-flow prod-flow' aria-label={section.figureLabel}>
                {profile.path.map((stage, index) => <li key={stage} className={index === profile.path.length - 1 ? 'is-end' : undefined}>{stage}</li>)}
              </ol>
            </div>
          </div>
          <div className='prod-card-disclosure'><Disclosure>{section.disclosure}</Disclosure></div>
        </div>
      </div>
    </Section>
  );
}

/* Runtime: the run timeline, advancing step by step. */
function Runtime({section}: {section: RunSection}) {
  const words = useVocabulary();
  return (
    <Section id='runtime' tone='sage'>
      <div className='page-split is-reversed'>
        <div>
          <SectionHeader heading={section} layout='stacked' />
          <CheckList items={section.points} />
          <div className='prod-run-disclosure'><Disclosure>{section.disclosure}</Disclosure></div>
        </div>
        <div>
          <FigureLabel number={3} label={section.kicker ?? section.headline} />
          <div className='page-card'>
            <div className='page-card-bar'><span className='cp-dot' aria-hidden='true' /><span>{section.runTitle}</span><span>{words.stepState.active}</span></div>
            <ol className='page-card-body prod-steps'>
              {section.steps.map(step => (
                <li key={step.id} className={`is-${step.state}`}>
                  <span className='prod-step-mark' aria-hidden='true'>{step.state === 'done' ? <Check size={12} strokeWidth={2.6} /> : null}</span>
                  <span className='prod-step-copy'><strong>{step.title}</strong><small>{step.detail}</small></span>
                  <span className='prod-step-state'>{words.stepState[step.state]}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>
    </Section>
  );
}

/* AI models: the violet model layer feeding the green gateway. */
function Intelligence({section}: {section: FeatureSection}) {
  const words = useVocabulary();
  const authority = section.items[section.items.length - 1];
  const models = section.items.slice(0, -1);
  return (
    <Section id='intelligence' tone='night'>
      <SectionHeader heading={section} />
      <FigureLabel number={4} label={section.kicker ?? section.headline} />
      <div className='prod-ai'>
        <div className='prod-ai-layer is-model'>
          <p className='prod-ai-label'>{words.proposedBy}</p>
          <ul>
            {models.map(item => (
              <li key={item.id}><Icon name={item.icon} size={18} /><span><strong>{item.title}</strong><small>{item.body}</small></span></li>
            ))}
          </ul>
        </div>
        <div className='prod-ai-arrow' aria-hidden='true'><span /><ArrowRight size={18} /></div>
        <div className='prod-ai-layer is-gateway'>
          <p className='prod-ai-label'>{words.gateway}</p>
          {authority && <div className='prod-ai-authority'><Icon name={authority.icon} size={22} /><strong>{authority.title}</strong><small>{authority.body}</small></div>}
        </div>
      </div>
    </Section>
  );
}

/* Supervision: photograph with the decision path. */
function Supervision({section}: {section: FigureSection}) {
  return (
    <Section id='supervision'>
      <div className='page-split'>
        <div className='prod-supervision-media'>
          <Media image={section.image} sizes='(min-width: 64rem) 50vw, 100vw' />
          <ol className='prod-supervision-path' aria-label={section.figureLabel}>
            {section.points.map((point, index) => <li key={point} className={index === section.points.length - 1 ? 'is-end' : undefined}><span>{String(index + 1).padStart(2, '0')}</span>{point}</li>)}
          </ol>
        </div>
        <div>
          <SectionHeader heading={section} layout='stacked' />
          {section.link && <ActionLink link={section.link} variant='text' />}
        </div>
      </div>
    </Section>
  );
}
