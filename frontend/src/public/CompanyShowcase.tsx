import { usePublicText, usePublicCollection } from './content/ContentContext';
import { useEffect } from 'react';
import { ArrowDown, ArrowRight, ArrowUpRight } from 'lucide-react';
import './company-showcase.css';

type Destination = 'product' | 'signup';



export default function CompanyShowcase({ onNavigate }: { onNavigate: (route: Destination) => void }) {
  const text = usePublicText('CompanyShowcase');
const portfolio = usePublicCollection('CompanyShowcase','portfolio', [
  { number: '01', name: text('field-sot-cm-e32c33dce0', "SOT CM"), category: 'Content operations', description: text('field-publishing-workflows-and-administrative-contr-f78301d3f1', "Publishing workflows and administrative control for websites."), href: text('field-https-sot-org-onrender-com-products-sot-cms-c496610fe3', "https://sot-org.onrender.com/products/sot-cms") },
  { number: '02', name: text('field-sot-track-8f5ed54325', "SOT TRACK"), category: 'Execution visibility', description: text('field-planning-tracking-and-visibility-into-deliver-4a37aa01f7', "Planning, tracking and visibility into delivery."), href: text('field-https-sot-org-onrender-com-products-sot-track-a0151cfb61', "https://sot-org.onrender.com/products/sot-track") },
  { number: '03', name: text('field-sot-browser-a87e224ab8', "SOT Browser"), category: 'Desktop software', description: text('field-a-native-browser-with-smart-handoff-and-insta-43c4c8e488', "A native browser with smart handoff and installer distribution."), href: text('field-https-sot-org-onrender-com-products-sot-brows-46e495e8de', "https://sot-org.onrender.com/products/sot-browser") },
] as const);

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
          <p className='company-eyebrow'>{text('copy-audoryn-a5cc2b7928', "AUDORYN ")}<span>/</span> {text('copy--company-f515efdc98', " COMPANY")}</p>
          <h1 id='company-title'>{text('copy-built-by-sot-435b5f42b7', "Built by SOT.")}<br/><em>{text('copy-made-for-real-work-6e42e0437f', "Made for real work.")}</em></h1>
          <p className='company-hero-lead'>{text('copy-audoryn-is-an-sot-product-it-brings-ai-worker-ce180ec027', "Audoryn is an SOT product. It brings AI workers into practical workflows while keeping authority, review and responsibility visible to people.")}</p>
          <button className='company-hero-link' type='button' onClick={() => document.getElementById('company-story')?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' })}>{text('copy-meet-the-company-fa80fed94d', "Meet the company ")}<ArrowDown size={16} strokeWidth={1.6} aria-hidden='true'/></button>
        </div>
        <div className='company-mark-stage' aria-label={text('copy-sot-brand-mark-815e0a63eb', "SOT brand mark")}>
          <span className='company-mark-ring company-mark-ring-one' aria-hidden='true'/>
          <span className='company-mark-ring company-mark-ring-two' aria-hidden='true'/>
          <img src='/sot-mark.png' alt={text('alt-sot-logo-a0d43671b6', "SOT logo")} width='288' height='288'/>
          <span className='company-stage-label' aria-hidden='true'>{text('copy-sot-building-in-public-e8fae90f61', "SOT / BUILDING IN PUBLIC")}</span>
        </div>
      </div>
      <div className='public-container company-hero-foot'><span>{text('copy-independent-product-building-1a9c2e7940', "INDEPENDENT PRODUCT BUILDING")}</span><span>{text('copy-web-data-automation-e3a6efaf56', "WEB · DATA · AUTOMATION")}</span></div>
    </section>

    <section id='company-story' className='company-story public-container' data-company-reveal>
      <div className='company-section-label'><span>{text('copy-01-the-company-f6a79fb546', "01 / THE COMPANY")}</span><span>{text('copy-about-sot-cdcbdd2b0f', "ABOUT SOT")}</span></div>
      <div className='company-story-content'>
        <h2>{text('copy-a-practice-of-turning-ideas-into-working-soft-a1c12db17d', "A practice of turning ideas into working software.")}</h2>
        <div className='company-story-aside'><p>{text('copy-sot-is-the-product-brand-founded-by-divine-og-a35a441d57', "SOT is the product brand founded by Divine Ogadinma Destiny in Nigeria. Its public portfolio spans web products, data analytics and intelligent automation.")}</p><p>{text('copy-audoryn-extends-that-work-into-autonomous-sof-8391e7c9ea', "Audoryn extends that work into autonomous software: a place to define what AI workers can do, supervise what they are doing and keep consequential decisions accountable.")}</p><a href={text('copy-https-sot-org-onrender-com-sot-brand-0101b7eee9', "https://sot-org.onrender.com/sot-brand")} target='_blank' rel='noopener noreferrer'>{text('copy-visit-sot-brand-4bf7d6d880', "Visit SOT Brand ")}<ArrowUpRight size={16} aria-hidden='true'/></a></div>
      </div>
    </section>

    <section className='company-portfolio' data-company-reveal>
      <div className='public-container'>
        <div className='company-section-label'><span>{text('copy-02-the-work-d0c3894473', "02 / THE WORK")}</span><span>{text('copy-public-portfolio-82f46f3973', "PUBLIC PORTFOLIO")}</span></div>
        <div className='company-portfolio-heading'><h2>{text('copy-more-than-one-product-abdc9f4609', "More than one product.")}<br/><em>{text('copy-one-building-mindset-8edbdd9e91', "One building mindset.")}</em></h2><p>{text('copy-three-projects-featured-on-sot-s-public-site-ac29c8d8e4', "Three projects featured on SOT’s public site show the range of work behind the brand.")}</p></div>
        <div className='company-portfolio-list'>
          {portfolio.map((item) => <a key={item.number} href={item.href} target='_blank' rel='noopener noreferrer' className='company-portfolio-row'>
            <span className='company-portfolio-number'>{item.number}</span><span className='company-portfolio-name'>{item.name}</span><span className='company-portfolio-detail'><small>{item.category}</small><span>{item.description}</span></span><ArrowUpRight size={22} strokeWidth={1.5} aria-hidden='true'/>
          </a>)}
        </div>
      </div>
    </section>

    <section className='company-audoryn' data-company-reveal>
      <div className='public-container company-audoryn-inner'>
        <div className='company-section-label'><span>{text('copy-03-the-next-question-aa6b64e4ac', "03 / THE NEXT QUESTION")}</span><span>{text('copy-audoryn-d5a49e3294', "AUDORYN")}</span></div>
        <div className='company-audoryn-main'><span className='company-audoryn-symbol' aria-hidden='true'>{text('copy-a-2f65dd1e9b', "A")}<span>↗</span></span><div><p className='company-eyebrow'>{text('copy-why-audoryn-af9df11167', "WHY AUDORYN")}</p><h2>{text('copy-when-software-can-act-c2582e34bd', "When software can act, ")}<em>{text('copy-who-stays-in-control-cfc80078a1', "who stays in control?")}</em></h2><p>{text('copy-audoryn-is-sot-s-answer-to-a-practical-produc-e3aff102d6', "Audoryn is SOT’s answer to a practical product question. Give AI workers clear responsibilities and useful tools, then make their boundaries, approvals and activity understandable to the people operating them.")}</p><button type='button' onClick={() => onNavigate('product')}>{text('copy-explore-audoryn-724748852c', "Explore Audoryn ")}<ArrowRight size={17} aria-hidden='true'/></button></div></div>
      </div>
    </section>

    <section className='company-founder public-container' data-company-reveal>
      <div className='company-section-label'><span>{text('copy-04-the-founder-10ee143756', "04 / THE FOUNDER")}</span><span>{text('copy-nigeria-ae6fc1f5ea', "NIGERIA")}</span></div>
      <div className='company-founder-inner'><div className='company-founder-monogram' aria-hidden='true'>{text('copy-d-44d9e7f030', "D")}<span>.</span></div><div><p className='company-eyebrow'>{text('copy-founder-of-sot-25b416e80c', "FOUNDER OF SOT")}</p><h2>{text('copy-divine-ogadinma-destiny-575003ceb8', "Divine Ogadinma Destiny")}</h2><p>{text('copy-full-stack-developer-and-tech-entrepreneur-bu-d4040d5081', "Full-stack developer and tech entrepreneur building across software, data and automation. SOT’s public portfolio documents the products and projects behind that work.")}</p><a href={text('copy-https-sot-org-onrender-com-abe8807249', "https://sot-org.onrender.com/")} target='_blank' rel='noopener noreferrer'>{text('copy-explore-the-sot-portfolio-229391aafa', "Explore the SOT portfolio ")}<ArrowUpRight size={16} aria-hidden='true'/></a></div></div>
    </section>

    <section className='company-closing'><div className='public-container company-closing-inner' data-company-reveal><div><p className='company-eyebrow'>{text('copy-start-with-the-product-36f1733de7', "START WITH THE PRODUCT")}</p><h2>{text('copy-see-the-work-e1a6f9762f', "See the work. ")}<em>{text('copy-keep-the-control-14794c4e8a', "Keep the control.")}</em></h2></div><button type='button' onClick={() => onNavigate('signup')}>{text('copy-get-started-da2b012e4a', "Get started ")}<ArrowRight size={17} aria-hidden='true'/></button></div></section>
  </div>;
}
