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
  return (
    <div role="tablist" aria-label={label} className="hp-inspector-tabs">
      {tabs.map((tab, index) => (
        <button
          key={tab.id}
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
