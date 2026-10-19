// Explicit local fixtures around the real AppShell and Inspector. Never connects to Beta.
import { createRoot } from "react-dom/client";
import { Theme } from "@radix-ui/themes";
import "@radix-ui/themes/styles.css";
import "../src/styles.css";
import { AppShell } from "../src/components/shell/AppShell";
import { runApi, useRunInspector } from "../src/store/runInspector";
import { useWorkbench } from "../src/store/workbench";
import type { HpChatRunSnapshot, HpRunStatus } from "../src/api/types";
const query = new URLSearchParams(location.search);
const status = (query.get("status") ?? "succeeded") as HpRunStatus;
const terminal = ["succeeded", "failed", "cancelled"].includes(status);
const now = Date.now();
const reply =
  "已整理好本次委托的资料。\n\n**本周重点**\n1. 产品概览与执行入口已核对。\n2. 相关资料保留在「使用资料与输出」。\n\n下一步可直接查看资料，决定是否授权后续操作。";
const snapshot: HpChatRunSnapshot = {
  source_kind: "chat",
  run: {
    run_id: "overview-run",
    conversation_id: "overview-chat",
    session_id: null,
    trigger_message_id: "user-message",
    retry_of_run_id: null,
    agent_strategy: "react",
    status,
    failure:
      status === "failed"
        ? {
            code: "provider_error",
            message: "模型服务暂时不可用，本次执行未能完成。",
            retryable: true,
          }
        : null,
    version: 1,
    created_at: terminal ? "2026-10-18T15:59:40Z" : new Date(now - 95000).toISOString(),
    started_at:
      status === "queued" || query.has("missing")
        ? null
        : terminal
          ? "2026-10-18T15:59:43Z"
          : new Date(now - 92000).toISOString(),
    finished_at: terminal ? "2026-10-18T16:01:12Z" : null,
    updated_at: new Date(now).toISOString(),
    budget: null,
  },
  assistant_message: {
    message_id: "overview-message",
    conversation_id: "overview-chat",
    role: "assistant",
    status: terminal ? "completed" : "pending",
    content:
      status === "succeeded"
        ? query.has("long")
          ? Array(12).fill(reply).join("\n\n")
          : reply
        : status === "running"
          ? "正在核对你交代的资料，已找到本周相关记录。"
          : null,
    sequence: 2,
    client_request_id: null,
    produced_by_run_id: "overview-run",
    created_at: "2026-10-18T15:59:40Z",
    completed_at: terminal ? "2026-10-18T16:01:12Z" : null,
  },
};
runApi.getRun = async () => structuredClone(snapshot);
useWorkbench.getState().loadConversations = async () => {};
if (query.has("live")) {
  useWorkbench.setState({
    activeRun: snapshot.run,
    messages: [snapshot.assistant_message],
    pollGeneration: 1,
    activeRunProgress:
      status === "running"
        ? {
            phase: "executing_tool",
            summary: "正在读取本周资料并核对来源",
            scope: { runId: "overview-run", generation: 1, receivedAt: Date.now() },
          }
        : null,
  });
}
// Only fixture controls set a pre-verified approval fact; production remains lazy.
if (query.has("approval")) {
  useRunInspector.subscribe((state) => {
    if (state.runId !== "overview-run" || state.approvalLoaded) return;
    useRunInspector.setState({
      approvalLoaded: true,
      approvals:
        query.get("approval") === "pending"
          ? [
              {
                approval_id: "demo",
                run_id: "overview-run",
                operation_id: "demo",
                action_summary: "更新资料",
                tool_name: "write",
                status: "pending",
              },
            ]
          : [],
    });
  });
}
createRoot(document.getElementById("root")!).render(
  <Theme>
    <AppShell />
  </Theme>,
);
