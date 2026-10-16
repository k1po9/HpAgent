import { useCallback, useLayoutEffect, useRef, useState, type RefObject } from "react";
import type { HpMessage } from "../../api/types";
import { useShell } from "../../store/shell";
import { MERCURY_ENABLED, MERCURY_STAGE } from "./config";
import { makeParticles, phasePath, scopeSeed, smooth, type Anchors } from "./geometry";
import { MercuryRenderer } from "./renderer";
import { MercuryTimeline, createMercuryTimeline } from "./timeline";
import { mercurySpeed } from "./palette";
import { mercuryRect as rect, mercuryHostVisible as hostVisible, mercuryBlocks } from "./dom";

interface Playback {
  scope: string;
  messages: HpMessage[];
  navigation: number;
}
interface Session extends Playback {
  serial: number;
  seed: number;
  region: HTMLDivElement;
  host: HTMLElement;
  anchors: Anchors;
  renderer: MercuryRenderer;
  timeline: MercuryTimeline;
  frame: number;
  last: number;
  slow: number;
  inline: { opacity: string; maskImage: string; webkitMaskImage: string; willChange: string };
}
let sequence = 0;
function restoreInline(s: Session) {
  Object.assign(s.region.style, s.inline);
}
function trace(s: Session, type: string, reason?: string) {
  window.dispatchEvent(
    new CustomEvent("hp-mercury-event", {
      detail: {
        type,
        reason,
        serial: s.serial,
        seed: s.seed,
        stage: MERCURY_STAGE,
        progress: s.timeline.progress,
        velocity: s.timeline.velocity,
        target: s.timeline.target,
        quality: s.renderer.quality,
      },
    }),
  );
}

/** Local presentation controller. Business target belongs exclusively to useReadingFold. */
export function useMercuryMotion({
  scope,
  conversationKey,
  messages,
  blocked,
  folded,
  allowed,
  region,
  viewport,
  maskId,
}: {
  scope: string;
  conversationKey: string;
  messages: HpMessage[];
  blocked: string | null;
  folded: boolean;
  allowed: boolean;
  region: RefObject<HTMLDivElement | null>;
  viewport: RefObject<HTMLDivElement | null>;
  maskId: string;
}) {
  const navigation = useShell((s) => s.requestToken),
    modal = useShell((s) => s.modal);
  const pending = useShell((s) => Boolean(s.pendingRoute || s.pendingResourceChange));
  const canvas = useRef<HTMLCanvasElement>(null),
    maskPath = useRef<SVGPathElement>(null);
  const session = useRef<Session | null>(null);
  const [playback, setPlayback] = useState<Playback | null>(null);
  const active = Boolean(
    playback &&
    playback.scope === scope &&
    playback.messages === messages &&
    playback.navigation === navigation &&
    !blocked &&
    allowed &&
    !modal &&
    !pending,
  );
  const stop = useCallback((reason: string, target?: 0 | 1, release = false, notify = true) => {
    const s = session.current;
    if (!s) return;
    if (s.frame) cancelAnimationFrame(s.frame);
    s.frame = 0;
    s.last = 0;
    trace(s, reason === "settled" ? "settled" : "cancelled", reason);
    if (target !== undefined) s.timeline.finish(target);
    restoreInline(s);
    s.renderer.clear(true);
    const layer = canvas.current;
    if (layer) {
      layer.dataset.mercuryState = "idle";
      layer.dataset.progress = String(s.timeline.progress);
    }
    if (release) {
      s.renderer.dispose();
      session.current = null;
    }
    if (notify) setPlayback(null);
  }, []);
  useLayoutEffect(() => () => stop("unmount", undefined, true, false), [stop]);
  useLayoutEffect(() => {
    const s = session.current;
    if (
      s &&
      (s.scope !== scope ||
        s.messages !== messages ||
        s.navigation !== navigation ||
        blocked ||
        !allowed ||
        modal ||
        pending)
    )
      stop("facts-or-scope-changed", folded ? 1 : 0, true);
  }, [scope, messages, navigation, blocked, allowed, modal, pending, folded, stop]);

  const prepare = (target: 0 | 1) => {
    if (
      !MERCURY_ENABLED ||
      !allowed ||
      blocked ||
      modal ||
      pending ||
      document.hidden ||
      innerWidth < 960
    )
      return;
    const el = region.current,
      layer = canvas.current;
    if (!el || !layer || el.closest(".hp-shell--sidebar-collapsed") || el.closest("[hidden]")) {
      // A settled hidden reading surface may reuse its original geometry below.
      if (!session.current || target !== 0) return;
    }
    let s = session.current;
    if (s && (s.scope !== scope || s.messages !== messages || s.navigation !== navigation)) {
      stop("stale", folded ? 1 : 0, true);
      s = null;
    }
    if (!s) {
      if (!el || !layer || !viewport.current || !el.getClientRects().length) return;
      const id = conversationKey.includes(":")
        ? conversationKey.slice(conversationKey.indexOf(":") + 1)
        : null;
      const host = Array.from(
        el.closest(".hp-shell")?.querySelectorAll<HTMLElement>("[data-conversation-id]") ?? [],
      ).find((n) => n.dataset.conversationId === id);
      if (!host || !hostVisible(host)) return;
      const reading = rect(el.getBoundingClientRect());
      if (reading.width < 280 || reading.height < 120) return;
      if (MERCURY_STAGE !== "points" && !CSS.supports("mask-image", `url("#${maskId}")`)) return;
      const blocks = mercuryBlocks(viewport.current, reading);
      const seed = scopeSeed(conversationKey);
      try {
        s = {
          scope,
          messages,
          navigation,
          serial: ++sequence,
          seed,
          region: el,
          host,
          anchors: { reading, host: rect(host.getBoundingClientRect()) },
          renderer: new MercuryRenderer(layer, makeParticles(seed, blocks)),
          timeline: createMercuryTimeline(folded ? 1 : 0, mercurySpeed(layer)),
          frame: 0,
          last: 0,
          slow: 0,
          inline: {
            opacity: el.style.opacity,
            maskImage: el.style.maskImage,
            webkitMaskImage: el.style.webkitMaskImage,
            willChange: el.style.willChange,
          },
        };
        session.current = s;
      } catch {
        return;
      }
    }
    if (!hostVisible(s.host)) {
      stop("host-unavailable", target, true);
      return;
    }
    const reversing = !s.timeline.settled;
    s.timeline.goto(target);
    if (layer) {
      layer.dataset.mercuryState = "playing";
      layer.dataset.seed = String(s.seed);
      layer.dataset.serial = String(s.serial);
      layer.dataset.stage = MERCURY_STAGE;
    }
    trace(s, reversing ? "reversed" : "started");
    setPlayback({ scope, messages, navigation });
  };
  useLayoutEffect(() => {
    const s = session.current,
      layer = canvas.current;
    if (!active || !s || !layer) return;
    if (MERCURY_STAGE !== "points") s.region.style.maskImage = `url("#${maskId}")`;
    s.region.style.willChange = "mask-image, opacity";
    const paint = (progress: number) => {
      if (MERCURY_STAGE === "points")
        s.region.style.opacity = String(1 - smooth(0.1, 0.48, progress));
      else maskPath.current?.setAttribute("d", phasePath(progress));
      const begin = performance.now();
      s.renderer.draw(progress, s.anchors, MERCURY_STAGE);
      layer.dataset.progress = progress.toFixed(5);
      layer.dataset.velocity = s.timeline.velocity.toFixed(5);
      layer.dataset.target = String(s.timeline.target);
      layer.dataset.quality = s.renderer.quality;
      layer.dataset.drawMs = (performance.now() - begin).toFixed(3);
    };
    const tick = (now: number) => {
      s.frame = 0;
      if (session.current !== s) return;
      const shell = useShell.getState();
      const invalid =
        document.hidden ||
        innerWidth < 960 ||
        shell.requestToken !== s.navigation ||
        Boolean(shell.modal || shell.pendingRoute || shell.pendingResourceChange) ||
        !s.region.isConnected ||
        !hostVisible(s.host);
      if (invalid) {
        stop("interrupted", s.timeline.target, true);
        return;
      }
      const actual = s.region.getBoundingClientRect(),
        host = s.host.getBoundingClientRect(),
        expected = s.anchors;
      if (
        Math.abs(actual.x - expected.reading.x) > 2 ||
        Math.abs(actual.y - expected.reading.y) > 2 ||
        Math.abs(actual.width - expected.reading.width) > 2 ||
        Math.abs(actual.height - expected.reading.height) > 2 ||
        Math.abs(host.x - expected.host.x) > 2 ||
        Math.abs(host.y - expected.host.y) > 2
      ) {
        stop("geometry-changed", s.timeline.target, true);
        return;
      }
      const delta = s.last ? now - s.last : 1000 / 60;
      s.last = now;
      if (delta > 180) {
        stop("long-frame", s.timeline.target, true);
        return;
      }
      s.slow = delta > 26 ? s.slow + 1 : 0;
      if (s.slow >= 3 && s.renderer.quality !== "low") {
        s.renderer.quality = "low";
        trace(s, "quality-changed", "frame-budget");
      }
      try {
        paint(s.timeline.advance(delta / 1000));
      } catch {
        stop("renderer-unavailable", s.timeline.target, true);
        return;
      }
      if (s.timeline.settled) {
        stop("settled", s.timeline.target);
        return;
      }
      s.frame = requestAnimationFrame(tick);
    };
    // Update the phase in the same commit as target semantics; no bright full-body flash.
    try {
      paint(s.timeline.progress);
    } catch {
      stop("renderer-unavailable", s.timeline.target, true);
      return;
    }
    if (!s.frame) s.frame = requestAnimationFrame(tick);
  }, [active, playback, folded, maskId, stop]);
  useLayoutEffect(() => {
    if (!active) return;
    const cancel = () => {
      const s = session.current;
      if (s) stop("user-or-layout-interrupt", s.timeline.target, true);
    };
    window.addEventListener("resize", cancel);
    window.addEventListener("wheel", cancel, { passive: true });
    window.addEventListener("touchmove", cancel, { passive: true });
    return () => {
      window.removeEventListener("resize", cancel);
      window.removeEventListener("wheel", cancel);
      window.removeEventListener("touchmove", cancel);
    };
  }, [active, stop]);
  return { canvas, maskPath, active, prepare };
}
