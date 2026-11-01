import { useEffect, useImperativeHandle, useRef, useState, type Ref, type RefObject } from "react";
import { useMotionAllowed } from "../motion/preference";
import type { IdentityStage, StageQuality } from "./scene/IdentityStage";

export type IdentityPhase = "idle" | "verifying" | "confirmed";
export type SceneStatus = "loading" | "ready" | "fallback";

export interface IdentitySceneHandle {
  pulse: () => void;
}

export interface ScenePoint {
  x: number;
  y: number;
}
/** Live points on the band, in design-canvas coordinates. */
export interface SceneAnchors {
  a: ScenePoint;
  b: ScenePoint;
  centre: ScenePoint;
}
export interface SceneFrame {
  denied: number;
  pointerX: number;
  pointerY: number;
  anchors: SceneAnchors | null;
}

const NARROW_WIDTH = 900;
const DENIED_MS = 3200;
const DESIGN_WIDTH = 1920;
const DESIGN_HEIGHT = 1080;
const WARMUP_FRAMES = 4;

/** Probes once: null without WebGL2, otherwise whether a GPU is doing the rendering. */
function probeWebgl(): { software: boolean } | null {
  if (typeof WebGL2RenderingContext === "undefined") return null;
  try {
    const context = document.createElement("canvas").getContext("webgl2");
    if (!context) return null;
    const info = context.getExtension?.("WEBGL_debug_renderer_info");
    const renderer = info ? String(context.getParameter(info.UNMASKED_RENDERER_WEBGL)) : "";
    context.getExtension?.("WEBGL_lose_context")?.loseContext();
    return { software: /swiftshader|llvmpipe|software|basic render/i.test(renderer) };
  } catch {
    return null;
  }
}

function chooseQuality(software: boolean): StageQuality {
  const memory = (navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? 8;
  const low =
    software ||
    innerWidth < NARROW_WIDTH ||
    (navigator.hardwareConcurrency || 8) <= 4 ||
    memory <= 4;
  return low ? "low" : "high";
}

const follow = (dt: number, rate: number) => 1 - Math.exp(-dt * rate);

/** The strands' visual crossing plus two callout points, as upstream App.svelte frames them. */
function measureAnchors(stage: IdentityStage): SceneAnchors {
  const k = Math.min(innerWidth / DESIGN_WIDTH, innerHeight / DESIGN_HEIGHT);
  const at = (u: number) => {
    const point = stage.project(u);
    return { x: point.x / k, y: point.y / k };
  };
  const [p1, p2, q1, q2] = [at(0.225), at(0.275), at(0.725), at(0.775)];
  const rx = p2.x - p1.x;
  const ry = p2.y - p1.y;
  const sx = q2.x - q1.x;
  const sy = q2.y - q1.y;
  const den = rx * sy - ry * sx;
  const t = Math.abs(den) > 1e-3 ? ((q1.x - p1.x) * sy - (q1.y - p1.y) * sx) / den : 0.5;
  return { a: at(0.65), b: at(0.36), centre: { x: p1.x + rx * t, y: p1.y + ry * t } };
}

/**
 * Decorative backdrop. The form never waits on it: WebGL, the lazy chunk or an asset
 * failing only leaves the CSS paper behind the panel. It reads no credential content,
 * and no authentication outcome depends on anything it does.
 */
export function IdentityScene({
  phase,
  rejection,
  panel,
  compact = false,
  onStatus,
  onDenied,
  onFrame,
  ref,
}: {
  phase: IdentityPhase;
  rejection: number;
  panel: RefObject<HTMLElement | null>;
  compact?: boolean;
  onStatus: (status: SceneStatus) => void;
  onDenied: (denied: boolean) => void;
  onFrame?: (frame: SceneFrame) => void;
  ref: Ref<IdentitySceneHandle>;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const stage = useRef<IdentityStage | null>(null);
  const [mounted, setMounted] = useState(false);
  const motion = useMotionAllowed();
  const live = useRef({
    phase,
    motion,
    compact,
    relayout: null as (() => void) | null,
    denied: false,
    glitch: 0,
    pulses: 0,
    pointer: [0, 0] as [number, number],
  });

  useEffect(() => {
    live.current.phase = phase;
    live.current.motion = motion;
    live.current.compact = compact;
    live.current.relayout?.();
  }, [phase, motion, compact]);

  useImperativeHandle(ref, () => ({
    pulse: () => {
      if (!live.current.motion) return;
      live.current.pulses += 1;
      stage.current?.pulse((live.current.pulses * 0.137) % 1);
    },
  }));

  useEffect(() => {
    const state = live.current;
    const element = canvas.current;
    const webgl = probeWebgl();
    if (!element || !webgl) {
      onStatus("fallback");
      return;
    }
    let cancelled = false;
    let created: IdentityStage | null = null;
    let observer: ResizeObserver | null = null;
    const report = (instance: IdentityStage) => {
      onFrame?.({
        denied: instance.targets.denied,
        pointerX: instance.targets.parallaxX,
        pointerY: instance.targets.parallaxY,
        anchors: state.compact ? null : measureAnchors(instance),
      });
    };
    const surface = element.closest(".hp-auth-surface");
    const layout = () => {
      const rect = panel.current?.getBoundingClientRect();
      created?.setLayout({
        panel: rect
          ? { left: rect.left, top: rect.top, width: rect.width, height: rect.height }
          : null,
        compact: live.current.compact,
      });
      // Without a running loop nothing else repaints the resized drawing buffer.
      if (!live.current.motion && created) {
        created.renderOnce();
        report(created);
      }
    };
    live.current.relayout = layout;
    const fail = () => {
      if (cancelled) return;
      cancelled = true;
      created?.dispose();
      created = null;
      stage.current = null;
      setMounted(false);
      onStatus("fallback");
    };
    const onPointer = (event: PointerEvent) => {
      live.current.pointer = [
        (event.clientX / innerWidth) * 2 - 1,
        (event.clientY / innerHeight) * 2 - 1,
      ];
    };
    import("./scene/IdentityStage")
      .then(({ IdentityStage: Stage }) => {
        if (cancelled) return;
        const instance = new Stage(
          element,
          chooseQuality(webgl.software),
          `${import.meta.env.BASE_URL}identity/`,
          fail,
        );
        created = instance;
        stage.current = instance;
        layout();
        let frames = 0;
        instance.onFrame = (dt) => {
          if (!state.motion) return;
          const t = instance.targets;
          frames += 1;
          if (frames === WARMUP_FRAMES) {
            instance.playAssemble();
            onStatus("ready");
          }
          t.verify += ((state.phase === "idle" ? 0 : 1) - t.verify) * follow(dt, 6);
          t.denied += ((state.denied ? 1 : 0) - t.denied) * follow(dt, state.denied ? 28 : 3);
          t.parallaxX += (state.pointer[0] - t.parallaxX) * follow(dt, 3);
          t.parallaxY += (state.pointer[1] - t.parallaxY) * follow(dt, 3);
          t.cardsReveal += ((frames > WARMUP_FRAMES ? 1 : 0) - t.cardsReveal) * follow(dt, 1.6);
          state.glitch *= Math.exp(-dt * 6);
          t.glitch = state.glitch;
          t.gather += ((state.phase === "confirmed" ? 1 : 0) - t.gather) * follow(dt, 2.5);
          report(instance);
          instance.maxFps = instance.busy || t.verify > 0.01 || t.denied > 0.01 ? 0 : 30;
        };
        window.addEventListener("pointermove", onPointer, { passive: true });
        window.addEventListener("resize", layout);
        surface?.addEventListener("scroll", layout, { passive: true });
        if (panel.current) {
          observer = new ResizeObserver(layout);
          observer.observe(panel.current);
        }
        setMounted(true);
      })
      .catch(fail);
    return () => {
      cancelled = true;
      state.relayout = null;
      observer?.disconnect();
      window.removeEventListener("pointermove", onPointer);
      window.removeEventListener("resize", layout);
      surface?.removeEventListener("scroll", layout);
      created?.dispose();
      stage.current = null;
      setMounted(false);
    };
  }, [onFrame, onStatus, panel]);

  // The RAF exists only while motion is allowed; otherwise one settled frame stands in.
  useEffect(() => {
    const instance = stage.current;
    if (!mounted || !instance) return;
    if (motion) {
      instance.start();
      return () => instance.stop();
    }
    instance.stop();
    instance.setAssembled();
    instance.targets.cardsReveal = 1;
    instance.targets.parallaxX = instance.targets.parallaxY = 0;
    instance.targets.verify = 0;
    instance.targets.denied = live.current.denied ? 1 : 0;
    instance.renderOnce();
    if (!live.current.compact)
      onFrame?.({ denied: 0, pointerX: 0, pointerY: 0, anchors: measureAnchors(instance) });
    onStatus("ready");
  }, [motion, mounted, onFrame, onStatus]);

  useEffect(() => {
    if (!rejection) return;
    const state = live.current;
    state.denied = true;
    state.glitch = 1;
    onDenied(true);
    if (!state.motion) {
      if (stage.current) stage.current.targets.denied = 1;
      stage.current?.renderOnce();
    }
    const timer = setTimeout(() => {
      state.denied = false;
      onDenied(false);
      if (!state.motion && stage.current) {
        stage.current.targets.denied = 0;
        stage.current.renderOnce();
      }
    }, DENIED_MS);
    return () => {
      clearTimeout(timer);
      state.denied = false;
      onDenied(false);
    };
  }, [rejection, onDenied]);

  useEffect(() => {
    if (phase === "confirmed" && motion) stage.current?.playDissolve();
  }, [phase, motion]);

  return <canvas className="hp-identity-canvas" ref={canvas} aria-hidden="true" />;
}
