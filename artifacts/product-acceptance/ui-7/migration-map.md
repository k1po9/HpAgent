# UI-7 能力与删除映射

基线：e6791e3。删除前复核生产 import；正式入口均复用 UI-3～UI-6 已有领域操作，不改变 API/状态机。

| 删除内容 | 正式入口 | 本轮证据 |
| --- | --- | --- |
| TestPages.ArtifactsPage | AI Header → ArtifactCreateForm；消息行 ArtifactMessageItems | ArtifactCreateForm.test.tsx（初始要求、busy 单提交、来源、关闭/导航迟到隔离、恢复/错误）；ui-7-unification.spec.ts（真实 POST instruction）及 UI-6 回归 |
| ArtifactPanel | InspectorHost → ArtifactInspector / ArtifactComposer / ArtifactPreview | UI-6 单元与浏览器回归；UI-7 七视口、断点草稿/iframe/查询计数 |
| TestPages.DiagnosticsPage | ExecutionBlock、TaskOutputs、RunInspector 高级诊断；AI Header 与 Task 高级详情 RunLookup | RunLookup.test.tsx（服务端核实、Work 来源、A→B→A/同 ID 重查/退出、403/404）；RunInspector/Trace 既有回归与 UI-7 浏览器查询 |
| TestPages.SaveWorkspaceDialog 重导出 | AppShell 直接 import SaveToWorkspaceDialog | 保存命令/组件、Workspace / Artifact 浏览器回归 |
| WorkPanel / WorkManagement.WorkCreateForm | TaskScreen / TaskInspector / TaskActions / TaskEditor / TaskBudgetDialog / TaskDeliveryDecision | taskPresentation、TaskIntegration、TaskSelfReview、UI-5 tasks/recovery 浏览器回归；要求 payload 不改 |
| WorkManagement.WorkResourcePanel | TaskResources / WorkInputs / GrantEditor | UI-4/5 授权回归；Conversation 与 Work 主体分离 |
| ResearchOutputs | TaskInspector 成果页 → Research 报告与已发布输出 | TaskIntegration.test.tsx、UI-5 recovery：Markdown、文件独立重试；保留最多 100 次执行限制 |
| WorkspacePanel | WorkspaceScreen / WorkspaceSidebar / FileInspector / ResourceManager / RunResources | 下表八项；UI-4、direct-upload、workspace-p1/p3、manual-repair |
| LegacyUtilities.NotificationInbox | Task Header → TaskInbox | TaskIntegration.test.tsx 通知 API、错误、分页；UI-7 移动收件箱暂挂原任务 |
| LegacyUtilities.EmptySelection | ChatPane / Composer 正式空态 | App / App.recovery / HpThread.ui2 回归与 UI-2 浏览器 |
| TracePanel 侧板外壳 | RunTraceContent（只在 Run 高级诊断挂载） | traceStore / TraceDetail / RunInspector 回归；保留节点、详情、Model Input 与缓冲/请求 generation |
| artifacts.openArtifact / deps.onOpen / Shell import | 组件向 Shell 发导航意图 | 静态扫描；artifacts.test.ts 断言领域 store 无导航 API |
| traceStore.open / setOpen / onOpen / Shell import | 显式诊断选择生命周期、独立 followRun | sessionIsolation 与 workbench/traceStore 历史选择保护；Shell 是显示与导航权威 |

RunStatus、TraceTree、TraceDetail、ModelInputView、ArtifactPreview、QQBindingPanel 和 RegistrationQqGate 保留。QQ 引导接入统一 Surface，与业务层共用 modal 协调；注册及明确跳过路径保持。

## WorkspacePanel 八项测试语义迁移

| 原语义 | 正式回归 |
| --- | --- |
| 取消后迟到候选/失败不回灌，版本 Run 仍可读取已发布输出 | RunResources.test.tsx migrated cancellation；WorkspaceCommands.test.tsx published outputs 对象隔离/真实保存 |
| 离开 running Run 后旧候选不恢复 | RunResources 分页/离开/A→B→A 反例与取消反例；卸载使 token 失效 |
| 授权 A→B→A 隔离 | WorkspaceMigration.test.tsx GrantEditor 主体隔离/账户 reset；UI4SelfReview.test.tsx 并发共享 flight、A→B→A 与账户世代 |
| 未知活动 Run 404 可见 | RunResources.test.tsx unknown active Run 404，不将拒绝视为空 |
| 同 Run queued→running 再查候选 | RunResources.test.tsx migrated queued→running；RunInspector 传真实 run.status |
| 迟到候选分页在切对象后失效 | RunResources.test.tsx 分页重复提交去重、晚旧页失败与 A→B→A；正式候选的 scope 是选中 Run，切对话关闭旧 Host 后请求失效 |
| 当前目录创建、名称独立、所选子目录刷新保持 | WorkspaceMigration.test.tsx + manual-repair / UI-4 E2E；按 UI-4 正式交互创建后显式进入子目录，刷新保留 URL 中目录 |
| 上传保存成功、授权失败仅补授权 | WorkspaceMigration.test.tsx 与 workspaceOperations.test.ts；direct-upload / manual-repair 真实 API 回归；upload/save 各一次、grant 两次 |

旧 hash 兼容保留在 shell.parseRoute。无明确对象上下文的旧成果/诊断 hash 返回 AI；AppShell 不再从 Trace 直播缓存猜对象。

## 样式与配置

styles.css 保留单一导入入口：tokens → base → shell → conversation → run → workspace → tasks → artifact → unification。
语义色、间距、字号、尺寸、focus 与动效时长集中到 tokens.css；普通标题/正文用 rem 支持文本放大，状态及 Radix 色仍保留语义。
清理 hp-test-*、hp-page-*、旧 Work/Research/Workspace 外壳、Artifact/Trace 固定侧板宽度及旧媒体规则；额外逐选择器扫描清单见 logs/deleted-css-selectors.txt。共享 hp-operation-form、Markdown、code、iframe、Trace 节点/详情不删除。
仓库未发现旧/新 Shell 发布切换开关；未新增第四入口、路由库、查询缓存或 Motion 框架。
