/**
 * Run progress phase labels (contract §12.4 phase enum).
 *
 * Kept in a plain module (not alongside a component) so the run-status strip and
 * the mock server can share them without tripping Fast Refresh's
 * only-export-components rule.
 */

/** Stable Chinese labels for the contract phase enum; unknown → generic. */
const PHASE_LABELS: Record<string, string> = {
  assembling_context: "正在加载上下文",
  recalling_memory: "正在召回记忆",
  calling_model: "正在等待模型生成",
  selecting_tools: "正在选择工具",
  executing_tool: "正在执行工具",
  finalizing: "正在完成处理",
};

export function phaseLabel(phase: string): string {
  return PHASE_LABELS[phase] ?? "正在处理";
}
