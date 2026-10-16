import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { useShell } from "../../store/shell";
import { useAuth } from "../../store/auth";
import { useRunInspector } from "../../store/runInspector";
import { useArtifacts } from "../../store/artifacts";
import { useWorks } from "../../store/works";
import { useWorkspace } from "../../store/workspace";
import { useMotionAllowed, motionAllowed, subscribeMotion } from "../motion/preference";
import { inspectorIntent } from "../spatial/inspectorIntent";
import { playFilament } from "../spatial/filament";
export type InspectorPhase = "closed" | "receiving" | "landed" | "settled" | "closing";
const accounts = new WeakMap<object, number>();
let accountSerial = 0;
function accountKey(account: object | null) {
  if (!account) return 0;
  if (!accounts.has(account)) accounts.set(account, ++accountSerial);
  return accounts.get(account)!;
}
/** Owns presentation only. Object components remain mounted and querying under a hidden/inert gate. */
export function useInspectorFlow(compact: boolean) {
  const route = useShell((s) => s.route),
    expanded = useShell((s) => s.expanded);
  const account = useAuth((s) => s.account),
    status = useAuth((s) => s.status);
  const allowed = useMotionAllowed();
  const unavailable = useRunInspector((s) => s.unavailable),
    error = useRunInspector((s) => s.error),
    runId = useRunInspector((s) => s.runId);
  const inspector = route.inspector;
  const scope = JSON.stringify([
    accountKey(account),
    status,
    route.screen,
    route.conversationId,
    route.directoryId,
  ]);
  const key = inspector
    ? JSON.stringify([
        scope,
        inspector.kind,
        inspector.objectId,
        // Artifact versions are internal selection, not a new spatial arrival.
        inspector.kind === "artifact" || inspector.kind === "file" ? null : inspector.versionId,
        inspector.origin?.conversationId,
        inspector.origin?.messageId,
        inspector.origin?.workId,
        inspector.origin?.originalVersionId,
        inspector.origin?.sourceRequirementRevision,
        inspector.origin?.directoryId,
        inspector.origin?.runId,
      ])
    : null;
  const [view, setView] = useState<{
    key: string | null;
    scope: string;
    phase: InspectorPhase;
    expanded: boolean;
    arrived?: boolean;
    details?: boolean;
    token?: number;
    exitAnchor?: { left: number; top: number; width: number; height: number };
  }>({ key: null, scope, phase: "closed", expanded });
  const serial = useRef(0),
    latest = useRef({ key, scope });
  const previous = useRef<{
    key: string;
    scope: string;
    source: HTMLElement | null;
    expanded: boolean;
    landed: boolean;
  } | null>(null);
  const target = useRef<HTMLElement>(null);
  const returnStart = useRef({ x: 0, y: 0 });
  const exitAnchor = useRef({ left: 20, top: 24, width: 200, height: 24 });
  const receiverRef = useCallback((node: HTMLElement | null) => {
    target.current = node;
    node?.classList.add("hp-inspector-receiver");
  }, []);
  useLayoutEffect(() => {
    const node = target.current,
      panel = node?.closest(".hp-inspector");
    if (!node || !panel) return;
    const r = node.getBoundingClientRect(),
      p = panel.getBoundingClientRect();
    returnStart.current = { x: r.left + Math.min(24, r.width / 3), y: r.top + r.height / 2 };
    exitAnchor.current = {
      left: r.left - p.left,
      top: r.top - p.top,
      width: r.width,
      height: r.height,
    };
  });
  const artifactError = useArtifacts((s) =>
    inspector?.kind === "artifact" ? Boolean(s.queries[inspector.objectId]?.error) : false,
  );
  const fileError = useWorkspace((s) => {
    if (inspector?.kind !== "file") return false;
    const node = s.tree?.nodes.find((n) => n.node_id === inspector.objectId);
    return Boolean(
      s.error || (s.tree && !node) || (node?.file_id && s.cache[`file:${node.file_id}`]?.error),
    );
  });
  const taskError = useWorks((s) =>
    inspector?.kind === "task" ? Boolean(s.detailErrors[inspector.objectId]) : false,
  );
  const safeError =
    taskError ||
    artifactError ||
    fileError ||
    (inspector?.kind === "run" && runId === inspector.objectId && (unavailable || Boolean(error)));
  useLayoutEffect(() => {
    latest.current = { key, scope };
  });
  useLayoutEffect(() => {
    const token = ++serial.current;
    const current = () =>
      serial.current === token && latest.current.key === key && latest.current.scope === scope;
    let dispose = () => {},
      timer: ReturnType<typeof setTimeout> | undefined,
      unsubscribe = () => {};
    if (!key) {
      const old = previous.current;
      previous.current = null;
      if (old && old.scope === scope && !compact && motionAllowed()) {
        setView({
          key: null,
          scope,
          phase: "closing",
          expanded: old.expanded,
          exitAnchor: exitAnchor.current,
        });
        const finish = () => {
          dispose();
          unsubscribe();
          clearTimeout(timer);
          if (current()) setView({ key: null, scope, phase: "closed", expanded: old.expanded });
        };
        timer = setTimeout(finish, 680);
        unsubscribe = subscribeMotion(() => {
          if (!motionAllowed()) finish();
        });
        if (old.landed && old.source && target.current)
          dispose = playFilament({
            source: old.source,
            target: target.current,
            token,
            current,
            closing: true,
            returnStart: returnStart.current,
            arrive: () => {},
            settle: () => {},
          });
        window.addEventListener("resize", finish, { once: true });
        const clean = dispose;
        dispose = () => {
          clean();
          window.removeEventListener("resize", finish);
        };
      } else setView({ key: null, scope, phase: "closed", expanded });
    } else {
      const source = inspectorIntent(useShell.getState().route)?.source ?? null;
      previous.current = {
        key,
        scope,
        source,
        expanded: useShell.getState().expanded,
        landed: false,
      };
      const receive =
        (inspector?.kind === "run" ||
          inspector?.kind === "artifact" ||
          inspector?.kind === "file" ||
          inspector?.kind === "task") &&
        source &&
        !compact &&
        allowed &&
        !safeError;
      const update = (phase: InspectorPhase, arrived = false) => {
        if (current())
          setView((v) => ({
            key,
            scope,
            phase,
            expanded: useShell.getState().expanded,
            arrived,
            token,
            details:
              phase === "receiving"
                ? false
                : !arrived || (v.key === key && v.token === token && Boolean(v.details)),
          }));
      };
      update(receive ? "receiving" : "settled");
      if (receive && target.current)
        dispose = playFilament({
          source,
          target: target.current,
          token,
          current,
          arrive: (actual = true) => {
            if (current() && previous.current) previous.current.landed = actual;
            update("landed", actual);
          },
          settle: () => update("settled", previous.current?.landed ?? false),
        });
      else update("settled");
    }
    return () => {
      if (serial.current === token) serial.current = token + 1;
      dispose();
      clearTimeout(timer);
      unsubscribe();
    };
    // Tab/expanded/requestToken do not own a flight or the object's query lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, scope]);
  // Safety changes settle the existing intent; they never start another flight.
  useLayoutEffect(() => {
    if (compact || !allowed || safeError) {
      serial.current++;
      // Safety must remove the gate before paint and invalidate every outstanding frame.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setView((v) => ({
        ...v,
        key,
        scope,
        phase: key ? "settled" : "closed",
        arrived: false,
        details: true,
      }));
    }
  }, [compact, allowed, safeError, key, scope]);
  useLayoutEffect(() => {
    if (previous.current) previous.current.expanded = expanded;
  }, [expanded]);
  const completeTitle = useCallback((expectedKey: string | null, expectedToken: number) => {
    if (serial.current !== expectedToken || latest.current.key !== expectedKey) return;
    setView((v) =>
      v.key === expectedKey && v.token === expectedToken && v.scope === latest.current.scope
        ? { ...v, details: true }
        : v,
    );
  }, []);
  const closing =
    !key &&
    view.scope === scope &&
    (view.key !== null || view.phase === "closing") &&
    !compact &&
    allowed;
  const phase: InspectorPhase = key
    ? view.key === key && view.scope === scope
      ? view.phase
      : "receiving"
    : closing
      ? "closing"
      : "closed";
  return {
    phase,
    receiverRef,
    exitAnchor: view.exitAnchor ?? { left: 20, top: 24, width: 200, height: 24 },
    presentationKey: key,
    presentationToken: view.token ?? 0,
    completeTitle,
    detailsReveal: Boolean(
      key &&
      view.key === key &&
      view.scope === scope &&
      view.details &&
      (phase === "landed" || phase === "settled"),
    ),
    arrived: Boolean(key && view.key === key && view.scope === scope && view.arrived),
    scope,
    expanded: closing ? view.expanded : expanded,
    occupies: Boolean(key) || closing,
    reveal: Boolean(
      key &&
      view.key === key &&
      view.scope === scope &&
      (phase === "landed" || phase === "settled"),
    ),
  };
}
export type InspectorFlow = ReturnType<typeof useInspectorFlow>;
