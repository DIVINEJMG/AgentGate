import { useEffect, useRef, useState } from 'react';
import type { HeroWorld } from './heroWorld';
import './home-hero.css';

type TargetRoute = 'signup' | 'product';

// Kept as presentation data so future CMS content can replace the copy without changing the scene logic.
const HERO_SCENES = [
  {
    word: 'AUTONOMY',
    label: '01 / THE WORK',
    title: 'Give AI workers real work.',
    body: 'Create focused roles, assign durable jobs, and let useful work move forward across your organization.',
  },
  {
    word: 'CONTROL',
    label: '02 / THE BOUNDARY',
    title: 'Decide what may happen.',
    body: 'Every external action meets explicit capabilities and deterministic policy before it can proceed.',
  },
  {
    word: 'OVERSIGHT',
    label: '03 / THE PEOPLE',
    title: 'Keep judgment with people.',
    body: 'Sensitive work pauses for human approval, with the context and record needed to make a clear decision.',
  },
] as const;

export default function HomeHero({ onNavigate }: { onNavigate: (route: TargetRoute) => void }) {
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
  }, []);

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

  return <section className={`audoryn-hero home-hero-scene-${slide}`} ref={sectionRef} aria-label='Audoryn introduction' onFocusCapture={() => setPaused(true)} onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setPaused(false); }}>
    <div className='home-hero-atmosphere' aria-hidden='true' />
    <div className='home-hero-sculpture' data-ready={ready} aria-hidden='true'>
      <div className='home-hero-static'><span /><span /><span /></div>
      <canvas ref={canvasRef} />
    </div>
    <div className='home-hero-model-hit' ref={hitRef} aria-hidden='true' />
    <div className='home-hero-inner public-container'>
      <div className='home-hero-topline'><span>AUDORYN / AN SOT PRODUCT</span><span>AI WORKFORCE CONTROL PLANE</span></div>
      <div className='home-hero-display' key={`word-${slide}`}><h1>{scene.word}</h1></div>
      <div className='home-hero-content' key={`copy-${slide}`}>
        <p className='home-hero-label'>{scene.label}</p>
        <h2>{scene.title}</h2>
        <p>{scene.body}</p>
      </div>
      <div className='home-hero-footer'>
        <div className='home-hero-actions'><button className='public-cta' onClick={() => onNavigate('signup')}>Get started</button><button className='public-text-action' onClick={() => onNavigate('product')}>Explore product <span>→</span></button></div>
        <span className='home-hero-interaction'>MOVE / DRAG TO EXPLORE</span>
        <div className='home-hero-pagination' aria-label='Hero scenes'>
          <span className='home-hero-count'>0{slide + 1} / 0{HERO_SCENES.length}</span>
          {HERO_SCENES.map((item, index) => <button key={item.word} className={index === slide ? 'is-active' : ''} onClick={() => selectSlide(index)} aria-label={`Show ${item.word.toLowerCase()} scene`} aria-current={index === slide ? 'true' : undefined}><span /></button>)}
        </div>
      </div>
    </div>
  </section>;
}
