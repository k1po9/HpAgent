# UI-7 自检记录

> 2026-10-08（Asia/Shanghai）。状态：3 个 P2 均已修复；修复批次 405 项单元、74 项 Chromium 及完整前端检查通过，见[修复记录](ui-7-self-review-fixes.md)。以下原自检结论、失败与测试次数保留为该独立批次历史。
> 审查对象：[UI-7 实施报告](ui-7-unification-cleanup-report.md)及当前未提交实现。
> HEAD 为 `e6791e35990ce6da811f6204806def202e770076`；该 HEAD 是 UI-6，不能单独表示本次被审代码。

## 结论与范围

检查了正式入口/旧壳删除、Surface 层级与焦点、Run 查询响应归属、Trace 状态拆分、Tabs/路由、响应式/Motion 接线及报告证据。独立复跑 12 个文件、87 项已有定向回归全部通过；新增 3 个反例全部失败，并用当前前端的 Chromium 操作确认相应用户行为/网络请求。

| 问题                                        | 等级 | 来源                                  | 当前状态       |
| ------------------------------------------- | ---- | ------------------------------------- | -------------- |
| R1：迟到 Run 查询关闭后来打开的账户弹窗     | P2   | UI-7 新增 RunLookup 的导航保护遗漏    | 已修复，复验通过 |
| R2：模态焦点圈定跳过末尾原生 summary        | P2   | 原 Surface 已存在，本轮统一交互仍保留 | 已修复，复验通过 |
| R3：未打开诊断的普通聊天终态触发 Trace 查询 | P2   | UI-7 删除 Trace open 守卫后引入       | 已修复，复验通过 |

本轮未发现可确认的 P0/P1；此结论仅限上述审查范围，不代表完整产品验收。原报告中 typecheck/lint/build、391 项单元、62 项 Chromium 属于原实施批次，本次没有重跑这些全量检查，不能作为本次自检结果。真机软键盘、完整读屏、200% 文本全流程及全站对比度等原未覆盖项继续保留。

自检未修改业务代码或已有测试。临时反例运行后移出 `web/src`，以 `.txt` 保留于证据目录，避免让正常测试集新增预期失败项。本次仅运行本地 Vite 和浏览器响应 fixture，没有启动真实后端、访问测试/业务数据库或发送业务变更请求；Vite 5288 已停止。

## R1 · P2：迟到的 Run 查询会关闭后来打开的账户弹窗

**位置：** [RunLookup.tsx](../../web/src/components/run/RunLookup.tsx#L33) 的 `current()` 和成功后 `openInspector`；[shell.ts](../../web/src/store/shell.ts#L217) 的导航收尾清除 `modal`。

**复现：**

1. 在 AI Header 展开“按执行编号查询”，提交一个合法、可访问的 Run 查询，延迟 GET 响应。
2. 请求仍在途时打开“账户设置”，当前前景已切换为账户弹窗。
3. 原 GET 返回成功，RunLookup 调用 `openInspector`；其内部 `navigate` 把 `modal` 设为 null。
4. Chromium 实测账户弹窗被关闭，改为显示执行详情。单元反例期望 `modal="account"`，实际为 null。

**根因与影响：** 当前保护检查组件仍挂载、表单请求 token、Shell requestToken 和账户引用。打开账户/资料等弹窗使用 `useShell.setState({ modal: ... })`，不会增加 requestToken；AI Header 的 RunLookup 也仍挂载，因此迟到结果被当作当前导航意图。查询结果会打断用户已开始的账户操作；单纯页面切换/A→B→A 的已有测试无法覆盖这一情况。

**修复建议：** 为异步导航记录发起时的交互/前景层世代，前景弹窗变化使其自动打开意图失效，或只提供“查询完成，查看结果”的显式入口。允许原 GET 成功完成，但不能为展示它关闭后来打开的 modal。不要用查询完成后增加全局 requestToken 的方式使其它合法领域请求失效，也不要重新引入领域 store 导航 bridge。

**复验门槛：** R1 反例通过；覆盖账户、资料、Sidebar、父对象内新弹窗与在途查询组合；新前景不被关闭/抢走，正常无后续交互时仍可打开 Run；现有同 ID 重查、A→B→A、403/404 和账户隔离保护保留。对应 A7-04/A7-08/A7-10。

## R2 · P2：焦点圈定使通知详情无法通过 Tab 到达

**位置：** [Surface.tsx](../../web/src/components/shell/Surface.tsx#L144) 的可聚焦控件枚举与首尾回绕；实际受影响入口见 [TaskInbox.tsx](../../web/src/components/tasks/TaskInbox.tsx#L134)。

**复现：**

1. 打开移动端账户收件箱，加载一条没有 work_id/run_id 的普通通知，分页未满。
2. 对话框内容末尾是原生 `<details><summary>通知详情</summary>…</details>`，其前面最后一个 button 是“刷新收件箱”。
3. 从标题连续按 Tab，Chromium 的焦点反复在“关闭”和“刷新收件箱”之间切换，始终不到“通知详情”。
4. 单元反例也确认在刷新按钮上按 Tab 被 `preventDefault()`，提前回到首个按钮。

**根因与影响：** Surface 枚举 button/input/select/textarea/a/[tabindex="0"]，漏掉默认可聚焦的 summary 等原生元素。于是错误地把刷新按钮认作最后一个控件，在浏览器能把焦点移到 summary 前截断正常 Tab 顺序。用户无法用常规键盘导航展开通知详情；Task 高级页末尾“当前完整要求”也具有相同结构风险。

`git show HEAD:web/src/components/shell/Surface.tsx` 可确认原实现已有同样的 selector 遗漏。本项不是 UI-7 新增回归，但 UI-7 的键盘统一和验收仍需解决，不能因旧问题就标记为通过。

**修复建议：** 优先利用 native dialog 的浏览器焦点圈定，减少与其重复的手动首尾回绕；若仍需自定义，使用完整且正确过滤 hidden/inert/disabled 的可 Tab 元素集合，包含原生 summary、有效 tabindex、iframe 等实际语义。不能只给某个通知 summary 加 tabindex 来规避公共 primitive 的缺陷。

**复验门槛：** R2 反例和真实 Chromium Tab/Shift+Tab 路径通过；所有可操作 summary 可达且 Enter/Space 可展开；末尾/开头 summary、闭合 details 中隐藏控件、iframe、disabled fieldset、父子 modal 和桌面非模态分别验证。保留 Esc/焦点返回且不允许 Tab 进入背景。对应 A7-09/A7-19/A7-20。

## R3 · P2：未进入诊断也会在普通 Chat 完成时加载 Trace

**位置：** [workbench.ts](../../web/src/store/workbench.ts#L457) 的终态回调；[traceStore.ts](../../web/src/components/trace/traceStore.ts#L89) 的显式诊断选择与 followRun。

**复现：**

1. 恢复一个 running Chat Run，不打开任何 Inspector 或高级诊断。
2. `startRunMonitor` 的 `followRun(runId)` 已将直播来源写入 Trace store 的 runId。
3. 正常 `run.succeeded` SSE 到达，终态回调仅检查 `trace.runId === runId`，随即调用 `loadTrace()`。
4. 单元反例观察到 loadTrace 调用 1 次；Chromium 在没有 Inspector 的普通聊天页面记录到额外 `GET /api/v1/runs/chat-run/trace` 1 次。

**根因与影响：** UI-7 删除 `trace.open` 后把原 `open && runId匹配` 简化为 `runId匹配`，但 followRun 的直播来源并不表示用户已请求诊断。结果每次符合该条件的普通聊天完成都可能下载完整 Trace，破坏诊断懒加载，引入无必要网络/解析/缓存开销。该请求不同于应保留的终态权威 Run GET；不能以同步最终消息的必要请求解释。

**修复建议：** 显式区分直播来源和已选择的诊断查询生命周期，例如保留独立的诊断目标/订阅有效标记，或由已挂载的 Run 高级诊断控制终态 Trace 刷新。只在选中当前 Run 的高级诊断有效时同步 Trace，保留历史 Run 选择锁、事件缓冲、generation 和权威终态消息确认；不要恢复 `open/setOpen` 导航 bridge。

**复验门槛：** R3 反例通过；未开诊断/概览页普通完成无 Trace GET；打开当前 Run 高级页时终态仍刷新，选中历史 Run 时当前 Chat 不加载错误 Trace；关页/退出/账户改变后面板请求失效，degraded 恢复与最后消息确认不回归。对应 A7-05/A7-21，保持 FE-B3。

## 独立证据与报告修正要求

证据入口：[ui-7/self-review](../../artifacts/product-acceptance/ui-7/self-review/README.md)。

| 本次检查             | 独立结果                                                                           |
| -------------------- | ---------------------------------------------------------------------------------- |
| 原 UI-7 归档证据哈希 | 148/148 存在且一致；证明归档完整性，不证明源码与原运行时完全同一                   |
| 自检前后前端文件记录 | 56 项一致，含删除状态；本次未改用户前端源码/已有测试                               |
| 已有定向 Vitest      | 12 文件、87 项通过，exit 0                                                         |
| 新增定向反例         | 1 文件、3 项失败，exit 1；失败分别对应 R1/R2/R3                                    |
| Chromium + 当前 Vite | 2 个记录脚本、3 个场景均观察到预期缺陷；脚本 exit 0 表示记录成功，不是应用通过验收 |
| Browser API/SSE      | 全部为响应 fixture / 合成终态事件，记录中请求均为 GET；无真实后端状态链验证        |

原报告的已运行通过次数不需抹去，但 A7-04 的迟到交互保护、A7-05 的诊断懒加载，以及 A7-09/A7-19/A7-21 涉及的键盘/资源行为存在上述已确认缺口，应以本次自检补充结论为准，不能仍据历史自动化笼统称完整满足。

修复时把 3 个反例移入正式回归，保留修复前失败和修复后通过记录，再运行受影响 RunLookup/Surface/App/Shell/Trace/Workbench/任务通知测试、完整前端检查及相应 Chromium 流程。刷新自检/实施报告与 A7 状态，记录新文件指纹及独立命令退出码；不把本次 87 项或原 391/62 项相加为修复批次结果。原独立自检批次未实施修复；随后执行的修复与独立验证见[修复记录](ui-7-self-review-fixes.md)，不改写以上原失败证据。

## 后续修复与最终复验

R1 补交互世代与 Shell 前景变化的迟到导航保护；R2 补共享可 Tab 元素及边界处理；R3 补明确选中诊断的终态刷新 action。原三个反例本轮修复前 3 失败、修复后同源 3 通过，已迁入 RunLookup/Surface/Workbench 正式回归。

修复后的完整 Vitest **54 文件 / 405 项通过**，typecheck / lint / build 均退出 0、Lint 无警告；**74 项 Chromium 全部通过**，含 12 个新的 fixture 修复场景和 62 个既有受影响场景。各历史批次不相加。64 项前端状态/指纹与相对自检基线的 14 项修复文件单独记录；原 148 项实施证据与 16 项独立自检证据均校验保持，见[修复证据](../../artifacts/product-acceptance/ui-7/self-review-fixes/README.md)。

对应 A7 记录已更新。真机/读屏/全产品文本缩放/完整色对与全量资源峰值范围继续部分覆盖，未据此声明完整阶段验收；未部署或创建 commit/PR。
