import { usePublicText, usePublicContent, usePublicCollection } from './content/ContentContext';
import { payloadOf } from './content/contract';
import { useEffect, useState } from 'react';
import { ArrowRight, Fingerprint, KeyRound, LockKeyhole, PauseCircle, ScrollText, ShieldCheck, ShieldX } from 'lucide-react';
import './security-showcase.css';

type Decision = 'allow' | 'review' | 'deny';





export default function SecurityShowcase({ onNavigate }: { onNavigate: (route: 'product' | 'signup') => void }) {
  const text = usePublicText('SecurityShowcase');
const decisions: Record<Decision, { label: string; title: string; detail: string; note: string }> = {
  allow: { label: text('field-allow-167eee28ce', "ALLOW"), title: text('field-the-action-may-proceed-a66afdb3f4', "The action may proceed."), detail: text('field-a-permitted-request-can-reach-the-provider-ad-a3a5249935', "A permitted request can reach the provider adapter after the current identity, capability, risk and policy checks pass."), note: text('field-only-an-allowed-request-can-invoke-the-provid-93b8bc397a', "Only an allowed request can invoke the provider.") },
  review: { label: text('field-require-approval-e68f25e675', "REQUIRE APPROVAL"), title: text('field-the-action-waits-for-a-person-560df9917e', "The action waits for a person."), detail: text('field-the-run-pauses-and-an-authorized-reviewer-rec-15f455d318', "The run pauses and an authorized reviewer receives the request. Before an approved action resumes, Audoryn checks its authority again."), note: text('field-approval-is-bound-to-the-exact-requested-acti-d6959bfdb2', "Approval is bound to the exact requested action.") },
  deny: { label: text('field-deny-8d0ee83500', "DENY"), title: text('field-the-action-stops-here-22fac81fb5', "The action stops here."), detail: text('field-a-denied-request-is-blocked-at-the-gateway-th-798dcdbb90', "A denied request is blocked at the gateway. The connected provider is never called for that action."), note: text('field-missing-or-uncertain-authority-fails-closed-ea926c8d04', "Missing or uncertain authority fails closed.") },
};
const controls = usePublicCollection('SecurityShowcase','controls', [
  { icon: Fingerprint, number: '01', title: text('field-distinct-identities-7236c243c1', "Distinct identities"), text: text('field-a-human-session-cannot-act-as-an-ai-worker-ev-7ba10d93e4', "A human session cannot act as an AI worker. Every managed worker has a separate agent identity, and organization boundaries are checked.") },
  { icon: KeyRound, number: '02', title: text('field-protected-credentials-8b6f235803', "Protected credentials"), text: text('field-connected-provider-credentials-remain-in-the-26bb90f2ea', "Connected provider credentials remain in the backend credential boundary. Models do not receive provider tokens or direct provider tools.") },
  { icon: PauseCircle, number: '03', title: text('field-human-intervention-fe41c66238', "Human intervention"), text: text('field-sensitive-actions-can-wait-for-review-organiz-2087e4b7c5', "Sensitive actions can wait for review. Organization, worker and integration execution can be suspended without deleting their configuration.") },
  { icon: ScrollText, number: '04', title: text('field-a-record-to-inspect-6018015ee8', "A record to inspect"), text: text('field-requests-decisions-approvals-and-provider-out-04b89235ae', "Requests, decisions, approvals and provider outcomes carry correlation context so operators can follow what happened.") },
]);

  const [decision, setDecision] = useState<Decision>('review');
  const bundle = usePublicContent();
  if (bundle.page && bundle.source !== 'static') {
    for (const item of payloadOf(bundle.page).security_decisions as {decision:Decision;label:string;heading:string;description:string;note:string}[]) decisions[item.decision]={label:item.label,title:item.heading,detail:item.description,note:item.note};
  }
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
        <div className='security-hero-copy'><p className='security-kicker'>{text('copy-audoryn-security-4397c23ce3', "AUDORYN / SECURITY")}</p><h1>{text('copy-every-action-has-103a099e1b', "Every action has")}<br/>{text('copy-a-boundary-bb17bed45c', "a boundary.")}</h1><p>{text('copy-ai-can-plan-the-work-audoryn-decides-whether-78ceb6dabd', "AI can plan the work. Audoryn decides whether an external action is allowed, must wait for a person or must stop.")}</p><div className='security-hero-line'><ShieldCheck size={18} strokeWidth={1.6}/><span>{text('copy-deterministic-authority-human-oversight-669069e84a', "DETERMINISTIC AUTHORITY / HUMAN OVERSIGHT")}</span></div></div>
        <div className='security-decision' aria-label={text('copy-illustrative-audoryn-action-decision-1a2b7b7bc1', "Illustrative Audoryn action decision")}><div className='security-decision-head'><span>{text('copy-an-action-request-08800080bc', "AN ACTION REQUEST")}</span><span>{text('copy-illustrative-flow-ca1ccbf99b', "ILLUSTRATIVE FLOW")}</span></div><div className='security-request'><span className='security-request-icon'><LockKeyhole size={21} strokeWidth={1.5}/></span><div><small>{text('copy-developer-assistant-github-a54a7269ec', "DEVELOPER ASSISTANT / GITHUB")}</small><strong>{text('copy-create-an-issue-96426fdf3c', "Create an issue")}</strong></div></div><div className='security-decision-path'><div><span>01</span><strong>{text('copy-agent-identity-377692faa6', "Agent identity")}</strong><i/></div><div><span>02</span><strong>{text('copy-declared-capability-1c1e397f84', "Declared capability")}</strong><i/></div><div><span>03</span><strong>{text('copy-risk-and-current-policy-391e032d32', "Risk and current policy")}</strong><i/></div></div><div className='security-decision-options' aria-label={text('copy-explore-possible-policy-decisions-5da5adb321', "Explore possible policy decisions")}>{(Object.keys(decisions) as Decision[]).map((item) => <button type='button' key={item} className={decision === item ? `active ${item}` : ''} aria-pressed={decision === item} onClick={() => setDecision(item)}>{decisions[item].label}</button>)}</div><div className={`security-decision-result ${decision}`} aria-live='polite' key={decision}><span>{decision === 'allow' ? <ShieldCheck size={18}/>:decision === 'deny' ? <ShieldX size={18}/>:<PauseCircle size={18}/>} {current.label}</span><strong>{current.title}</strong><p>{current.detail}</p></div><div className='security-decision-foot'>{current.note}</div></div>
      </div>
    </section>

    <section id='security-boundary' className='security-boundary public-container' data-security-reveal><div className='security-boundary-intro'><p className='security-kicker'>{text('copy-the-control-path-d052e00394', "THE CONTROL PATH")}</p><h2>{text('copy-a-connected-tool-does-not-grant-permission-to-6075b0720d', "A connected tool does not grant permission to use it.")}</h2><p>{text('copy-each-request-passes-through-explicit-checks-b-9656188f3c', "Each request passes through explicit checks before it reaches an external system. AI instructions and memory provide context; they do not become authority.")}</p></div><div className='security-boundary-steps'><div><span>{text('copy-01-who-3c5c2b0535', "01 / WHO")}</span><strong>{text('copy-identity-b7a0468017', "Identity")}</strong><p>{text('copy-confirm-the-worker-and-its-organization-b55fcf9479', "Confirm the worker and its organization.")}</p></div><div><span>{text('copy-02-what-8b73732462', "02 / WHAT")}</span><strong>{text('copy-capability-c93e4a8046', "Capability")}</strong><p>{text('copy-match-the-requested-operation-to-a-declared-s-a701b419ae', "Match the requested operation to a declared scope.")}</p></div><div><span>{text('copy-03-now-0dd6c23cd5', "03 / NOW")}</span><strong>{text('copy-risk-policy-870f2521c9', "Risk + policy")}</strong><p>{text('copy-evaluate-current-conditions-and-the-determini-9f24af0930', "Evaluate current conditions and the deterministic rule.")}</p></div><div><span>{text('copy-04-outcome-40a1cb34bd', "04 / OUTCOME")}</span><strong>{text('copy-act-wait-or-stop-e441297cee', "Act, wait or stop")}</strong><p>{text('copy-allow-require-human-approval-or-deny-afdc969450', "Allow, require human approval or deny.")}</p></div></div></section>

    <section className='security-controls' data-security-reveal><div className='public-container'><div className='security-controls-heading'><div><p className='security-kicker'>{text('copy-operational-safeguards-57559ef80d', "OPERATIONAL SAFEGUARDS")}</p><h2>{text('copy-control-continues-after-a-decision-6627ca03c2', "Control continues after a decision.")}</h2></div><p>{text('copy-authority-can-change-while-work-is-underway-a-034f259771', "Authority can change while work is underway. Audoryn keeps the relevant checks and the human response in the execution path.")}</p></div><div className='security-control-grid'>{controls.map((item) => { const Icon = item.icon; return <div key={item.title}><div className='security-control-top'><span>{item.number}</span><Icon size={20} strokeWidth={1.5} aria-hidden='true'/></div><h3>{item.title}</h3><p>{item.text}</p></div>; })}</div></div></section>

    <section className='security-close public-container' data-security-reveal><div><p className='security-kicker'>{text('copy-security-by-design-86b81a540c', "SECURITY BY DESIGN")}</p><h2>{text('copy-useful-autonomy-needs-clear-authority-47bbf399c6', "Useful autonomy needs clear authority.")}</h2><p>{text('copy-see-how-these-controls-fit-into-the-product-t-641544075f', "See how these controls fit into the product, then start with a worker and a defined job.")}</p></div><div><button type='button' className='security-primary' onClick={() => onNavigate('product')}>{text('copy-explore-the-product-155936362b', "Explore the product ")}<ArrowRight size={16}/></button><button type='button' className='security-secondary' onClick={() => onNavigate('signup')}>{text('copy-get-started-3980693691', "Get started ")}<ArrowRight size={15}/></button></div></section>
  </div>;
}
