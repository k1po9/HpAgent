import { useEffect, useState } from "react";
import { useWorks } from "../../store/works";
import { useShell } from "../../store/shell";
import { subscribeWork } from "../../sse/workFeed";
import { taskType, presentTask, compareTasks } from "./taskPresentation";
// Mounted exactly once by AppShell. Sidebar and Inspector are passive consumers.
export function useTaskController() {
  const now = useWorks((s) => s.now);
  const route = useShell((s) => s.route);
  const items = useWorks((s) => s.items);
  const generation = useWorks((s) => s.generation);
  const selected =
    route.inspector?.kind === "task" ? route.inspector.objectId : route.inspector?.origin?.workId;
  const enabled = route.screen === "tasks" || route.inspector?.kind === "task";
  const [visible, setVisible] = useState(!document.hidden);
  useEffect(() => {
    const update = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  useEffect(() => {
    if (!enabled || !visible) return;
    let valid = true;
    let timer: ReturnType<typeof setTimeout>;
    async function sync() {
      useWorks.setState({ now: Date.now() });
      if (!useWorks.getState().scanPaused) await useWorks.getState().load();
      if (valid) timer = setTimeout(() => void sync(), 10000);
    }
    void sync();
    return () => {
      valid = false;
      clearTimeout(timer);
      useWorks.getState().cancelScan();
    };
  }, [enabled, visible, generation]);
  const ids =
    enabled && visible
      ? Array.from(
          new Set([
            ...(selected ? [selected] : []),
            ...items
              .filter(
                (w) =>
                  (!route.type || route.type === "all" || taskType(w) === route.type) &&
                  presentTask(w, now).bucket === (route.bucket ?? "attention"),
              )
              .sort((a, b) => compareTasks(a, b, now))
              .map((w) => w.work_id),
          ]),
        )
          .filter(
            (id) =>
              !["stopped", "completed"].includes(items.find((w) => w.work_id === id)?.status ?? ""),
          )
          .slice(0, 2)
          .join(",")
      : "";
  useEffect(() => {
    const stops = ids
      ? ids.split(",").map((id) =>
          subscribeWork(
            id,
            () => useWorks.getState().cursors[id] ?? 0,
            (e) => useWorks.getState().receive(e),
            () => useWorks.getState().refresh(id),
          ),
        )
      : [];
    return () => stops.forEach((stop) => stop());
  }, [ids, generation]);
}
