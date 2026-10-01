import { useState } from 'react';

const AREAS = [
  {
    label: 'Workers',
    index: '01',
    summary: 'Roles, identity, schedules',
    title: 'Every worker has a defined place in the workforce.',
    description: 'A worker starts with a role, a responsibility, a schedule and a human supervisor. Its identity stays distinct from the people who oversee it.',
    path: ['ROLE', 'IDENTITY', 'SUPERVISOR'],
    signal: 'DEFINED BEFORE WORK BEGINS',
  },
  {
    label: 'Jobs',
    index: '02',
    summary: 'Objectives, queue, runtime',
    title: 'Work moves with an objective and a visible state.',
    description: 'Jobs carry the expected result, requirements and completion criteria into a durable queue. Teams can follow what is waiting, running and complete.',
    path: ['OBJECTIVE', 'QUEUE', 'RESULT'],
    signal: 'WORK REMAINS INSPECTABLE',
  },
  {
    label: 'Governance',
    index: '03',
    summary: 'Policy, risk, approvals',
    title: 'Authority is checked at the action boundary.',
    description: 'A connected tool is never permission on its own. Capabilities, risk and policy decide whether an action can proceed or must wait for approval.',
    path: ['REQUEST', 'POLICY', 'DECISION'],
    signal: 'AUTHORITY IS EXPLICIT',
  },
  {
    label: 'Supervision',
    index: '04',
    summary: 'Escalations, incidents, audit',
    title: 'People can see the moments that need them.',
    description: 'Exceptional work is routed to a named human. The decision and outcome stay together in an audit record the team can inspect.',
    path: ['SIGNAL', 'HUMAN', 'RECORD'],
    signal: 'HUMAN ATTENTION STAYS VISIBLE',
  },
] as const;

export default function HomeOperatingView() {
  const [active, setActive] = useState(0);
  const area = AREAS[active];

  return <div className='home-operating' aria-label='Explore the Audoryn operating model'>
    <div className='home-operating-topline'><span>WORKFORCE / OPERATING MODEL</span><span>SELECT A LAYER TO EXPLORE</span></div>
    <div className='home-operating-body'>
      <div className='home-operating-nav' role='tablist' aria-label='Operating model layers'>
        {AREAS.map((item, index) => <button
          key={item.label}
          type='button'
          role='tab'
          id={`home-operating-tab-${index}`}
          aria-controls='home-operating-panel'
          aria-selected={active === index}
          className={active === index ? 'is-active' : ''}
          onClick={() => setActive(index)}
          onKeyDown={(event) => {
            const next = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? (index + 1) % AREAS.length
              : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? (index - 1 + AREAS.length) % AREAS.length
              : event.key === 'Home' ? 0
              : event.key === 'End' ? AREAS.length - 1 : null;
            if (next === null) return;
            event.preventDefault();
            setActive(next);
            document.getElementById(`home-operating-tab-${next}`)?.focus();
          }}
        >
          <span className='home-operating-index'>{item.index}</span>
          <span className='home-operating-label'><strong>{item.label}</strong><small>{item.summary}</small></span>
          <span className='home-operating-arrow' aria-hidden='true'>↗</span>
        </button>)}
      </div>
      <div className='home-operating-panel' role='tabpanel' id='home-operating-panel' aria-labelledby={`home-operating-tab-${active}`} key={active}>
        <div className='home-operating-panel-head'><span>VIEW / {area.index}</span><span className='home-operating-pulse'>SYSTEM LAYER</span></div>
        <div className='home-operating-diagram' aria-hidden='true'>
          <div className='home-operating-track' />
          {area.path.map((step, index) => <div className={`home-operating-node home-operating-node-${index + 1}`} key={step}>
            <span className='home-operating-node-number'>0{index + 1}</span>
            <span className='home-operating-node-mark' />
            <strong>{step}</strong>
          </div>)}
        </div>
        <div className='home-operating-detail'>
          <span className='home-operating-signal'><i aria-hidden='true' />{area.signal}</span>
          <h3>{area.title}</h3>
          <p>{area.description}</p>
        </div>
      </div>
    </div>
  </div>;
}
