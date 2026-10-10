import { usePublicText, usePublicCollection } from './content/ContentContext';
import { useState } from 'react';



export default function HomeOperatingView() {
  const text = usePublicText('HomeOperatingView');
const AREAS = usePublicCollection('HomeOperatingView','AREAS', [
  {
    label: text('field-workers-85163e5610', "Workers"),
    index: '01',
    summary: text('field-roles-identity-schedules-745e50c22d', "Roles, identity, schedules"),
    title: text('field-every-worker-has-a-defined-place-in-the-workf-89b4d9da4c', "Every worker has a defined place in the workforce."),
    description: text('field-a-worker-starts-with-a-role-a-responsibility-ccf7d50e74', "A worker starts with a role, a responsibility, a schedule and a human supervisor. Its identity stays distinct from the people who oversee it."),
    path: [text('field-role-565aa2aef1', "ROLE"), text('field-identity-18d26c021d', "IDENTITY"), text('field-supervisor-0843250cdd', "SUPERVISOR")],
    signal: text('field-defined-before-work-begins-d9b8dff0bc', "DEFINED BEFORE WORK BEGINS"),
  },
  {
    label: text('field-jobs-1156036829', "Jobs"),
    index: '02',
    summary: text('field-objectives-queue-runtime-5f3eb587cd', "Objectives, queue, runtime"),
    title: text('field-work-moves-with-an-objective-and-a-visible-st-789b8eb66e', "Work moves with an objective and a visible state."),
    description: text('field-jobs-carry-the-expected-result-requirements-a-ac13242768', "Jobs carry the expected result, requirements and completion criteria into a durable queue. Teams can follow what is waiting, running and complete."),
    path: [text('field-objective-fa89312554', "OBJECTIVE"), text('field-queue-e6e1850645', "QUEUE"), text('field-result-d4499aeef2', "RESULT")],
    signal: text('field-work-remains-inspectable-7ec605cb6a', "WORK REMAINS INSPECTABLE"),
  },
  {
    label: text('field-governance-f2b380bde6', "Governance"),
    index: '03',
    summary: text('field-policy-risk-approvals-964e9a0184', "Policy, risk, approvals"),
    title: text('field-authority-is-checked-at-the-action-boundary-6394c316f5', "Authority is checked at the action boundary."),
    description: text('field-a-connected-tool-is-never-permission-on-its-o-e49c8a9036', "A connected tool is never permission on its own. Capabilities, risk and policy decide whether an action can proceed or must wait for approval."),
    path: [text('field-request-dcd502e679', "REQUEST"), text('field-policy-af3080a300', "POLICY"), text('field-decision-d325b21ab0', "DECISION")],
    signal: text('field-authority-is-explicit-4e82d15a0e', "AUTHORITY IS EXPLICIT"),
  },
  {
    label: text('field-supervision-139db7b565', "Supervision"),
    index: '04',
    summary: text('field-escalations-incidents-audit-1d6ff31b17', "Escalations, incidents, audit"),
    title: text('field-people-can-see-the-moments-that-need-them-f2d0a92293', "People can see the moments that need them."),
    description: text('field-exceptional-work-is-routed-to-a-named-human-t-50f2967ec0', "Exceptional work is routed to a named human. The decision and outcome stay together in an audit record the team can inspect."),
    path: [text('field-signal-1ead560f03', "SIGNAL"), text('field-human-ad0e3c9f5a', "HUMAN"), text('field-record-b6fb478db7', "RECORD")],
    signal: text('field-human-attention-stays-visible-0798a05ed0', "HUMAN ATTENTION STAYS VISIBLE"),
  },
] as const);

  const [activeId, setActiveId] = useState(AREAS[0].id);
  const active=Math.max(0,AREAS.findIndex(item=>item.id===activeId));
  const setActive=(index:number)=>setActiveId(AREAS[index].id);
  const area = AREAS[active];

  return <div className='home-operating' aria-label={text('copy-explore-the-audoryn-operating-model-a9d7e0253b', "Explore the Audoryn operating model")}>
    <div className='home-operating-topline'><span>{text('copy-workforce-operating-model-556f4d0c82', "WORKFORCE / OPERATING MODEL")}</span><span>{text('copy-select-a-layer-to-explore-1fd5163efc', "SELECT A LAYER TO EXPLORE")}</span></div>
    <div className='home-operating-body'>
      <div className='home-operating-nav' role='tablist' aria-label={text('copy-operating-model-layers-31c3040381', "Operating model layers")}>
        {AREAS.map((item, index) => <button
          key={item.id}
          type='button'
          role='tab'
          id={`home-operating-tab-${item.id}`}
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
            document.getElementById(`home-operating-tab-${AREAS[next].id}`)?.focus();
          }}
        >
          <span className='home-operating-index'>{item.index}</span>
          <span className='home-operating-label'><strong>{item.label}</strong><small>{item.summary}</small></span>
          <span className='home-operating-arrow' aria-hidden='true'>↗</span>
        </button>)}
      </div>
      <div className='home-operating-panel' role='tabpanel' id='home-operating-panel' aria-labelledby={`home-operating-tab-${area.id}`} key={area.id}>
        <div className='home-operating-panel-head'><span>{text('copy-view-e3aceeca52', "VIEW / ")}{area.index}</span><span className='home-operating-pulse'>{text('copy-system-layer-5dfddd8ad0', "SYSTEM LAYER")}</span></div>
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
