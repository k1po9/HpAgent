import { animateSpatial, motionAllowed } from "../motion/preference";

/** One title arrival, then current-token details. Requests never wait for this light. */
export function flashReception(node: HTMLElement, complete: () => void) {
  const finish = () => {
    node.classList.remove("hp-inspector-accept", "hp-inspector-title-arrival");
    complete();
  };
  if (!motionAllowed()) {
    finish();
    return () => {};
  }
  node.classList.add("hp-inspector-accept", "hp-inspector-title-arrival");
  try {
    return animateSpatial(
      node,
      [{ opacity: 0 }, { opacity: 1 }],
      180,
      "cubic-bezier(.18,.72,.22,1)",
      finish,
    );
  } catch {
    finish();
    return () => {};
  }
}
