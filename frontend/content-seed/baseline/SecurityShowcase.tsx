import { useEffect, useState } from 'react';
import { ArrowRight, Fingerprint, KeyRound, LockKeyhole, PauseCircle, ScrollText, ShieldCheck, ShieldX } from 'lucide-react';
import './security-showcase.css';

type Decision = 'allow' | 'review' | 'deny';

const decisions: Record<Decision, { label: string; title: string; detail: string; note: string }> = {
  allow: { label: 'ALLOW', title: 'The action may proceed.', detail: 'A permitted request can reach the provider adapter after the current identity, capability, risk and policy checks pass.', note: 'Only an allowed request can invoke the provider.' },
  review: { label: 'REQUIRE APPROVAL', title: 'The action waits for a person.', detail: 'The run pauses and an authorized reviewer receives the request. Before an approved action resumes, Audoryn checks its authority again.', note: 'Approval is bound to the exact requested action.' },
  deny: { label: 'DENY', title: 'The action stops here.', detail: 'A denied request is blocked at the gateway. The connected provider is never called for that action.', note: 'Missing or uncertain authority fails closed.' },
};

const controls = [
  { icon: Fingerprint, number: '01', title: 'Distinct identities', text: 'A human session cannot act as an AI worker. Every managed worker has a separate agent identity, and organization boundaries are checked.' },
  { icon: KeyRound, number: '02', title: 'Protected credentials', text: 'Connected provider credentials remain in the backend credential boundary. Models do not receive provider tokens or direct provider tools.' },
  { icon: PauseCircle, number: '03', title: 'Human intervention', text: 'Sensitive actions can wait for review. Organization, worker and integration execution can be suspended without deleting their configuration.' },
  { icon: ScrollText, number: '04', title: 'A record to inspect', text: 'Requests, decisions, approvals and provider outcomes carry correlation context so operators can follow what happened.' },
];

export default function SecurityShowcase({ onNavigate }: { onNavigate: (route: 'product' | 'signup') => void }) {
  const [decision, setDecision] = useState<Decision>('review');
  const current = decisions[decision];

  useEffect(() => {
    const root = document.querySelector<HTMLElement>('.security-page');
    if (!root || window.matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) return;
    const observer = new IntersectionObserver((entries) => { entries.forEach((entry) => { if (entry.isIntersecting) { entry.target.classList.add('is-visible'); observer.unobserve(entry.target); } }); }, { threshold: .08 });
    const items = root.querySelectorAll<HTMLElement>('[data-security-reveal]');
    items.forEach((item) => observer.observe(item));
    root.classList.add('security-motion-ready');
    return () => { observer.disconnect(); root.classList.remove('security-motion-ready'); };
  }, []);

  return <div className='security-page'>
    <section className='security-hero'>
      <div className='public-container security-hero-grid'>
        <div className='security-hero-copy'><p className='security-kicker'>AUDORYN / SECURITY</p><h1>Every action has<br/>a boundary.</h1><p>AI can plan the work. Audoryn decides whether an external action is allowed, must wait for a person or must stop.</p><div className='security-hero-line'><ShieldCheck size={18} strokeWidth={1.6}/><span>DETERMINISTIC AUTHORITY / HUMAN OVERSIGHT</span></div></div>
        <div className='security-decision' aria-label='Illustrative Audoryn action decision'><div className='security-decision-head'><span>AN ACTION REQUEST</span><span>ILLUSTRATIVE FLOW</span></div><div className='security-request'><span className='security-request-icon'><LockKeyhole size={21} strokeWidth={1.5}/></span><div><small>DEVELOPER ASSISTANT / GITHUB</small><strong>Create an issue</strong></div></div><div className='security-decision-path'><div><span>01</span><strong>Agent identity</strong><i/></div><div><span>02</span><strong>Declared capability</strong><i/></div><div><span>03</span><strong>Risk and current policy</strong><i/></div></div><div className='security-decision-options' aria-label='Explore possible policy decisions'>{(Object.keys(decisions) as Decision[]).map((item) => <button type='button' key={item} className={decision === item ? `active ${item}` : ''} aria-pressed={decision === item} onClick={() => setDecision(item)}>{decisions[item].label}</button>)}</div><div className={`security-decision-result ${decision}`} aria-live='polite' key={decision}><span>{decision === 'allow' ? <ShieldCheck size={18}/>:decision === 'deny' ? <ShieldX size={18}/>:<PauseCircle size={18}/>} {current.label}</span><strong>{current.title}</strong><p>{current.detail}</p></div><div className='security-decision-foot'>{current.note}</div></div>
      </div>
    </section>

    <section className='security-boundary public-container' data-security-reveal><div className='security-boundary-intro'><p className='security-kicker'>THE CONTROL PATH</p><h2>A connected tool does not grant permission to use it.</h2><p>Each request passes through explicit checks before it reaches an external system. AI instructions and memory provide context; they do not become authority.</p></div><div className='security-boundary-steps'><div><span>01 / WHO</span><strong>Identity</strong><p>Confirm the worker and its organization.</p></div><div><span>02 / WHAT</span><strong>Capability</strong><p>Match the requested operation to a declared scope.</p></div><div><span>03 / NOW</span><strong>Risk + policy</strong><p>Evaluate current conditions and the deterministic rule.</p></div><div><span>04 / OUTCOME</span><strong>Act, wait or stop</strong><p>Allow, require human approval or deny.</p></div></div></section>

    <section className='security-controls' data-security-reveal><div className='public-container'><div className='security-controls-heading'><div><p className='security-kicker'>OPERATIONAL SAFEGUARDS</p><h2>Control continues after a decision.</h2></div><p>Authority can change while work is underway. Audoryn keeps the relevant checks and the human response in the execution path.</p></div><div className='security-control-grid'>{controls.map((item) => { const Icon = item.icon; return <div key={item.title}><div className='security-control-top'><span>{item.number}</span><Icon size={20} strokeWidth={1.5} aria-hidden='true'/></div><h3>{item.title}</h3><p>{item.text}</p></div>; })}</div></div></section>

    <section className='security-close public-container' data-security-reveal><div><p className='security-kicker'>SECURITY BY DESIGN</p><h2>Useful autonomy needs clear authority.</h2><p>See how these controls fit into the product, then start with a worker and a defined job.</p></div><div><button type='button' className='security-primary' onClick={() => onNavigate('product')}>Explore the product <ArrowRight size={16}/></button><button type='button' className='security-secondary' onClick={() => onNavigate('signup')}>Get started <ArrowRight size={15}/></button></div></section>
  </div>;
}
