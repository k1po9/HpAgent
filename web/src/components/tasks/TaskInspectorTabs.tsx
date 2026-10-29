import { TabsIndicator, TabsList, TabsRoot, TabsTab } from "@lobehub/ui";
import { useShell, type Inspector } from "../../store/shell";

/** Tabs own selection/keyboard only; TaskPanel owns visited content and drafts. */
export function TaskInspectorTabs({
  tabs,
  selected,
}: {
  tabs: readonly { id: NonNullable<Inspector["tab"]>; label: string }[];
  selected: Inspector["tab"];
}) {
  return (
    <TabsRoot
      className="hp-task-tabs"
      value={selected}
      variant="square"
      onValueChange={(value) => {
        const tab = tabs.find((item) => item.id === value);
        if (tab) useShell.getState().setInspectorTab(tab.id);
      }}
    >
      <TabsList aria-label="任务信息" variant="square">
        {tabs.map((tab, index) => (
          <TabsTab
            key={tab.id}
            value={tab.id}
            variant="square"
            id={`task-tab-${tab.id}`}
            aria-controls={`task-panel-${tab.id}`}
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
              event.stopPropagation();
              const target = tabs[next]!;
              useShell.getState().setInspectorTab(target.id);
              document.getElementById(`task-tab-${target.id}`)?.focus();
            }}
          >
            {tab.label}
          </TabsTab>
        ))}
        <TabsIndicator variant="square" />
      </TabsList>
    </TabsRoot>
  );
}
