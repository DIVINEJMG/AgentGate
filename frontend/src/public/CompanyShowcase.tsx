import { useEffect } from 'react';
import { ArrowDown, ArrowRight, ArrowUpRight } from 'lucide-react';
import './company-showcase.css';

type Destination = 'product' | 'signup';

const portfolio = [
  { number: '01', name: 'SOT CM', category: 'Content operations', description: 'Publishing workflows and administrative control for websites.', href: 'https://sot-org.onrender.com/products/sot-cms' },
  { number: '02', name: 'SOT TRACK', category: 'Execution visibility', description: 'Planning, tracking and visibility into delivery.', href: 'https://sot-org.onrender.com/products/sot-track' },
  { number: '03', name: 'SOT Browser', category: 'Desktop software', description: 'A native browser with smart handoff and installer distribution.', href: 'https://sot-org.onrender.com/products/sot-browser' },
] as const;

export default function CompanyShowcase({ onNavigate }: { onNavigate: (route: Destination) => void }) {
  useEffect(() => {
    const root = document.querySelector<HTMLElement>('.company-page');
    if (!root || window.matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) return;
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.08, rootMargin: '0px 0px -5% 0px' });
    root.querySelectorAll<HTMLElement>('[data-company-reveal]').forEach((item) => observer.observe(item));
    root.classList.add('company-motion-ready');
    return () => { observer.disconnect(); root.classList.remove('company-motion-ready'); };
  }, []);

  return <div className='company-page'>
    <section className='company-hero' aria-labelledby='company-title'>
      <div className='public-container company-hero-inner'>
        <div className='company-hero-copy'>
          <p className='company-eyebrow'>AUDORYN <span>/</span> COMPANY</p>
          <h1 id='company-title'>Built by SOT.<br/><em>Made for real work.</em></h1>
          <p className='company-hero-lead'>Audoryn is an SOT product. It brings AI workers into practical workflows while keeping authority, review and responsibility visible to people.</p>
          <button className='company-hero-link' type='button' onClick={() => document.getElementById('company-story')?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' })}>Meet the company <ArrowDown size={16} strokeWidth={1.6} aria-hidden='true'/></button>
        </div>
        <div className='company-mark-stage' aria-label='SOT brand mark'>
          <span className='company-mark-ring company-mark-ring-one' aria-hidden='true'/>
          <span className='company-mark-ring company-mark-ring-two' aria-hidden='true'/>
          <img src='/sot-mark.png' alt='SOT logo' width='288' height='288'/>
          <span className='company-stage-label' aria-hidden='true'>SOT / BUILDING IN PUBLIC</span>
        </div>
      </div>
      <div className='public-container company-hero-foot'><span>INDEPENDENT PRODUCT BUILDING</span><span>WEB · DATA · AUTOMATION</span></div>
    </section>

    <section id='company-story' className='company-story public-container' data-company-reveal>
      <div className='company-section-label'><span>01 / THE COMPANY</span><span>ABOUT SOT</span></div>
      <div className='company-story-content'>
        <h2>A practice of turning ideas into working software.</h2>
        <div className='company-story-aside'><p>SOT is the product brand founded by Divine Ogadinma Destiny in Nigeria. Its public portfolio spans web products, data analytics and intelligent automation.</p><p>Audoryn extends that work into autonomous software: a place to define what AI workers can do, supervise what they are doing and keep consequential decisions accountable.</p><a href='https://sot-org.onrender.com/sot-brand' target='_blank' rel='noopener noreferrer'>Visit SOT Brand <ArrowUpRight size={16} aria-hidden='true'/></a></div>
      </div>
    </section>

    <section className='company-portfolio' data-company-reveal>
      <div className='public-container'>
        <div className='company-section-label'><span>02 / THE WORK</span><span>PUBLIC PORTFOLIO</span></div>
        <div className='company-portfolio-heading'><h2>More than one product.<br/><em>One building mindset.</em></h2><p>Three projects featured on SOT’s public site show the range of work behind the brand.</p></div>
        <div className='company-portfolio-list'>
          {portfolio.map((item) => <a key={item.number} href={item.href} target='_blank' rel='noopener noreferrer' className='company-portfolio-row'>
            <span className='company-portfolio-number'>{item.number}</span><span className='company-portfolio-name'>{item.name}</span><span className='company-portfolio-detail'><small>{item.category}</small><span>{item.description}</span></span><ArrowUpRight size={22} strokeWidth={1.5} aria-hidden='true'/>
          </a>)}
        </div>
      </div>
    </section>

    <section className='company-audoryn' data-company-reveal>
      <div className='public-container company-audoryn-inner'>
        <div className='company-section-label'><span>03 / THE NEXT QUESTION</span><span>AUDORYN</span></div>
        <div className='company-audoryn-main'><span className='company-audoryn-symbol' aria-hidden='true'>A<span>↗</span></span><div><p className='company-eyebrow'>WHY AUDORYN</p><h2>When software can act, <em>who stays in control?</em></h2><p>Audoryn is SOT’s answer to a practical product question. Give AI workers clear responsibilities and useful tools, then make their boundaries, approvals and activity understandable to the people operating them.</p><button type='button' onClick={() => onNavigate('product')}>Explore Audoryn <ArrowRight size={17} aria-hidden='true'/></button></div></div>
      </div>
    </section>

    <section className='company-founder public-container' data-company-reveal>
      <div className='company-section-label'><span>04 / THE FOUNDER</span><span>NIGERIA</span></div>
      <div className='company-founder-inner'><div className='company-founder-monogram' aria-hidden='true'>D<span>.</span></div><div><p className='company-eyebrow'>FOUNDER OF SOT</p><h2>Divine Ogadinma Destiny</h2><p>Full-stack developer and tech entrepreneur building across software, data and automation. SOT’s public portfolio documents the products and projects behind that work.</p><a href='https://sot-org.onrender.com/' target='_blank' rel='noopener noreferrer'>Explore the SOT portfolio <ArrowUpRight size={16} aria-hidden='true'/></a></div></div>
    </section>

    <section className='company-closing'><div className='public-container company-closing-inner' data-company-reveal><div><p className='company-eyebrow'>START WITH THE PRODUCT</p><h2>See the work. <em>Keep the control.</em></h2></div><button type='button' onClick={() => onNavigate('signup')}>Get started <ArrowRight size={17} aria-hidden='true'/></button></div></section>
  </div>;
}
