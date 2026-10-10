import { usePublicText, usePublicContent } from './content/ContentContext';
import {payloadOf} from './content/contract';
import {PublicMedia} from './ResourcePage';
import { useEffect, useRef, useState } from 'react';
import type { ControlWorld } from './controlWorld';
import './home-gate-scene.css';
import './public-media.css';

type Mode = 'hero' | 'journey';
const clamp = (value: number) => Math.min(1, Math.max(0, value));

export default function HomeGateScene({ mode = 'hero' }: { mode?: Mode }) {
  const text = usePublicText('HomeGateScene');
  const PHASES = [text('stage-defined','Work is defined'),text('stage-policy','Policy is evaluated'),text('stage-approval','A person decides'),text('stage-recorded','Execution is recorded')];


  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [ready, setReady] = useState(false);
  const [phase, setPhase] = useState(0);
  const bundle=usePublicContent();
  const media=mode==='journey'&&bundle.page?(payloadOf(bundle.page).journey as {media?:Parameters<typeof PublicMedia>[0]['reference'][]}[])[phase]?.media?.[0]:undefined;

  useEffect(() => {
    const stage = stageRef.current;
    const canvas = canvasRef.current;
    if (!stage || !canvas) return;

    let world: ControlWorld | null = null;
    let active = false;
    let disposed = false;
    let loading = false;
    let frame = 0;
    let current = 0;
    let target = 0;
    let pointerX = 0;
    let pointerY = 0;
    const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');

    const updateProgress = () => {
      const source = mode === 'journey' ? stage.closest('.home-journey') : stage.closest('.public-hero');
      if (!source) return;
      if (mode === 'journey') {
        const chapters = source.querySelectorAll('.home-journey-chapter');
        const readingLine = window.innerHeight * .48;
        let currentChapter = 0;
        chapters.forEach((chapter, index) => {
          if (chapter.getBoundingClientRect().top <= readingLine) currentChapter = index;
        });
        const bounds = chapters[currentChapter]?.getBoundingClientRect();
        const fraction = bounds ? clamp((readingLine - bounds.top) / bounds.height) : 0;
        target = (currentChapter + fraction) / Math.max(1, chapters.length);
        setPhase((previous) => previous === currentChapter ? previous : currentChapter);
        if (motionQuery.matches) renderOnce();
      } else {
        const bounds = source.getBoundingClientRect();
        target = clamp(-bounds.top / Math.max(1, bounds.height));
      }
    };

    const renderOnce = () => {
      if (!world) return;
      world.render(motionQuery.matches ? (mode === 'hero' ? .08 : target) : current, 0, 0, 0, false);
    };

    const tick = (time: number) => {
      if (!active || !world || motionQuery.matches || document.hidden) { frame = 0; return; }
      current += (target - current) * .085;
      world.render(mode === 'hero' ? current * .38 : current, pointerX, pointerY, time / 1000, true);
      frame = window.requestAnimationFrame(tick);
    };

    const resume = () => {
      if (world && active && !motionQuery.matches && !document.hidden && !frame) frame = window.requestAnimationFrame(tick);
    };

    const loadWorld = async () => {
      if (world || loading || disposed) return;
      loading = true;
      try {
        const { createControlWorld } = await import('./controlWorld');
        if (disposed) return;
        world = createControlWorld(canvas);
        const bounds = stage.getBoundingClientRect();
        world.resize(bounds.width, bounds.height);
        updateProgress();
        current = target;
        setReady(true);
        renderOnce();
        resume();
      } catch {
        // The static architectural illustration remains when WebGL is unavailable.
      } finally {
        loading = false;
      }
    };

    const observer = new IntersectionObserver(([entry]) => {
      active = entry.isIntersecting;
      if (!active) {
        window.cancelAnimationFrame(frame);
        frame = 0;
      } else {
        updateProgress();
        void loadWorld();
        resume();
      }
    }, { rootMargin: '220px' });
    observer.observe(stage);

    const resizeObserver = new ResizeObserver(() => {
      if (!world) return;
      const bounds = stage.getBoundingClientRect();
      world.resize(bounds.width, bounds.height);
      if (motionQuery.matches) renderOnce();
    });
    resizeObserver.observe(stage);

    const movePointer = (event: PointerEvent) => {
      if (event.pointerType === 'touch' || motionQuery.matches) return;
      const bounds = stage.getBoundingClientRect();
      pointerX = clamp((event.clientX - bounds.left) / bounds.width) * 2 - 1;
      pointerY = clamp((event.clientY - bounds.top) / bounds.height) * 2 - 1;
    };
    const resetPointer = () => { pointerX = 0; pointerY = 0; };
    const onMotionChange = () => {
      if (motionQuery.matches) {
        window.cancelAnimationFrame(frame);
        frame = 0;
        renderOnce();
      } else resume();
    };

    stage.addEventListener('pointermove', movePointer);
    stage.addEventListener('pointerleave', resetPointer);
    window.addEventListener('scroll', updateProgress, { passive: true });
    window.addEventListener('resize', updateProgress);
    document.addEventListener('visibilitychange', resume);
    motionQuery.addEventListener('change', onMotionChange);

    return () => {
      disposed = true;
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      resizeObserver.disconnect();
      stage.removeEventListener('pointermove', movePointer);
      stage.removeEventListener('pointerleave', resetPointer);
      window.removeEventListener('scroll', updateProgress);
      window.removeEventListener('resize', updateProgress);
      document.removeEventListener('visibilitychange', resume);
      motionQuery.removeEventListener('change', onMotionChange);
      world?.dispose();
    };
  }, [mode,media?.asset_id]);

  return (
    <figure className={`home-gate home-gate-${mode}`} role="img" aria-label={text('copy-a-work-unit-moves-through-the-audoryn-control-5e489b2287', "A work unit moves through the Audoryn control path: work, policy, human approval, and execution")}>
      <div className="home-gate-art" ref={stageRef} data-ready={ready}>
        {media?<div className='public-scene-media'><PublicMedia reference={media}/></div>:<><div className="home-gate-static" aria-hidden="true"><span /><span /><span /><i /></div><canvas ref={canvasRef} aria-hidden="true" /></>}
        <div className="home-gate-coordinate" aria-hidden="true"><span>{text('copy-ag-control-plane-67af4ee941', "AG / CONTROL PLANE")}</span><span>01 — 04</span></div>
        {mode === 'journey' && <div className="home-gate-phase" aria-hidden="true"><span>0{phase + 1} / 04</span><strong>{PHASES[phase]}</strong></div>}
      </div>
      <figcaption><span>{text('copy-the-control-path-ebed635bb1', "THE CONTROL PATH")}</span><span>{text('copy-work-moves-forward-authority-stays-visible-45c3e76beb', "Work moves forward. Authority stays visible.")}</span></figcaption>
    </figure>
  );
}
