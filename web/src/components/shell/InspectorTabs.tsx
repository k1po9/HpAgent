import styles from "./InspectorTabs.module.css";
import { useLayoutEffect, useRef } from "react";
import { useMotionAllowed } from "../motion/preference";
import type { Inspector } from "../../store/shell";
import { useShell } from "../../store/shell";
export function InspectorTabs({
  prefix,
  label,
  tabs,
  selected,
  panelId,
}: {
  prefix: string;
  label: string;
  tabs: readonly { id: NonNullable<Inspector["tab"]>; label: string }[];
  selected: Inspector["tab"];
  panelId?: string;
}) {
  const root = useRef<HTMLDivElement>(null),
    indicator = useRef<HTMLSpanElement>(null);
  const motion = useMotionAllowed();
  useLayoutEffect(() => {
    const node = root.current,
      line = indicator.current;
    if (!node || !line) return;
    const measure = () => {
      const button = node.querySelector<HTMLElement>('[aria-selected="true"]');
      if (!button) return;
      line.style.width = `${button.offsetWidth}px`;
      line.style.transform = `translate(${button.offsetLeft}px, ${button.offsetTop + button.offsetHeight - 2}px)`;
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [selected]);
  return (
    <div
      ref={root}
      role="tablist"
      aria-label={label}
      className={`hp-inspector-tabs ${styles.tabs}`}
      data-tab-kind={prefix}
      data-tab-motion={motion ? "enabled" : "reduced"}
    >
      <span
        ref={indicator}
        className={`hp-inspector-tab-indicator ${styles.indicator}`}
        aria-hidden="true"
      />
      {tabs.map((tab, index) => (
        <button
          key={tab.id}
          type="button"
          className={styles.tab}
          role="tab"
          id={`${prefix}-tab-${tab.id}`}
          aria-selected={selected === tab.id}
          aria-controls={panelId ?? `${prefix}-panel-${tab.id}`}
          tabIndex={selected === tab.id ? 0 : -1}
          onClick={() => useShell.getState().setInspectorTab(tab.id)}
          onKeyDown={(event) => {
            const next =
              event.key === "ArrowRight"
                ? (index + 1) % tabs.length
                : event.key === "ArrowLeft"
                  ? (index + tabs.length - 1) % tabs.length
                  : event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? tabs.length - 1
                      : -1;
            if (next < 0) return;
            event.preventDefault();
            useShell.getState().setInspectorTab(tabs[next]!.id);
            document.getElementById(`${prefix}-tab-${tabs[next]!.id}`)?.focus();
          }}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
