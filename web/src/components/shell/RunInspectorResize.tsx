import { useEffect, useRef, useState } from "react";
import { useShell } from "../../store/shell";

import { measureShell, runWidthBounds } from "./layout";
export function RunInspectorResize() {
  const width = useShell((s) => s.runInspectorWidth);
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: number; x: number; width: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [bounds, setBounds] = useState(() => runWidthBounds(measureShell(null)));
  useEffect(() => {
    const shell = document.querySelector<HTMLElement>(".hp-shell");
    const update = () => setBounds(runWidthBounds(measureShell(shell)));
    update();
    const observer = new ResizeObserver(update);
    if (shell) observer.observe(shell);
    window.addEventListener("resize", update);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", update);
    };
  }, []);
  useEffect(() => {
    if (!dragging) return;
    document.body.classList.add("hp-run-resizing");
    return () => {
      document.body.classList.remove("hp-run-resizing");
    };
  }, [dragging]);
  const current = Math.min(bounds.max, Math.max(bounds.min, width));
  const setWidth = (next: number) =>
    useShell.setState({
      runInspectorWidth: Math.round(Math.min(bounds.max, Math.max(bounds.min, next))),
    });
  const finish = () => {
    drag.current = null;
    setDragging(false);
  };
  return (
    <div
      ref={ref}
      className="hp-run-resize"
      role="separator"
      aria-label="调整执行详情宽度"
      aria-orientation="vertical"
      aria-valuemin={bounds.min}
      aria-valuemax={bounds.max}
      aria-valuenow={current}
      aria-valuetext={`${current} 像素`}
      tabIndex={0}
      title="拖动调整宽度；左右方向键微调，Home / End 到达边界"
      data-dragging={dragging}
      onPointerDown={(event) => {
        if (event.button !== 0 || !event.isPrimary) return;
        event.preventDefault();
        event.currentTarget.focus({ preventScroll: true });
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { id: event.pointerId, x: event.clientX, width: current };
        setDragging(true);
      }}
      onPointerMove={(event) => {
        const start = drag.current;
        if (start?.id === event.pointerId) setWidth(start.width + start.x - event.clientX);
      }}
      onPointerUp={finish}
      onPointerCancel={finish}
      onLostPointerCapture={finish}
      onKeyDown={(event) => {
        const next =
          event.key === "ArrowLeft"
            ? current + (event.shiftKey ? 48 : 16)
            : event.key === "ArrowRight"
              ? current - (event.shiftKey ? 48 : 16)
              : event.key === "Home"
                ? bounds.min
                : event.key === "End"
                  ? bounds.max
                  : null;
        if (next === null) return;
        event.preventDefault();
        setWidth(next);
      }}
    />
  );
}
