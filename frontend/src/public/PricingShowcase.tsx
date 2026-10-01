import { useEffect } from 'react';
import { ArrowRight, Check, ChevronDown, ShieldCheck } from 'lucide-react';
import './pricing-showcase.css';

const plans = [
  { name: 'Free', price: '0', limit: '3', audience: 'Start with a small supervised workforce.', state: 'AVAILABLE NOW' },
  { name: 'Team', price: '49', limit: '15', audience: 'Coordinate a growing workforce across a team.', state: 'CATALOG PLAN' },
  { name: 'Business', price: '199', limit: '75', audience: 'Run more workers across recurring company work.', state: 'CATALOG PLAN' },
  { name: 'Scale', price: '499', limit: '250', audience: 'Plan for a larger governed workforce.', state: 'CATALOG PLAN' },
] as const;

export default function PricingShowcase({ onNavigate }: { onNavigate: (route: 'signup' | 'product') => void }) {
  useEffect(() => {
    const root = document.querySelector<HTMLElement>('.pricing-page');
    if (!root || window.matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) return;
    const observer = new IntersectionObserver((entries) => { entries.forEach((entry) => { if (entry.isIntersecting) { entry.target.classList.add('is-visible'); observer.unobserve(entry.target); } }); }, { threshold: .08, rootMargin: '0px 0px -5% 0px' });
    const items = root.querySelectorAll<HTMLElement>('[data-pricing-reveal]');
    items.forEach((item) => observer.observe(item));
    root.classList.add('pricing-motion-ready');
    return () => { observer.disconnect(); root.classList.remove('pricing-motion-ready'); };
  }, []);

  return <div className='pricing-page'>
    <section className='pricing-hero'><div className='public-container pricing-hero-inner'><div><p className='pricing-kicker'>AUDORYN / PRICING</p><h1>Start free.<br/>Grow with the work.</h1></div><div className='pricing-hero-side'><p>Choose capacity for the number of AI workers you need. The same authority and oversight model applies at every size.</p><span><i/> FREE WORKSPACE AVAILABLE NOW</span></div></div></section>

    <section className='pricing-plans public-container' data-pricing-reveal><div className='pricing-section-head'><div><p className='pricing-kicker'>PLAN CATALOG</p><h2>Capacity at a glance.</h2></div><p>Monthly USD catalog prices. Paid upgrades are not yet available for self-service purchase.</p></div><div className='pricing-plan-grid'>{plans.map((plan, index) => <article className={index === 0 ? 'is-free' : ''} key={plan.name}><div className='pricing-plan-top'><span>0{index + 1} / {plan.name.toUpperCase()}</span><span>{plan.state}</span></div><h3>{plan.name}</h3><p className='pricing-plan-audience'>{plan.audience}</p><div className='pricing-plan-price'><strong>${plan.price}</strong><span>/ month<br/>catalog</span></div><div className='pricing-plan-limit'><span>MANAGED WORKERS</span><strong>Up to {plan.limit}</strong></div>{index === 0 ? <button type='button' onClick={() => onNavigate('signup')}>Get started free <ArrowRight size={15}/></button> : <span className='pricing-plan-unavailable'>Paid activation not yet open</span>}</article>)}</div><p className='pricing-catalog-note'>The backend currently defaults organizations to Free. The paid plan figures above describe the catalog; they are not a live checkout offer.</p></section>

    <section className='pricing-common' data-pricing-reveal><div className='public-container pricing-common-inner'><div><p className='pricing-kicker'>THE SAME CONTROL MODEL</p><h2>Capacity changes.<br/>Authority does not.</h2></div><div className='pricing-common-list'><div><ShieldCheck size={19} strokeWidth={1.5}/><span>Capability and policy checks on external actions</span></div><div><Check size={19} strokeWidth={1.5}/><span>Human approval when the rule requires it</span></div><div><Check size={19} strokeWidth={1.5}/><span>Connected tools and inspectable results</span></div></div></div></section>

    <section className='pricing-questions public-container' data-pricing-reveal><div className='pricing-questions-heading'><p className='pricing-kicker'>COMMON QUESTIONS</p><h2>Before you begin.</h2></div><div className='pricing-faq'><details><summary>Can I start without a paid plan?<ChevronDown size={16}/></summary><p>Yes. New organizations start on Free, which supports up to three managed workers.</p></details><details><summary>Can I purchase Team, Business or Scale today?<ChevronDown size={16}/></summary><p>No. The paid plans are in the product catalog, but a billing provider and checkout are not connected yet.</p></details><details><summary>Does a larger plan give workers more authority?<ChevronDown size={16}/></summary><p>No. Plan capacity is separate from identity, capability, risk, policy, approvals and incident controls.</p></details></div></section>

    <section className='pricing-close'><div className='public-container'><p className='pricing-kicker'>START WITH AUDORYN</p><h2>Put the first worker to work.</h2><p>Begin on Free and define a clear role, job and supervisor.</p><div><button type='button' onClick={() => onNavigate('signup')}>Get started <ArrowRight size={16}/></button><button type='button' onClick={() => onNavigate('product')}>Explore the product <ArrowRight size={16}/></button></div></div></section>
  </div>;
}
