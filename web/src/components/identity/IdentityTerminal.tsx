import { useCallback, useEffect, useRef, useState, type ReactNode, type Ref } from "react";
import { animateSpatial, useMotionAllowed } from "../motion/preference";
import { applyAnchors, DEFAULT_ANCHORS } from "./identityAnchors";
import { IdentityChrome, IdentityHeader, type IdentityMode } from "./IdentityChrome";
import {
  IdentityScene,
  type IdentityPhase,
  type IdentitySceneHandle,
  type SceneFrame,
  type SceneStatus,
} from "./IdentityScene";
import { useIdentityCanvas } from "./identityLayout";
import { LicenseNotice } from "./LicenseNotice";
import type { ClientLocation } from "./useClientLocation";

const DESIGN_WIDTH = 1920;
const DESIGN_HEIGHT = 1080;
/** Full-bleed identity space, staged on the upstream 1920x1080 design canvas. */
export function IdentityTerminal({
  phase,
  rejection,
  mode,
  typed,
  location,
  denied,
  onDenied,
  sceneRef,
  onSceneStatus,
  children,
}: {
  phase: IdentityPhase;
  rejection: number;
  mode: IdentityMode;
  typed: boolean;
  location: ClientLocation;
  denied: boolean;
  onDenied: (denied: boolean) => void;
  sceneRef: Ref<IdentitySceneHandle>;
  onSceneStatus?: (status: SceneStatus) => void;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const layout = useRef<HTMLDivElement>(null);
  const design = useRef<HTMLDivElement>(null);
  const [scene, setScene] = useState<SceneStatus>("loading");
  const motion = useMotionAllowed();
  const canvas = useIdentityCanvas();
  const [, rescale] = useState(0);
  useEffect(() => {
    const onResize = () => rescale((tick) => tick + 1);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const scale = Math.min(innerWidth / DESIGN_WIDTH, innerHeight / DESIGN_HEIGHT);
  const onStatus = useCallback(
    (status: SceneStatus) => {
      setScene(status);
      onSceneStatus?.(status);
    },
    [onSceneStatus],
  );
  const onFrame = useCallback(({ denied: level, pointerX, pointerY, anchors }: SceneFrame) => {
    const root = layout.current;
    if (!root) return;
    root.style.setProperty("--denied", level.toFixed(3));
    root.style.setProperty("--pointer-x", pointerX.toFixed(3));
    root.style.setProperty("--pointer-y", pointerY.toFixed(3));
    if (anchors && design.current) applyAnchors(design.current, anchors);
  }, []);
  const driven = motion && scene === "ready";
  // Nothing animates the palette without a running scene, so it simply switches.
  useEffect(() => {
    if (driven || !layout.current) return;
    layout.current.style.setProperty("--denied", denied ? "1" : "0");
  }, [driven, denied]);
  useEffect(() => {
    if (design.current && scene !== "ready") applyAnchors(design.current, DEFAULT_ANCHORS);
  }, [scene, canvas]);
  // The fields and their focus live in this subtree, so it shakes without remounting.
  useEffect(() => {
    const slab = panel.current?.querySelector<HTMLElement>(".hp-identity-slab");
    if (!rejection || !slab) return;
    return animateSpatial(
      slab,
      [
        { transform: "translateX(0)" },
        { transform: "translateX(-14px)" },
        { transform: "translateX(12px)" },
        { transform: "translateX(-8px)" },
        { transform: "translateX(5px)" },
        { transform: "translateX(0)" },
      ],
      310,
      "cubic-bezier(0.36, 0.07, 0.19, 0.97)",
    );
  }, [rejection]);
  return (
    <div
      className="hp-auth-layout"
      ref={layout}
      data-layout={canvas ? "canvas" : "flow"}
      data-mode={mode}
      data-phase={phase}
      data-scene={scene}
      data-motion={motion ? "on" : "off"}
      data-denied={denied || undefined}
    >
      <IdentityScene
        ref={sceneRef}
        phase={phase}
        rejection={rejection}
        panel={panel}
        compact={!canvas}
        onStatus={onStatus}
        onDenied={onDenied}
        onFrame={onFrame}
      />
      <div
        className="hp-identity-design"
        ref={design}
        style={
          canvas
            ? {
                width: innerWidth / scale,
                height: innerHeight / scale,
                transform: `scale(${scale})`,
              }
            : undefined
        }
      >
        <section className="hp-auth-brand">
          <IdentityHeader />
        </section>
        {canvas ? (
          <IdentityChrome
            phase={phase}
            denied={denied}
            rejection={rejection}
            mode={mode}
            typed={typed}
            location={location}
          />
        ) : null}
        <div className="hp-identity-wrap">
          <div className="hp-identity-panel" ref={panel}>
            {children}
          </div>
        </div>
        <footer className="hp-identity-footer">
          <LicenseNotice />
          <div className="sig" aria-hidden="true">
            <span>POWERED BY</span>
            <b>HPAGENT</b>
            <i />
          </div>
        </footer>
      </div>
    </div>
  );
}
