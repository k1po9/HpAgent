# UI-7 统一与清理实施计划

> 状态：前端代码已实施，自动化结果及人工验收边界见[实施报告](ui-7-unification-cleanup-report.md)；制定日期：2026-10-08（Asia/Shanghai）。
> 以《HpAgent 前端 UI 重构方案 v1.0》为产品基准，结合 UI-6 实现及自检修复后的当前代码制定。
> 制定本计划时仅生成计划与文档索引，未实施 UI-7、未运行应用测试、未启动业务服务，也未创建 commit / PR。以下基线与目标保留为规划记录，本轮执行结果单独记录于实施报告。

## 1. 基准、范围与依赖

| 项目         | 核验基准                                                                                                                                                                                                                       |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 项目         | `/home/hp/workspace/HpAgent_web`                                                                                                                                                                                               |
| HEAD         | `e6791e35990ce6da811f6204806def202e770076`                                                                                                                                                                                     |
| 提交         | `feat(web): implement UI-6 artifacts and fix review findings`                                                                                                                                                                  |
| 提交时间     | 2026-10-08 09:52:13 +08:00                                                                                                                                                                                                     |
| 初始工作区   | `git status --short` 为空                                                                                                                                                                                                      |
| 方案         | [HpAgent 前端 UI 重构方案 v1.0](/mnt/d/HuaweiMoveData/Users/黄培/Desktop/ui/HpAgent_前端_UI_重构方案_v1.0.md)；原 Windows 路径为 `D:/HuaweiMoveData/Users/黄培/Desktop/ui/HpAgent_前端_UI_重构方案_v1.0.md`                    |
| 方案 SHA-256 | `debffa18de54203fa2e7037108b9f0b3f736e6b350516a9c96fd87590217f892`                                                                                                                                                             |
| 重点章节     | §3、§8–10、§12–14、§16；UI-7 退出标准见 §14.2                                                                                                                                                                                  |
| 冻结决策     | D01/D02/D03/D07/D08/D09/D10；重点回归 FE-B1/FE-B3，并保留 FE-B4/FE-B5                                                                                                                                                          |
| 前序记录     | [UI-3 报告](ui-3-execution-diagnostics-report.md)、[UI-4 报告](ui-4-workspace-permissions-report.md)、[UI-5 报告](ui-5-task-center-report.md)、[UI-6 报告](ui-6-html-artifact-report.md)、[UI-6 自检修复](ui-6-self-review.md) |

方案原始源码基线是 `23ad691`，不能直接拿其中旧文件状态作为本阶段事实。本计划以下结论均以本表 HEAD 的静态代码为依据；前序报告里的通过次数属于历史批次，不作为本次验证结果。实施开始时重新记录 HEAD / dirty diff，复核本计划提到的调用链和遗留项。

UI-7 的目标是完成视图迁移收尾，并统一 Motion、Responsive、Accessibility：全部原能力有正式入口；Shell 是面板导航唯一权威；各视口没有页面横向溢出和操作遮挡；减少动效时功能完整；转场不引起额外业务 mount、重复订阅或重复加载。

本阶段允许修改旧组件外壳、Shell、样式、交互 primitives，以及迁移直接涉及的前端生命周期接线。保留 React / Zustand / Radix / assistant-ui / hash 导航，不引入路由、查询缓存或 Motion 框架作为前提。不改变 Run / Work / Workspace / Artifact 持久化模型、状态机、SSE 和后端协议。

UI-1～UI-6 是依赖，UI-8 接收全产品综合回归、长期性能和最终交接。UI-7 必须完成自身布局、键盘、焦点和减少动效的验证，不能全部推迟给 UI-8。BE-A1/BE-A2/BE-AW1 及其它 BE-* 独立排期；不增加第四入口、非 HTML Artifact、每周调度、回收站或批量危险操作。

## 2. 当前实现与实际差距

| 编号  | 当前代码事实与证据                                                                                                                                                                                                   | UI-7 处理                                                                                                                   |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| C7-01 | [App](../../web/src/App.tsx) 已使用 Pre-Shell 和按账户 key 的 AppShell；[AppShell](../../web/src/components/shell/AppShell.tsx) 已只有 AI / 空间 / 任务，使用正式 WorkspaceScreen / TaskScreen                       | 保留现有架构，不再设计八页入口替换；当前入口代码中未发现旧/新 Shell 切换开关，实施前再核查配置                              |
| C7-02 | AppShell 仍 import `TestPages.ArtifactsPage`，用于“从回复生成 HTML”弹窗；[TestPages](../../web/src/components/TestPages.tsx) 还包含 DiagnosticsPage 和保存弹窗重导出                                                 | 先提取正式 HTML 创建表单，保留来源选择与生成要求，再删除 TestPages 及无调用重导出                                           |
| C7-03 | [artifacts store](../../web/src/store/artifacts.ts) 已无 openArtifactId / openVersionId，但仍有 `openArtifact`、依赖 `onOpen` 和对 Shell 的 import；调用者为 ArtifactsPage / WorkPanel                               | 迁移或删除最后调用点后移除导航适配；不重做 UI-6 的实体、版本合并、幂等意图和 poller                                         |
| C7-04 | [ArtifactPanel](../../web/src/components/ArtifactPanel.tsx) 已只是读取 Shell 的临时适配壳，由 TestPages 引用；正式 [ArtifactInspector](../../web/src/components/artifact/ArtifactInspector.tsx) 已接入 InspectorHost | 删除适配壳及其专属旧样式，保留 ArtifactPreview 和正式 Inspector                                                             |
| C7-05 | [traceStore](../../web/src/components/trace/traceStore.ts) 仍有 `open / setOpen`、`onOpen` 导航回调；`selectRun` 也设置 open，`followRun` 依赖 open 阻止聊天抢选择                                                   | 拆开“诊断查询目标/生命周期”和“面板显示”；保留历史选择保护、事件缓冲及 generation，不直接删 open 后放任 followRun 切换       |
| C7-06 | [RunInspector](../../web/src/components/run/RunInspector.tsx) 高级页仍渲染 `<TracePanel embedded />`；[TracePanel](../../web/src/components/trace/TracePanel.tsx) 即使 embedded 仍读取 open 决定是否显示             | 将 TracePanel 内有效内容提取为正式 Trace 内容组件，由 Run 高级页显式挂载；不把整个 TracePanel 当死代码删除                  |
| C7-07 | WorkPanel / WorkManagement / ResearchOutputs / LegacyUtilities 未发现生产入口 import；WorkspacePanel 只有自身单元测试 import                                                                                         | 建立能力等价映射和测试迁移后删除孤立旧组件；测试引用不等于需要永久保留旧实现                                                |
| C7-08 | [Surface](../../web/src/components/shell/Surface.tsx) 使用 native dialog 和手动 Tab 边界，桌面用 aside；焦点清理由 Surface 和 Shell 分别处理。modal 改变时 Tag 从 aside 变 dialog                                    | 统一焦点与层级；断点变化保持同一内容子树，避免整块 Inspector 重新挂载、草稿丢失、iframe 重载或重复查询                      |
| C7-09 | [InspectorHost](../../web/src/components/shell/InspectorHost.tsx) 只按 1279px 判定 modal。AppShell 对 task-inbox 特判卸载 Host，sidebar 和保存弹窗等有独立开关；QQ 引导另用 Radix AlertDialog                        | 统一前景层协调，审查保存、确认、收件箱、侧栏与 QQ 引导的组合；移动端只保留一个活动 modal，暂挂父层而不清除对象上下文        |
| C7-10 | [styles.css](../../web/src/styles.css) 共 2292 行，旧 hp-test-* / hp-page-* / 固定 Trace/Artifact 宽度，与 UI-1～UI-6 补丁并存；Radix 变量和硬编码色值混用                                                           | 先梳理活跃选择器再分模块归整、建立统一 tokens；保留仍用于正式视图的共享类，逐条清除覆盖冲突                                 |
| C7-11 | Shell 已有 1280/960/600 附近断点、100dvh、safe-area 和减少动效规则；但 Sidebar 隐藏仅在 1280～1439 且 inspecting 时触发                                                                                              | 按真实可用宽度和 Canvas 560px 下限决定折叠；不能只靠固定区间，扩大阅读也需参与宽度预算                                      |
| C7-12 | [main](../../web/src/main.tsx) 使用 StrictMode 和 Radix light Theme；现有 CSS 未发现 MORPH/FLOW/ASSEMBLE/SETTLE 的统一实现，仅 Trace spinner 等局部表现；登录流程没有明确的一次性 Assembly 来源信号                  | 增加可中断的 CSS / Web Animations 动效和显式登录标记，保持 Shell / runtime 生命周期；恢复会话和 QQ 刷新 /me 不触发 Assembly |
| C7-13 | 四类 Inspector 已有 tablist/tab/tabpanel 及不同键盘实现；Workspace 目录采用原生嵌套列表/按钮，FileList 使用语义 table；[a11y E2E](../../web/e2e/a11y.spec.ts) 只做登录与基本控件检查                                 | 统一 Tabs、可访问名称、状态播报、就地错误、焦点返回与触摸目标；扩充实际键盘和模态验证                                       |

上述 C7-08～C7-13 属于静态差距和风险，不代表本次已经在运行页面复现。尤其移动软键盘、200% 文本缩放、完整读屏/对比度仍是前序报告明确未完成的验收，须在本阶段分别记录覆盖程度。

## 3. 删除前的能力与入口映射

每个删除项都需要“旧能力 → 正式入口 → 回归证据”三联记录。以下是实施目标与当前候选入口；静态存在不能代替流程实测。

| 旧组件/能力                                                                                    | 正式入口及迁移决定                                                                                                                 | 删除前检查                                                                                                                     |
| ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| ArtifactsPage：选择当前对话已完成回复、填写初始生成要求、创建/恢复生成、打开已有对象           | 新增建议 `artifact/ArtifactCreateForm.tsx`，继续放 AI Header 的创建弹窗；消息行保留 ArtifactMessageItems                           | 非空 completed assistant 来源；可填写初始 instruction；同 key 恢复、busy 单提交、来源切换/关闭后不自动弹回旧对象；失败保留输入 |
| ArtifactPanel：预览/版本/修改                                                                  | InspectorHost → ArtifactInspector → ArtifactComposer / ArtifactPreview                                                             | 原 version 深链、失败/重开、手选优先、历史版下载和源码副本保存完整                                                             |
| DiagnosticsPage：聊天与 Work 执行选择、高级诊断、按 Run ID 查询                                | 消息 ExecutionBlock、Task 成果页执行记录、Run 高级页；补正式 `run/RunLookup.tsx`，在 AI 上下文菜单和 Task 高级详情提供编号查询入口 | “按执行编号查询”当前仅在旧页内，须先补入口再删；GET 核实真实 Run 后打开唯一 Inspector，403/404 为对象不可用，不复制 Chat feed  |
| TestPages.SaveWorkspaceDialog 重导出                                                           | 直接 import workspace/SaveToWorkspaceDialog                                                                                        | AppShell 已直连；全仓无重导出调用再删除；已有 file_id 和 text/plain HTML 源码两条链均保留                                      |
| WorkPanel：查看/暂停/恢复/停止/推进、关联对话、预算、投递决策、成果与保存记录                  | TaskScreen / TaskInspector / TaskActions / TaskTargets / TaskBudgetDialog / TaskDeliveryDecision / TaskOutputs                     | UI-5 四桶和 M01～M21 不变；pausing/stopping 的次要待决动作可达；Work 的控制命令不被 Run cancel 替代                            |
| WorkCreateForm：创建/修订 Research、提醒和普通任务                                             | TaskEditor + taskRequirement                                                                                                       | 原完整 requirement payload、capability、immediate/once/daily、版本冲突与草稿保护不丢                                           |
| WorkResourcePanel                                                                              | TaskInspector 资料页 → TaskResources / WorkInputs / GrantEditor                                                                    | Work grant 与 Conversation grant 分开；已有 inputs、资源请求和授权可管理                                                       |
| ResearchOutputs：报告、Run 失败、已发布文件下载/保存                                           | 任务类型 Research → TaskInspector 成果页 → 读取报告与输出文件                                                                      | Markdown/GFM、最近最多 100 次执行限制、报告失败重试、已发布文件保存均可达；不新增 Research 一级入口                            |
| WorkspacePanel：目录/搜索/上传/来源/移动/删除/impact/版本/权限/本次候选                        | WorkspaceScreen / WorkspaceSidebar / FileInspector / FileVersions / ResourceManager；RunResources 展示 candidate/fixed/read        | 当前目录创建、上传成功授权失败只补授权、撤权观察、候选分页/过期/queued→running、impact、CAS 和另存流程有正式组件回归           |
| LegacyUtilities.NotificationInbox                                                              | 任务 Header → TaskInbox                                                                                                            | 同通知 API、分页/失败/账户隔离、实际 payload 和 Work/Run 回链；不把账户收件箱称已读                                            |
| LegacyUtilities.EmptySelection                                                                 | ChatPane 的正式空态与 Composer                                                                                                     | 无对话可直接输入、失败创建保留草稿；确认无 import 后删除                                                                       |
| RunStatus                                                                                      | 保留；ExecutionBlock / RunInspector 仍调用                                                                                         | 保留 retry 安全和 stop 状态测试，只统一外观；不能按旧文件名删除                                                                |
| TraceTree / TraceDetail / ModelInputView、ArtifactPreview、QQBindingPanel / RegistrationQqGate | 保留有效内容和业务逻辑，局部调整展示/焦点协调                                                                                      | visibility none/summary/full_safe、权限失效清理、sandbox/source 校验、QQ challenge 与跳过路径保持                              |
| hp-test-* / hp-page-*、旧外壳固定宽度和媒体规则                                                | 目标 Shell / Screen / Inspector 样式                                                                                               | 源码和活跃 E2E 无依赖；尺寸/状态截图通过；hp-operation-form 等共享类不能一并删除                                               |

WorkspacePanel.test.tsx 现有 8 个用例的语义必须迁移：候选在取消/离开后不回灌、A→B→A 保护、未知活动 Run 404、queued→running 刷新、分页切会话失效、目录创建和上传授权部分成功。分别归入 RunResources、workspaceOperations、WorkspaceCommands 或正式组件集成测试；保留断言强度后才删除旧测试文件。其它孤立组件若发现未列能力，先补本表，再执行删除。

旧 hash 兼容继续保留在 shell.parseRoute：#chat / #files / #works / #research / #authority / #artifacts / #diagnostics / #account 归一到三入口或上下文弹窗。删除旧页壳不等于删除兼容解析。无明确对象上下文的旧成果/诊断 hash 返回 AI，不从任意缓存猜对象；刷新对象仍经服务端核实。

## 4. 状态、Host 与交互职责

### 4.1 删除 bridge 的完整顺序

1. 将正式创建表单和原对象打开操作直接接入 `useShell.openInspector`，携带 conversationId / messageId / workId / versionId / 稳定触发 key；不要把 POST 的成功自动打开逻辑放回领域 store。
2. 新表单复用 `createArtifact` 的 CommandResult、固定幂等意图和请求世代保护。等待期间编辑的文本不被旧成功清除；切来源/页面、关闭或账户变化后不自动导航。新增 Run 编号查询也采用账户 generation + 请求 token，覆盖同 ID 重查和 A→B→A。
3. 完成旧组件能力映射与测试迁移，删除孤立 WorkPanel 等旧调用者。若仍有有效调用点，先迁移再继续。
4. 删除 artifacts 的 `openArtifact` / `onOpen` / Shell import，删除 traceStore 的 `setOpen` / `onOpen` / Shell import；实体查询、命令和轮询不含导航副作用。
5. 静态搜索旧符号应无生产代码命中，构建/定向回归通过；旧 hash 解析仍存在。没有再引入 store 双向订阅 bridge。现有 artifacts.test.ts 的 onOpen 断言迁移为“原对象缓存更新、当前 Shell 选择不变”的集成断言，不能随接口删除而丢掉迟到命令的保护测试。

Trace 查询目标不是第二份面板导航权威。建议用 `selectedRunId`（或明确 queryRunId）和 `liveRunId` 区分 Inspector 高级页选中对象与聊天事件来源；有效内容仍由 Shell 中 Run 对象和高级 tab 决定。

- Run 高级页进入时显式选择查询目标；离开高级页/关闭使面板请求失效并释放面板轮询，不取消后台执行。
- Workbench 的 followRun / applyEvent 接线保留，但不能更换正在选中的历史对象；仅匹配查询目标的事件可合并。聊天 feed 不复制成 Inspector feed。
- `open` 原先还承担请求失效和历史选择锁，替代这些职责后才能删除。reset、Model Input generation、事件缓冲/溢出处理、同 Run 终态合并全部保留。
- 无选中诊断时可更新直播来源，但不主动加载全部 Trace/Model Input；历史/Work Run 仍走现有按需 GET 与有限 polling。

### 4.2 唯一 Host、模态层和草稿

Shell 保留唯一 `route.inspector`、合法 tab、version、origin、有界 backStack（最多 5 层）、requestToken 和布局偏好。同对象再次点击聚焦现有内容；兄弟对象 replace 顶层选择；父→子 push 内部栈。浏览器返回/前进与内部返回分别验证，不把内存栈写入刷新后的 URL。

建议增加纯表现层 Surface 协调器：登记 surfaceId、类型、父层、触发 key 和活动层级，统一 native dialog / Radix 引导的前景交接。业务表单草稿、HTML、SaveSource、Task/Run 数据仍留在现有组件或操作层，不塞进导航 store，也不重建上传/保存状态机。

- 桌面 Inspector 是命名非模态区域，Tab 可以离开；<1280px 是 modal sheet。断点切换使用稳定 Host DOM 与内容子树，例如同一个 dialog 元素在非模态 open / showModal 模式间切换，而不是条件切换 aside/dialog 后重建子树。最终方案必须由真实浏览器验证非模态语义、焦点和 top layer。
- 移动端一次只激活一个 modal。保存/impact/accept 确认置于 Inspector 之上，父层暂挂、inert 且不参与键盘，返回后恢复原对象、tab、草稿和滚动。协调的是可交互层，不要求清空父路由或复制父数据。
- task-inbox 不再靠卸载 Inspector 来避免叠层；Sidebar 抽屉与账户/资料/创建弹窗遵守相同交接。不可取消的已提交操作仍按原目标恢复；暂挂不等于业务取消。
- Sidebar 只渲染一个可交互实例；AppShell 当前同一 sidebar 可同时进入隐藏 aside 和抽屉，需避免重复组件 effect / DOM ID。切宿主不得造成重复 tree 查询或对话列表订阅。
- Esc 只交给最上层：确认/表单 → 上一层 Inspector 对象 → 关闭 Inspector。原有 dirtyTaskEditor / dirtyResourceEditor / 附件离开保护不可被通用关闭绕过。
- 统一焦点负责人，避免 Surface cleanup 和 shell.closeInspector 的延迟 focus 互相覆盖。打开聚焦标题/首个合理控件，返回父对象聚焦子入口，关闭回触发行；入口已消失则回所属列表/Canvas 标题。Run 入口也补稳定 key；跨账户时不聚焦旧账户 DOM。
- 暂挂/关闭必须区分：暂挂保持草稿与选择，停止无必要的面板交互/轮询；Artifact 构建、活动 Chat feed 和 Work 控制器继续按既有所有权运行。显式关闭/对象替换可执行既有清理；退出全部 reset。

### 4.3 公共 primitives 的边界

统一 Surface Header、关闭/返回、Tabs 键盘、图标按钮名称/tooltip、五态反馈（loading/empty/unavailable/error/ready）和表单就地错误。建议提取 `InspectorTabs`、`IconButton`、`ObjectState` 等小组件，名称为计划建议，不是现有实现声明。

共享的是交互语义与外观；Task/Run/File/Artifact 仍提供各自有效数据和动作。404/权限失效清正文与危险操作，网络错误可保留旧数据并提示待同步，409 保留输入后刷新重新确认。错误 code/request_id 仍可在详情定位。禁止把所有业务对象做成 JSON 配置式 CRUD。

Tabs 选择集中通过 Shell action 校验并 URL replace；当前 Run tab 只 setState、其它组件各自写法不一致，需归整。ArrowLeft/Right、Home/End、roving tabindex、tab↔panel 关联统一；懒加载仍由各内容控制，不能因统一 Tabs 预加载全部诊断。

## 5. 视觉、响应式、动效与无障碍实施规范

### 5.1 tokens 与样式清理

沿现有 Radix Theme 建一套 `--hp-*` 语义 tokens：surface/subtle/border/text/muted/accent/danger/success/warning、间距、字号、圆角、focus、层级、尺寸和动效时长。优先映射 Radix 变量，必要的品牌色集中定义；不在各模块再复制色值集合。

间距 4/8/12/16/24/32px；普通控件 36px，触摸区域至少 44px；表格行建议 48px；页面标题 24px，Inspector 标题 18px，正文 14～16px；letter-spacing 为 0，区域不套浮动卡片，卡片圆角不超过 8px。处理现有 Rail 10px / Surface 12px 等与方案不一致的局部规格。

建议 `styles.css` 保留单一导入入口，按顺序导入 tokens/base、shell/surface、conversation、workspace、tasks、artifact、trace 内容模块。文件可放 `web/src/styles/`；实施中确保旧规则先被迁移再删除，避免单纯把 2292 行复制到多个文件。

清理次序：旧页壳选择器 → 删除组件专属样式 → Trace/Artifact 固定侧板宽度与旧媒体规则 → Shell/Screen 重复覆盖 → 统一 tokens。保留 Markdown/code/iframe、共享表单、RunStatus、TraceTree/Detail 等仍使用的样式。不以 `.hp-*` 名称或文件位置判断死代码；搜索动态 class、嵌套选择器与 E2E locator，并保存删除清单。

### 5.2 Responsive 与滚动

| 可用宽度    | 行为及验证重点                                                                                                                                                     |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| ≥1280px     | 四区可并排；Rail 64px、Sidebar 默认 256px、Inspector 默认 420px，扩大 520px；Canvas 至少 560px。按容器可用宽度扣除边框预算，空间不足优先折叠 Sidebar，出现侧栏按钮 |
| 960～1279px | Rail + 可折叠 Sidebar + Canvas；Inspector 为右侧 modal sheet，不挤压 Canvas；展开/关闭 Sidebar 有真实控件                                                          |
| 600～959px  | Rail 56px，Sidebar 抽屉，Inspector 近全屏；背景 inert，前景滚动和返回可达                                                                                          |
| <600px      | 三入口底部导航、Header 打开 Sidebar、Inspector 全屏；safe-area 和动态高度适配，移动端单一活动 modal                                                                |

默认四区预算为 `64 + 256 + 560 + 420 = 1300px`，扩大阅读为 `1400px`（均未计边框）。因此 ≥1280 本身不保证能同时展开四区。用统一的可用宽度判断和布局模式，CSS 与 Host modal 状态不能各自维护不一致断点；可由 ResizeObserver / matchMedia 组合获得容器宽度，不引入新响应式库。

MainCanvas 与 Inspector 独立滚动，Conversation 阅读宽度建议 760px；Composer 位于本区域底部。Sidebar、消息锚点、任务筛选和目录状态在切换/暂挂后恢复。长中文/英文标题与文件名两行/保留尾部，提供可访问完整名；URL、路径、Markdown 表格、代码、Trace metadata 自身换行或内部滚动，不能撑宽整个页面。

移动端沿用 100dvh 与 safe-area；真实软键盘下验证输入、发送、停止、关闭按钮可见且可滚动到达。只有实测需要时才补 visualViewport 调整，避免重复扣减键盘高度。Artifact 全屏时 Chat Composer 保留在背景并不可交互；双 Composer 不同时占前景。

### 5.3 Motion

| 类别     | 实施场景                                         | 时长和退出约束                                                                                           |
| -------- | ------------------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| MORPH    | 发送↔停止、快速/深度选择、扩大阅读按钮状态       | 120～180ms；固定包围盒，opacity/transform 切换；状态文案同步变化，不依赖动画完成才能操作                 |
| FLOW     | File/Task/Artifact/Run 打开或切换 Inspector      | 180～240ms；有限平移与面板布局过渡；快速 A→B→A 可打断，数据选择先正确；不双挂业务内容                    |
| ASSEMBLE | 用户显式登录或注册后确认会话建立，首次进入 Shell | 600～900ms，总时长≤1s；纯视觉覆盖层 aria-hidden/pointer-events:none，完成/中断即销毁；Shell 一次业务挂载 |
| SETTLE   | 终态收敛、收起、列表位置变化                     | 120～200ms；不用循环装饰脉冲；选中/键盘操作的 Task 保留上下文，桶变化提示且不造成误点                    |

使用 CSS / Web Animations API；动效 key 绑定视觉状态，不能让 AppShell、ChatPane、assistant-ui runtime、useTaskController 按动画状态或 Inspector 开关重新挂载。布局过渡不发送领域命令、不重放 send/create/save，也不逐 token 执行消息 enter 动画。

显式登录来源由 LoginForm 成功流程发出一次性 UI 标记，认证通过后消费；/me 刷新恢复、QQBindingPanel 完成后的 check、切页/刷新/Inspector 操作均不触发。注册成功但自动登录失败不播放，稍后真实登录成功才可播放。账户切换与退出清理标记/定时器；降低性能或减少动效直接进入可操作 Shell，功能不能等粒子完成。

Auth 视觉层只用无敏感内容的图形，不截图/克隆密码字段，不把输入文本制作粒子。`prefers-reduced-motion: reduce` 跳过粒子、共享元素、大位移、平滑滚动；WAAPI 也须显式处理，不能只依赖 CSS media。运行时切换偏好应取消在途装饰动画并收敛到最终布局，焦点/选中/请求次数一致。

### 5.4 Accessibility

- 以方案的 WCAG 2.2 AA 为验收目标：普通文字至少 4.5:1，大字和必要非文字交互边界至少 3:1；保留图标/文字，颜色不是唯一状态含义。覆盖 muted、disabled 与 focus/边界的实际适用要求，记录具体色对和测量结果，不写“已认证”。
- Rail 主导航 aria-current；有名称和 tooltip 的图标按钮；移动主要操作至少 44×44px。原生嵌套目录按钮保持标准 Tab/Enter/Space 与 aria-expanded，若选择 ARIA tree 必须同时实现完整树键盘，不能只加 role。
- FileList 保留 table/th 语义；四类 Tabs 正确关联 panel；长标题、200% 文本缩放下按钮不截断、控件不重叠。
- modal trap/inert 与桌面非模态行为真实验证；保存/impact/accept/QQ 的层级统一，Esc 不穿透背景。屏幕阅读器只接触活动层。
- loading/失败/完成适度 aria-live；只播报执行阶段或结果，不逐 SSE token 播报。错误与相关字段用 aria-describedby/aria-invalid 关联，提示不只靠 toast。
- Composer 和新增生成要求支持中文 IME；composition 中 Enter 不提交，保持已有 Enter / Shift+Enter 行为。键盘焦点可见且不被 fixed Header/底栏遮挡。

## 6. 工作包、顺序与退出条件

实施链：`U7-0 → U7-1 → U7-2 → U7-3 → U7-4 → U7-5 → U7-6 → U7-7`。先完成能力补位和状态所有权，再整理布局与动效；每个工作包形成可独立审阅的文件差异和定向证据。此为后续实施计划，不是本次执行结果。

| 工作包                     | 主要工作                                                                                                           | 退出条件                                                                                                |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------- |
| U7-0 基线与清单            | 记录新 HEAD/dirty diff；读取变更；核查生产 import、旧符号、旧 CSS/配置、已有测试与失败                             | 完成 C7 清单、删除前映射、Workspace 旧测试语义表；相关基线结果与环境限制可追溯                          |
| U7-1 正式能力补位          | 提取 ArtifactCreateForm；补 RunLookup；迁移所有有效旧能力、焦点来源与幂等恢复                                      | Header/消息/Task 入口可达；初始生成要求和按 Run ID 查询不丢；失败/迟到不抢对象                          |
| U7-2 去 bridge 与旧壳      | Trace 查询/直播分离、提取有效 Trace 内容；迁移旧单元测试；删除 TestPages/ArtifactPanel/孤立旧组件及 store 导航回调 | Shell 是唯一面板权威；领域 store 不 import Shell；历史 Trace 不被直播抢走；能力回归通过；旧 hash 仍正常 |
| U7-3 Surface 与焦点统一    | 单一活动 modal、父层暂挂、稳定 Host/Sidebar 内容、Tabs action、焦点/Esc/草稿保护、QQ 层协调                        | 桌面/手机和断点切换不卸载同对象内容；逐层焦点恢复；背景不可操作；未提交输入和已提交恢复意图保留         |
| U7-4 tokens/CSS/Responsive | 模块化有效样式、清死选择器、宽度预算、滚动/safe-area/长内容/200% 文本/触摸                                         | 七视口及边界宽度无页面溢出/重叠；默认/扩大模式 Canvas 下限；活跃 E2E 不依赖旧壳类                       |
| U7-5 Motion                | 四类动效、一次登录标记、视觉层清理、可中断与 reduced-motion                                                        | 动效关闭和打开功能一致；刷新不 Assembly；动画不多 mount/feed/fetch；输入和焦点不等动画                  |
| U7-6 无障碍完善            | 名称/tooltip、Tabs/目录/表格键盘、错误关联、适度播报、对比度及实际键盘/读屏检查                                    | A7 键盘/层级/表单/对比度项有证据；人工与自动化分别记录；尚未覆盖不能写完整通过                          |
| U7-7 回归与交接            | 完整前端检查、受影响 E2E、必要后端契约、截图/流程/删除清单与报告                                                   | A7-01～A7-26 逐项状态；无未解决 P0/P1；必验项满足才能声明 UI-7 完整验收；UI-8/BE 交接明确               |

U7-3/U7-4/U7-5 之间若改变 Host 生命周期，立即复验 Trace、Artifact、保存和 Chat 订阅；不能到最终阶段才发现重复 mount。若需要后端契约变化，单独登记 BE 变更，不扩大 UI-7。

## 7. 文件影响与删除清单

| 文件/目录                                                                                                                          | 计划操作                                                                                         |
| ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `web/src/components/artifact/ArtifactCreateForm.tsx`（建议新增）                                                                   | 替代 ArtifactsPage 的有效表单；明确来源、instruction、恢复、关闭/切来源保护                      |
| `web/src/components/run/RunLookup.tsx`（建议新增）                                                                                 | 上下文 Run ID 查询、不可用/错误、请求隔离与 Shell origin 接线                                    |
| `web/src/components/shell/AppShell.tsx`、`InspectorHost.tsx`、`Surface.tsx`、建议公共 primitives                                   | 去旧页依赖、协调前景层/Sidebar、稳定宿主、统一焦点、Tabs 和五态                                  |
| `web/src/store/shell.ts`、`shell.test.ts`                                                                                          | 合法 tab URL replace、模态表现状态/焦点描述和宽度预算；保留 hash 兼容/草稿导航保护               |
| `web/src/components/trace/traceStore.ts`、测试；`run/RunInspector.tsx`；`store/workbench.ts` 的 Trace 接线                         | 去 open 导航状态和 bridge，分离直播来源与诊断目标，保留请求/事件隔离                             |
| `web/src/components/trace/TracePanel.tsx`                                                                                          | 先提取 Trace 内容（建议 `RunTraceContent.tsx`）；移除旧侧板 Header/开关壳；TraceTree/Detail 保留 |
| `web/src/store/artifacts.ts` 及相关测试                                                                                            | 去 openArtifact/onOpen/Shell import；实体、命令、轮询及 UI-6 自检回归不改语义                    |
| `web/src/components/TestPages.tsx`、`ArtifactPanel.tsx`                                                                            | 正式能力迁移后删除                                                                               |
| `web/src/components/WorkPanel.tsx`、`WorkManagement.tsx`、`ResearchOutputs.tsx`、`WorkspacePanel.tsx`、`shell/LegacyUtilities.tsx` | 核验映射与调用后删除；WorkspacePanel.test.tsx 用例先转移再删除                                   |
| `web/src/components/LoginForm.tsx`、`RegistrationQqGate.tsx`、`store/sessionLifecycle.ts`、建议 Motion UI 模块                     | 显式登录一次标记、引导层协调、动画/焦点清理；Auth 协议和注册失败路径保留                         |
| 四类 Inspector、RunStatus、Tree/FileList/Composer 等                                                                               | 仅按需接统一 primitives、名称/错误/状态播报和布局；不重写领域逻辑                                |
| `web/src/styles.css`、建议 `web/src/styles/*.css`                                                                                  | tokens、有效样式归整、旧规则删除、响应式与 Motion                                                |
| `web/src/components/shell/*.test.tsx`（建议新增）                                                                                  | 真实前提的层级/宿主/焦点/减少动效测试；native dialog 行为以浏览器补证                            |
| `web/e2e/a11y.spec.ts`、建议 `ui-7-shell.spec.ts` / `ui-7-responsive.spec.ts` / `ui-7-a11y.spec.ts`                                | 正式能力入口、断点跨越、单 modal、键盘/焦点、动效/请求计数/截图；旧 spec 保留业务断言            |
| `docs/implementation/README.md`、UI-7 报告及证据目录                                                                               | 增加索引；记录删除映射、验收覆盖与 UI-8 交接                                                     |

建议新增文件只在承担明确复用职责时创建，不为拆分数量拆文件。删除旧组件必须同步修正文档中“当前入口”指向；历史实施报告中的旧路径/证据保留历史含义，不批量覆盖。

## 8. 验收矩阵与验证方法

| ID    | 场景                                                 | 通过条件                                                                                                 | 验证层                       |
| ----- | ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ---------------------------- |
| A7-01 | 旧能力和删除扫描                                     | §3 每项有正式入口与实际流程；TestPages/ArtifactPanel/旧 bridge 无生产引用；RunStatus/Trace 内容保留      | 静态 + 组件 + E2E            |
| A7-02 | 旧 hash 与新深链、刷新/前进/返回                     | 归一到三入口；无上下文不猜对象；tab/version 合法；不存在/无权对象同样不可用                              | Shell + E2E                  |
| A7-03 | Header HTML 创建与消息行                             | 保留来源选择和初始要求；恢复固定 key；连点不重复；关闭/切来源后迟到成功不抢对象                          | 组件 + 真实 API E2E          |
| A7-04 | 按 Run ID 查询 Chat/Work、同 ID 重查、乱序           | 请求先核实；Work Run 不伪造 assistant_message；403/404 清内容；A→B→A/退出后结果不回灌                    | 延迟 Promise + E2E           |
| A7-05 | 历史 Trace 与聊天事件并存                            | Shell 对象保持历史 Run；仅匹配事件合并；无第二 Chat stream；高级页关闭停止自有查询                       | Trace/Run/生命周期           |
| A7-06 | Trace/Model Input 迟到与权限变化                     | generation/request token 保留；none/summary/full_safe/unavailable 正确；无跨账户诊断泄漏                 | 现有 Trace 回归 + 新反例     |
| A7-07 | Inspector 同对象/兄弟/父子/深链                      | 唯一 Host；同对象聚焦，兄弟替换，栈≤5；刷新不恢复旧内存栈；任务筛选和原版本保留                          | Shell + E2E                  |
| A7-08 | modal 保存/impact/accept/账户/收件箱/Sidebar/QQ 组合 | 移动只有一个活动 modal，父层暂挂；背景 inert；不卸载父对象或重置草稿；无重复 Sidebar effect              | 组件 + Chromium              |
| A7-09 | Esc 与焦点返回                                       | 最上层先处理；再回父对象/关 Host；Run/File/Task/Artifact 都有稳定回焦点，触发消失回标题                  | Chromium 键盘 + 人工         |
| A7-10 | dirty 草稿、导航和保存恢复                           | Task/权限/附件的原离开保护有效；保存源冻结；已提交操作按原目标反馈，不能套到新对象                       | 现有 UI-4/5/6 + 集成         |
| A7-11 | 1279↔1280、959↔960、599↔600 实时 resize              | Inspector 内容、修改草稿、预览/选择不重新挂载；无额外加载/双 feed；modal/焦点模式正确                    | mount/请求计数 + Chromium    |
| A7-12 | 桌面默认与扩大阅读/Sidebar                           | Canvas ≥560px；实际宽度不足自动折叠 Sidebar；按钮可重新打开，四区没有重叠                                | 多尺寸 DOM 几何 + 截图       |
| A7-13 | 七视口、空/加载/错误/不可用/ready                    | 页面无横向溢出；Header/Actions/关闭可达；内部代码/表格允许自身滚动；四类对象均覆盖                       | Chromium 参数化              |
| A7-14 | 长中文/英文标题、路径/URL、代码、200% 文本           | 内容可读，完整名称可访问；控件不截断、不重叠，文本放大不只是缩小 viewport                                | 浏览器 + 人工                |
| A7-15 | 真机软键盘/安全区/横屏/双 Composer                   | 输入和提交操作可达；底导航不遮挡；Artifact 前景时背景 Composer 不可操作                                  | 真机人工 + 截图辅助          |
| A7-16 | 显式登录/注册与 /me 恢复、自动登录失败               | Assembly 只在成功显式认证后一次；刷新/QQ check 不播；失败无成功动画；视觉层完成即移除                    | Auth 组件 + E2E              |
| A7-17 | MORPH/FLOW/SETTLE 快速打断                           | 包围盒稳定、对象选择正确、点击不被动画锁；无重复命令；Task 键盘上下文不被重排抢走                        | 组件 + Chromium              |
| A7-18 | reduced-motion 初始/运行时切换                       | 粒子/大位移/平滑滚动关闭，WAAPI 取消；功能、焦点、状态、请求数相同                                       | media 模拟 + 人工            |
| A7-19 | 四类 Tabs、Rail/目录/表格键盘                        | 名称/关联/roving tabindex/方向与 Home/End 正确；目录遵守选定原生语义；焦点可见不被遮挡                   | E2E 键盘 + 读屏              |
| A7-20 | 就地错误、状态播报、对比度与触摸                     | 表单关联错误；不逐 token 播报；适用色对≥4.5:1/3:1；主要触摸目标≥44px                                     | DOM/几何 + 测量 + 人工       |
| A7-21 | StrictMode、AI→空间→任务→AI、Host 开关               | 同一 Run 至多一条 Chat feed；Work feed≤2；Artifact 每版一个 poller；允许计划内轮询，禁止转场触发冗余 GET | App 生命周期 + 请求/连接记录 |
| A7-22 | 注销/401/账户 A→B→A 与在途动画/GET/POST              | 清 UI 标记/焦点记录、Timer、Blob URL、草稿；迟到响应不回灌；普通业务 403 不注销                          | sessionIsolation + 集成      |
| A7-23 | WorkspacePanel 原测试语义迁移、文件操作              | scope fence、候选取消/分页/404/queued→running、partial success、impact/CAS 保持；保存不自动授权          | 正式组件/操作层 + UI-4 E2E   |
| A7-24 | 任务状态/控制/投递/Research 与通知                   | M01～M21 不变；pausing/stopping 待决入口、报告/发布文件/通知完整；渠道接受不称已读                       | UI-5 回归 + 受影响契约       |
| A7-25 | Artifact 全链与 UI-6 自检回归                        | 默认最近成功与手选保护、多版本收敛、sandbox/source、历史版下载、源码副本保存；手工 v2 不替代原 Task 交付 | UI-6 全套定向 + E2E          |
| A7-26 | CSS/配置和最终文档                                   | 无旧页壳活跃选择器/切换开关；tokens 单一来源；各项结果和未覆盖分别记录；无 P0/P1                         | 静态 + 全前端检查 + 报告     |

布局至少覆盖 360×800、390×844、768×1024、1024×768、1280×800、1440×900、1920×1080。补边界宽度与窄高/横向窗口；截图包括四类 Inspector 打开/关闭、error/empty/长标题、扩大阅读、父子返回、上层保存和 reduced-motion。不能用全局 overflow:hidden 掩盖不可达内容来满足几何检查。

“不重复 mount/fetch”用计数而非截图证明：记录 runtime 实例、控制器挂载、活动 feed、timer/poller 及关键 GET/POST。Inspector 首次按需读取、切对象/高级 tab 的必要 GET、显式刷新和既有定时轮询属于允许请求；仅动画、扩大宽度、resize、暂挂恢复不应产生冗余请求。StrictMode 开发重放与普通生产交互分别记录，要求活跃资源单实例和去重，不把 StrictMode 的所有 effect 调用次数机械定为 1。

### 8.1 已有回归基础

- `App.test.tsx` / `App.recovery.test.tsx`：同对话重选、草稿/runtime/订阅、对象 lookup 与账户隔离。
- `store/shell.test.ts` / `sessionIsolation.test.ts`：旧 hash、有限返回栈、tab/version 与 Trace 选择/请求隔离。
- `components/run/RunInspector.test.tsx`、`store/runInspector.test.ts`、`traceStore.test.ts`、`TraceDetail.test.tsx`：独立 Work Run、懒加载、轮询可见性、Model Input。
- `components/workspace/*`、`store/workspace.test.ts`：上传/保存恢复、版本 CAS、对象查询和自检修复。迁移 WorkspacePanel.test.tsx 后保留同级语义。
- `components/tasks/*`、`store/works.test.ts`：四桶、创建修订、预算投递、资料/输出/通知与自检修复。
- `store/artifacts.test.ts` / `UI6SelfReview.test.ts`、`components/artifact/*`、`ArtifactPreview.test.tsx`：UI-6 原能力和全部自检修复。
- `sse/*`、`store/workbench.test.ts`、`HpThread.ui2.test.tsx`：去重、断线/终态恢复、流式、附件与 IME。

上述是存在的测试入口，不表示本次执行通过。新增测试重点是能力丢失、旧状态去除后的竞态、稳定 Host 和前景层行为，避免只验证 CSS 字符串或复刻实现内部细节。

### 8.2 后续执行命令与环境

先跑改动相关 Vitest，再全套前端检查；以下为未来实施命令，本次未执行：

```bash
cd /home/hp/workspace/HpAgent_web/web
npm test -- src/store/shell.test.ts src/store/sessionIsolation.test.ts src/App.recovery.test.tsx src/components/run/RunInspector.test.tsx src/components/trace/traceStore.test.ts
# 随工作包加入正式创建表单/RunLookup/Surface、Workspace 迁移、Task、Artifact 定向测试。
npm run typecheck
npm run lint
npm run build
npm test
```

按 [测试指南](../development/testing.md) 先配置专用可丢弃测试数据库、migration/API/worker 三角色 DSN、独立 Redis DB、文件目录和端口。Playwright 启动真实 API / PostgreSQL / Redis 和 Fake Executor，保持 workers=1，不并行同账号。测试 fixture 可能清表，不能沿用共享业务库默认配置。

```bash
cd /home/hp/workspace/HpAgent_web/web
npm run test:e2e -- e2e/auth.spec.ts e2e/a11y.spec.ts e2e/multi-tab.spec.ts e2e/disconnect.spec.ts e2e/stop-retry.spec.ts e2e/ui-2-ai.spec.ts e2e/ui-3-execution.spec.ts e2e/ui-4-workspace.spec.ts e2e/ui-5-tasks.spec.ts e2e/ui-5-recovery.spec.ts e2e/artifact.spec.ts e2e/ui-6-artifact.spec.ts e2e/ui-6-self-review.spec.ts
# ui-7-shell / ui-7-responsive / ui-7-a11y 建立后单独加入，同样串行。
```

同时根据入口迁移复验 `conversation.spec.ts`、`markdown.spec.ts`、`manual-repair.spec.ts`、`workspace-direct-upload.spec.ts`、`workspace-p1.spec.ts`、`workspace-p3.spec.ts`；不能仅修 locator 使测试变绿，却删除业务断言。若触碰任务验收、版本/授权操作或 DTO 请求组装，补跑对应真实后端契约用例（Work foundation/integration、Workspace v4.1、Artifact），不因只改外观默认全跑后端，也不以 Fake Executor 代替真实事务验证。

长命令依用户 AGENTS.md 使用长异步等待：无中间输出需求的空 write_stdin / functions.wait ≥180000ms，数分钟任务优先 300000ms；外层 functions.exec timeout 长于嵌套等待；完成即返回，不为报告“仍在运行”反复轮询。

真实软键盘、200% 文本全流程、键盘/读屏和色对测量独立记录。缺真机或读屏环境时继续完成其它验证，但相关 A7 项标记部分覆盖/未覆盖，不声明完整无障碍或完整 UI-7 验收。

## 9. 交付、退出门槛与回退

实施后交付建议：

```text
docs/implementation/ui-7-unification-cleanup-report.md
artifacts/product-acceptance/ui-7/
  README.md                  # HEAD、环境、A7 映射与覆盖边界
  logs/                      # 命令/退出码、基线失败、修复及复验
  screenshots/               # 尺寸、对象、状态与动效偏好明确
  flows/                     # 成功、失败恢复、并发/迟到响应
  migration-map.md           # 旧能力→新入口→验证，组件/符号/CSS 删除清单
```

完整退出必须同时满足：

1. 三入口及所有原能力可达；TestPages、ArtifactPanel、孤立旧壳和导航 bridge 已清理，Trace 有效内容与 RunStatus 仍可用；旧 hash 兼容通过。
2. Shell 管理唯一 Inspector；历史对象不被聊天/版本完成抢走；父子返回、合法 tab/version 和跨账户隔离通过。
3. 稳定 Host/Sidebar、单一活动 modal、Esc/焦点/草稿/保存恢复正确；断点/动效不产生额外业务 mount/feed/fetch。
4. tokens 与有效样式统一，七视口/长内容/扩大阅读/200% 文本和键盘场景无页面溢出或遮挡；真实软键盘与读屏覆盖状态明确。
5. 四类动效按规范且可打断；减少动效功能和焦点一致；Assembly 来源正确、不接触敏感表单、完成即清理。
6. A7-01～A7-26 各有通过/失败/部分覆盖/未覆盖、对应证据和环境；完整前端检查与受影响 E2E 通过，无未解决 P0/P1。任一必验项未达不得标记完整验收通过。
7. FE-B1/FE-B3 明确回归；UI-5 M01～M21、FE-B4/FE-B5 和 UI-6 自检保护保留；新增后端依赖仍为独立 BE 项。

UI-8 接收完整产品端到端能力矩阵、长会话/大量文件与任务的综合性能、真实模型/Temporal/渠道验证和最终发布交接。UI-7 报告区分静态检查、Vitest、真实 API/Fake Executor 浏览器、真实后端事务与人工验证，既有通过计数不叠加为本批结果。

回退按本阶段前端提交/工作包处理，依次回退 Motion、布局/primitive、状态迁移与旧壳删除，保证恢复后的入口与 store 版本匹配。旧壳在 Git 历史中恢复，不保留可发布双 Shell 开关。不清空服务器对象、不反改 Work 状态、不回滚用户文件；本阶段不需要数据库迁移。若回退时恢复旧 store API，必须同步恢复相应调用点，不能留下混合状态权威。
