import { useLayoutEffect, useRef, type RefObject } from "react";
import { animateSpatial } from "./preference";

export function useSpatialFeedback(
  ref: RefObject<HTMLElement | null>,
  identity: string | null,
  select: (root: HTMLElement) => HTMLElement | null,
  kind: "seat" | "region",
) {
  const previous = useRef(identity);
  const selectRef = useRef(select);
  useLayoutEffect(() => {
    selectRef.current = select;
  }, [select]);
  useLayoutEffect(() => {
    const changed = previous.current !== identity;
    previous.current = identity;
    if (!changed || !identity || !ref.current) return;
    const node = selectRef.current(ref.current);
    if (!node) return;
    // Never replace or reorder the real node, or animate a rejected navigation.
    return animateSpatial(
      node,
      kind === "seat"
        ? [
            { transform: "translateX(2px)", boxShadow: "inset 0 1px 0 rgb(255 255 255 / 70%)" },
            { transform: "none", boxShadow: "none" },
          ]
        : [
            { opacity: 0.72, transform: "translateY(6px)" },
            { opacity: 1, transform: "none" },
          ],
      kind === "seat" ? 220 : 300,
    );
  }, [ref, identity, kind]);
}
