// The hero's control-plane window (illustrative): workers, one request travelling from the model's
// proposal through the gateway's checks to a decision, and the record stream.
//
// With motion allowed and the window on screen, the decision steps through hold → allow → deny
// and records arrive in the stream, until the visitor interacts. Decisions are a radio group.
import {useId, useRef, useState, type KeyboardEvent} from 'react';
import {Check, Cpu} from 'lucide-react';
import type {Decision, DecisionDemo, Record as AuditRecord} from '../../contract/contract.generated';
import {useSite} from '../../content/SiteContent';
import {DecisionTag} from '../../components/primitives';
import {useTicker} from '../../components/motion';
import './control-plane.css';

const ORDER: Decision[] = ['review', 'allow', 'deny'];
const CHOICES: Decision[] = ['allow', 'review', 'deny'];
const STREAM = 5;

export function ControlPlane({demo, records, streamLabel}: {demo: DecisionDemo; records: AuditRecord[]; streamLabel: string}) {
  const site = useSite();
  const words = site.vocabulary;
  const root = useRef<HTMLDivElement>(null);
  const [decision, setDecision] = useState<Decision>('review');
  const [touched, setTouched] = useState(false);
  const [tick, setTick] = useState(0);
  const buttons = useRef<Partial<Record<Decision, HTMLButtonElement | null>>>({});
  const outcomeId = useId();

  useTicker(root, 2800, () => {
    setTick(value => value + 1);
    if (!touched) setDecision(current => ORDER[(ORDER.indexOf(current) + 1) % ORDER.length]);
  });

  function choose(next: Decision, focus = false) {
    setTouched(true);
    setDecision(next);
    if (focus) buttons.current[next]?.focus();
  }
  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    const step = ({ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1} as Record<string, number>)[event.key];
    if (!step) return;
    event.preventDefault();
    choose(CHOICES[(CHOICES.indexOf(decision) + step + CHOICES.length) % CHOICES.length], true);
  }

  const actors = [...new Map(records.map(record => [record.actor, record])).values()].slice(0, 5);
  const stream = Array.from({length: Math.min(STREAM, records.length)}, (_, index) => ({record: records[(tick + index) % records.length], key: tick + index}));
  const outcome = demo.outcomes[decision];

  return (
    <div ref={root} className={`cp site-window is-${decision}`} onPointerDown={() => setTouched(true)}>
      <div className='cp-bar'>
        <span className='cp-dot' aria-hidden='true' />
        <span className='cp-path'>{site.brand.name.toLowerCase()} / {words.gateway.toLowerCase()}</span>
        <span className='site-badge'>{site.labels.illustrative}</span>
        <span className='cp-ref'>{demo.record.reference}</span>
      </div>

      <div className='cp-body'>
        <aside className='cp-col cp-workers' aria-label={words.worker}>
          <p className='cp-label'>{words.worker}</p>
          <ul>
            {actors.map(record => (
              <li key={record.actor} className={record.actor === demo.worker ? 'is-current' : undefined}>
                <span className={`cp-status is-${record.actor === demo.worker ? decision : record.decision === 'review' ? 'review' : 'allow'}`} aria-hidden='true' />
                <span>{record.actor}</span>
              </li>
            ))}
          </ul>
        </aside>

        <div className='cp-col cp-request'>
          <div className='cp-proposal'>
            <p className='cp-label is-model'><Cpu size={12} aria-hidden='true' /> {words.proposedBy}</p>
            <p className='cp-proposal-text'>{demo.action}</p>
            <dl className='cp-meta'>
              <div><dt>{words.worker}</dt><dd>{demo.worker}</dd></div>
              <div><dt>{words.target}</dt><dd>{demo.target}</dd></div>
            </dl>
          </div>

          <ol className='cp-path-checks' aria-label={words.checks}>
            {demo.checks.map(check => (
              <li key={check.id}>
                <span className='cp-check' aria-hidden='true'><Check size={11} strokeWidth={2.6} /></span>
                <span><strong>{check.label}</strong>{check.detail && <small>{check.detail}</small>}</span>
              </li>
            ))}
          </ol>

          <div className='cp-gate' aria-hidden='true'><span /></div>

          <div className='cp-choices' role='radiogroup' aria-label={words.chooseDecision} onKeyDown={onKey}>
            {CHOICES.map(choice => (
              <button key={choice} ref={element => {buttons.current[choice] = element;}} type='button' role='radio'
                aria-checked={decision === choice} aria-controls={outcomeId} tabIndex={decision === choice ? 0 : -1}
                className={`is-${choice}`} onClick={() => choose(choice)} onFocus={() => setTouched(true)}>
                {demo.outcomes[choice].label}
              </button>
            ))}
          </div>

          <div id={outcomeId} className='cp-outcome' aria-live='polite'>
            <DecisionTag decision={decision} label={words.decisions[decision]} />
            <p className='cp-outcome-title'>{outcome.title}</p>
            <p className='cp-outcome-body'>{outcome.body}</p>
          </div>
        </div>

        <div className='cp-col cp-stream'>
          <p className='cp-label'>{streamLabel}</p>
          <ol aria-label={streamLabel}>
            {stream.map(({record, key}, index) => (
              <li key={key} className={index === stream.length - 1 && tick > 0 ? 'is-new' : undefined}>
                <span className='cp-time'>{record.time}</span>
                <span className='cp-actor'>{record.actor}</span>
                <span className='cp-action'>{record.action}</span>
                <DecisionTag decision={record.decision} label={words.decisions[record.decision]} />
              </li>
            ))}
          </ol>
          <p className='cp-prompt' aria-hidden='true'>{site.brand.name.toLowerCase()} <span>›</span> <i /></p>
        </div>
      </div>
    </div>
  );
}
