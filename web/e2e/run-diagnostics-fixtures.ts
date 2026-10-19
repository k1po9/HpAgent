import type { HpRunBudget, HpTraceTree, HpTraceEventNode } from "../src/api/types";
export const diagnosticBudget: HpRunBudget = {
  status: "closed",
  mode: "observe",
  policy_version: "web-token-v2",
  tokens: {
    input: { used: 10860, reserved: 0, limit: 40000 },
    output: { used: 1620, reserved: 0, limit: 12000 },
    total: { used: 12480, reserved: 0, limit: 50000 },
  },
  model_calls: { settled: 3, in_flight: 0, unmetered: 0, total_attempts: 3, limit: 20 },
  by_source: {
    provider: { input_tokens: 10860, output_tokens: 1620, total_tokens: 12480 },
    measured: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
    estimated: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
  },
  usage_state: "complete",
  usage_quality: "provider",
  has_estimates: false,
  model_total_tokens_used: 12480,
  model_total_tokens_limit: 50000,
  tool_calls_used: 2,
  tool_calls_limit: 20,
  bytes_scanned_used: 0,
  bytes_scanned_limit: 0,
};
function node(
  id: string,
  parent: string | null,
  name: string,
  type: string,
  ms: number,
  metadata = {},
  children: HpTraceEventNode[] = [],
): HpTraceEventNode {
  return {
    event: {
      trace_event_id: id,
      trace_run_id: "trace-run",
      parent_event_id: parent,
      event_type: type,
      name,
      status: "completed",
      started_at: "2026-10-19T02:36:57Z",
      ended_at: "2026-10-19T02:37:18Z",
      duration_ms: ms,
      metadata,
    },
    children,
  };
}
export const diagnosticTree: HpTraceTree = {
  run: {
    trace_run_id: "trace-run",
    run_id: "0199ed39-a674-7001-8000-000000000007",
    conversation_id: "diagnostic-chat",
    strategy: "react",
    status: "completed",
    started_at: "2026-10-19T02:36:57Z",
    ended_at: "2026-10-19T02:37:18Z",
    metadata: {},
  },
  roots: [
    node("root", null, "整理本周资料", "agent", 21000, {}, [
      node("plan", "root", "理解委托与规划", "llm", 3200, {
        model: "gpt-5.4",
        model_call_id: "call-1",
        fallback_attempt: "1",
        token_usage: {
          input_tokens: 2400,
          output_tokens: 420,
          total_tokens: 2820,
          usage_source: "provider",
        },
      }),
      node("tools", "root", "查阅工作区资料", "tool", 4200, {}, [
        node("files", "tools", "查找已授权的周报", "tool", 820),
        node("read", "tools", "读取并核对来源", "tool", 3380),
      ]),
      node("check", "root", "交叉检查与归纳", "llm", 5400, {
        model: "gpt-5.4",
        model_call_id: "call-2",
        fallback_attempt: "1",
        token_usage: {
          input_tokens: 3860,
          output_tokens: 520,
          total_tokens: 4380,
          usage_source: "provider",
        },
      }),
      node("reply", "root", "组织回复与交付", "llm", 8200, {
        model: "gpt-5.4",
        model_call_id: "call-3",
        fallback_attempt: "1",
        token_usage: {
          input_tokens: 4600,
          output_tokens: 680,
          total_tokens: 5280,
          usage_source: "provider",
        },
      }),
    ]),
  ],
};
