// Real shell and Inspector components with explicit, local browser fixture data. No Beta API.
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useShell } from "../src/store/shell";
import { createRoot } from "react-dom/client";
import { Theme } from "@radix-ui/themes";
import "@radix-ui/themes/styles.css";
import "../src/styles.css";
import { AppShell } from "../src/components/shell/AppShell";
import { runApi } from "../src/store/runInspector";
import { useWorkbench } from "../src/store/workbench";
import type { HpRunSnapshot } from "../src/api/types";
const snapshot: HpRunSnapshot = {
  source_kind: "work",
  run: {
    source_kind: "work",
    run_id: "frame-run",
    execution_id: "execution",
    work_id: "work",
    requirement_revision: 1,
    work_control_epoch: 1,
    conversation_id: null,
    session_id: null,
    status: "succeeded",
    version: 1,
    created_at: "2026-10-19T00:00:00Z",
    started_at: null,
    finished_at: "2026-10-19T00:00:02Z",
    updated_at: "2026-10-19T00:00:02Z",
    failure_code: null,
    failure_message: null,
    strategy_kind: "generic_agent",
    executor_key: "general",
    result_json: {
      summary: "已整理今日委托的执行记录。\n资料和输出保留在所属任务中，可继续查看执行过程。",
    },
    budget: null,
    branches: [],
  },
};
runApi.getRun = async () => {
  await new Promise((resolve) => setTimeout(resolve, 180));
  if (new URLSearchParams(location.search).has("failure")) throw new Error("fixture offline");
  return structuredClone(snapshot);
};
useWorkbench.getState().loadConversations = async () => {};
export function ArrivalTrigger() {
  const [host, setHost] = useState<Element | null>(null);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setHost(document.querySelector(".hp-main-canvas")));
    return () => cancelAnimationFrame(frame);
  }, []);
  return host && new URLSearchParams(location.search).has("arrival")
    ? createPortal(
        <button
          id="frame-arrival-source"
          style={{ position: "fixed", top: 220, left: 430 }}
          onClick={() =>
            useShell.getState().openInspector({
              kind: "run",
              objectId: "frame-run",
              origin: { triggerId: "frame-arrival-source" },
            })
          }
        >
          查看示例执行
        </button>,
        host,
      )
    : null;
}
createRoot(document.getElementById("root")!).render(
  <Theme>
    <AppShell />
    <ArrivalTrigger />
  </Theme>,
);
