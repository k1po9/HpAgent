import { useEffect, useState, type RefObject } from "react";

import { measureShell } from "./layout";

/** CSS tokens are the shared source for widths and the sidebar's fit calculation. */
export function useShellWidth(ref: RefObject<HTMLElement | null>) {
  const [layout, setLayout] = useState(() => measureShell(null));
  useEffect(() => {
    const node = ref.current;
    const update = () => {
      const next = measureShell(node);
      setLayout((current) =>
        Object.entries(next).every(([key, value]) => current[key as keyof typeof current] === value)
          ? current
          : next,
      );
    };
    update();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    if (node) observer?.observe(node);
    window.addEventListener("resize", update);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [ref]);
  return layout;
}
