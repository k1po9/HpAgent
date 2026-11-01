import { useEffect, useRef, useState } from "react";
import { animateSpatial } from "../motion/preference";
import type { IdentityPhase } from "./IdentityScene";

/** The silhouette's head within the personnel file, in card coordinates. */
const HEAD = { x: 146, y: 160, r: 66 };
const ROUTE_X = 15;

function offsetWithin(node: HTMLElement, root: HTMLElement) {
  let left = 0;
  let top = 0;
  let current: HTMLElement | null = node;
  while (current && current !== root) {
    left += current.offsetLeft;
    top += current.offsetTop;
    current = current.offsetParent as HTMLElement | null;
  }
  return { left, top };
}

function ringArc(start: number, sweep: number) {
  const at = (angle: number) => {
    const radians = (angle * Math.PI) / 180;
    return `${HEAD.x + HEAD.r * Math.cos(radians)} ${HEAD.y + HEAD.r * Math.sin(radians)}`;
  };
  return `M ${at(start)} A ${HEAD.r} ${HEAD.r} 0 ${sweep > 180 ? 1 : 0} 1 ${at(start + sweep)}`;
}

function linkLabel(
  phase: IdentityPhase,
  denied: boolean,
  focused: string | null,
  next: string | null,
) {
  if (phase === "confirmed") return "CONFIRMED";
  if (phase === "verifying") return "VERIFYING";
  if (denied) return "NOT VERIFIED";
  if (focused) return `PARSING · ${focused}`;
  return next ? `AWAITING · ${next}` : "READY";
}

/**
 * Leads the eye from the live input to the portrait: a leader line and a parsing ring. It knows
 * field labels and whether a field is filled, never their content or length.
 */
export function IdentityLink({
  phase,
  denied,
  filled,
  mode,
}: {
  phase: IdentityPhase;
  denied: boolean;
  /** One entry per rendered field, in DOM order; an optional field counts as filled. */
  filled: boolean[];
  mode: string;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const line = useRef<SVGPathElement>(null);
  const start = useRef<SVGCircleElement>(null);
  const traveller = useRef<SVGCircleElement>(null);
  const [focusedIndex, setFocusedIndex] = useState<number | null>(null);
  const [labels, setLabels] = useState<string[]>([]);
  const firstOpen = filled.indexOf(false);
  const target = focusedIndex ?? (firstOpen >= 0 ? firstOpen : 0);
  const filledKey = filled.join();

  useEffect(() => {
    const card = svg.current?.parentElement;
    if (!card) return;
    const fieldsOf = () => [...card.querySelectorAll<HTMLElement>(".hp-identity-field")];
    const onFocus = () => {
      const active = document.activeElement?.closest<HTMLElement>(".hp-identity-field");
      const index = active ? fieldsOf().indexOf(active) : -1;
      setFocusedIndex(index >= 0 ? index : null);
    };
    const onInput = () => {
      if (traveller.current) {
        animateSpatial(
          traveller.current as unknown as HTMLElement,
          [
            { offsetDistance: "0%", opacity: 1 },
            { offsetDistance: "100%", opacity: 0 },
          ],
          520,
          "ease-in",
        );
      }
    };
    const measure = () => {
      const fields = fieldsOf();
      const slab = card.querySelector<HTMLElement>(".hp-identity-slab");
      const field = fields[target];
      if (!field || !slab || !line.current || !start.current) return;
      const from = offsetWithin(field, card);
      const x = from.left + 12;
      const y = from.top + 15;
      const above = offsetWithin(slab, card).top - 5;
      // Enters the ring from the side so it never crosses the photo's labels.
      const ringLeft = HEAD.x - HEAD.r;
      const d = `M ${x} ${y} L ${x} ${above} L ${ROUTE_X} ${above} L ${ROUTE_X} ${HEAD.y} L ${ringLeft} ${HEAD.y}`;
      line.current.setAttribute("d", d);
      start.current.setAttribute("cx", String(x));
      start.current.setAttribute("cy", String(y));
      traveller.current?.style.setProperty("offset-path", `path("${d}")`);
    };
    setLabels(fieldsOf().map((field) => field.querySelector("span")?.textContent ?? ""));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(card);
    card.addEventListener("focusin", onFocus);
    card.addEventListener("focusout", onFocus);
    card.addEventListener("input", onInput);
    return () => {
      observer.disconnect();
      card.removeEventListener("focusin", onFocus);
      card.removeEventListener("focusout", onFocus);
      card.removeEventListener("input", onInput);
    };
  }, [target, mode, filledKey]);

  const focused = focusedIndex === null ? null : (labels[focusedIndex] ?? null);
  const next = firstOpen >= 0 ? (labels[firstOpen] ?? null) : null;
  const label = linkLabel(phase, denied, focused, next);
  const settled = phase === "idle" && !denied && !focused && !next;
  return (
    <>
      <svg
        className="hp-identity-link"
        ref={svg}
        aria-hidden="true"
        data-focused={focused ? true : undefined}
        data-settled={settled || undefined}
        data-alert={denied || undefined}
      >
        <path className="leader" ref={line} />
        <g className="parse">
          <path d={ringArc(-60, 210)} />
          <path d={ringArc(170, 90)} />
        </g>
        <circle className="closed" cx={HEAD.x} cy={HEAD.y} r={HEAD.r} />
        <circle className="start" ref={start} r="3.5" />
        <circle className="traveller" ref={traveller} r="3" />
      </svg>
      <div className="hp-identity-link-label" aria-hidden="true" data-alert={denied || undefined}>
        {label}
      </div>
    </>
  );
}
