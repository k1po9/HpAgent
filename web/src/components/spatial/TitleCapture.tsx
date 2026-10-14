import { useId, useLayoutEffect, useRef } from "react";
import { motionAllowed, subscribeMotion } from "../motion/preference";
import { relayTargetPoint } from "./filament";

/** Receiver ornament only. The existing Inspector arrival/content gate owns activation. */
export function TitleCapture({ active }: { active: boolean }) {
  const id = useId().replace(/:/g, "");
  const ref = useRef<SVGSVGElement>(null);
  useLayoutEffect(() => {
    const svg = ref.current;
    const header = svg?.parentElement;
    const title = header?.querySelector<HTMLElement>(".hp-inspector-receiver");
    if (!svg || !header || !title) return;
    const frame = svg.querySelector<SVGPathElement>(".capture-frame")!;
    const sheen = svg.querySelector<SVGPathElement>(".capture-sheen")!;
    const node = svg.querySelector<SVGCircleElement>(".capture-node")!;
    let animations: Animation[] = [];
    const cancel = () => {
      animations.forEach((a) => a.cancel());
      animations = [];
    };
    const layout = (animate = false) => {
      cancel();
      const r = title.getBoundingClientRect(),
        h = header.getBoundingClientRect();
      if (!r.width || !r.height) return;
      const left = r.left - h.left - 11,
        top = r.top - h.top - 7;
      const width = r.width + 22,
        height = r.height + 14;
      const point = relayTargetPoint(title);
      const x = point.x - h.left - left,
        y = point.y - h.top - top;
      const l = 0.8,
        t = 0.8,
        right = width - 0.8,
        b = height - 0.8,
        radius = Math.min(10, (height - 2) / 2);
      const path = `M ${x} ${y} C ${x - 7} ${y + 3} ${l} ${y + 3} ${l} ${b - radius} Q ${l} ${b} ${l + radius} ${b} H ${right - radius} Q ${right} ${b} ${right} ${b - radius} V ${t + radius} Q ${right} ${t} ${right - radius} ${t} H ${l + radius} Q ${l} ${t} ${l} ${t + radius} V ${Math.max(t + radius, y - 7)}`;
      svg.style.left = `${left}px`;
      svg.style.top = `${top}px`;
      svg.setAttribute("width", String(width));
      svg.setAttribute("height", String(height));
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      svg.querySelectorAll("path").forEach((p) => p.setAttribute("d", path));
      node.setAttribute("cx", String(x));
      node.setAttribute("cy", String(y));
      svg.querySelectorAll("linearGradient").forEach((g) => {
        g.setAttribute("x1", String(x));
        g.setAttribute("y1", String(y));
        g.setAttribute("x2", String(g.id.endsWith("sheen") ? width - 11 : width));
        g.setAttribute("y2", String(t));
      });
      if (!frame.getTotalLength) return;
      const length = frame.getTotalLength();
      frame.style.strokeDasharray = `${length} ${length}`;
      frame.style.strokeDashoffset = active ? "0" : String(length);
      sheen.style.strokeDasharray = `${Math.max(18, length * 0.18)} ${length + 48}`;
      sheen.style.strokeDashoffset = String(active ? length * 0.1 : length);
      if (active && animate && motionAllowed() && frame.animate) {
        animations = [
          frame.animate([{ strokeDashoffset: String(length) }, { strokeDashoffset: "0" }], {
            duration: 520,
            easing: "cubic-bezier(.22,.78,.25,1)",
          }),
          sheen.animate(
            [
              { strokeDashoffset: String(length * 0.95) },
              { strokeDashoffset: String(length * 0.1) },
            ],
            { duration: 690, easing: "cubic-bezier(.24,.8,.24,1)" },
          ),
          node.animate(
            [
              { transform: "translateY(0) scale(.88)", opacity: 0.78 },
              { transform: "translateY(0) scale(1.12)", opacity: 1, offset: 0.45 },
              { transform: "translateY(0) scale(1)", opacity: 0.94 },
            ],
            { duration: 440, easing: "cubic-bezier(.24,.84,.34,1)" },
          ),
        ];
      }
    };
    layout(true);
    const resize = () => layout();
    let initialized = false;
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => {
            // Initial observer delivery must not cancel the arrival animation.
            if (initialized) resize();
            initialized = true;
          });
    observer?.observe(title);
    window.addEventListener("resize", resize);
    const unsubscribe = subscribeMotion(() => {
      if (!motionAllowed()) cancel();
    });
    return () => {
      cancel();
      observer?.disconnect();
      unsubscribe();
      window.removeEventListener("resize", resize);
    };
  }, [active]);
  return (
    <svg ref={ref} className="hp-title-capture" aria-hidden="true" data-active={active}>
      <defs>
        <linearGradient id={`${id}-frame`} gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#e4a08a" stopOpacity=".9" />
          <stop offset="34%" stopColor="#d7b5a9" stopOpacity=".92" />
          <stop offset="62%" stopColor="#b6c4ce" stopOpacity=".96" />
          <stop offset="100%" stopColor="#90a9b8" stopOpacity=".98" />
        </linearGradient>
        <linearGradient id={`${id}-sheen`} gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#fff7f0" stopOpacity="0" />
          <stop offset="30%" stopColor="#f5c3ad" stopOpacity=".78" />
          <stop offset="66%" stopColor="#ffffff" stopOpacity=".98" />
          <stop offset="100%" stopColor="#ccd8e0" stopOpacity=".2" />
        </linearGradient>
        <radialGradient id={`${id}-node`}>
          <stop offset="0%" stopColor="#fff8f2" stopOpacity="1" />
          <stop offset="52%" stopColor="#e8b5a0" stopOpacity=".88" />
          <stop offset="100%" stopColor="#d8e3ea" stopOpacity="0" />
        </radialGradient>
      </defs>
      <path className="capture-shadow" />
      <path className="capture-frame" stroke={`url(#${id}-frame)`} strokeWidth="1.38" />
      <path className="capture-sheen" stroke={`url(#${id}-sheen)`} strokeWidth="1.84" />
      <circle className="capture-node" r="2.75" fill={`url(#${id}-node)`} />
    </svg>
  );
}
