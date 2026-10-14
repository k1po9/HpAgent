import { useSyncExternalStore } from "react";

export type MotionPreference = "system" | "reduce";
const key = "hpagent.motion";
const event = "hpagent-motion-change";
export function motionPreference(): MotionPreference {
  try {
    return localStorage.getItem(key) === "reduce" ? "reduce" : "system";
  } catch {
    return "system";
  }
}
export function setMotionPreference(value: MotionPreference) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Restricted storage still permits this session's setting.
  }
  sessionPreference = value;
  window.dispatchEvent(new Event(event));
}
let sessionPreference: MotionPreference | undefined;
export function motionAllowed() {
  return (
    !document.hidden &&
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches &&
    (sessionPreference ?? motionPreference()) !== "reduce"
  );
}
export function subscribeMotion(listener: () => void) {
  const query = window.matchMedia("(prefers-reduced-motion: reduce)");
  const storage = () => {
    sessionPreference = undefined;
    listener();
  };
  query.addEventListener("change", listener);
  document.addEventListener("visibilitychange", listener);
  window.addEventListener(event, listener);
  window.addEventListener("storage", storage);
  return () => {
    query.removeEventListener("change", listener);
    document.removeEventListener("visibilitychange", listener);
    window.removeEventListener(event, listener);
    window.removeEventListener("storage", storage);
  };
}
export function useMotionAllowed() {
  return useSyncExternalStore(subscribeMotion, motionAllowed, () => false);
}
export function useMotionPreference() {
  return useSyncExternalStore(
    subscribeMotion,
    () => sessionPreference ?? motionPreference(),
    () => "system" as const,
  );
}

/** Local animation owner: cancellation always reveals the current DOM state. */
export function animateSpatial(
  node: HTMLElement,
  frames: Keyframe[],
  duration: number,
  easing = "cubic-bezier(.2,.7,.2,1)",
  done?: () => void,
) {
  if (!motionAllowed() || !node.animate) {
    done?.();
    return () => {};
  }
  const animation = node.animate(frames, { duration, easing });
  let disposed = false;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    unsubscribe();
    window.removeEventListener("resize", dispose);
    animation.cancel();
    done?.();
  };
  const unsubscribe = subscribeMotion(() => {
    if (!motionAllowed()) dispose();
  });
  window.addEventListener("resize", dispose);
  animation.finished.then(dispose, dispose);
  return dispose;
}
