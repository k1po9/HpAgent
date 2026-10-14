import { useLayoutEffect, useRef } from "react";
import type { Screen } from "../../store/shell";
import { animateSpatial } from "../motion/preference";

/** One decoration measured from the accepted route's real button. */
export function RailTrack({ screen }: { screen: Screen }) {
  const ref = useRef<HTMLSpanElement>(null);
  const cancelRef = useRef<() => void>(() => {});
  useLayoutEffect(() => () => cancelRef.current(), []);
  const previous = useRef<Screen | null>(null);
  useLayoutEffect(() => {
    const node = ref.current;
    const nav = node?.parentElement;
    if (!node || !nav) return;

    const place = (animate: boolean) => {
      const target = nav.querySelector<HTMLElement>('[aria-current="page"]');
      if (!target) return;
      const a = target.getBoundingClientRect(),
        b = nav.getBoundingClientRect();
      const horizontal = getComputedStyle(nav).flexDirection === "row";
      const from = getComputedStyle(node);
      const frame = { transform: from.transform, width: from.width, height: from.height };

      const end = {
        transform: `translate(${horizontal ? a.x - b.x + 12 : -10}px, ${horizontal ? -5 : a.y - b.y + 15}px)`,
        width: `${horizontal ? Math.max(3, a.width - 24) : 3}px`,
        height: `${horizontal ? 3 : Math.max(3, a.height - 30)}px`,
      };
      if (
        !animate &&
        node.style.transform === end.transform &&
        node.style.width === end.width &&
        node.style.height === end.height
      )
        return;
      cancelRef.current();
      Object.assign(node.style, end, { opacity: "1" });
      if (animate)
        cancelRef.current = animateSpatial(node, [frame, end], 360, "cubic-bezier(.2,.75,.2,1)");
    };
    place(previous.current !== null && previous.current !== screen);
    previous.current = screen;
    const observer = new ResizeObserver(() => place(false));
    observer.observe(nav);
    for (const button of nav.querySelectorAll("button")) observer.observe(button);
    return () => {
      observer.disconnect();
    };
  }, [screen]);
  return <span ref={ref} className="hp-rail-track" aria-hidden="true" />;
}
