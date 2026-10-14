import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { useHandoff } from "../input-action/handoff";
import { useAuth } from "../../store/auth";
import { useMotionAllowed } from "../motion/preference";
import type { VitalityState, VitalityView } from "./policy";
function Glyph({ state, detail }: { state: VitalityState; detail: boolean }) {
  const halo = detail && <circle cx="20" cy="20" r="18" opacity=".18" strokeWidth=".8" />;
  return (
    <svg
      viewBox="0 0 40 40"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {halo}
      {state === "waiting" && (
        <>
          <circle cx="20" cy="20" r="12" opacity=".2" />
          <g className="hv-rotor">
            <path
              d={
                detail
                  ? "M20 8 A12 12 0 0 1 32 20 M20 32 A12 12 0 0 1 8 20"
                  : "M8 20 A12 12 0 1 0 10.8074667 12.2865487"
              }
            />
            <circle cx="10.8074667" cy="12.2865487" r="1.5" fill="currentColor" stroke="none" />
          </g>
          {detail && <circle cx="20" cy="20" r="5" opacity=".35" />}
        </>
      )}
      {state === "thinking" && (
        <>
          {detail && <circle className="hv-field" cx="20" cy="20" r="14" opacity=".15" />}{" "}
          {[11, 20, 29].map((x, i) => (
            <circle
              key={x}
              className="hv-thought"
              style={{ animationDelay: `${i * 160}ms` }}
              cx={x}
              cy="20"
              r={detail ? 2.6 : 2.2}
              fill="currentColor"
              stroke="none"
            />
          ))}
        </>
      )}
      {state === "tool" && (
        <>
          <path
            d={
              detail
                ? "M8 25 L15 13 L26 13 L32 25 L20 30 Z M15 13 L20 30 L26 13"
                : "M10 25 L20 12 L30 25 Z"
            }
            opacity=".45"
          />
          {(detail
            ? [
                [8, 25],
                [15, 13],
                [26, 13],
                [32, 25],
                [20, 30],
              ]
            : [
                [10, 25],
                [20, 12],
                [30, 25],
              ]
          ).map(([x, y], i) => (
            <circle
              key={i}
              className="hv-node"
              style={{ animationDelay: `${i * 180}ms` }}
              cx={x}
              cy={y}
              r="2.2"
              fill="currentColor"
              stroke="none"
            />
          ))}
        </>
      )}
      {state === "running" && (
        <g className="hv-work">
          <path d="M20 8 L32 20 L20 32 L8 20 Z" opacity=".7" />
          <circle
            className="hv-work-core"
            cx="20"
            cy="20"
            r="2.5"
            fill="currentColor"
            stroke="none"
          />
          {[
            [20, 8],
            [8, 20],
            [20, 32],
            [32, 20],
          ].map(([x, y], i) => (
            <circle
              key={i}
              className="hv-node"
              style={{ animationDelay: `${i * 180}ms` }}
              cx={x}
              cy={y}
              r="1.4"
              fill="currentColor"
              stroke="none"
            />
          ))}
          {detail && <path d="M14 20 H26" opacity=".4" />}
        </g>
      )}
      {state === "succeeded" && (
        <>
          <circle cx="20" cy="20" r="12" opacity=".35" />
          <path className="hv-check" pathLength="1" d="M13 20 L18 25 L27 15" />
        </>
      )}
      {state === "failed" && (
        <>
          <path d="M20 8 A12 12 0 0 1 30 27 M20 32 A12 12 0 0 1 10 13" opacity=".6" />
          <g className="hv-break">
            <path d="M16 16 L24 24 M24 16 L16 24" />
          </g>
        </>
      )}
      {["cancelling", "cancelled", "idle"].includes(state) && (
        <>
          <circle cx="20" cy="20" r="12" opacity=".35" />
          <rect x="16" y="16" width="8" height="8" rx="1" />
        </>
      )}
    </svg>
  );
}
function Signal({
  state,
  size,
  runId,
}: {
  state: VitalityState;
  size: "compact" | "detail";
  runId: string;
}) {
  const handoff = useHandoff();
  const account = useAuth((s) => s.account?.account_id);
  const receiving =
    size === "compact" &&
    !["idle", "succeeded", "failed", "cancelling", "cancelled"].includes(state) &&
    handoff !== null &&
    handoff.ticket.account === account &&
    (handoff.ticket.run === runId || (!handoff.ticket.run && handoff.ticket.previousRun !== runId));
  const ref = useRef<HTMLSpanElement>(null);
  const allowed = useMotionAllowed();
  const [visible, setVisible] = useState(true);
  const motion = allowed && visible;
  const [relay, setRelay] = useState({
    current: state,
    outgoing: null as VitalityState | null,
    revision: 0,
    entering: false,
    celebrate: false,
    opacity: 1,
    transform: "none",
  });
  const previous = useRef(state),
    generation = useRef(0);
  useLayoutEffect(() => {
    if (!ref.current || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) =>
      setVisible(Boolean(entry?.isIntersecting)),
    );
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => {
    const token = ++generation.current;
    const changed = previous.current !== state;
    const wasTerminal = ["succeeded", "failed", "cancelled"].includes(previous.current);
    previous.current = state;
    const current = ref.current?.querySelector<HTMLElement>(".hp-vitality__current");
    const old = ref.current?.querySelector<HTMLElement>(".hp-vitality__outgoing");
    const currentStyle = current && getComputedStyle(current),
      oldStyle = old && getComputedStyle(old);
    const oldDominant = oldStyle && Number(oldStyle.opacity) > Number(currentStyle?.opacity ?? 1);
    const computed = oldDominant ? oldStyle : currentStyle;
    // A local, bounded presentation pair; business labels render directly from props.
    setRelay((prior) =>
      !motion || !changed
        ? { ...prior, current: state, outgoing: null, entering: false, celebrate: false }
        : {
            current: state,
            outgoing: oldDominant ? prior.outgoing : prior.current,
            revision: prior.revision + 1,
            entering: true,
            celebrate: !wasTerminal && ["succeeded", "failed"].includes(state),
            opacity: Number(computed?.opacity ?? 1),
            transform: computed?.transform ?? "none",
          },
    );
    if (!motion || !changed) return;
    const timer = window.setTimeout(() => {
      if (generation.current === token)
        setRelay((p) => ({ ...p, outgoing: null, entering: false, celebrate: false }));
    }, 620);
    return () => {
      window.clearTimeout(timer);
    };
  }, [state, motion]);
  return (
    <span
      ref={ref}
      className={`hp-vitality hp-vitality--${size}`}
      data-vitality={state}
      data-previous={relay.outgoing ?? undefined}
      data-handoff-run={runId}
      data-handoff={receiving ? handoff?.phase : undefined}
      data-animate={motion}
      data-celebrate={relay.celebrate}
      aria-hidden="true"
    >
      <span
        className="hp-vitality__frame"
        data-ring-target={state === "running" ? "running" : undefined}
      >
        {relay.outgoing && (
          <span
            key={`old-${relay.revision}`}
            className="hp-vitality__layer hp-vitality__outgoing"
            style={
              {
                "--hv-out-opacity": relay.opacity,
                "--hv-out-transform": relay.transform,
              } as CSSProperties
            }
          >
            <Glyph state={relay.outgoing} detail={size === "detail"} />
          </span>
        )}
        <span
          key={`current-${relay.revision}`}
          className="hp-vitality__layer hp-vitality__current"
          data-entering={relay.entering}
        >
          <Glyph state={relay.current} detail={size === "detail"} />
        </span>
      </span>
    </span>
  );
}
export function VitalityHeading({
  view,
  runId,
  generation,
  size = "compact",
  testId,
}: {
  view: VitalityView;
  runId: string;
  generation?: number;
  size?: "compact" | "detail";
  testId?: string;
}) {
  const allowed = useMotionAllowed();
  const account = useAuth((s) => s.account?.account_id ?? "anonymous");
  return (
    <div
      className={`hp-run-heading hp-vitality-heading hp-vitality-heading--${size}`}
      data-vitality={view.state}
      data-animate={allowed}
    >
      <span data-testid={!view.terminal && view.state !== "idle" ? "run-spinner" : undefined}>
        <Signal
          key={`${account}:${runId}:${generation ?? "snapshot"}`}
          state={view.state}
          runId={runId}
          size={size}
        />
      </span>
      <div className="hp-vitality-copy">
        <span role="status" data-testid={testId} className="hp-vitality-label">
          {view.state === "thinking"
            ? "模型处理中"
            : view.state === "tool"
              ? "工具调用中"
              : view.runText}
        </span>
        <span
          className="hp-vitality-caption"
          data-testid={view.progressText ? "run-progress" : undefined}
        >
          {view.caption}
        </span>
      </div>
    </div>
  );
}
