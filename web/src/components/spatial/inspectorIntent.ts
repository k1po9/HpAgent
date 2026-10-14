import type { Inspector, Route } from "../../store/shell";
import { useAuth } from "../../store/auth";
export type InspectorIntent = {
  route: Route;
  source: HTMLElement | null;
  account: object | null;
};
let intent: InspectorIntent | null = null;
/** Navigation supplies only an approved, identity-bound source, never animation state. */
export function captureInspectorIntent(route: Route, inspector: Inspector): InspectorIntent {
  const source = inspector.origin?.triggerId
    ? document.getElementById(inspector.origin.triggerId)
    : document.activeElement instanceof HTMLElement && document.activeElement !== document.body
      ? document.activeElement
      : null;
  return { route, source, account: useAuth.getState().account };
}
export function approveInspectorIntent(next: InspectorIntent | null) {
  intent = next;
}
export function inspectorIntent(route: Route) {
  return intent?.route === route && intent.account === useAuth.getState().account ? intent : null;
}
