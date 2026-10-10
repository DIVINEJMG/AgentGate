import { usePublicText, usePublicCollection, usePublicContent } from './content/ContentContext';
import {payloadOf} from './content/contract';
import {PublicMedia} from './ResourcePage';
import { useEffect, useRef, useState } from 'react';
import type { HeroWorld } from './heroWorld';
import './home-hero.css';
import './public-media.css';

type TargetRoute = 'signup' | 'product';

// Kept as presentation data so future CMS content can replace the copy without changing the scene logic.


export default function HomeHero({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
  const text = usePublicText('HomeHero');
const HERO_SCENES = usePublicCollection('HomeHero','HERO_SCENES', [
  {
    word: text('field-autonomy-420717be0c', "AUTONOMY"),
    label: text('field-01-the-work-f635daf91c', "01 / THE WORK"),
    title: text('field-give-ai-workers-real-work-76c7eb1cf0', "Give AI workers real work."),
    body: text('field-create-focused-roles-assign-durable-jobs-and-e42e7e8ad8', "Create focused roles, assign durable jobs, and let useful work move forward across your organization."),
  },
  {
    word: text('field-control-46d51d1f9d', "CONTROL"),
    label: text('field-02-the-boundary-1113735f8a', "02 / THE BOUNDARY"),
    title: text('field-decide-what-may-happen-cb2e31655a', "Decide what may happen."),
    body: text('field-every-external-action-meets-explicit-capabili-90567e9585', "Every external action meets explicit capabilities and deterministic policy before it can proceed."),
  },
  {
    word: text('field-oversight-1717cc98a5', "OVERSIGHT"),
    label: text('field-03-the-people-4fed599598', "03 / THE PEOPLE"),
    title: text('field-keep-judgment-with-people-04e9e242da', "Keep judgment with people."),
    body: text('field-sensitive-work-pauses-for-human-approval-with-a8e0a68fe9', "Sensitive work pauses for human approval, with the context and record needed to make a clear decision."),
  },
] as const);

  const sectionRef = useRef<HTMLElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hitRef = useRef<HTMLDivElement>(null);
  const worldRef = useRef<HeroWorld | null>(null);
  const slideRef = useRef(0);
  const visibleRef = useRef(false);
  const [slide, setSlide] = useState(0);
  const [ready, setReady] = useState(false);
  const [paused, setPaused] = useState(false);
  const [timerKey, setTimerKey] = useState(0);
  const scene = HERO_SCENES[slide];
  const bundle=usePublicContent();
  const media=bundle.page?(payloadOf(bundle.page).hero_scenes as {id:string;media?:Parameters<typeof PublicMedia>[0]['reference'][]}[]).find(item=>item.id===scene.id)?.media?.[0]:undefined;

  useEffect(() => {
    slideRef.current = slide;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      worldRef.current?.render(slide, 0, 0, 0, false);
    }
  }, [slide]);

  useEffect(() => {
    const section = sectionRef.current;
    const canvas = canvasRef.current;
    const hit = hitRef.current;
    if (!section || !canvas || !hit) return;
    let disposed = false;
    let loading = false;
    let frame = 0;
    let lastFrame = 0;
    let currentSlide = slideRef.current;
    let pointerX = 0;
    let pointerY = 0;
    let dragStart: number | null = null;
    let dragOffset = 0;
    const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');

    const tick = (time: number) => {
      const world = worldRef.current;
      if (!world || !visibleRef.current || document.hidden || motionQuery.matches) { frame = 0; return; }
      if (time - lastFrame < 32) { frame = window.requestAnimationFrame(tick); return; }
      lastFrame = time;
      currentSlide += (slideRef.current - currentSlide) * .065;
      if (Math.abs(currentSlide - slideRef.current) < .001) currentSlide = slideRef.current;
      dragOffset *= .94;
      world.render(currentSlide, pointerX + dragOffset, pointerY, time / 1000, true);
      frame = window.requestAnimationFrame(tick);
    };
    const resume = () => {
      if (worldRef.current && visibleRef.current && !document.hidden && !motionQuery.matches && !frame) {
        frame = window.requestAnimationFrame(tick);
      }
    };
    const load = async () => {
      if (worldRef.current || loading || disposed) return;
      loading = true;
      try {
        const { createHeroWorld } = await import('./heroWorld');
        if (disposed) return;
        const world = createHeroWorld(canvas);
        worldRef.current = world;
        const bounds = section.getBoundingClientRect();
        world.resize(bounds.width, bounds.height);
        world.render(slideRef.current, 0, 0, 0, false);
        setReady(true);
        resume();
      } catch {
        // The CSS sculpture stays visible if the browser cannot create WebGL.
      } finally {
        loading = false;
      }
    };
    const observer = new IntersectionObserver(([entry]) => {
      visibleRef.current = entry.isIntersecting;
      if (entry.isIntersecting) { void load(); resume(); }
      else { window.cancelAnimationFrame(frame); frame = 0; }
    }, { rootMargin: '150px' });
    observer.observe(section);
    const resizeObserver = new ResizeObserver(() => {
      const bounds = section.getBoundingClientRect();
      worldRef.current?.resize(bounds.width, bounds.height);
      if (motionQuery.matches) worldRef.current?.render(slideRef.current, 0, 0, 0, false);
    });
    resizeObserver.observe(section);

    const movePointer = (event: PointerEvent) => {
      if (event.pointerType === 'touch' || motionQuery.matches) return;
      const bounds = hit.getBoundingClientRect();
      pointerX = Math.max(-1, Math.min(1, ((event.clientX - bounds.left) / bounds.width) * 2 - 1));
      pointerY = Math.max(-1, Math.min(1, ((event.clientY - bounds.top) / bounds.height) * 2 - 1));
      if (dragStart !== null) {
        dragOffset = Math.max(-.8, Math.min(.8, (event.clientX - dragStart) / 360));
      }
    };
    const beginDrag = (event: PointerEvent) => {
      if (event.pointerType === 'touch' || motionQuery.matches) return;
      dragStart = event.clientX;
      setPaused(true);
      hit.setPointerCapture(event.pointerId);
    };
    const endDrag = () => { dragStart = null; setPaused(false); };
    const resetPointer = () => { pointerX = 0; pointerY = 0; };
    const onMotionChange = () => {
      if (motionQuery.matches) {
        window.cancelAnimationFrame(frame);
        frame = 0;
        worldRef.current?.render(slideRef.current, 0, 0, 0, false);
      } else resume();
    };
    hit.addEventListener('pointermove', movePointer);
    hit.addEventListener('pointerdown', beginDrag);
    hit.addEventListener('pointerup', endDrag);
    hit.addEventListener('pointercancel', endDrag);
    hit.addEventListener('pointerleave', resetPointer);
    document.addEventListener('visibilitychange', resume);
    motionQuery.addEventListener('change', onMotionChange);
    return () => {
      disposed = true;
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      resizeObserver.disconnect();
      hit.removeEventListener('pointermove', movePointer);
      hit.removeEventListener('pointerdown', beginDrag);
      hit.removeEventListener('pointerup', endDrag);
      hit.removeEventListener('pointercancel', endDrag);
      hit.removeEventListener('pointerleave', resetPointer);
      document.removeEventListener('visibilitychange', resume);
      motionQuery.removeEventListener('change', onMotionChange);
      worldRef.current?.dispose();
      worldRef.current = null;
    };
  }, [media?.asset_id]);

  useEffect(() => {
    if (paused || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const timer = window.setInterval(() => {
      if (visibleRef.current && !document.hidden && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        setSlide((previous) => (previous + 1) % HERO_SCENES.length);
      }
    }, 7200);
    return () => window.clearInterval(timer);
  }, [paused, timerKey]);

  const selectSlide = (index: number) => {
    setSlide(index);
    setTimerKey((value) => value + 1);
  };

  return <section className={`audoryn-hero home-hero-scene-${slide}`} ref={sectionRef} aria-label={text('copy-audoryn-introduction-9749039862', "Audoryn introduction")} onFocusCapture={() => setPaused(true)} onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setPaused(false); }}>
    <div className='home-hero-atmosphere' aria-hidden='true' />
    <div className='home-hero-sculpture' data-ready={ready} aria-hidden='true'>
      {media?<div className='public-scene-media'><PublicMedia reference={media}/></div>:<><div className='home-hero-static'><span /><span /><span /></div><canvas ref={canvasRef} /></>}
    </div>
    <div className='home-hero-model-hit' ref={hitRef} aria-hidden='true' />
    <div className='home-hero-inner public-container'>
      <div className='home-hero-topline'><span>{text('copy-audoryn-an-sot-product-c6440b4287', "AUDORYN / AN SOT PRODUCT")}</span><span>{text('copy-ai-workforce-control-plane-3947fb7f36', "AI WORKFORCE CONTROL PLANE")}</span></div>
      <div className='home-hero-display' key={`word-${slide}`}><h1>{scene.word}</h1></div>
      <div className='home-hero-content' key={`copy-${slide}`}>
        <p className='home-hero-label'>{scene.label}</p>
        <h2>{scene.title}</h2>
        <p>{scene.body}</p>
      </div>
      <div className='home-hero-footer'>
        <div className='home-hero-actions'><button className='public-cta' onClick={() => onNavigate('signup')}>{text('copy-get-started-760ec87a21', "Get started")}</button><button className='public-text-action' onClick={() => onNavigate('product')}>{text('copy-explore-product-7927662087', "Explore product ")}<span>→</span></button></div>
        <span className='home-hero-interaction'>{text('copy-move-drag-to-explore-7211dd0745', "MOVE / DRAG TO EXPLORE")}</span>
        <div className='home-hero-pagination' aria-label={text('copy-hero-scenes-035f2d30eb', "Hero scenes")}>
          <span className='home-hero-count'>0{slide + 1} / 0{HERO_SCENES.length}</span>
          {HERO_SCENES.map((item, index) => <button key={item.word} className={index === slide ? 'is-active' : ''} onClick={() => selectSlide(index)} aria-label={`Show ${item.word.toLowerCase()} scene`} aria-current={index === slide ? 'true' : undefined}><span /></button>)}
        </div>
      </div>
    </div>
  </section>;
}
