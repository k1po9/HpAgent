import { useSyncExternalStore } from "react";

/** Below these the scaled design canvas gets unreadable, so the panel flows instead. */
const CANVAS_MIN_WIDTH = 1100;
const CANVAS_MIN_HEIGHT = 620;

function subscribe(listener: () => void) {
  window.addEventListener("resize", listener);
  return () => window.removeEventListener("resize", listener);
}

const canvasFits = () => innerWidth >= CANVAS_MIN_WIDTH && innerHeight >= CANVAS_MIN_HEIGHT;

export function useIdentityCanvas() {
  return useSyncExternalStore(subscribe, canvasFits);
}
