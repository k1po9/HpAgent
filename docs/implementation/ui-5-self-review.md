# UI-5 任务中心自检记录

日期：2026-10-08（Asia/Shanghai）。对象：[UI-5 实施报告](ui-5-task-center-report.md)、[实施计划](ui-5-task-center-plan.md)及当前工作区实现。原自检开始时 HEAD 为 `be57e0bd22623974ce57498aed78c086fc5f6673`，UI-5 实现尚未提交；后续修复与提交范围见末尾记录。

**结论：3 个已复现的 P2 问题已完成修复。** 下文保留原自检发现与失败证据，修复实现及本批独立验证见末尾“修复与复验”。原实施阶段的通过数量保留为历史记录，不与本批累计。

## 原自检复用与验证范围

复用前一窗口保留的 [3 项反例源码](../../artifacts/product-acceptance/ui-5/self-review/UI5SelfReview.repro.test.tsx.txt)，对照当前代码核实根因，并重新运行保存日志。没有找到前一窗口这轮自检的完整报告与运行日志，因此以下数量是本轮独立测量，不与此前结果累计。

| 验证 | 本轮结果 | 证据 |
| --- | --- | --- |
| 定向已有回归：六个 tasks 测试文件，以及 works、shell、sessionIsolation | 9 文件、80 项通过；28.88 秒，测试命令 exit 0 | [regression.log](../../artifacts/product-acceptance/ui-5/self-review/regression.log) |
| R1–R3 反例：断言正确行为 | 1 文件、3 项失败；12.65 秒，测试命令 exit 1 | [repro.log](../../artifacts/product-acceptance/ui-5/self-review/repro.log) |

测试使用 jsdom 和 API stub，证明前端渲染、查询缓存和表单生命周期行为；不代表真实模型、渠道或 Temporal 验证。本轮未重跑完整 Vitest、类型检查、lint、构建、浏览器和后端契约；这些历史结果仍见原报告。

## R1 · P2 · 执行完成后，已缓存的空输出列表不会重新读取

位置：[TaskOutputs.tsx](../../web/src/components/tasks/TaskOutputs.tsx) 的 `PublishedOutput`，以及 [useTaskQuery.ts](../../web/src/components/tasks/useTaskQuery.ts) 的缓存命中分支。

复现：在 Run 执行中打开“读取报告与输出文件”，接口返回空列表；随后同一 Run 成功且产生文件，Work 的 row_version 增长；再次打开输出。执行记录已显示“执行成功”，文件区仍显示“暂无已发布文件”。R1 断言 published-files 应读取两次，实际只有一次。

原因：执行记录 key 包含 Work row_version，输出文件 key 却只有 work_id/run_id；查询层对已有成功结果直接返回，空数组也是成功结果。收起、重开或重新挂载不会主动失效该缓存；成功空态也没有刷新按钮。用户可能无法发现、下载或保存刚生成的文件。

修复建议：为可变化的输出建立明确失效条件，至少覆盖同一 Run 从执行中进入终态，并提供主动刷新路径；避免仅靠重挂载或清理所有账户缓存。报告缓存也应核查同类生命周期，但本轮直接复现的是 published-files。

复验门槛：R1 通过；补充输出保持展开、收起后重开、同 Run 终态变化、独立报告/文件失败恢复，以及账户切换迟到响应用例。不能只把空列表伪装成失败或要求用户整页重载。

## R2 · P2 · 缺少 continuation 的任务打开成果页会崩溃

位置：[TaskOutputs.tsx](../../web/src/components/tasks/TaskOutputs.tsx) 的 `work.continuation.operation_ref`；入口为 [TaskInspector.tsx](../../web/src/components/tasks/TaskInspector.tsx) 的 outputs 页签。

复现：向 Work Store 放入缺少 continuation 的 M21 异常快照，通过成果页深链或页签打开 Inspector。R2 渲染抛出 `TypeError: Cannot read properties of undefined (reading 'operation_ref')`，不能完成只读查看。

原因：投影与动作资格已把缺 continuation 识别为“状态待核实”，但 Inspector 仍可进入成果页，其组件直接解引用 continuation。这与 M21“仅查看/刷新”及报告中的异常快照降级说明不一致。现有纯函数 M21 通过不能证明完整页签可渲染。

修复建议：在成果页安全处理缺失字段，或在 Inspector 对无效快照提供统一只读降级与刷新入口；不能仅隐藏列表按钮，因为深链仍可进入。此反例只证明 continuation 缺失，不推断所有异常字段都有同一问题。

复验门槛：R2 通过；覆盖 undefined/null continuation、成果页直接深链与页签切换；不抛异常，提示状态待核实，可刷新恢复，且不开放验收、重发等变更动作。

## R3 · P2 · 重试读取任务要求会丢失编辑草稿

位置：[TaskEditor.tsx](../../web/src/components/tasks/TaskEditor.tsx) 的 `!query.loading` 条件及 `EditorForm` 内部字段 state。

复现：先打开再关闭编辑器以建立要求缓存；再次打开时基线刷新遭遇临时网络失败，保留缓存表单；修改“目标 / 提醒内容”和“修订原因”，点击“重试要求”。请求成功后目标恢复服务器旧值，修订原因清空。R3 的目标保留断言失败，日志显示实际值回到原始目标；原因清空也可由日志 DOM 及重新初始化逻辑核实。

原因：retry 将 loading 置为 true，外层条件导致 `EditorForm` 卸载。草稿存于表单局部 state，加载完成重新挂载后从服务器要求初始化。此路径不同于已有 409 冲突测试，因此既有“冲突保留草稿”通过没有覆盖它。

修复建议：已有表单重试基线读取时保持草稿存活，或将草稿提升到绑定对象和账户的所有者；服务器新基线与草稿分别维护，明确审阅后再采用新版本。仍须保留 403/404 清理、跨账户隔离和关闭确认行为。

复验门槛：R3 通过；目标、约束、时间与修订原因在成功/失败重试中保持；刷新获得新 revision 时不会静默替换用户输入；切换对象或账户不泄漏旧草稿；既有 409 与未知结果幂等重试测试继续通过。

## 原反例复现方法（修复前基线）

仓库根目录执行以下命令。反例以 `.txt` 保存，不进入常规测试发现；临时复制的文件在命令结束后清理。执行前确认该临时路径没有其他人的文件。

```bash
cp artifacts/product-acceptance/ui-5/self-review/UI5SelfReview.repro.test.tsx.txt web/src/components/tasks/UI5SelfReview.repro.test.tsx
cd web
npm test -- src/components/tasks/UI5SelfReview.repro.test.tsx --maxWorkers=1
# 当前预期：3 failed，exit 1。记录退出码后清理临时副本。
rm src/components/tasks/UI5SelfReview.repro.test.tsx
```

已有回归在 `web` 目录运行：

```bash
npm test -- src/components/tasks/TaskIntegration.test.tsx src/components/tasks/taskOperations.test.ts src/components/tasks/taskPresentation.test.ts src/components/tasks/taskRequirement.test.ts src/components/tasks/useTaskController.test.tsx src/components/tasks/useTaskQuery.test.tsx src/store/works.test.ts src/store/shell.test.ts src/store/sessionIsolation.test.ts --maxWorkers=1 --testTimeout=15000
```

反例现已转为正式 [TaskSelfReview.test.tsx](../../web/src/components/tasks/TaskSelfReview.test.tsx)。原 `.txt` 保留修复前源码；R1 正式用例将“再次点击读取”调整为先确认输出仍展开，再收起/重开，并保持“终态后重新读取两次且新文件可见”的断言，同时新增收起期间终态变化用例。旧 `.txt` 的打开方式不作为修复后验收命令。


## 修复与复验（2026-10-08）

本次用户明确要求修复并提交。开始时 UI-5 实现、计划、报告、证据及本记录尚未提交，基线仍为 `be57e0b`；本批提交包含该 UI-5 实现及 R1–R3 修复，不改后端契约或数据库迁移。完整证据见 [修复证据入口](../../artifacts/product-acceptance/ui-5/self-review/README.md)。

| 问题 | 修复方式 | 正式回归 |
| --- | --- | --- |
| R1 | Query 使用稳定对象 key 和可选 revision；新 revision 取消/拒绝旧在途读取，保留同对象已读数据。Run 列表按 Work row_version 重查且保持子组件挂载；报告/文件按 Run status 失效。提供分别刷新报告/文件的入口，包括成功空态。 | 展开状态终态刷新、收起后重开、缓存报告更新、空态主动刷新、报告/文件独立失败恢复、旧状态及旧账户迟到响应。 |
| R2 | 成果页安全访问 continuation；Inspector 全页签提供状态待核实和刷新入口。异常快照的资料管理及通知目标变更关闭，接受和投递决策沿用 validSnapshot 限制。 | undefined/null、成果页深链、概览/成果/资料页签切换、不开放验收/投递动作、刷新有效快照后恢复。 |
| R3 | 已有缓存表单不因读取 loading 卸载，核实期间禁止提交。读取返回要求与本地草稿分别维护，审阅后才采用新基线；表单按 Work/账户世代绑定，403/404 清理草稿。 | 目标/约束/时间/修订原因在成功及失败重试、加载中保持；新 revision 明确审阅再提交；关闭确认、对象/账户切换、迟到响应、403/404 清理。原 409、未知响应 key/If-Match 恢复继续回归。 |

正式测试命令：

```bash
cd web
npm test -- src/components/tasks/TaskSelfReview.test.tsx src/components/tasks/TaskIntegration.test.tsx src/components/tasks/useTaskQuery.test.tsx --maxWorkers=1 --testTimeout=15000
npm run typecheck
npm run lint
npm run build
npm test -- --maxWorkers=1 --testTimeout=15000
npm run test:e2e -- e2e/ui-5-recovery.spec.ts e2e/ui-5-tasks.spec.ts e2e/manual-repair.spec.ts e2e/artifact.spec.ts e2e/a11y.spec.ts
```

修复前正式副本的三项反例均失败，见 [before-fixes.log](../../artifacts/product-acceptance/ui-5/self-review/fix-logs/before-fixes.log)。修复后首批定向 3 文件、26 项通过（20.61 秒），见 [targeted-initial.log](../../artifacts/product-acceptance/ui-5/self-review/fix-logs/targeted-initial.log)。最终完整前端门禁为 43 文件、329 项通过（189.74 秒），TypeScript、lint 和构建通过；五个 spec 的 Chromium 15 项全部通过（4.9 分钟），外层命令均 exit 0。详细日志见修复证据页；没有使用历史结果替代本批复验。
