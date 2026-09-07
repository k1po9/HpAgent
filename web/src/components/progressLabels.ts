/**
 * Run progress phase labels (contract §12.4 phase enum).
 *
 * Kept in a plain module (not alongside a component) so the run-status strip and
 * the mock server can share them without tripping Fast Refresh's
 * only-export-components rule.
 */

/** Stable Chinese labels for the contract phase enum; unknown → generic. */
const PHASE_LABELS: Record<string, string> = {
  starting: "正在启动任务",
  assembling_context: "正在加载上下文",
  recalling_memory: "正在召回记忆",
  calling_model: "正在等待模型生成",
  selecting_tools: "正在选择工具",
  executing_tool: "正在执行工具",
  finalizing: "正在完成处理",
  generating: "正在生成结果",
  planning: "正在制定计划",
  plan_ready: "计划已生成",
  executing_step: "正在执行计划步骤",
  evaluating_step: "正在评估步骤结果",
  replanning: "正在调整计划",
  synthesizing: "正在汇总结果",
  waiting_for_account_execution: "正在等待当前账户的其他任务完成",
};

export function phaseLabel(phase: string): string {
  return PHASE_LABELS[phase] ?? "正在处理";
}
