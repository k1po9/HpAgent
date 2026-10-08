# UI-1～UI-8 前端重构交接

更新于 2026-10-09（Asia/Shanghai）。UI-1～UI-7 已实现并提交；UI-8 的综合验收、定点修复及复验代码随本次文档整理提交。**现有自动化门禁在各自记录范围内通过，UI-8 仍未满足最终产品放行条件。** 当前契约见[前端架构](../architecture/web-ui.md)，操作见[功能指南](../operations/web-workbench.md)，复验见[测试指南](../development/testing.md)，原始数据见[证据索引](../../artifacts/product-acceptance/ui-refactor/README.md)。

## 依据与提交沿革

产品输入为《HpAgent_前端_UI_重构方案_v1.0.md》（2026-10-06），SHA-256 `debffa18de54203fa2e7037108b9f0b3f736e6b350516a9c96fd87590217f892`；另核对用户提供的 UI-1 验收与自审材料。附件的阶段操作措辞是设计/历史输入，当前授权范围由用户请求决定。D01～D10 的三入口、Pre-Shell、四区 Shell、唯一 Inspector、HTML Artifact、确定性 Task 投影和保存/授权/接受分离已落实到当前架构；剩余 BE 增强独立排期。

| 阶段 | 交付与验证重点 | 已有提交 |
| --- | --- | --- |
| UI-1 | Shell、路由、账户与统一 session reset/dispose、迟到响应隔离；初期模拟回归 104 项。 | `cc40862` |
| UI-2 | 对话/消息分页、标题、草稿与锚点、IME、模式、附件/授权、冻结发送意图与按对话提交锁。 | `325a5d7` |
| UI-3 | 消息执行块、Run Inspector、审批、Trace/Model Input 按需诊断；终态输出与权限失效补修。 | `d577c36`、`40a318a` |
| UI-4 | 目录/搜索、File Inspector、共享授权、上传保存恢复、impact 和版本 CAS。 | `be57e0b` |
| UI-5 | 四桶及 M01～M21、完整分页、创建/修订、预算/投递、Research 与局部收件箱。 | `9560c99` |
| UI-6 | HTML 上下文预览、版本/修改、轮询恢复、下载、源码保存和精确 Task 交付边界。 | `e6791e3` |
| UI-7 | 删除旧壳/bridge/CSS、补齐既有能力入口、稳定 Surface/焦点、tokens 与响应式/动效统一。 | `41ff9a9` |
| UI-8 | 全能力矩阵、真实后端状态链、性能观测、并发/取消/SSE/权限修复、059 迁移与交接。 | 本次提交，父提交 `41ff9a9`。 |

表内早期验证只是阶段历史；当前可验证范围以下方最终批次及正式测试为准，不能累加阶段次数。完整提交 ID 由 Git 历史查询。

## 已关闭的自审缺陷

| 阶段 | 已修复的行为 | 正式回归入口 |
| --- | --- | --- |
| UI-2 R1～R3 | 撤权响应丢失后空规则读回不崩溃；切主体后 busy 收尾不污染新操作；A 在途不阻止 B 发送。 | ConversationResources、HpThread.ui2、store/ui2。 |
| UI-3 R1～R3 | Run 终态刷新输出；明确权限拒绝清正文缓存/拒绝迟到回灌；Trace 隐藏恢复不重复定时链。 | RunInspector、RunResources、traceStore。 |
| UI-4 R1～R4 | 同 key 并发订阅共享一次查询；版本未知结果关闭重开仍保留原意图；撤权读回失败仍执行全局失效；合法根目录不误报。 | UI4SelfReview、WorkspaceMigration、workspaceOperations。 |
| UI-5 R1～R3 | 输出缓存因终态失效；异常快照成果页可安全阅读；要求提交重试保留原草稿。 | TaskSelfReview、TaskIntegration、ui-5-recovery。 |
| UI-6 R1～R3 | 逐版本乱序合并；消息行恢复轮询；默认版本仅由本次成功加载初始化且尊重明确手选。 | UI6SelfReview、UI6InspectorSelfReview、ui-6-self-review。 |
| UI-7 R1～R3 | 迟到 RunLookup 不关闭后开的前景；原生 summary/iframe/fieldset 的 Tab 边界；普通聊天终态不额外查 Trace。 | RunLookup、Surface、traceStore、ui-7-self-review。 |
| UI-8 R8-01 | A→B→A 时，迟到成功 POST 重新同步仍选中的发送目标；旧空查询、切对话/账户不覆盖新状态。 | store/ui2、ui-2-ai，并保留修复前网络与反例。 |

历史反例见证据 `history/` 和 `initial/logs/send-race-before.log`；行为测试保留在源码，已删除只用于临时复现的测试文本副本。

## UI-8 后端与验收补修

| 工作包 / 原问题 | 根因与最终修改 | 验证范围 |
| --- | --- | --- |
| F8-01 / B8-05、B8-07 | 测试 lifecycle Worker 缺真实策略 Activity；Research 还保留旧 Workflow、Start 方法及 ID。复用 `test/support/lifecycle_workers.py` 注册真实控制 Activity，初始及替换 Worker 都执行 canonical `AgentLifecycleWorkflow`。修正实际账户/Run/Execution/attempt scope 路径、版本化撤权 DTO，以及 inbox/QQ 独立目标和显式重试决议。 | 两策略成功/失败/取消、Web/QQ 冻结 source/context、真实 Brain 进入、失败原因、History replay、开始丢确认/重复投递、撤权与取消重投、Work/Chat 并行、重启后 Reconciler 幂等。 |
| F8-02 / B8-01、B8-03 | Run fixture 补 `source_kind`，验证投影不原地修改 CommandResult，并新增 Work 分支。旧 Work terminal Outbox fixture 补事件类型、Work/Run 归属和 payload；补齐后发现 Publisher 会尝试读取 Chat snapshot，现对有 `work_id` 或无 Conversation 的旧终态行只标记消费。 | ready/pending 下载链接、Chat/Work 来源；独立及关联 Chat 的 Work 旧终态行不读取 Chat snapshot、不发布 Chat SSE；正常 `publish_work_event` 与 Chat terminal 契约回归。 |
| F8-03 / B8-04 | 原在线用例依赖 0.05 秒延迟，改为有界屏障，用 Redis NUMSUB 确认订阅再释放执行器。另用确定性反例证明 Gateway 先快照后订阅会丢失握手期间终态；改为订阅/缓冲后读取快照，保持 snapshot 为第一帧。 | started/progress/delta 的同 stream、连续 seq、正确 Message；握手期间完成的旧快照收敛，终态快照丢弃缓冲 delta；晚订阅、退化、gap、溢出、权限/断线恢复。 |
| F8-04 / B8-02 | 最短导入链为 `web_api.app → fake_executor → web_artifacts.build → agent_activities.__init__ → runtime → temporalio`。API 不调用 Temporal Client；runtime.md 明确 Dispatcher/Worker 执行职责。将 Build/Generator 导入移到启用 Fake Artifact Executor 的构造路径。另修正 Workspace 异常诊断 nullable 字段的静态类型问题。 | 保留原禁止 `temporalio`/Agent/Sandbox 的独立进程检查，同时禁止网络连接和后台任务启动；API 启停、命令及 Artifact 浏览器流程；8 个 Workflow sandbox prepare。 |
| F8-05 / B8-06 | Worker 发布 Research 需要 INSERT artifacts/versions，旧禁止断言不适用；API admission 只 INSERT queued version，没有 UPDATE artifact_versions 的调用路径。新增 **059** 撤销 API 的 UPDATE；001–058 原内容与 checksum 不变。补验发现已取消生产 Run 的版本仍 running，修复 Worker 终态事务中的版本收敛。 | 真实三角色、有效关联数据上的必要创建成功、禁止 UPDATE/DELETE 的 SQLSTATE 42501、账户错误 FK 23503、业务入口跨账户 404、不可变 provenance/result 的 P0001、构建/失败重试/取消与再次推进/Work 采用和接受证据；全新库及 058→059 升级。 |
| F8-06 / B8-08 | 方法返回观察到的权威状态，允许取消 finalizer 尚未执行时返回 `cancelling`。旧断言错误要求每次即时返回同一终态；另复现 `complete` 锁前读取 running、拿锁时已 cancelling 而抛出冲突的产品竞态。将成功资格检查移入权威行锁内。 | 取消已提交后完成/取消 finalize 两顺序，running 下两种锁顺序；最终 Run 为 succeeded/cancelled，Message 内容匹配、终态 Outbox 唯一、重复 finalize 和迟到完成不改写终态。 |

额外 A8-F01：取消生产 Run 后版本仍 running，会导致 Artifact poller 持续轮询和 Composer 禁用；Worker 终态事务修复收敛，queued/running 取消、重复 finalizer、迟到生成和再次推进均有正式反例。A8-T01：浏览器在 1024→1280px 后过早量测仍为 modal 的 Inspector；用例现在等待响应式语义和原生 `:modal` 同时达到断点再检查原几何断言，没有修改布局或扩大 timeout。

2026-10-09 复验补修 Work 时间约束：事务 `now()` 可能早于已有行时间，修订、控制、准入、完成及投递写入的 updated_at/终态时间钳到不早于 created_at 和原 updated_at；未来时间戳反例已加入 `test/work_domain/test_foundation.py`。Reminder 投递领取改为最长 3 秒有界等待，仍须拿到真实 fulfillment 行。

## 最终批次与证据边界

| 批次 | 前端 | 浏览器 | 后端 | 结论与证据 |
| --- | --- | --- | --- | --- |
| 2026-10-08 初始综合 | typecheck/lint/build、54 文件 408 项通过 | 完整 Chromium 76；生产 dist 2 通过 | 197 项中 182 通过、15 失败 | 历史失败定位；[initial](../../artifacts/product-acceptance/ui-refactor/README.md#初始综合验收)。 |
| 2026-10-08 完整修复 | typecheck/lint/build、54 文件 408 项通过 | 完整 Chromium 76，生产 dist 2；无失败/跳过/flaky | 206 通过、0 失败/错误/跳过；原 197 保留、新增 9 | B8-01～B8-08 关闭；[fixes](../../artifacts/product-acceptance/ui-refactor/README.md#完整修复批次)。 |
| 2026-10-09 当前模型与非 QQ/手机复验 | typecheck/lint/build、54 文件 408 项通过 | 桌面 Chromium 13，生产 dist 1；无失败 | 200 通过、7 QQ/NapCat 按范围排除；新增未来时间戳回归 | [recheck](../../artifacts/product-acceptance/ui-refactor/README.md#当前模型与范围复验)。 |

完整 Chromium 与生产 dist 场景重叠，后端定向结果也包含在完整集合中；不可相加。10-09 的 13 项桌面集合不等于重跑 76 项全浏览器，200/7 也不替代 10-08 的完整后端范围。每份原始命令、退出码、JUnit/Playwright 结果和环境元数据按批次保留，不能把目录存在视为通过。

完整修复后端使用真实 PostgreSQL 三角色、Redis 和 Temporal，Brain/工具为受控实现；浏览器使用真实 API/PG/Redis/文件服务及 Fake Run/Artifact executor。API import 与 8 个 Workflow sandbox prepare 共 34 项通过；当时受影响 Python Ruff 与所选 5 个文件的 Mypy 通过。新库 001～059 和 058→059 升级保留旧 checksum。以上仅覆盖指定集合，未宣称全仓库 Python 测试通过。

10-09 模型复验从同配置一次性 Worker 容器，经当前 ModelClient 完成两次合成 echo 工具调用，第三次返回 OK，4.16 秒；供应商边界通过。10-08 的无 HTTP 响应保留为历史故障。此项没有走真实业务 Run Activity，不等于业务多轮工具链验收完成。

## 能力矩阵与复验入口

下表收敛原 A8-01～A8-28；具体成功范围须结合上述批次和剩余验收，不能仅根据测试文件存在判断通过。

| 编号 | 方案要求 / 产品能力 | 当前入口与实现 | 复验入口 |
| --- | --- | --- | --- |
| A8-01 | D01–D04：三入口、默认 AI、身份确认前不展示业务 | App / AppShell / shell.parseRoute；头像账户 | App、App.recovery、shell、LoginForm、auth / a11y E2E |
| A8-02 | 旧 hash 与深链、刷新、返回、不可用对象 | Shell 集中解析；单对象 Inspector 查询 | shell、RunLookup、RunInspector、FileInspector、Task / Artifact E2E |
| A8-03 | D03/D07/FE-B3：一个 Inspector，父子返回与前景层 | InspectorHost / Surface / Shell UI store | Surface、useInspectorFlow、UI-7 unification / self-review |
| A8-04 | FE-B1：401/退出、账户更换、迟到查询隔离 | sessionLifecycle 统一 reset/dispose 与 generation | sessionIsolation、client、UI-8 订阅/定时器/Blob 退出测量 |
| A8-05 | 对话标题与历史/消息分页、内存草稿 | ConversationSidebar / ConversationHeader / workbench | Sidebar、ConversationHeader、ui2、conversation / UI-2 E2E |
| A8-06 | Composer、IME、快速/深度、附件、输出下载 | HpThread adapter / ChatPane，单一消息来源 | HpThread.ui2、workbench、UI-2、markdown、conversation E2E |
| A8-07 | 发送意图同 key、响应丢失与 A→B→A | Workbench 意图缓存、权威查询重新同步 | ui2、workbench、UI-2 丢响应/并发 E2E，修复前后反例 |
| A8-08 | 真实执行、取消/重试、终态覆盖 | ExecutionBlock / runFeed / polling | RunStatus、ExecutionBlock、runFeed、sseClient、stop-retry / disconnect |
| A8-09 | Trace / Model Input 历史对象、可见范围与权限拒绝 | Run Inspector 高级诊断按需选择 | traceStore、TraceDetail、workbench.trace、UI-3/7；后端 model_observability |
| A8-10 | 空间树、目录、搜索/分页、文件/来源/保留信息 | WorkspaceScreen / Sidebar / FileInspector | workspace、WorkspaceScale、WorkspaceCommands、UI-4 / workspace E2E |
| A8-11 | D09：保存和授权分离、部分成功补救 | workspaceOperations / ResourceManager / GrantEditor | WorkspaceMigration、UI4SelfReview、direct-upload、manual-repair |
| A8-12 | owner≠Agent、递归、候选冻结、撤权 | 明确 Conversation / Work 主体；RunResources | RunResources、workspace API/PG、真实 Temporal 撤权工具测试 |
| A8-13 | impact/CAS、冲突保留输入、文件版本 | FileInspector / Workspace commands | FileInspector、WorkspaceCommands、UI4SelfReview、workspace-p3、PG p3 |
| A8-14 | T01–T19、M01–M21：四桶统一且动作独立 | taskPresentation，列表/详情/计数统一消费 | taskPresentation 全表驱动；TaskIntegration / TaskSelfReview / UI-5 |
| A8-15 | FE-B2/FE-B6：完整扫描、范围计数、深链 | Works 分页与单对象 upsert；Task controller | works、useTaskController、useTaskQuery，51st task E2E |
| A8-16 | 暂停/恢复/停止/修订/推进、版本门禁 | TaskActions / TaskEditor | taskOperations / taskRequirement、UI-5，work_foundation / work_domain |
| A8-17 | 模型预算口径、只增、CAS | TaskBudgetDialog / Task Inspector | taskPresentation、TaskIntegration、UI-5、work_integration |
| A8-18 | FE-B4：收敛中仍有未决事实与可用投递决议 | TaskDeliveryDecision / attentionReasons | M03/M04/M14/M20、UI-5 fixtures、PG work_integration |
| A8-19 | 三种投递 outcome、当前/历史、accepted≠已读 | 具体 Delivery 的风险确认与服务端最终裁决 | taskOperations / TaskIntegration、UI-5、work_integration |
| A8-20 | Research 报告、证据、输出下载、收件箱 | Task 成果与执行 / TaskInbox | TaskIntegration / TaskSelfReview、UI-5 recovery、research API |
| A8-21 | D05/D08：HTML、构建状态/失败/恢复、轮询去重 | Artifact Inspector / artifacts / ArtifactMessageItems | artifacts、UI6SelfReview、UI6InspectorSelfReview、UI-6 / artifact E2E |
| A8-22 | 历史选择不被抢占、最新成功 parent、修改幂等 | ArtifactComposer / artifactUi | ArtifactInspector、ArtifactMessageItems、UI-6 真实 API 版本恢复 |
| A8-23 | FE-B5：手工新版不替代原 Task 交付 | TaskOutputs / TaskActions + Artifact origin | TaskIntegration、UI-6 fixture 原 v1 / 手工 v2、PG work_integration |
| A8-24 | sandbox、下载释放、冻结源码保存、同名恢复 | ArtifactPreview / artifactDownloads / SaveToWorkspaceDialog | ArtifactPreview、artifactDownloads、workspaceOperations、UI-6 与 UI-8 真实链 |
| A8-25 | 七视口、长标题、扩大阅读、200% 文本、reduced-motion | CSS tokens / Shell / Surface / Assembly | UI-2/4/5/6/7 截图与 geometry；Assembly / Surface 单测 |
| A8-26 | 键盘、Tabs、summary/iframe、Esc 层级、焦点返回 | Surface / tabOrder / InspectorHost | UI-7 self-review 12 项、UI-3/4/5/6、a11y |
| A8-27 | 不重复 runtime/feed、最多两条 Work、退出释放 | Shell 会话层 controller，领域 store 监控 | App / App.recovery 的 StrictMode runtime 与草稿集成；UI-8 真 API 测量：Chat peak 1 / Work peak 2，退出归零 |
| A8-28 | 性能与交接、D10 既有能力和 BE 边界 | 生产构建 preview、日志、操作指南与增强列表 | 本轮生产构建 UI-8 spec、构建体积/长任务测量、文档链接和静态扫描 |

## 性能与资源

初始生产构建主 JS 838,462 字节、CSS 717,413 字节，保留 >500 kB chunk 提示。两个本机 Chromium 生产样本 DOMContentLoaded 约 786/342 ms，操作链长任务 12/10 次，最长 235/247 ms；无约定性能预算，不宣称 p95 达标。原始样本见 `initial/logs/build-assets.json` 与 `production-performance.json`。

最终生产观测保留在 `fixes/production/`：Chat feed 峰值 1、Work 峰值 2；退出后被观测的 SSE、≥100 ms timeout、应用 interval、Blob URL 归零。范围不覆盖全部 iframe、短 timer、堆内存或所有 runtime 实例；StrictMode 单 runtime 与草稿另由 App 集成回归验证。七视口、扩大阅读、200% 模拟文本、reduced-motion 与四种 Inspector 的最后完整截图/测量统一存放 `fixes/regression-evidence/`。

## 尚未闭合的放行门槛

| 项目 | 当前状态 |
| --- | --- |
| 手机真机中文 IME、软键盘、非零 safe-area、横屏/窄高 | 未验证；Chromium 模拟与 200% 注入截图不能代替真机。 |
| 实际屏幕阅读器、全流程键盘与完整状态对比度/200% 文本矩阵 | 未验证；局部键盘/命名/色对测量通过不代表全产品认证。 |
| 真实模型业务 Run 的多轮工具往返 | provider boundary 已通过，真实 Run Activity 仍未验证。 |
| 真实 QQ 收发、回执与 uncertain 决议 | 受控 adapter 有证据；没有隔离真实目标的验证。10-09 按范围排除 QQ/NapCat。 |
| 生产 lease/timeout 的崩溃接管与去重 | 未验证；受控取消/Reconciler/短租约证据不能替代默认生产恢复。Research 已知限制见[实施索引](README.md)。 |
| 业务环境迁移与部署 | 本轮仅提交，未部署或迁移业务库。10-09 复验记录曾观察业务库停在 058、代码要求 059，容器重启；这是当时观测，不能作为实时健康状态。 |

## 独立后续增强

| 标签 | 独立需求 | 当前可交付边界 |
| --- | --- | --- |
| BE-W1 / P1 | 文件有效主体授权反查 | 管理明确选中主体；历史使用独立展示，不遍历全账户伪造反查 |
| BE-AW1 / P1 | Artifact 版本的一等空间保存与 provenance | 显式保存 `.html.txt` 源码副本，不自动授权 |
| BE-W2 / P2 | 聚合 File Inspector 与明确定义更新时间 | 分 Tab 组合现有查询，不伪造更新时间 |
| BE-W3 / P2 | 目录级 cursor / 大规模树分页 | 现有全树和搜索 cursor；已有规模测试单独解释 |
| BE-W4 / P2 | 回收站与恢复身份语义 | 当前移除空间入口，无恢复按钮 |
| BE-W5 / P2 | 图片 MIME 与预览 | 当前白名单文本与 PDF/Office 文档 |
| BE-T1 / P2 | 每周/自定义周期 | 立即、单次、每天与明确时区 |
| BE-T2 / P2 | 完整历史 requirement / Run cursor | 修订事件摘要、当前要求、最近最多 100 次执行 |
| BE-T3 / P2 | 服务端桶/计数/最新执行与审批聚合 | 串行全页扫描、明确已加载范围；审批位于具体 Run |
| BE-X1 / P1 | 通用未决 operation 人工对账 | 具体 Delivery 决议可达；其他 operation 仅核查与诊断 |
| BE-A1 / P3 | 任意历史 parent 分支 | 后端以最近成功版本为 parent |
| BE-A2 / P2 | 手工迭代版替换原 Task 交付 | 原 Task 接受精确交付证据，新 HTML 不自动替换 |

## 升级、回退和文档整理

UI-8 包含新增 `059_artifact_api_permissions.sql`；001～058 未修改。059 收窄 API UPDATE，无数据搬迁。发布前走正常 migration 与应用发布流程，核对 API ready / Worker 健康；步骤见[部署](../operations/deployment.md)。回退应用不删除 schema history 或用户对象、不反向执行 057；若将来需要恢复权限，需说明合法路径并用新的前向迁移。

本次把阶段计划、报告、自审/修复指南合并为本交接与当前前端架构，保留关键反例、最终日志/环境/指纹和最后完整截图。删除 UI-2～UI-7 重复截图、多轮中间输出、临时复现源码及依赖本机容器名的一次性验收编排脚本。历史内容仍可从既有提交恢复，当前复验使用正式测试和测试指南。Durable Work、Workspace 与更早产品验收不在本次 UI 清理范围。

保留证据逐文件迁移来源与 SHA-256 见 [retained-evidence.json](../../artifacts/product-acceptance/ui-refactor/retained-evidence.json)。历史日志/audit 中的原路径和“原证据保留”描述表示当时审计，不应解释为清理后原目录仍存在；原始结果没有改写。本次文档/清理检查另存 `docs-sync/`，不混入旧批次计数。

本次整理重新运行 typecheck、lint、build 与完整 Vitest（54 文件、408 项），全部通过；受影响 Python 文件 Ruff 通过。扩大到 11 个服务文件的 Mypy 发现 `src/delivery/service.py:310–313` 的 4 个既有 nullable 索引错误；原 HEAD 副本独立检查也复现同样 4 项。本次没有扩大修改范围来修复这些既有问题，完整 Mypy 不标为通过。文档链接、证据哈希、JUnit 解析和 diff 格式检查结果见[整理检查记录](../../artifacts/product-acceptance/ui-refactor/docs-sync/validation.json)。
