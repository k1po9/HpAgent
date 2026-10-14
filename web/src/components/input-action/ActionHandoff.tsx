import { useLayoutEffect } from "react";
import { useAuth } from "../../store/auth";
import { useShell } from "../../store/shell";
import { useWorkbench } from "../../store/workbench";
import { useMotionAllowed, motionAllowed } from "../motion/preference";
import { cancelHandoff, phaseHandoff, useHandoff, handoffSnapshot } from "./handoff";
import { orbitGeometry, temperature } from "./geometry";
const NS = "http://www.w3.org/2000/svg";
const node = <K extends keyof SVGElementTagNameMap>(tag: K) => document.createElementNS(NS, tag);
/** Owns only one decorative relay. It neither submits nor delays business state. */
export function ActionHandoff() {
  const intent = useHandoff(),
    ticket = intent?.ticket;
  const run = useWorkbench((s) => s.activeRun);
  const generation = useWorkbench((s) => s.pollGeneration);
  const stopping = useWorkbench((s) => s.stopping),
    conversation = useWorkbench((s) => s.activeConversationId);
  const account = useAuth((s) => s.account?.account_id),
    navigation = useShell((s) => s.requestToken);
  const allowed = useMotionAllowed();

  useLayoutEffect(() => () => cancelHandoff(), []);
  useLayoutEffect(() => {
    if (ticket?.run && run?.run_id === ticket.run && !["queued", "running"].includes(run.status))
      cancelHandoff(ticket.id);
  }, [ticket, run?.run_id, run?.status]);
  useLayoutEffect(() => {
    if (!ticket) return;
    const id = ticket.id;
    if (
      !allowed ||
      account !== ticket.account ||
      conversation !== ticket.conversation ||
      navigation !== ticket.navigation ||
      stopping ||
      (ticket.generation !== undefined && ticket.generation !== generation) ||
      ((run?.run_id ?? null) !== ticket.previousRun &&
        useWorkbench.getState().activeRun?.status !== "queued" &&
        useWorkbench.getState().activeRun?.status !== "running")
    ) {
      cancelHandoff(id);
      return;
    }
    if (!ticket.run) return;
    if (run?.run_id !== ticket.run) {
      cancelHandoff(id);
      return;
    }
    let raf = 0,
      overlay: SVGSVGElement | null = null,
      disposed = false;
    const eligible = () => {
      const s = useWorkbench.getState();
      return (
        !disposed &&
        motionAllowed() &&
        !s.stopping &&
        !document.querySelector("dialog[open]:not(.hp-surface--region)") &&
        s.activeRun?.run_id === ticket.run &&
        ["queued", "running"].includes(s.activeRun.status) &&
        s.pollGeneration === ticket.generation &&
        s.activeConversationId === ticket.conversation &&
        useAuth.getState().account?.account_id === ticket.account &&
        useShell.getState().requestToken === ticket.navigation &&
        handoffSnapshot()?.ticket.id === id
      );
    };
    const finish = () => {
      if (disposed) return;
      disposed = true;
      cancelAnimationFrame(raf);
      overlay?.remove();
      window.removeEventListener("resize", finish);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("wheel", finish, true);
      window.removeEventListener("touchmove", finish, true);
      cancelHandoff(id);
    };
    // The accepted reply triggers the thread's own scroll/layout. Settle that before departure;
    // user scrolling still cancels immediately, and any scrolling after departure cancels.
    const onScroll = () => {
      if (overlay) finish();
    };
    window.addEventListener("resize", finish);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("wheel", finish, { capture: true, passive: true });
    window.addEventListener("touchmove", finish, { capture: true, passive: true });
    const deadline = performance.now() + 600;
    let stableSince = performance.now(),
      lastPosition = "";
    const retry = () => {
      if (performance.now() < deadline) raf = requestAnimationFrame(start);
      else finish();
    };
    const start = () => {
      if (!eligible()) {
        finish();
        return;
      }
      const receiver = document.querySelector<HTMLElement>(
        `.hp-vitality--compact[data-handoff-run="${CSS.escape(ticket.run!)}"]`,
      );
      const svg = receiver?.querySelector<SVGSVGElement>(".hp-vitality__current svg");
      if (!svg) {
        retry();
        return;
      }
      const frame = receiver!.querySelector<HTMLElement>(".hp-vitality__frame")!;
      const frameRect = frame.getBoundingClientRect();
      const side = Number.parseFloat(getComputedStyle(svg).width);
      // Measure the stable host, not C's transient scaled incoming glyph.
      const target = new DOMRect(
          frameRect.left + (frameRect.width - side) / 2,
          frameRect.top + (frameRect.height - side) / 2,
          side,
          side,
        ),
        source = document
          .querySelector<HTMLElement>(".hp-composer__action")
          ?.getBoundingClientRect();
      if (
        !source ||
        target.width <= 0 ||
        target.height <= 0 ||
        target.top < 0 ||
        target.bottom > innerHeight ||
        target.left < 0 ||
        target.right > innerWidth
      ) {
        retry();
        return;
      }
      const position = [frameRect.left, frameRect.top, source.left, source.top]
        .map((n) => Math.round(n))
        .join(":");
      if (position !== lastPosition) {
        lastPosition = position;
        stableSince = performance.now();
      }
      if (performance.now() - stableSince < 60) {
        retry();
        return;
      }
      const g = orbitGeometry(source, target);
      overlay = node("svg");
      overlay.classList.add("hp-action-relay");
      overlay.setAttribute("aria-hidden", "true");
      overlay.setAttribute("viewBox", `0 0 ${innerWidth} ${innerHeight}`);
      overlay.dataset.runId = ticket.run!;
      overlay.dataset.phase = "travelling";
      const measurement = node("path");
      measurement.setAttribute("d", g.travel);
      overlay.append(measurement);
      document.body.append(overlay);
      const travel = measurement.getTotalLength();
      measurement.setAttribute("d", g.path);
      const total = measurement.getTotalLength();
      if (!(travel > 0 && total > travel)) {
        finish();
        return;
      }
      measurement.setAttribute("visibility", "hidden");
      const paths = Array.from({ length: 18 }, () => {
        const p = node("path");
        p.setAttribute("d", g.path);
        p.setAttribute("fill", "none");
        p.setAttribute("stroke-width", "2.5");
        p.setAttribute("stroke-linecap", "round");
        overlay!.append(p);
        return p;
      });
      const arc = node("path");
      arc.setAttribute("d", g.path);
      arc.setAttribute("fill", "none");
      arc.setAttribute("stroke", "#2879bf");
      arc.setAttribute("stroke-width", String((target.width * 1.8) / 40));
      arc.setAttribute("stroke-linecap", "round");
      arc.setAttribute("visibility", "hidden");
      overlay.append(arc);
      const dot = node("circle");
      dot.setAttribute("r", "2.6");
      overlay.append(dot);
      phaseHandoff(id, "travelling");
      let phase = "travelling";
      const began = performance.now();
      let previousFrame = began;
      const draw = (now: number) => {
        const currentTarget = frame.getBoundingClientRect();
        if (
          Math.abs(currentTarget.left - frameRect.left) > 2 ||
          Math.abs(currentTarget.top - frameRect.top) > 2
        ) {
          finish();
          return;
        }
        if (!eligible()) {
          finish();
          return;
        }
        // A background or long frame gap resolves to C; never fast-forward old visual work.
        if (now - previousFrame > 180) {
          finish();
          return;
        }
        previousFrame = now;
        const elapsed = now - began;
        if (elapsed > 1100) {
          finish();
          return;
        }
        const t = Math.min(1, elapsed / 560),
          a = Math.max(0, Math.min(1, (elapsed - 560) / 240));
        const head =
          elapsed < 560
            ? travel * (1 - (1 - t) ** 2)
            : travel + (total - travel) * (1 - (1 - a) ** 2);
        const next = elapsed < 560 ? "travelling" : "forming";
        if (next !== phase) {
          phase = next;
          phaseHandoff(id, "forming");
          overlay!.dataset.phase = phase;
        }
        // Read the current state tone without binding the relay lifetime to progress updates.
        const tone = getComputedStyle(frame).color;
        const rgb = tone
          .match(/[\d.]+/g)
          ?.slice(0, 3)
          .map(Number) ?? [40, 121, 191];
        const captureTone = `rgb(${[40, 121, 191].map((v, i) => Math.round(v + (rgb[i]! - v) * a)).join(",")})`;
        arc.setAttribute("stroke", captureTone);
        overlay!.dataset.arcFraction = String(a);
        overlay!.dataset.head = String(head);
        overlay!.dataset.travelLength = String(travel);
        const tail = Math.max(0, Math.min(head, travel) - 110) * (1 - a) + travel * a;
        const end = Math.min(head, travel);
        paths.forEach((p, i) => {
          const from = tail + ((end - tail) * i) / 18,
            to = tail + ((end - tail) * (i + 1)) / 18;
          p.setAttribute("stroke-dasharray", `${Math.max(0, to - from)} ${total + 20}`);
          p.setAttribute("stroke-dashoffset", String(-from));
          p.setAttribute("stroke", temperature((from + to) / 2 / travel));
          p.setAttribute("opacity", String(((i + 1) / 18) * (1 - a)));
        });
        if (a > 0) {
          arc.setAttribute("visibility", "visible");
          arc.setAttribute("stroke-dasharray", `${head - travel} ${total + 20}`);
          arc.setAttribute("stroke-dashoffset", String(-travel));
        }
        const pos = measurement.getPointAtLength(head);
        dot.setAttribute("cx", String(pos.x));
        dot.setAttribute("cy", String(pos.y));
        dot.setAttribute("fill", a > 0 ? captureTone : temperature(Math.min(1, head / travel)));
        dot.setAttribute("r", String(2.6 * (1 - a) + ((target.width * 1.5) / 40) * a));
        if (a === 1) {
          finish();
          return;
        }
        raf = requestAnimationFrame(draw);
      };
      draw(began);
    };
    raf = requestAnimationFrame(start);
    return finish;
  }, [ticket, generation, run?.run_id, allowed, account, conversation, navigation, stopping]);
  return null;
}
