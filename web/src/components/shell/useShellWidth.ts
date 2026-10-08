import { useEffect, useState, type RefObject } from "react";
export function useShellWidth(ref: RefObject<HTMLElement | null>) {
  const [width, setWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const node = ref.current;
    const update = () => setWidth(node?.getBoundingClientRect().width || window.innerWidth);
    update();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    if (node) observer?.observe(node);
    window.addEventListener("resize", update);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [ref]);
  return width;
}
