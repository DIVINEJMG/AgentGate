import { useEffect } from 'react';
import { ArrowRight, ArrowUpRight, BookOpenText, Boxes, Compass, LockKeyhole, Scale } from 'lucide-react';
import './resources-showcase.css';

type Destination = 'product' | 'solutions' | 'security' | 'pricing' | 'login';

const guides = [
  { number: '01', icon: Boxes, topic: 'THE PLATFORM', title: 'Explore the product', description: 'See how workers, jobs, runtime, supervision and connected tools fit together.', route: 'product' },
  { number: '02', icon: Compass, topic: 'WHERE IT FITS', title: 'Find a starting point', description: 'Browse practical responsibilities supported by Audoryn workforce templates.', route: 'solutions' },
  { number: '03', icon: LockKeyhole, topic: 'THE CONTROL MODEL', title: 'Understand security', description: 'Follow an action through identity, capability, policy and human review.', route: 'security' },
  { number: '04', icon: Scale, topic: 'CAPACITY', title: 'Review plan structure', description: 'Compare worker capacity and see the current billing availability.', route: 'pricing' },
] as const;

export default function ResourcesShowcase({ onNavigate }: { onNavigate: (route: Destination) => void }) {
  useEffect(() => {
    const root = document.querySelector<HTMLElement>('.resources-page');
    if (!root || window.matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) return;
    const observer = new IntersectionObserver((entries) => { entries.forEach((entry) => { if (entry.isIntersecting) { entry.target.classList.add('is-visible'); observer.unobserve(entry.target); } }); }, { threshold: .08, rootMargin: '0px 0px -5% 0px' });
    const items = root.querySelectorAll<HTMLElement>('[data-resources-reveal]');
    items.forEach((item) => observer.observe(item));
    root.classList.add('resources-motion-ready');
    return () => { observer.disconnect(); root.classList.remove('resources-motion-ready'); };
  }, []);

  return <div className='resources-page'>
    <section className='resources-hero'><div className='public-container resources-hero-inner'><div><p className='resources-kicker'>AUDORYN / RESOURCES</p><h1>Know the system.<br/>Then put it to work.</h1><p>Clear starting points for understanding the product, finding a use case and seeing how autonomous work stays controlled.</p></div><div className='resources-hero-index' aria-hidden='true'><span>FIELD GUIDE / 01—04</span><div><i/><i/><i/><i/></div><strong>Start with what you need to know.</strong></div></div></section>

    <section className='resources-directory public-container' data-resources-reveal><div className='resources-directory-head'><div><p className='resources-kicker'>EXPLORE</p><h2>Four ways in.</h2></div><span>SELECT A TOPIC TO CONTINUE</span></div><div className='resources-rows'>{guides.map((guide) => { const Icon = guide.icon; return <button key={guide.number} type='button' onClick={() => onNavigate(guide.route)}><span className='resources-row-number'>{guide.number}</span><span className='resources-row-icon'><Icon size={22} strokeWidth={1.5} aria-hidden='true'/></span><span className='resources-row-copy'><small>{guide.topic}</small><strong>{guide.title}</strong></span><span className='resources-row-description'>{guide.description}</span><ArrowUpRight className='resources-row-arrow' size={19} aria-hidden='true'/></button>; })}</div></section>

    <section className='resources-workspace' data-resources-reveal><div className='public-container resources-workspace-inner'><div className='resources-workspace-icon'><BookOpenText size={26} strokeWidth={1.4}/></div><div><p className='resources-kicker'>FOR OPERATORS</p><h2>Guidance lives with the work.</h2><p>Signed-in teams can find operational guidance in the workspace, close to runtime, authorization and incident controls.</p></div><button type='button' onClick={() => onNavigate('login')}>Open the workspace <ArrowRight size={16}/></button></div></section>
  </div>;
}
