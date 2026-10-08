# UI-7 统一与清理实施报告

> 自检补充（2026-10-08）：发现 R1/R2/R3 三个 P2，均经定向反例与 Chromium 复现，当前待修复；见 [UI-7 自检记录](ui-7-self-review.md)。下文自动化计数保留原实施批次含义，相关 A7 交互保护、键盘及诊断懒加载结论需修复后独立复验。

> 日期：2026-10-08（Asia/Shanghai）。基线 HEAD：`e6791e35990ce6da811f6204806def202e770076`。
> 状态：前端代码实施及自动化复验完成；人工环境项部分覆盖，不声明完整 UI-7 验收或 WCAG 合规认证。
> 后续独立自检的 R1/R2/R3 已修复；当前修复批次 405 项单元、74 项 Chromium 与完整前端检查通过，见[自检修复](ui-7-self-review-fixes.md)。下文原实施通过次数保留为历史，不与修复批次相加。
> 执行依据：[UI-7 实施计划](ui-7-unification-cleanup-plan.md) 与原设计附件（SHA-256 `debffa18de54203fa2e7037108b9f0b3f736e6b350516a9c96fd87590217f892`）。附件旧源码基线及“本次只生成方案”的写作背景不替代本轮实施请求。

## 实施结果

完成 U7-0～U7-6 的前端实施，并按 U7-7 记录自动化和人工覆盖边界。三入口、React/Zustand/Radix/assistant-ui/hash 导航保持；未修改后端、持久化模型、Run/Work 状态机、SSE、授权/版本/验收协议，也未新增框架、数据库迁移、第四入口或调度能力。D01/D02/D03/D07/D08/D09/D10 维持冻结。

- **正式入口与旧壳清理**：提取 ArtifactCreateForm，保留已完成非空回复来源、初始生成要求、原幂等恢复、busy 单提交与迟到导航保护；AI Header 和 Task 高级详情补 RunLookup，先核实 Chat/Work snapshot，保持就地错误与输入。删除 TestPages、ArtifactPanel、WorkPanel、WorkManagement、ResearchOutputs、WorkspacePanel、LegacyUtilities；有效能力与八项 Workspace 旧测试语义见[迁移映射](../../artifacts/product-acceptance/ui-7/migration-map.md)。
- **面板权威**：artifacts store 删除 openArtifact/onOpen/Shell import；Trace 删除 open/setOpen/onOpen/Shell import，显式诊断选择与聊天 followRun 分离。RunTraceContent 保留 TraceTree/Detail/Model Input 与原 generation、事件缓冲和权限清理。旧 hash 兼容保留，旧 diagnostics 不从直播缓存猜对象。FE-B3 已有及新增竞态回归保留。
- **稳定 Host 与前景层**：Surface 使用稳定 dialog 元素，desktop region/modal 模式只改变表现；保存、收件箱、侧栏、权限/任务确认与 QQ 引导共享层级，暂挂父层但不卸载内容。嵌套 Surface 用稳定 Portal 脱离父层 hidden/inert DOM，并继承 Radix Theme；层级顺序按父子关系及打开次序协调。取消/Tab/Escape/焦点返回不依赖动画结束。
- **样式与响应式**：单一导入入口拆分有效模块，清理旧选择器/媒体规则，语义 tokens 映射 Radix，标题/正文采用 rem。实际 Shell 宽度减去 Rail、Sidebar、560px Canvas 与 420/520px Inspector 的预算决定侧栏折叠；Sidebar 只有一份业务内容。Header、长路径、代码/表格内部滚动、动态高度、安全区、移动主要操作 44px 均保留。
- **动效与可访问性**：MORPH/FLOW/ASSEMBLE/SETTLE 由 CSS/WAAPI 驱动现有视觉层；FLOW 不更换业务组件 key，运行时 reduced-motion 取消动画。显式认证成功发一次性 Assembly 标记，800ms 内移除纯图形层；恢复 /me、QQ check 不发标记，失败/注销清理。四类 Inspector 使用统一键盘 Tabs 与 Shell 合法 tab action/URL replace；错误、可访问名称、focus 与状态播报按组件语义补齐，流式 token 不新增逐字播报。

FE-B1 由 sessionIsolation/App/领域 generation 回归保留；FE-B4 的 pausing/stopping 次要投递动作及 UI-5 M01～M21 分类未改；FE-B5 与 UI-6 原交付版本/手选优先/幂等/sandbox/源码副本保存回归保留。

## 本轮验证与修复

开始时仅有用户的实施计划和索引变更；保留并在报告完成后更新其状态。基线是静态 HEAD/dirty 核验，首轮 8 文件/100 用例是入口迁移后的定向批次，不写作改动前测试结果，也不与最终计数相加。

初轮发现并修复：aside→dialog 导致跨断点重挂载；收件箱卸载 Host；嵌套确认随父层 hidden 隐藏；保存关闭的焦点返回顺序；Run 高级 tab 未 replace URL；候选 queued→running 未重新查询。最终层级复查还修复 Inspector 在 desktop→mobile 时抢到收件箱前方的问题：模式变化不作为重新打开，仅显式激活与嵌套操作提升前景。新增反例、组件 mount/iframe 状态/GET 计数及 Chromium 真实 native dialog 行为共同复验。

同时修复 TaskEditor 错误区 `<p>` 内嵌 `<details>/<pre>` 的无效 HTML。首轮并发执行完整 Vitest、浏览器、TS/build/lint 导致本机内存争用和多项超时；分批执行并使用 `npm test -- --maxWorkers=2`，没有降低断言或改超时掩盖功能失败。新增长截图浏览器矩阵自身为 120s 上限。

最终综合复验曾在重复使用的隔离库出现 57 通过 / 5 个 HTML 等待超时：只读诊断确认提醒/Research queued 执行累计占满默认 4 个协调容量，HTML 为 waiting_capacity，Inspector 正常打开。测试执行器只处理 Chat/Artifact，没有完成其它真实执行器的 Run。保留这批失败与诊断，用全新隔离数据库和文件目录按默认容量复验，未修改生产后端或清理旧库状态，见[容量调查](../../artifacts/product-acceptance/ui-7/logs/capacity-investigation.md)。

最终命令、退出码与通过计数见[证据目录](../../artifacts/product-acceptance/ui-7/README.md)和 `logs/frontend-exits.json`。日志区分初轮失败、真实修复、测试 locator 修正、最终复验。历史 UI-2～UI-6 截图保留原内容；既有 E2E 本轮输出复制到 UI-7/regression-evidence，再恢复历史文件。

原实施最终结果：typecheck / lint / build 均退出 0；完整 Vitest **53 文件 / 391 用例通过**；UI-7 定向 **6 / 6 通过**；全新隔离库上的 20 个受影响 Chromium spec **62 / 62 通过（15.2 分钟）**，综合批次已包含这 6 项，不相加。UI-7 新截图 42 张，既有业务本轮截图归档 106 张，几何、色对、控件包围盒记录独立保存。旧壳/两个导航 bridge/旧 CSS 的最终精确扫描无生产命中；测试 API 8187 / Vite 5280 已退出，无剩余监听。

后续独立自检确认 R1 迟到查询抢前景、R2 summary 键盘不可达、R3 普通聊天终态额外 Trace GET，均已补正式反例并修复。修复批次完整 Vitest **54 文件 / 405 项**、Chromium **74 / 74** 及前端检查全部通过；原 148 项实施证据保持，新证据另存 self-review-fixes。下表对应项以补充修复证据为当前结论，人工/仪表范围仍保留部分覆盖。

## A7 验收覆盖

“通过”表示下列明确自动化层通过，不表示真机/读屏或真实模型/渠道已验证；必验人工项及尚未完整覆盖的组合仍标记部分覆盖。

| ID | 状态 | 本轮证据与边界 |
| --- | --- | --- |
| A7-01 | 通过 | 生产 import/符号/CSS 扫描无旧壳与 bridge；迁移映射、正式组件/既有业务 E2E；RunStatus/Trace 有效内容保留 |
| A7-02 | 通过 | shell/App 旧 hash 与深链回归；四类浏览器 URL/tab、刷新/返回与不存在对象 |
| A7-03 | 通过 | ArtifactCreateForm 延迟 Promise/恢复/来源/busy；真实 API Header POST 初始要求与 UI-6 创建幂等 |
| A7-04 | 通过 | RunLookup 核实 Work/Chat 来源、同 ID 重查、A→B→A/退出和 403/404；R1 补账户/资料/Sidebar/父对象子弹窗迟到保护及仅 resize 保持意图；合法未知 UUID 真实 API 404 |
| A7-05 | 通过 | traceStore/workbench/sessionIsolation 历史选择保护；R3 补普通/概览无 Trace GET、当前高级终态刷新、历史/关闭/账户隔离；RunInspector 高级生命周期/轮询及权威最后消息保留；未新增 Chat feed |
| A7-06 | 通过 | TraceDetail/traceStore Model Input none/summary/full_safe/unavailable、权限变化、账户迟到清理 |
| A7-07 | 通过 | Shell 同对象/兄弟/栈≤5、Tab replace；UI-3/5/6 父子返回/原版本及深链 |
| A7-08 | 部分覆盖 | native modal/保存/收件箱/嵌套任务放弃、Sidebar/QQ 统一层级；尚未穷举保存/impact/accept/账户/QQ 所有交叉排列 |
| A7-09 | 通过 | Chromium 逐层 Escape、保存触发返回、四类关闭；R2 补 summary/iframe/有效 tabindex、隐藏/禁用过滤、fieldset legend 与父子 modal / 桌面路径；Shell 稳定触发 key 与行消失 fallback；未替代人工读屏 |
| A7-10 | 通过 | UI-4/5/6 草稿、授权/附件离开保护、原源保存恢复与版本冲突；移动暂挂父草稿 |
| A7-11 | 通过 | 三对边界实时 resize：组件 mount 计数、同 iframe 状态/修改文本、Artifact GET 初始/最终 1；Tab 必要 GET 不算冗余 |
| A7-12 | 通过 | 七视口与扩大模式 DOM 几何；Canvas 桌面≥560；预算不足折叠侧栏，侧栏按钮可达 |
| A7-13 | 部分覆盖 | 四类 ready 七视口；UI-4/5/6/7 empty/error/loading/unavailable 截图与流程；未对每个对象×五态×全部视口做完全笛卡尔积 |
| A7-14 | 部分覆盖 | 长中英标题/路径、内部代码/表格、根字号 200% 与 UI-2 文本放大浏览器回归；全产品 200% 文本人工流程未完成 |
| A7-15 | 部分覆盖 | Chromium 手机视口/横向窗、safe-area/dvh、Artifact 前景隔离和单 modal；无真实移动设备软键盘/非零安全区实测 |
| A7-16 | 通过 | 显式认证一次性标记、恢复/失败/注销、计时移除与 reduced-motion 单元回归；Auth/Register 浏览器流程 |
| A7-17 | 通过 | 对象视觉层动画可打断/不挂双业务内容；Task 原键盘顺序冻结和选中上下文保留回归；CSS 不触发领域命令 |
| A7-18 | 通过 | 初始 reduced-motion CSS、实时 WAAPI cancel/Assembly 移除单元及浏览器；功能/草稿/选中保持 |
| A7-19 | 部分覆盖 | 四类 Tabs Arrow/Home/End/roving/关联、native 目录按钮与 table 语义；R2 补首尾 summary Enter/Space、iframe 内外 Tab/Shift+Tab 与完整键盘边界；未做真实读屏验收 |
| A7-20 | 部分覆盖 | 新表单就地错误关联、适度播报、移动 Header/Tabs 44px 与实际文本色对测量；全站所有颜色/disabled/focus 边界与人工读屏未完整审计 |
| A7-21 | 部分覆盖 | App StrictMode/runtime/feed、Task controller、Artifact poller 单元；R3 补普通/概览/历史/关闭终态 Trace 增量 0、当前高级增量 1，权威 Run GET 保留；resize/SSE/multi-tab 回归；未全量仪表化全部 timer/runtime/连接峰值 |
| A7-22 | 通过 | sessionIsolation/App/Trace/Artifact 在途与 A→B→A、401 清理；普通 403 局部不可用；Auth Assembly 标记注销清理 |
| A7-23 | 通过 | 八项旧 Workspace 语义映射与正式组件/操作层；UI-4/manual-repair/direct-upload/p1/p3 真实 API |
| A7-24 | 通过 | Task M01～M21、控制/投递/Research/通知、UI-5 四桶/分页/草稿恢复回归；渠道 accepted 未称已读 |
| A7-25 | 通过 | UI-6 全套定向及真实 API/Fake Executor E2E；每版轮询、原交付/历史下载、sandbox/source、源码保存、全部自检保护 |
| A7-26 | 部分覆盖 | 旧 CSS/配置扫描与单一 tokens；前端检查、变更/删除清单、报告；完整阶段门槛依赖上述人工与仪表覆盖项 |

## 交接与回退

本轮自动化通过后代码可审阅，不将上述部分覆盖项改称完整阶段退出。UI-8/人工验收接收真机软键盘/安全区、实际读屏、全产品 200% 文本和完整色对、所有前景层组合与完整资源峰值仪表记录；长会话/大量文件和任务的综合性能、真实模型/Temporal/渠道验证仍属于 UI-8。BE-A1/BE-A2/BE-AW1 等不由本轮新增依赖替代。

本轮未部署、未创建 commit/PR、未更改数据库模式或清理业务库。回退前端工作包并匹配相应 store/调用点；旧壳从 Git 历史恢复，不保留可发布双 Shell。不得回滚用户文件、反向修改 Work 状态或清服务器对象。
