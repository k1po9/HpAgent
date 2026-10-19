import { useId, useLayoutEffect, useRef } from "react";
import { motionAllowed, subscribeMotion } from "../motion/preference";
import { relayTargetPoint } from "./filament";

/** Decorative R2 ink. Activation and geometry come from the existing receiver;
 * this component never completes an arrival or changes the Inspector gate. */
export function InspectorTitleOrnament({
  active,
  kind,
}: {
  active: boolean;
  kind: "run" | "artifact";
}) {
  const id = useId().replace(/:/g, "");
  const ref = useRef<SVGSVGElement>(null);
  useLayoutEffect(() => {
    const svg = ref.current;
    const header = svg?.parentElement;
    const title = header?.querySelector<HTMLElement>(".hp-inspector-receiver");
    if (!svg || !header || !title) return;
    const paths = ["main", "branch", "echo-top", "echo-bottom", "flow-halo", "flow-core"].map(
      (name) => svg.querySelector<SVGPathElement>(`.cap-${name}`)!,
    );
    const [main, branch, echo, trace, halo, core] = paths as [
      SVGPathElement,
      SVGPathElement,
      SVGPathElement,
      SVGPathElement,
      SVGPathElement,
      SVGPathElement,
    ];
    const aura = svg.querySelector<SVGCircleElement>(".cap-dock-aura")!;
    const weld = svg.querySelector<SVGCircleElement>(".cap-dock")!;
    const arrival = svg.querySelector<SVGCircleElement>(".cap-arrival")!;
    const ink = [main, branch, echo, trace];
    let animations: Animation[] = [];
    let generation = 0;
    let geometry: number[] = [];
    let lengths: number[] = [];
    const cancel = () => {
      generation++;
      animations.forEach((animation) => animation.cancel());
      animations = [];
    };
    const paint = () => {
      ink.forEach((path, index) => {
        path.style.strokeDashoffset = active ? "0" : String(lengths[index] ?? 1000);
        path.style.opacity = active ? "1" : "0";
      });
      [halo, core, arrival].forEach((node) => (node.style.opacity = "0"));
      weld.style.opacity = active ? (kind === "artifact" ? ".86" : ".72") : "0";
      aura.style.opacity = active ? (kind === "artifact" ? ".22" : ".13") : "0";
      svg.dataset.handoff = active ? "settled" : "waiting";
    };
    const measure = () => {
      const r = title.getBoundingClientRect(),
        h = header.getBoundingClientRect();
      if (!title.isConnected || !r.width || !r.height) return false;
      geometry = [r.left, r.top, r.width, r.height, h.left, h.top];
      const insetX = kind === "run" ? 18 : 13,
        insetY = kind === "run" ? 12 : 9;
      const left = r.left - h.left - insetX,
        top = r.top - h.top - insetY;
      const width = r.width + insetX * 2,
        height = r.height + insetY * 2;
      const point = relayTargetPoint(title);
      const x = point.x - h.left - left,
        y = point.y - h.top - top;
      const dock = 8,
        bottom = height - 6;
      svg.style.left = `${left}px`;
      svg.style.top = `${top}px`;
      svg.setAttribute("width", String(width));
      svg.setAttribute("height", String(height));
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      const d =
        kind === "run"
          ? [
              `M ${x} ${y} C ${x - 9} ${y + 1} ${dock} ${y + 3} ${dock} ${y} L ${dock} 15 Q ${dock} 6 17 6 H 31`,
              `M ${dock} ${y} V ${height - 16} Q ${dock} ${bottom} 18 ${bottom} H 31`,
              `M ${width - 38} 6 H ${width - 18} Q ${width - 8} 6 ${width - 8} 15 V 19`,
              `M ${width - 40} ${bottom} H ${width - 18} Q ${width - 8} ${bottom} ${width - 8} ${height - 16} V ${height - 19}`,
            ]
          : [
              `M ${x} ${y} C ${x - 10} ${y + 0.4} ${dock} ${y + 1.5} ${dock} ${y + 6} Q ${dock} ${bottom} 20 ${bottom} H ${width - 20} Q ${width - 10} ${bottom} ${width - 8} ${height - 14}`,
              `M ${dock} ${y - 10} V ${y - 4}`,
              `M ${width - 49} ${height - 3.2} H ${width - 29}`,
              `M 25 ${height - 2.6} Q ${Math.round(width * 0.48)} ${height - 0.4} ${Math.round(width * 0.71)} ${height - 2.6}`,
            ];
      paths.forEach((path, index) => path.setAttribute("d", d[index < 4 ? index : 0]!));
      lengths = paths.map((path) => (path.getTotalLength ? Math.max(1, path.getTotalLength()) : 1));
      paths.forEach((path, index) => {
        const length = lengths[index]!;
        path.style.strokeDasharray =
          index < 4
            ? `${length} ${length}`
            : `${Math.min(kind === "artifact" ? 31 : 21, length * 0.24)} ${length + 45}`;
      });
      const dockY = kind === "run" ? y : y + 6;
      [aura, weld].forEach((node) => {
        node.setAttribute("cx", String(dock));
        node.setAttribute("cy", String(dockY));
      });
      aura.setAttribute("r", kind === "artifact" ? "10" : "8");
      arrival.setAttribute("cx", String(x));
      arrival.setAttribute("cy", String(y));
      svg.querySelectorAll("linearGradient").forEach((gradient) => {
        gradient.setAttribute("x1", String(x));
        gradient.setAttribute("y1", String(y));
        gradient.setAttribute("x2", String(width - (gradient.id.endsWith("ink") ? 9 : 12)));
        gradient.setAttribute(
          "y2",
          String(gradient.id.endsWith("ink") && kind === "run" ? 6 : bottom),
        );
      });
      return true;
    };
    const settle = () => {
      cancel();
      paint();
    };
    const start = () => {
      if (!measure()) return;
      paint();
      if (!active || !motionAllowed() || !main.animate) return;
      svg.dataset.handoff = "animating";
      ink.forEach((path, index) => (path.style.strokeDashoffset = String(lengths[index])));
      weld.style.opacity = "0";
      aura.style.opacity = "0";
      const ticket = generation;
      const animate = (node: SVGElement, frames: Keyframe[], options: KeyframeAnimationOptions) => {
        animations.push(node.animate(frames, { fill: "forwards", ...options }));
      };
      const duration = kind === "artifact" ? 760 : 540;
      ink.forEach((path, index) => {
        const delays = kind === "run" ? [0, 140, 255, 330] : [0, 210, 400, 485];
        const times = [duration, kind === "run" ? 470 : 330, 320, 310];
        animate(
          path,
          [
            { strokeDashoffset: String(lengths[index]), opacity: 1 },
            { strokeDashoffset: "0", opacity: index > 1 ? (index === 2 ? 0.65 : 0.62) : 1 },
          ],
          { duration: times[index], delay: delays[index], easing: "cubic-bezier(.18,.76,.27,1)" },
        );
      });
      [halo, core].forEach((node, index) => {
        const weight = index === 0 ? 0.34 : 0.92,
          length = lengths[0]!;
        animate(
          node,
          [
            { strokeDashoffset: "0", opacity: weight * 0.58, offset: 0 },
            { strokeDashoffset: String(-length * 0.15), opacity: weight, offset: 0.18 },
            { strokeDashoffset: String(-length * 0.78), opacity: weight * 0.74, offset: 0.78 },
            {
              strokeDashoffset: String(-length - (kind === "artifact" ? 25 : 18)),
              opacity: 0,
              offset: 1,
            },
          ],
          { duration: duration + 145, easing: "cubic-bezier(.28,.42,.36,1)" },
        );
      });
      animate(
        arrival,
        [
          { opacity: 0, transform: "scale(.78)" },
          { opacity: 0.9, transform: "scale(1.1)", offset: 0.18 },
          { opacity: 0, transform: "scale(.64)" },
        ],
        { duration: 285, easing: "ease-out" },
      );
      const delay = kind === "artifact" ? 205 : 165;
      animate(
        weld,
        [
          { opacity: 0, transform: "scale(.42)" },
          { opacity: 0.98, transform: "scale(1.35)", offset: 0.36 },
          { opacity: kind === "artifact" ? 0.86 : 0.72, transform: "scale(1)" },
        ],
        { duration: 345, delay, easing: "cubic-bezier(.17,.76,.32,1)" },
      );
      animate(
        aura,
        [
          { opacity: 0 },
          { opacity: 0.5, offset: 0.35 },
          { opacity: kind === "artifact" ? 0.22 : 0.13 },
        ],
        { duration: 450, delay, easing: "ease-out" },
      );
      void Promise.allSettled(animations.map((animation) => animation.finished)).then(() => {
        if (ticket === generation && svg.isConnected) settle();
      });
    };
    start();
    const resize = () => {
      const r = title.getBoundingClientRect(),
        h = header.getBoundingClientRect();
      const next = [r.left, r.top, r.width, r.height, h.left, h.top];
      // The initial observer notification must not cancel a real arrival.
      if (next.every((value, index) => Math.abs(value - (geometry[index] ?? NaN)) < 1)) return;
      cancel();
      measure();
      paint();
    };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
    observer?.observe(title);
    observer?.observe(header);
    window.addEventListener("resize", resize);
    const unsubscribe = subscribeMotion(() => {
      if (!motionAllowed()) settle();
    });
    return () => {
      cancel();
      observer?.disconnect();
      unsubscribe();
      window.removeEventListener("resize", resize);
    };
  }, [active, kind]);
  return (
    <svg
      ref={ref}
      className="hp-capture-dual"
      aria-hidden="true"
      focusable="false"
      data-decoration-only={kind}
    >
      <defs>
        <linearGradient id={`${id}-ink`} gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#ebb7a1" stopOpacity=".9" />
          <stop offset="26%" stopColor="#edc1b0" stopOpacity=".88" />
          <stop offset="64%" stopColor="#d1dce3" stopOpacity=".95" />
          <stop offset="100%" stopColor="#8da7b6" stopOpacity=".97" />
        </linearGradient>
        <linearGradient id={`${id}-light`} gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#ffc7ad" stopOpacity=".85" />
          <stop offset="37%" stopColor="#fff5ec" stopOpacity=".98" />
          <stop offset="68%" stopColor="#fbfeff" stopOpacity="1" />
          <stop offset="100%" stopColor="#b4d0df" stopOpacity=".67" />
        </linearGradient>
        <radialGradient id={`${id}-ember`}>
          <stop offset="0%" stopColor="#ffe6d7" stopOpacity=".95" />
          <stop offset="33%" stopColor="#efb39b" stopOpacity=".38" />
          <stop offset="100%" stopColor="#eab8a1" stopOpacity="0" />
        </radialGradient>
      </defs>
      <path className="cap-ink cap-main" stroke={`url(#${id}-ink)`} />
      <path className="cap-ink cap-branch" stroke={`url(#${id}-ink)`} />
      <path className="cap-echo cap-echo-top" />
      <path className="cap-echo cap-echo-bottom" />
      <path className="cap-flow-halo" stroke={`url(#${id}-light)`} />
      <path className="cap-flow-core" stroke={`url(#${id}-light)`} />
      <circle className="cap-dock-aura" fill={`url(#${id}-ember)`} />
      <circle className="cap-dock" r="1.95" />
      <circle className="cap-arrival" r="1.95" />
    </svg>
  );
}
