import type { SceneAnchors } from "./IdentityScene";

/** Design-space anchors for when no scene is projecting them. */
export const DEFAULT_ANCHORS: SceneAnchors = {
  a: { x: 640, y: 262 },
  b: { x: 588, y: 826 },
  centre: { x: 1014, y: 572 },
};

/** Writes per-frame anchors straight to the DOM; a React render per frame would be wasteful. */
export function applyAnchors(root: HTMLElement, { a, b, centre }: SceneAnchors) {
  const set = (selector: string, attrs: Record<string, number>) => {
    const node = root.querySelector(selector);
    if (node) for (const key in attrs) node.setAttribute(key, attrs[key]!.toFixed(1));
  };
  set("[data-anchor-line=a]", { x1: a.x, y1: a.y });
  set("[data-anchor-line=b]", { x1: b.x, y1: b.y });
  set("[data-anchor-dot=a]", { cx: a.x, cy: a.y });
  set("[data-anchor-dot=b]", { cx: b.x, cy: b.y });
  root.style.setProperty("--centre-x", `${centre.x}px`);
  root.style.setProperty("--centre-y", `${centre.y}px`);
}
