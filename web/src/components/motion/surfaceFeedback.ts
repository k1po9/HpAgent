import { animateSpatial, motionAllowed } from "./preference";
let retire = () => {};
export function clearSurfaceRetreat() {
  retire();
}
/** No copied children, text, controls, IDs or focus trap survive logical close. */
export function prepareSurfaceRetreat(node: HTMLDialogElement) {
  if (!motionAllowed() || !node.open || node.hidden) return () => {};
  const rect = node.getBoundingClientRect();
  const style = getComputedStyle(node);
  const plane = document.createElement("div");
  plane.className = "hp-surface-retreat";
  plane.setAttribute("aria-hidden", "true");
  plane.inert = true;
  Object.assign(plane.style, {
    position: "fixed",
    pointerEvents: "none",
    zIndex: "1000",
    left: `${rect.left}px`,
    top: `${rect.top}px`,
    width: `${rect.width}px`,
    height: `${rect.height}px`,
    borderRadius: style.borderRadius,
    background: style.backgroundColor,
    border: style.border,
    boxShadow: style.boxShadow,
  });
  return () => {
    clearSurfaceRetreat();
    if (!motionAllowed()) return;
    document.body.append(plane);
    retire = animateSpatial(
      plane,
      [
        { opacity: 0.12, transform: "none" },
        { opacity: 0, transform: "translateY(8px) scale(.99)" },
      ],
      210,
      "cubic-bezier(.35,.1,.62,1)",
      () => plane.remove(),
    );
  };
}
