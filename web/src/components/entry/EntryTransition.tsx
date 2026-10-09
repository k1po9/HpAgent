import { useRef, useSyncExternalStore, type RefObject } from "react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import { createParticlePainter, makeParticles } from "./particles";

gsap.registerPlugin(useGSAP);
const motionQuery = "(prefers-reduced-motion: reduce)";
const motionSnapshot = () => matchMedia(motionQuery).matches;
function subscribeMotion(callback: () => void) {
  const query = matchMedia(motionQuery);
  query.addEventListener("change", callback);
  return () => query.removeEventListener("change", callback);
}

export function EntryTransition({
  origin,
  destination,
  source,
  onComplete,
}: {
  origin: RefObject<HTMLDivElement | null>;
  destination: RefObject<HTMLDivElement | null>;
  source: "login" | "restore";
  onComplete: () => void;
}) {
  const reducedMotion = useSyncExternalStore(subscribeMotion, motionSnapshot);
  const canvas = useRef<HTMLCanvasElement>(null);
  useGSAP(
    (_context, contextSafe) => {
      let raf = 0;
      let done = false;
      let timeline: gsap.core.Timeline | undefined;
      let painter: ReturnType<typeof createParticlePainter> = null;
      const finish = () => {
        if (done) return;
        done = true;
        cancelAnimationFrame(raf);
        painter?.dispose();
        onComplete();
      };
      // No measurable source means there is no visual transition to preserve.
      // Complete synchronously, rather than blocking access on empty frames.
      if (!origin.current?.getBoundingClientRect().width) {
        finish();
        return;
      }
      const reduced = matchMedia("(prefers-reduced-motion: reduce)");
      const settle = () => {
        if (timeline) timeline.progress(1);
        else finish();
      };
      const visibility = () => {
        if (document.hidden) settle();
      };
      const preference = () => {
        if (reduced.matches) settle();
      };
      const play = contextSafe!(() => {
        if (done) return;
        const from = origin.current,
          to = destination.current;
        if (!from || !to) {
          finish();
          return;
        }
        const shape = from.querySelector<HTMLElement>(
          source === "restore" ? ".hp-recovery-orbit" : ".hp-auth-card",
        );
        const box = (shape ?? from).getBoundingClientRect();
        // A missing layout (hidden frame / nonvisual renderer) must not strand auth.
        if (!box.width || !box.height || document.hidden) {
          finish();
          return;
        }
        const regions = [".hp-rail", ".hp-context-sidebar", ".hp-main-canvas"].map((s) =>
          to.querySelector<HTMLElement>(s),
        );
        const visible = regions.filter(
          (node): node is HTMLElement =>
            !!node && node.getBoundingClientRect().width > 0 && !node.hidden,
        );
        const composer = to.querySelector<HTMLElement>(".hp-composer");
        const content = to.querySelector<HTMLElement>(".hp-main-canvas > section:not([hidden])");
        const detailNodes = [content, composer].filter(
          (node): node is HTMLElement => !!node && node.getBoundingClientRect().width > 0,
        );
        const targets = [...visible, ...(composer ? [composer] : [])].map((node) =>
          node.getBoundingClientRect(),
        );
        const low =
          (navigator.hardwareConcurrency || 8) <= 4 ||
          ((navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? 8) <= 4;
        const duration = reduced.matches ? 0.16 : source === "restore" ? 1 : 1.28;
        if (!reduced.matches) {
          painter = createParticlePainter(
            canvas.current,
            makeParticles(box, targets, low ? 48 : 168, source === "restore" ? "ring" : "rect"),
            innerWidth,
            innerHeight,
            Math.min(devicePixelRatio || 1, low ? 0.75 : 1),
          );
        }
        gsap.set(to, { opacity: 1 });
        const details = from.querySelectorAll(
          source === "restore" ? ".hp-recovery-detail" : ".hp-auth-brand, .hp-auth-card form",
        );
        gsap.set([from, ...visible, ...detailNodes], { willChange: "opacity,transform" });
        gsap.set([...visible, ...detailNodes], { opacity: 0 });
        timeline = gsap.timeline({ paused: true, onComplete: finish });
        if (reduced.matches) {
          timeline
            .to(from, { opacity: 0, duration: 0.16 }, 0)
            .to([...visible, ...detailNodes], { opacity: 1, duration: 0.16 }, 0);
        } else {
          const flow = { progress: 0 };
          timeline
            .to(details, { opacity: 0, duration: duration * 0.16 }, duration * 0.13)
            .to(from, { opacity: 0, duration: duration * 0.4, ease: "power1.in" }, duration * 0.18)
            .to(
              flow,
              {
                progress: 1,
                duration: duration * 0.72,
                ease: "none",
                onUpdate: () => painter?.draw(flow.progress),
              },
              duration * 0.18,
            );
          visible.forEach((node, i) =>
            timeline!.fromTo(
              node,
              { opacity: 0, x: i < 2 ? -8 : 0 },
              { opacity: 1, x: 0, duration: duration * 0.25, ease: "power2.out" },
              duration * (0.28 + i * 0.1),
            ),
          );
          detailNodes.forEach((node, i) =>
            timeline!.fromTo(
              node,
              { opacity: 0, y: 6 },
              { opacity: 1, y: 0, duration: duration * 0.18 },
              duration * (0.64 + i * 0.12),
            ),
          );
          // Fixed visual duration, independent from network / history loading.
          timeline.to({}, { duration: duration * 0.1 }, duration * 0.9);
        }
        timeline.play(0);
      });
      // Let AppShell restore its real route and measure its responsive layout.
      raf = requestAnimationFrame(() => {
        raf = requestAnimationFrame(play);
      });
      window.addEventListener("resize", settle);
      document.addEventListener("visibilitychange", visibility);
      reduced.addEventListener("change", preference);
      // Last-resort escape if frames stop arriving while the page remains visible.
      const watchdog = setTimeout(settle, 2200);
      return () => {
        done = true;
        cancelAnimationFrame(raf);
        clearTimeout(watchdog);
        timeline?.kill();
        painter?.dispose();
        window.removeEventListener("resize", settle);
        document.removeEventListener("visibilitychange", visibility);
        reduced.removeEventListener("change", preference);
      };
    },
    { dependencies: [source, onComplete], revertOnUpdate: true },
  );
  return reducedMotion ? null : (
    <canvas className="hp-entry-particles" ref={canvas} aria-hidden="true" />
  );
}
