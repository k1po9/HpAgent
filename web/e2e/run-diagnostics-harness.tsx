// Explicit local fixtures rendered through the production shell and components.
import { createRoot } from "react-dom/client";
import { Theme } from "@radix-ui/themes";
import "@radix-ui/themes/styles.css";
import "../src/styles.css";
import { AppShell } from "../src/components/shell/AppShell";
import { runApi } from "../src/store/runInspector";
import { useWorkbench } from "../src/store/workbench";
import { diagnosticBudget, diagnosticTree } from "./run-diagnostics-fixtures";
import type { HpChatRunSnapshot } from "../src/api/types";
const query = new URLSearchParams(location.search);
const running = query.has("running");
const snapshot: HpChatRunSnapshot = {
  source_kind: "chat",
  run: {
    run_id: "0199ed39-a674-7001-8000-000000000007",
    conversation_id: "diagnostic-chat",
    session_id: null,
    trigger_message_id: "request",
    retry_of_run_id: null,
    agent_strategy: "react",
    status: running ? "running" : "succeeded",
    failure: null,
    version: 1,
    created_at: "2026-10-19T02:36:57Z",
    started_at: "2026-10-19T02:36:57Z",
    finished_at: running ? null : "2026-10-19T02:37:18Z",
    updated_at: "2026-10-19T02:37:18Z",
    budget: query.has("missing") ? null : structuredClone(diagnosticBudget),
  },
  assistant_message: {
    message_id: "reply",
    conversation_id: "diagnostic-chat",
    role: "assistant",
    status: "completed",
    content: "本周资料已整理完毕，执行过程可在高级诊断中查看。",
    sequence: 14,
    client_request_id: null,
    produced_by_run_id: "0199ed39-a674-7001-8000-000000000007",
    created_at: "2026-10-19T02:36:57Z",
    completed_at: "2026-10-19T02:37:18Z",
  },
};
Object.assign(snapshot.run, { run_ordinal: 7 });
if (snapshot.run.budget && query.has("limited")) {
  snapshot.run.budget.mode = "enforce";
  snapshot.run.budget.status = "exhausted";
  snapshot.run.budget.tokens.total.limit = 10000;
}
if (snapshot.run.budget && running) {
  snapshot.run.budget.usage_state = "in_flight";
  snapshot.run.budget.model_calls.in_flight = 1;
  snapshot.run.budget.tokens.total.reserved = 2400;
}
runApi.getRun = async () => structuredClone(snapshot);
useWorkbench.getState().loadConversations = async () => {};
// Intercept every fixture request locally, including all unrelated shell reads.
const originalFetch = window.fetch;
window.fetch = async (input, init) => {
  const url = String(input instanceof Request ? input.url : input);
  if (!url.includes("/api/v1/")) return originalFetch(input, init);
  const tree = structuredClone(diagnosticTree);
  if (running) {
    tree.run.status = "running";
    tree.run.ended_at = null;
    tree.roots[0]!.event.status = "running";
    tree.roots[0]!.children[2]!.event.status = "running";
    tree.roots[0]!.children[2]!.event.duration_ms = null;
  }
  if (query.has("stress")) {
    let parent = tree.roots[0]!.children[1]!;
    for (let i = 0; i < 10; i++) {
      const child = structuredClone(tree.roots[0]!.children[0]!);
      child.event.trace_event_id = `deep-${i}`;
      child.event.parent_event_id = parent.event.trace_event_id;
      child.event.name = `深层 ${i + 1} · 很长的资料检查名称用于验证窄面板不会把耗时挤出可见区域`;
      child.event.duration_ms = i === 9 ? null : 1300;
      parent.children.push(child);
      parent = child;
    }
    tree.roots[0]!.children[2]!.event.status = "failed";
    tree.roots[0]!.children[2]!.event.metadata.error_code = "provider_timeout";
    const other = structuredClone(tree.roots[0]!.children[0]!);
    other.event.trace_event_id = "parallel-root";
    other.event.parent_event_id = null;
    other.event.name = "另一条独立执行路径";
    tree.roots.push(other);
  }
  return Response.json(
    url.includes("/trace")
      ? query.has("empty")
        ? { run: tree.run, roots: [] }
        : tree
      : {
          items: [],
          conversations: [],
          works: [],
          candidates: [],
          files: [],
          approvals: [],
          visibility: "none",
        },
  );
};
createRoot(document.getElementById("root")!).render(
  <Theme>
    <AppShell />
  </Theme>,
);
