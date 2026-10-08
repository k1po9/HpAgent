import { useEffect } from "react";
/** Animates an existing visual host, never the business component's identity. */
export function useInspectorFlow(objectKey: string | null) {
  useEffect(() => {
    if (!objectKey) return;
    const node = document.getElementById("inspector-title")?.closest("dialog");
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    let animation: Animation | undefined;
    if (node && !preference.matches && typeof node.animate === "function")
      animation = node.animate(
        [
          { opacity: 0.7, transform: "translateX(12px)" },
          { opacity: 1, transform: "none" },
        ],
        {
          duration: parseFloat(getComputedStyle(node).getPropertyValue("--hp-flow")) || 220,
          easing: "ease-out",
        },
      );
    const cancel = () => {
      if (preference.matches) animation?.cancel();
    };
    preference.addEventListener("change", cancel);
    return () => {
      animation?.cancel();
      preference.removeEventListener("change", cancel);
    };
  }, [objectKey]);
}
