import { usePublicText, usePublicCollection, usePublicContent } from './content/ContentContext';
import {payloadOf} from './content/contract';
import type {Entry} from './content/publicContent.generated';
import {EditorialEntries} from './ResourcePage';
import { useEffect } from 'react';
import { ArrowRight, ArrowUpRight, BookOpenText, Boxes, Compass, LockKeyhole, Scale } from 'lucide-react';
import './resources-showcase.css';

type Destination = 'product' | 'solutions' | 'security' | 'pricing' | 'login';



export default function ResourcesShowcase({ onNavigate }: { onNavigate: (route: Destination) => void }) {
  const text = usePublicText('ResourcesShowcase');
  const bundle=usePublicContent();
  const editorial=bundle.page?(payloadOf(bundle.page).entries as Entry[]||[]).filter(item=>item.category!=='directory'&&!item.label.startsWith('copy:')):[];
const guides = usePublicCollection('ResourcesShowcase','guides', [
  { number: '01', icon: Boxes, topic: text('field-the-platform-38842b400a', "THE PLATFORM"), title: text('field-explore-the-product-336c4b1220', "Explore the product"), description: text('field-see-how-workers-jobs-runtime-supervision-and-b7055fcd00', "See how workers, jobs, runtime, supervision and connected tools fit together."), route: 'product' },
  { number: '02', icon: Compass, topic: text('field-where-it-fits-7e4c169812', "WHERE IT FITS"), title: text('field-find-a-starting-point-c4eeadc583', "Find a starting point"), description: text('field-browse-practical-responsibilities-supported-b-f183a4b223', "Browse practical responsibilities supported by Audoryn workforce templates."), route: 'solutions' },
  { number: '03', icon: LockKeyhole, topic: text('field-the-control-model-e3ca3c8e62', "THE CONTROL MODEL"), title: text('field-understand-security-4214e7dc3d', "Understand security"), description: text('field-follow-an-action-through-identity-capability-949af820d2', "Follow an action through identity, capability, policy and human review."), route: 'security' },
  { number: '04', icon: Scale, topic: text('field-capacity-5dcc1a58dc', "CAPACITY"), title: text('field-review-plan-structure-281c896d4d', "Review plan structure"), description: text('field-compare-worker-capacity-and-see-the-current-b-3927b84abb', "Compare worker capacity and see the current billing availability."), route: 'pricing' },
] as const);

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
    {editorial.length>0&&<div className='public-container'><EditorialEntries entries={editorial}/></div>}
    <section className='resources-hero'><div className='public-container resources-hero-inner'><div><p className='resources-kicker'>{text('copy-audoryn-resources-cdb30c137e', "AUDORYN / RESOURCES")}</p><h1>{text('copy-know-the-system-292c533241', "Know the system.")}<br/>{text('copy-then-put-it-to-work-1bd0d196a1', "Then put it to work.")}</h1><p>{text('copy-clear-starting-points-for-understanding-the-p-678dcf2551', "Clear starting points for understanding the product, finding a use case and seeing how autonomous work stays controlled.")}</p></div><div className='resources-hero-index' aria-hidden='true'><span>{text('copy-field-guide-01-04-055834eaca', "FIELD GUIDE / 01—04")}</span><div><i/><i/><i/><i/></div><strong>{text('copy-start-with-what-you-need-to-know-d0437a195f', "Start with what you need to know.")}</strong></div></div></section>

    <section className='resources-directory public-container' data-resources-reveal><div className='resources-directory-head'><div><p className='resources-kicker'>{text('copy-explore-62f7efda09', "EXPLORE")}</p><h2>{text('copy-four-ways-in-80becfe0c8', "Four ways in.")}</h2></div><span>{text('copy-select-a-topic-to-continue-7af6132cb7', "SELECT A TOPIC TO CONTINUE")}</span></div><div className='resources-rows'>{guides.map((guide) => { const Icon = guide.icon; return <button key={guide.number} type='button' onClick={() => onNavigate(guide.route)}><span className='resources-row-number'>{guide.number}</span><span className='resources-row-icon'><Icon size={22} strokeWidth={1.5} aria-hidden='true'/></span><span className='resources-row-copy'><small>{guide.topic}</small><strong>{guide.title}</strong></span><span className='resources-row-description'>{guide.description}</span><ArrowUpRight className='resources-row-arrow' size={19} aria-hidden='true'/></button>; })}</div></section>

    <section className='resources-workspace' data-resources-reveal><div className='public-container resources-workspace-inner'><div className='resources-workspace-icon'><BookOpenText size={26} strokeWidth={1.4}/></div><div><p className='resources-kicker'>{text('copy-for-operators-06c21c40ac', "FOR OPERATORS")}</p><h2>{text('copy-guidance-lives-with-the-work-81d9868744', "Guidance lives with the work.")}</h2><p>{text('copy-signed-in-teams-can-find-operational-guidance-87baac7d50', "Signed-in teams can find operational guidance in the workspace, close to runtime, authorization and incident controls.")}</p></div><button type='button' onClick={() => onNavigate('login')}>{text('copy-open-the-workspace-aace079591', "Open the workspace ")}<ArrowRight size={16}/></button></div></section>
  </div>;
}
