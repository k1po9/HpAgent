import { useCallback, useLayoutEffect, useRef, useState, type RefObject } from "react";
import type { HpMessage, HpRun } from "../../api/types";
import { useShell } from "../../store/shell";
import { isTerminalRunStatus } from "../../store/workbench";
import { MERCURY_ENABLED, MERCURY_STAGE } from "./config";
import { mercuryBlocks, mercuryHost, mercuryHostVisible, mercuryRect } from "./dom";
import { makeParticles, phasePath, scopeSeed, type Anchors } from "./geometry";
import { MercuryRenderer } from "./renderer";
import { MercuryTimeline, createMercuryTimeline } from "./timeline";
import { mercurySpeed } from "./palette";

export interface ReadingFrame {
  key: string;
  messages: HpMessage[];
  run: HpRun | null;
  hasMore?: boolean;
}
interface Transition {
  owner: string;
  token: number;
  from: string;
  to: string;
  phase: "out" | "waiting" | "in";
  source?: ReadingFrame;
  arrivingMessages?: HpMessage[];
}
interface Session {
  token: number;
  direction: "out" | "in";
  key: string;
  messages: HpMessage[];
  host: HTMLElement;
  anchors: Anchors;
  timeline: MercuryTimeline;
  renderer: MercuryRenderer;
  region: HTMLDivElement;
  original: { maskImage: string; willChange: string };
  raf: number;
  last: number;
  slow: number;
  attempts: number;
}
const revealEvent = "hp-mercury-reveal-reading";
/** Explicit accepted sidebar gesture, distinct from Inspector navigation on the same route. */
export function revealConversationReading(id: string) {
  window.dispatchEvent(
    new CustomEvent(revealEvent, { detail: { id, token: useShell.getState().requestToken } }),
  );
}
const conversationId = (key: string) => key.slice(key.indexOf(":") + 1);
function event(type: string, s: Session, reason?: string) {
  window.dispatchEvent(
    new CustomEvent("hp-mercury-transit", {
      detail: {
        type,
        reason,
        token: s.token,
        direction: s.direction,
        key: s.key,
        seed: scopeSeed(s.key),
        progress: s.timeline.progress,
        quality: s.renderer.quality,
        anchors: s.anchors,
      },
    }),
  );
}
/** Observe accepted navigation only. Owns bounded presentation, never navigation/data/commands. */
export function useConversationTransit({
  frame,
  owner,
  ready,
  unavailable,
  folded,
  allowed,
  reveal,
  region,
  viewport,
  canvas,
  maskPath,
  maskId,
}: {
  frame: ReadingFrame;
  owner: string;
  ready: boolean;
  unavailable: boolean;
  folded: boolean;
  allowed: boolean;
  reveal: () => void;
  region: RefObject<HTMLDivElement | null>;
  viewport: RefObject<HTMLDivElement | null>;
  canvas: RefObject<HTMLCanvasElement | null>;
  maskPath: RefObject<SVGPathElement | null>;
  maskId: string;
}) {
  const navigation = useShell((s) => s.requestToken),
    route = useShell((s) => s.route),
    modal = useShell((s) => s.modal),
    pending = useShell((s) => Boolean(s.pendingRoute || s.pendingResourceChange));
  const [transition, setTransition] = useState<Transition | null>(null),
    session = useRef<Session | null>(null);
  const failure = useRef(0);
  const valid = Boolean(
    transition &&
    transition.owner === owner &&
    transition.token === navigation &&
    route.screen === "ai" &&
    route.conversationId === transition.to &&
    allowed &&
    !modal &&
    !pending &&
    !unavailable,
  );
  const liveTarget = Boolean(
    ready &&
    transition &&
    conversationId(frame.key) === transition.to &&
    frame.run &&
    !isTerminalRunStatus(frame.run.status),
  );
  if (
    transition &&
    (!valid ||
      liveTarget ||
      (transition.phase === "in" && transition.arrivingMessages !== frame.messages))
  )
    setTransition(null);
  else if (transition?.phase === "waiting" && ready && conversationId(frame.key) === transition.to)
    setTransition({ ...transition, phase: "in", arrivingMessages: frame.messages });
  const retiring = valid && transition?.phase === "out";
  const busy = valid;
  const active = valid && transition?.phase !== "waiting";
  const release = useCallback(
    (reason: string) => {
      cancelAnimationFrame(failure.current);
      failure.current = 0;
      const s = session.current;
      if (s) {
        cancelAnimationFrame(s.raf);
        Object.assign(s.region.style, s.original);
        s.renderer.dispose();
        canvas.current?.setAttribute("data-mercury-state", "idle");
        event("cancelled", s, reason);
        session.current = null;
      }
    },
    [canvas],
  );
  const cancel = useCallback(
    (reason: string) => {
      release(reason);
      setTransition(null);
    },
    [release],
  );
  useLayoutEffect(() => () => release("unmount"), [release]);
  useLayoutEffect(() => {
    return useShell.subscribe((next, previous) => {
      if (next.requestToken === previous.requestToken) return;
      const alreadyMoving = Boolean(transition);
      cancel("new-navigation");
      if (
        !MERCURY_ENABLED ||
        !allowed ||
        unavailable ||
        document.hidden ||
        innerWidth < 960 ||
        next.modal ||
        next.pendingRoute ||
        next.pendingResourceChange ||
        next.route.screen !== "ai" ||
        !next.route.conversationId
      )
        return;
      const to = next.route.conversationId,
        from = previous.route.conversationId;
      if (previous.route.screen !== "ai" || !from || from === to) return;
      const el = region.current,
        host = el && from ? mercuryHost(el, from) : undefined;
      const canRetire =
        !alreadyMoving &&
        ready &&
        !folded &&
        from !== to &&
        frame.key.endsWith(":" + from) &&
        el &&
        !el.closest("[hidden],[inert],.hp-shell--sidebar-collapsed") &&
        host &&
        mercuryHostVisible(host);
      setTransition({
        owner,
        token: next.requestToken,
        from: from ?? "",
        to,
        phase: canRetire ? "out" : "waiting",
        source: canRetire ? frame : undefined,
      });
      reveal();
    });
  }, [frame, owner, ready, unavailable, folded, allowed, reveal, region, cancel, transition]);
  useLayoutEffect(() => {
    const restore = (event: Event) => {
      const { id, token } = (event as CustomEvent<{ id: string; token: number }>).detail,
        shell = useShell.getState();
      if (
        !folded ||
        id !== conversationId(frame.key) ||
        shell.route.screen !== "ai" ||
        shell.route.conversationId !== id ||
        shell.requestToken !== token ||
        shell.pendingRoute ||
        shell.modal
      )
        return;
      if (MERCURY_ENABLED && allowed && !unavailable && !document.hidden && innerWidth >= 960)
        setTransition({ owner, token, from: id, to: id, phase: "waiting" });
      reveal();
    };
    window.addEventListener(revealEvent, restore);
    return () => window.removeEventListener(revealEvent, restore);
  }, [folded, frame.key, owner, allowed, unavailable, reveal]);
  useLayoutEffect(() => {
    if (!transition || !valid) {
      release("authority-or-preference");
      return;
    }
    if (transition.phase === "waiting" || session.current) return;
    const fail = (reason: string) => {
      release(reason);
      failure.current = requestAnimationFrame(() => {
        failure.current = 0;
        cancel(reason);
      });
    };
    const el = region.current,
      layer = canvas.current,
      v = viewport.current;
    const key = transition.phase === "out" ? transition.source!.key : frame.key;
    const host = el ? mercuryHost(el, conversationId(key)) : undefined;
    if (
      !el ||
      !layer ||
      !v ||
      !host ||
      !mercuryHostVisible(host) ||
      el.closest(".hp-shell--sidebar-collapsed") ||
      !CSS.supports("mask-image", `url("#${maskId}")`)
    ) {
      fail("missing-host-or-renderer");
      return;
    }
    const r = mercuryRect(el.getBoundingClientRect());
    if (r.width < 280 || r.height < 120) {
      fail("small-reading-surface");
      return;
    }
    let renderer: MercuryRenderer;
    try {
      renderer = new MercuryRenderer(layer, makeParticles(scopeSeed(key), mercuryBlocks(v, r)));
    } catch {
      fail("canvas-unavailable");
      return;
    }
    const s: Session = {
      token: transition.token,
      direction: transition.phase,
      key,
      messages: transition.phase === "out" ? transition.source!.messages : frame.messages,
      host,
      anchors: { reading: r, host: mercuryRect(host.getBoundingClientRect()) },
      timeline: createMercuryTimeline(transition.phase === "out" ? 0 : 1, mercurySpeed(layer)),
      renderer,
      region: el,
      original: { maskImage: el.style.maskImage, willChange: el.style.willChange },
      raf: 0,
      last: 0,
      slow: 0,
      attempts: 0,
    };
    session.current = s;
    s.timeline.goto(s.direction === "out" ? 1 : 0);
    el.style.setProperty("mask-image", `url("#${maskId}")`);
    el.style.setProperty("will-change", "mask-image");
    maskPath.current?.setAttribute("d", phasePath(s.timeline.progress));
    layer.setAttribute("data-mercury-state", "transit");
    layer.setAttribute("data-direction", s.direction);
    layer.setAttribute("data-conversation", conversationId(key));
    layer.setAttribute("data-seed", String(scopeSeed(key)));
    layer.setAttribute("data-token", String(s.token));
    layer.setAttribute("data-stage", MERCURY_STAGE);
    const complete = () => {
      Object.assign(el.style, s.original);
      s.renderer.dispose();
      layer.setAttribute("data-mercury-state", "idle");
      session.current = null;
      event("settled", s);
      if (s.direction === "out")
        setTransition((t) =>
          t?.token === s.token ? { ...t, phase: "waiting", source: undefined } : t,
        );
      else setTransition(null);
    };
    let started = false;
    const tick = (now: number) => {
      s.raf = 0;
      if (session.current !== s) return;
      const shell = useShell.getState();
      if (
        document.hidden ||
        innerWidth < 960 ||
        shell.requestToken !== s.token ||
        shell.modal ||
        shell.pendingRoute ||
        shell.pendingResourceChange ||
        !el.isConnected ||
        !mercuryHostVisible(s.host)
      ) {
        cancel("interrupted");
        return;
      }
      const domMessages = Array.from(v.querySelectorAll<HTMLElement>("[data-message-id]"));
      if (
        domMessages.length !== s.messages.length ||
        domMessages.some((n, i) => n.dataset.messageId !== s.messages[i]?.message_id)
      ) {
        if (++s.attempts > 30) {
          cancel("runtime-not-ready");
          return;
        }
        s.raf = requestAnimationFrame(tick);
        return;
      }
      if (!started) {
        // The real external runtime has committed this exact conversation's DOM.
        s.anchors.reading = mercuryRect(el.getBoundingClientRect());
        if (s.direction === "in") {
          const saved = conversationPosition(key);
          if (saved) {
            const anchor = domMessages.find((n) => n.dataset.messageId === saved.anchorMessageId);
            if (saved.atBottom) v.scrollTop = v.scrollHeight;
            else if (anchor)
              v.scrollTop +=
                anchor.getBoundingClientRect().top - v.getBoundingClientRect().top - saved.offset;
          }
        }
        s.renderer.dispose();
        try {
          s.renderer = new MercuryRenderer(
            layer,
            makeParticles(scopeSeed(key), mercuryBlocks(v, s.anchors.reading)),
          );
        } catch {
          cancel("canvas-unavailable");
          return;
        }
        started = true;
        event("started", s);
      }
      const actual = el.getBoundingClientRect(),
        h = host.getBoundingClientRect(),
        expected = s.anchors;
      if (
        Math.abs(actual.x - expected.reading.x) > 3 ||
        Math.abs(actual.width - expected.reading.width) > 3 ||
        Math.abs(h.x - expected.host.x) > 3 ||
        Math.abs(h.y - expected.host.y) > 3
      ) {
        cancel("geometry-changed");
        return;
      }
      // Loading indicators and the independent Composer may change vertical layout.
      // Follow the real surface without restarting time or changing particle IDs.
      s.anchors.reading = mercuryRect(actual);
      const delta = s.last ? now - s.last : 1000 / 60;
      s.last = now;
      if (delta > 180) {
        cancel("long-frame");
        return;
      }
      s.slow = delta > 26 ? s.slow + 1 : 0;
      if (s.slow >= 3 && s.renderer.quality !== "low") {
        s.renderer.quality = "low";
        event("quality-changed", s);
      }
      const p = s.timeline.advance(delta / 1000);
      maskPath.current?.setAttribute("d", phasePath(p));
      try {
        const t = performance.now();
        s.renderer.draw(p, s.anchors, MERCURY_STAGE);
        layer.setAttribute("data-draw-ms", (performance.now() - t).toFixed(3));
      } catch {
        cancel("renderer-unavailable");
        return;
      }
      layer.setAttribute("data-progress", p.toFixed(5));
      layer.setAttribute("data-quality", s.renderer.quality);
      if (s.timeline.settled) {
        complete();
        return;
      }
      s.raf = requestAnimationFrame(tick);
    };
    s.raf = requestAnimationFrame(tick);
  }, [
    transition,
    valid,
    ready,
    frame,
    region,
    viewport,
    canvas,
    maskPath,
    maskId,
    cancel,
    release,
  ]);
  useLayoutEffect(() => {
    if (!busy) return;
    const stop = () => cancel("layout-or-input");
    window.addEventListener("resize", stop);
    window.addEventListener("wheel", stop, { passive: true });
    window.addEventListener("touchmove", stop, { passive: true });
    return () => {
      window.removeEventListener("resize", stop);
      window.removeEventListener("wheel", stop);
      window.removeEventListener("touchmove", stop);
    };
  }, [busy, cancel]);
  return {
    frame: retiring && transition?.source ? transition.source : frame,
    retiring,
    busy,
    active,
  };
}
// Read existing presentation memory only; never writes drafts or adds history queries.
import { useConversationUi } from "../../store/conversationUi";
const conversationPosition = (key: string) => useConversationUi.getState().entries[key];
