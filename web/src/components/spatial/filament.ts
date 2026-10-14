import { animateSpatial, motionAllowed, subscribeMotion } from "../motion/preference";
// Fractional browser zoom can report a flush edge a few ten-thousandths of a CSS pixel outside.
const EDGE_ROUNDING = 0.01;
const NS = "http://www.w3.org/2000/svg";
let sequence = 0;
const svgNode = <K extends keyof SVGElementTagNameMap>(tag: K) => document.createElementNS(NS, tag);
function usable(node: HTMLElement, decorative = false) {
  const r = node.getBoundingClientRect(),
    style = getComputedStyle(node);
  return (
    style.visibility !== "hidden" &&
    style.display !== "none" &&
    node.isConnected &&
    !node.closest(decorative ? "[hidden]" : "[hidden],[inert]") &&
    r.width > 0 &&
    r.height > 0 &&
    r.top >= -EDGE_ROUNDING &&
    r.left >= -EDGE_ROUNDING &&
    r.bottom <= innerHeight + EDGE_ROUNDING &&
    r.right <= innerWidth + EDGE_ROUNDING
  );
}
export function relaySourceAnchor(source: HTMLElement) {
  return (
    source
      .closest(".hp-execution-block")
      ?.querySelector<HTMLElement>("[data-run-relay-status], .hp-runstrip .hp-vitality") ??
    source.querySelector<HTMLElement>("[data-relay-anchor]") ??
    source
  );
}
const sourcePoint = (source: HTMLElement) => {
  const anchor = relaySourceAnchor(source),
    r = anchor.getBoundingClientRect();
  return {
    x: anchor.matches("[data-run-relay-status], .hp-vitality")
      ? r.left + r.width / 2
      : r.right - Math.min(4, r.width / 4),
    y: r.top + r.height / 2,
  };
};
export const relayTargetPoint = (target: HTMLElement) => {
  const r = target.getBoundingClientRect();
  return { x: r.left + Math.min(24, r.width / 3), y: r.top + r.height / 2 };
};
function curve(start: { x: number; y: number }, end: { x: number; y: number }) {
  const dx = end.x - start.x,
    dy = end.y - start.y,
    sign = dx < 0 ? -1 : 1;
  return `M ${start.x} ${start.y} C ${start.x + sign * Math.max(42, Math.abs(dx) * 0.28)} ${start.y + dy * 0.05 - 14} ${end.x - sign * Math.max(56, Math.abs(dx) * 0.22)} ${end.y - dy * 0.05 + 10} ${end.x} ${end.y}`;
}
/** Map B5.3's cubic parameter pace onto real SVG arc length so the head and reveal still coincide. */
function openingPace(start: { x: number; y: number }, end: { x: number; y: number }) {
  const dx = end.x - start.x,
    dy = end.y - start.y,
    sign = dx < 0 ? -1 : 1;
  const c1 = { x: start.x + sign * Math.max(42, Math.abs(dx) * 0.28), y: start.y + dy * 0.05 - 14 };
  const c2 = { x: end.x - sign * Math.max(56, Math.abs(dx) * 0.22), y: end.y - dy * 0.05 + 10 };
  const lengths = [0];
  let previous = start;
  for (let i = 1; i <= 64; i++) {
    const t = i / 64,
      u = 1 - t;
    const point = {
      x: u * u * u * start.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * end.x,
      y: u * u * u * start.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * end.y,
    };
    lengths.push(lengths[i - 1]! + Math.hypot(point.x - previous.x, point.y - previous.y));
    previous = point;
  }
  return (t: number) => {
    const index = Math.min(64, Math.max(0, t * 64)),
      lower = Math.floor(index);
    return (
      (lengths[lower]! + (lengths[Math.min(64, lower + 1)]! - lengths[lower]!) * (index - lower)) /
      lengths[64]!
    );
  };
}
/** B5.3 optical layers; actual SVG tip owns arrival, all frames stay local. */
export function playFilament(args: {
  source: HTMLElement;
  target: HTMLElement;
  token: number;
  current: () => boolean;
  arrive: (actual?: boolean) => void;
  settle: () => void;
  closing?: boolean;
  returnStart?: { x: number; y: number };
}) {
  const { source, target, token, current, arrive, settle, closing = false } = args;
  let disposed = false,
    raf = 0,
    overlay: SVGSVGElement | null = null;
  let unsubscribe = () => {},
    sourceFeedback = () => {};
  const epoch = performance.now();
  const trace = (stage: string, reason: string) =>
    window.dispatchEvent(
      new CustomEvent("hp-inspector-relay-event", {
        detail: {
          token,
          direction: closing ? "return" : "arrival",
          stage,
          reason,
          elapsed: performance.now() - epoch,
          tip: Number(overlay?.dataset.tipLength ?? 0),
          length: Number(overlay?.dataset.totalLength ?? 0),
        },
      }),
    );
  const finish = (reveal = true, reason = "interrupted") => {
    if (disposed) return;
    trace("finished", reason);
    disposed = true;
    cancelAnimationFrame(raf);
    unsubscribe();
    for (const type of ["resize", "scroll", "wheel", "touchmove"])
      window.removeEventListener(type, interrupt, true);
    overlay?.remove();
    source.classList.remove("hp-inspector-origin");
    if (reveal && current()) {
      arrive(false);
      settle();
    }
  };
  let departed = closing;
  const interrupt = (event: Event) => {
    if (event.type === "scroll" && !departed) return;
    finish(true, event.type);
  };
  for (const type of ["resize", "scroll", "wheel", "touchmove"])
    window.addEventListener(type, interrupt, { capture: true, passive: true });
  unsubscribe = subscribeMotion(() => {
    if (!motionAllowed()) finish(true, "motion-or-hidden");
  });
  const eligible = () =>
    motionAllowed() &&
    innerWidth >= 1280 &&
    usable(source) &&
    usable(relaySourceAnchor(source)) &&
    usable(target, closing) &&
    !document.querySelector("dialog[open]:not(.hp-surface--region)");
  const launch = () => {
    try {
      if (!eligible()) {
        finish(true, "ineligible-source-target-motion-or-modal");
        return;
      }
      const origin = sourcePoint(source),
        reception = relayTargetPoint(target);
      const start = closing ? (args.returnStart ?? reception) : origin;
      let end = closing ? origin : reception;
      const sourceRect = source.getBoundingClientRect(),
        targetRect = target.getBoundingClientRect();
      const id = `hp-relay-${++sequence}`;
      overlay = svgNode("svg");
      overlay.classList.add("hp-inspector-relay");
      overlay.style.opacity = "0";
      overlay.setAttribute("viewBox", `0 0 ${innerWidth} ${innerHeight}`);
      overlay.setAttribute("aria-hidden", "true");
      Object.assign(overlay.dataset, {
        token: String(token),
        sourceX: String(origin.x),
        sourceY: String(origin.y),
        targetX: String(reception.x),
        targetY: String(reception.y),
        direction: closing ? "return" : "arrival",
        r422Material: "ember-gradient-silver",
      });
      // Constant definitions contain no business data. Unique IDs isolate quick switches.
      const defs = svgNode("defs");
      defs.innerHTML = `<linearGradient id="${id}-flame"><stop offset="0%" stop-color="#e8a28c" stop-opacity=".78"/><stop offset="25%" stop-color="#d68970" stop-opacity=".98"/><stop offset="52%" stop-color="#c6a9a0" stop-opacity=".97"/><stop offset="76%" stop-color="#a6bac8" stop-opacity=".93"/><stop offset="100%" stop-color="#8ea8b8" stop-opacity=".82"/></linearGradient><radialGradient id="${id}-spark"><stop offset="0%" stop-color="#fff5ee"/><stop offset="42%" stop-color="#e5b8a7" stop-opacity=".74"/><stop offset="100%" stop-color="#96aebb" stop-opacity="0"/></radialGradient><filter id="${id}-glow" x="-150%" y="-150%" width="400%" height="400%"><feGaussianBlur stdDeviation="1.05" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter><filter id="${id}-mist" x="-150%" y="-150%" width="400%" height="400%"><feGaussianBlur stdDeviation="3.85"/></filter>`;
      const mask = svgNode("mask");
      mask.id = `${id}-reveal`;
      mask.setAttribute("maskUnits", "userSpaceOnUse");
      mask.setAttribute("x", "-40");
      mask.setAttribute("y", "-40");
      mask.setAttribute("width", String(innerWidth + 80));
      mask.setAttribute("height", String(innerHeight + 80));
      const boundary = svgNode("path");
      boundary.setAttribute("fill", "none");
      boundary.setAttribute("stroke", "white");
      boundary.setAttribute("stroke-width", "44");
      boundary.style.strokeDasharray = "0 100000";
      boundary.style.opacity = "0";
      mask.append(boundary);
      defs.append(mask);
      overlay.append(defs);
      const layers = svgNode("g");
      layers.setAttribute("mask", `url(#${id}-reveal)`);
      overlay.append(layers);
      const paths = ["shell", "echo", "core"].map((name) => {
        const p = svgNode("path");
        p.classList.add(`hp-filament-${name}`);
        p.style.opacity = "0";
        layers.append(p);
        return p;
      });
      paths[0]!.setAttribute("filter", `url(#${id}-mist)`);
      paths[2]!.setAttribute("stroke", `url(#${id}-flame)`);
      paths[2]!.setAttribute("filter", `url(#${id}-glow)`);
      const glintGradient = svgNode("linearGradient");
      glintGradient.id = `${id}-glint`;
      glintGradient.setAttribute("gradientUnits", "userSpaceOnUse");
      [
        ["0%", "0"],
        ["34%", ".36"],
        ["68%", ".98"],
        ["100%", ".20"],
      ].forEach(([offset, opacity]) => {
        const stop = svgNode("stop");
        stop.setAttribute("offset", offset!);
        stop.setAttribute("stop-opacity", opacity!);
        glintGradient.append(stop);
      });
      defs.append(glintGradient);
      const glint = svgNode("path");
      glint.classList.add("hp-filament-living-glint");
      glint.setAttribute("stroke", `url(#${id}-glint)`);
      glint.style.opacity = "0";
      layers.append(glint);
      const makeSpark = (radius: number, className: string) => {
        const n = svgNode("circle");
        n.classList.add(className);
        n.setAttribute("fill", `url(#${id}-spark)`);
        n.setAttribute("filter", `url(#${id}-glow)`);
        n.setAttribute("r", String(radius));
        n.setAttribute("cx", String(start.x));
        n.setAttribute("cy", String(start.y));
        n.style.opacity = "0";
        overlay!.append(n);
        return n;
      };
      const head = makeSpark(4.6, "hp-filament-head");
      const sparks = closing
        ? []
        : [2.6].map((radius) => {
            const spark = makeSpark(radius, "hp-filament-spark");
            spark.dataset.r422Trace = "true";
            return spark;
          });
      const setPath = () => {
        const d = curve(start, end);
        boundary.setAttribute("d", d);
        paths.forEach((p) => p.setAttribute("d", d));
        glint.setAttribute("d", d);
        return boundary.getTotalLength();
      };
      // Entire subtree is already invisible before any measurement or first paint.
      document.body.append(overlay);
      let length = setPath();
      if (!Number.isFinite(length) || length <= 0) {
        finish(true, "invalid-path");
        return;
      }
      overlay.dataset.totalLength = String(length);
      overlay.dataset.tipLength = "0";
      if (!closing) source.classList.add("hp-inspector-origin");
      departed = true;
      const started = performance.now();
      // B5.3 parameter pace, with the approved compact flight. Return still fits the 680ms shell.
      const duration = closing ? 660 : 760;
      const pace = closing ? null : openingPace(start, end);
      let last = started,
        landed = false;
      const frame = (now: number) => {
        if (disposed) return;
        if (!current()) {
          finish(false, "stale-intent");
          return;
        }
        const r = source.getBoundingClientRect(),
          t = target.getBoundingClientRect();
        const reason =
          !usable(source) || !usable(relaySourceAnchor(source))
            ? "source-lost"
            : !target.isConnected
              ? "target-lost"
              : !motionAllowed()
                ? "motion-or-hidden"
                : now - last > 180
                  ? "long-frame"
                  : document.querySelector("dialog[open]:not(.hp-surface--region)")
                    ? "modal"
                    : !closing &&
                        !landed &&
                        (Math.abs(r.left - sourceRect.left) > 2 ||
                          Math.abs(r.top - sourceRect.top) > 2 ||
                          Math.abs(t.left - targetRect.left) > 2 ||
                          Math.abs(t.top - targetRect.top) > 2)
                      ? "geometry"
                      : null;
        if (reason) {
          finish(true, reason);
          return;
        }
        last = now;
        const progress = Math.min(1, (now - started) / duration);
        if (closing) {
          end = sourcePoint(source);
          length = setPath();
          overlay!.dataset.sourceX = String(end.x);
          overlay!.dataset.sourceY = String(end.y);
          overlay!.dataset.totalLength = String(length);
        }
        const tip = length * (closing ? progress : pace!(Math.min(1, progress * 1.12)));
        const tail = closing
          ? Math.max(0, tip - length * 0.25 * (0.5 + 0.5 * Math.sin(Math.PI * progress)))
          : 0;
        boundary.style.strokeDasharray = `${Math.max(0.01, tip - tail)} ${length + 50}`;
        boundary.style.strokeDashoffset = String(-tail);
        boundary.style.opacity = progress > 0 ? "1" : "0";
        overlay!.style.opacity = "1";
        overlay!.dataset.tipLength = String(tip);
        overlay!.dataset.tailLength = String(tail);
        const reveal = Math.max(0, Math.min(1, (progress - 0.04) / 0.7));
        const fade = closing
          ? Math.min(1, progress * 8) * (1 - Math.pow(progress, 3))
          : 1 - Math.max(0, (progress - 0.9) / 0.1);
        paths[0]!.style.opacity = String(
          (closing ? 0.42 : reveal < 0.1 ? 0 : 0.16 + reveal * 0.36) * fade,
        );
        paths[1]!.style.opacity = String(
          (closing ? 0.72 : reveal < 0.1 ? 0 : Math.min(0.78, reveal * 0.85)) * fade,
        );
        paths[2]!.style.opacity = String(
          (closing ? 0.9 : reveal < 0.12 ? 0 : Math.min(1, reveal * 1.1)) * fade,
        );
        updateLivingGlint(glint, glintGradient, boundary, length, tip, tail, closing);
        const point = boundary.getPointAtLength(tip);
        head.setAttribute("cx", String(point.x));
        head.setAttribute("cy", String(point.y));
        head.setAttribute("r", "4.6");
        head.style.opacity = String(
          closing
            ? fade * 0.9
            : Math.min(1, progress * 12) *
                (progress < 0.86 ? 1 - progress * 0.25 : Math.max(0, 1 - (progress - 0.86) / 0.14)),
        );
        sparks.forEach((n, i) => {
          const local = Math.max(
              0,
              Math.min(1, (progress - (i + 1) * 0.1) / 0.7, tip / length - 0.035 * (i + 1)),
            ),
            pt = boundary.getPointAtLength(length * local);
          n.setAttribute("cx", String(pt.x + Math.sin(local * 10) * [8, -10, 13][i]! * 0.18));
          n.setAttribute("cy", String(pt.y + Math.cos(local * 8) * [8, -10, 13][i]! * 0.12));
          n.style.opacity = String(
            local > 0 ? Math.min(0.82, local * 1.15) * (1 - progress * 0.35) * fade : 0,
          );
        });
        if (!landed && tip >= length - 0.5) {
          landed = true;
          trace("arrived", "actual-tip");
          if (current()) arrive(true);
        }
        if (progress < 1) raf = requestAnimationFrame(frame);
        else {
          if (current()) settle();
          finish(false, "completed");
          if (closing && current())
            sourceFeedback = animateSpatial(
              relaySourceAnchor(source),
              [
                { backgroundColor: "transparent" },
                {
                  backgroundColor: "color-mix(in srgb, var(--hp-brand) 6%, transparent)",
                  offset: 0.35,
                },
                { backgroundColor: "transparent" },
              ],
              360,
            );
        }
      };
      trace("started", "eligible");
      raf = requestAnimationFrame(frame);
    } catch {
      finish(true, "exception");
    }
  };
  if (closing) launch();
  else {
    trace("preparing", "layout-settle");
    const deadline = performance.now() + 600;
    let previousGeometry = "";
    const prepare = () => {
      if (disposed) return;
      if (!current()) {
        finish(false, "stale-intent");
        return;
      }
      if (!eligible()) {
        finish(true, "ineligible-before-departure");
        return;
      }
      const a = source.getBoundingClientRect(),
        b = target.getBoundingClientRect();
      const geometry = [a.left, a.top, a.width, a.height, b.left, b.top, b.width, b.height]
        .map((v) => Math.round(v))
        .join(":");
      // Two committed frame samples suffice; no mandatory 60ms delay on a stable opening.
      if (geometry === previousGeometry) launch();
      else if (performance.now() >= deadline) finish(true, "layout-timeout");
      else {
        previousGeometry = geometry;
        raf = requestAnimationFrame(prepare);
      }
    };
    raf = requestAnimationFrame(prepare);
  }
  return () => {
    sourceFeedback();
    finish(false, "dispose");
  };
}

function updateLivingGlint(
  glint: SVGPathElement,
  gradient: SVGLinearGradientElement,
  base: SVGPathElement,
  total: number,
  tip: number,
  tail: number,
  closing: boolean,
) {
  const clamp = (v: number) => Math.max(0, Math.min(1, v));
  const smooth = (v: number) => v * v * (3 - 2 * v);
  const pos = clamp(tip / total);
  const startup = smooth(clamp(pos / 0.16));
  const taper = smooth(clamp((pos - 0.74) / 0.26));
  const length = Math.max(2, 64 * (0.36 + 0.64 * startup) * (1 - 0.52 * taper));
  const end = Math.max(0, Math.min(tip, total));
  const start = Math.max(closing ? tail : 0, end - length);
  glint.style.strokeDasharray = `${Math.max(0.01, end - start)} ${total + 50}`;
  glint.style.strokeDashoffset = String(-start);
  glint.style.strokeWidth = String(2.15 * (0.91 + 0.12 * (1 - taper)));
  const outro = closing
    ? 1 - smooth(clamp((pos - 0.76) / 0.24))
    : 1 - 0.38 * smooth(clamp((pos - 0.86) / 0.14));
  glint.style.opacity = String(0.92 * smooth(clamp(pos / 0.11)) * outro);
  const a = base.getPointAtLength(start),
    b = base.getPointAtLength(end);
  gradient.setAttribute("x1", String(a.x));
  gradient.setAttribute("y1", String(a.y));
  gradient.setAttribute("x2", String(b.x || a.x + 0.1));
  gradient.setAttribute("y2", String(b.y));
  const mix = (a: number[], b: number[], t: number) =>
    a.map((v, i) => Math.round(v + (b[i]! - v) * clamp(t)));
  const t = closing ? 1 - pos : pos;
  const tone =
    t < 0.48
      ? mix([236, 164, 144], [224, 222, 219], smooth(t / 0.48))
      : mix([224, 222, 219], [158, 187, 202], smooth((t - 0.48) / 0.52));
  const colors = [
    mix(tone, [255, 252, 247], 0.34),
    mix(tone, [255, 250, 242], 0.62),
    mix(tone, [255, 255, 255], 0.83),
    mix(tone, [181, 203, 215], 0.38),
  ];
  [...gradient.children].forEach((stop, i) =>
    stop.setAttribute(
      "stop-color",
      "#" + colors[i]!.map((v) => v.toString(16).padStart(2, "0")).join(""),
    ),
  );
}
