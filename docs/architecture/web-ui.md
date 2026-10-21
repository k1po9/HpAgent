# Web 前端架构与交互契约

本文维护当前产品与交互约束。操作见[功能指南](../operations/web-workbench.md)，环境与覆盖边界见[测试指南](../development/testing.md)。

## 入口、布局和路由

身份恢复由 `App` / `LoginForm` 处理；checking、error 和 signedOut 阶段不挂载业务内容。认证后 `AppShell` 固定为 AppRail、ContextSidebar、MainCanvas、可选 Inspector 四区。

| 一级入口 | 路由与上下文 | 主要能力 |
| --- | --- | --- |
| AI | `#/ai`、`#/ai/{conversationId}` | 对话、消息、附件、快速/深度策略、执行块、资料授权和 HTML 生成。 |
| 空间 | `#/workspace?dir={nodeId}` | 目录、搜索、上传、文件详情、版本、来源及明确主体授权。 |
| 任务 | `#/tasks?bucket=attention&type=all&work={workId}` | Work 四桶、提醒/研究/通用类型、计划、预算、投递、成果和局部收件箱。 |

账户设置与 QQ 绑定从头像打开。Artifact、Trace、Model Input 和 Research 内容在对象上下文中打开。`inspect=run:{id}` / `artifact:{id}` / `file:{id}` / `task:{id}` 指定唯一 Inspector，版本和页签使用 `version` / `tab` 参数；打开对象仍须服务端核实账户权限。

`store/shell.ts` 是路由与显示权威：集中 parse/serialize、push/replace、对象 origin、最多五层内部返回栈和导航 token。旧 `#chat` / `#files` / `#works` / `#research` 规范化到三个入口；旧成果和诊断地址缺少明确对象时返回 AI。URL 仅保存标识和允许的枚举，表单内容和凭据不写入 URL。

`Surface` 统一侧栏、sheet 与 dialog 的 Esc、inert、Tab 圈定和焦点返回；上层局部弹窗暂挂父层。Inspector 的身份在断点变化时保持，iframe、草稿与请求不因布局切换重复创建。桌面宽度 ≥1280px 使用非模态侧栏；960～1279px 使用右侧 sheet，600～959px 接近全屏，<600px 全屏。Sidebar 还根据实际 Canvas / Inspector 所需宽度折叠。`styles.css` 的显式导入顺序是样式层叠入口；语义色、字号、间距、焦点与动效参数集中到 tokens，支持 reduced-motion。

## UI 状态与领域状态

AI 页面与任务页面共用 Work 查询控制器；可见时定期刷新权威快照，至多订阅两个相关活动 Work，离页/隐藏/退出清理本地监控而不取消服务端执行。来源消息旁的任务卡片只匹配 `sources` 中的准确会话和消息，不改写 assistant 正文。任务详情的“定位来源消息”使用 `#/ai/{conversationId}?message={messageId}&fromWork={workId}`，按需向前加载历史（自动最多 20 页），找不到时保留当前对话、提示与“返回来源任务”入口；关联但无来源消息只打开会话。

Run 概览与任务执行输出可按需读取能力成果，独立展示 outcome、summary、checks 与 gaps；Research 报告按 invocation_id 读取，HTML 打开 ResultRef 的准确版本。此查询只面向账户所有者，不是模型执行授权或 Work 验收回执；不把局部 succeeded 推断为 Work completed，也不宣称基础检查包含浏览器验证。

| 模块 | 责任 |
| --- | --- |
| `store/auth.ts` / `sessionLifecycle.ts` / `api/client.ts` | 身份检查、请求取消与账户世代隔离；普通 401 失效；CSRF 同意图重试。 |
| `store/shell.ts` / `conversationUi.ts` / `artifactUi.ts` | 导航、对象选择、对话草稿/模式/阅读锚点、Artifact 修改草稿等账户内存状态。 |
| `store/workbench.ts` / `works.ts` / `workspace.ts` / `artifacts.ts` | 权威快照、分页、命令意图、领域缓存与后台同步。 |
| `store/runInspector.ts` / `components/trace/traceStore.ts` | 所选历史 Run、按需诊断、Model Input 可见范围与请求世代。 |
| `sse/runFeed.ts` / `workFeed.ts` | Chat 与 Work 的独立订阅、退化恢复和集中 dispose。 |

退出或切账户时同步失效 API 请求、Chat/Work feed、轮询、查询、下载 Blob、对话/HTML 草稿及 Shell；迟到响应必须同时通过账户、对象和查询世代检查。首次认证保留深链，离开旧账户后清除旧对象路由。退出清理前端监控，不等于取消服务端 Run / Work。

组件发出显式导航意图；领域 store 不以 Trace 直播或 Artifact 缓存变化自动跳页。活动 Run 跟随和历史诊断选择分别维护，普通聊天完成不触发未打开的 Trace 查询。Work Run 不写入 Chat DTO、不占聊天槽位。Work feed 最多两条，快照恢复与完整分页共用不重叠的查询控制器；页面隐藏暂停列表轮询，恢复可见后同步。

`store/workbench.ts` 保持对外 API，内部 `store/workbench/` 分为对话、附件、发送意图、Run 动作与监控。`context.ts` 共享账户/对象/查询世代及冻结意图；门面负责统一 reset，`runMonitor.ts` 集中释放 SSE、预算定时器和轮询等待，不在各模块重复维护生命周期。

`styles/presentation/` 按调色、Shell、对话、Run、Surface、Composer、响应式和 Inspector 动效组织最终表现规则；导入顺序由 `styles.css` 固定。移动规则时保留层叠顺序。

## AI 对话与执行

`ConversationSidebar` 使用 cursor 分页、日期分组和已加载标题筛选；`ConversationHeader` 改名使用版本检查，冲突保留输入。`HpThread` 复用同一 external-store runtime 渲染消息，受控 Composer 保留 IME、草稿、附件、历史锚点和底部跟随；快速映射 ReAct，深度映射 Plan-and-Execute。

发送意图冻结 key 和 payload。未知 POST 先查权威快照，用户确认时重放原意图；仅对应草稿版本在成功后清空，发送锁按对话隔离。A→B→A 期间迟到的成功 POST 在账户和发送目标仍一致时重新同步当前对话，旧空快照不得覆盖新消息。

`ExecutionBlock` 展示真实 Run 状态、进度、失败、取消/重试与审批；不推演不存在的步骤。`RunInspector` 提供概览、资料、高级诊断。Run 终态刷新已发布输出；Trace GET 在途时隐藏/恢复只保留一个定时链。明确的 `403 model_input_unavailable` 清除已有正文缓存并递增世代，拒绝在途正文回灌；单个 404 仅影响该记录。

Run SSE Gateway 先订阅并缓冲，再读取快照；snapshot 始终为第一帧。终态快照丢弃缓冲 delta，活动快照按 stream/seq 收敛；断线、gap、溢出仍依赖权威查询恢复，不伪造或重放历史 started。实现与正式反例见 [`sse.py`](../../src/web_api/sse.py) 和 [`test_sse_gateway.py`](../../test/web_api/test_sse_gateway.py)。

## 空间、权限与保存

`WorkspaceScreen` / `WorkspaceSidebar` 展示树、直接子项和搜索分页；`FileInspector` 有预览、详情、版本历史、AI 使用范围。查询按账户和 key 共享去重，订阅者拥有独立视图资格，切主体或目录后拒绝旧响应。

保存、授权、输入引用是独立操作。所有者看到文件不等于 Agent 已授权，关联对话也不自动授权 Work。`GrantEditor` 复用 Conversation 与 Work adapter；目录递归必须显式选择，继承规则从源规则撤销。新增文件/权限在下一 Run 生效；撤权阻断冻结资源的后续访问，并立即清除受影响 Model Input 缓存，即使后续授权读回失败。

`UploadToWorkspaceDialog` / `SaveToWorkspaceDialog` 固定一次意图与目标，分别恢复上传、ready、保存和授权。保存成功而授权失败只补授权；ready 内容遇到同名保存冲突保留内容并以新名称继续。版本操作意图保存在账户内存，关闭重开仍可用原 key 确认未知结果。impact 绑定对象、参数及 tree revision；过期重新预览与确认。版本写入仍执行 revision/hash CAS，冲突保留输入并允许另存。

## 任务四桶与动作

`TaskScreen` / `TaskInspector` 使用 [`taskPresentation.ts`](../../web/src/components/tasks/taskPresentation.ts) 的同一纯投影，分类和 [`taskActions.ts`](../../web/src/components/tasks/taskActions.ts) 的动作资格分别判断。类型仅为提醒、研究、通用；`artifact_build` 归通用，展示 HTML 生成/修改。Inspector 页签为概览、成果与执行、使用资料、高级详情。

四桶为 `attention`（需要我处理）、`active`（进行中）、`waiting`（等待或已计划）、`ended`（已结束）。先保留 completed/stopped 终态；非终态异常结构显示状态待核实，仅允许查看/刷新；其余按以下优先级命中一次。

| 优先级 | 条件 | 桶与标签 |
| --- | --- | --- |
| T01～T02 | completed / stopped | 已结束：已完成 / 已停止。 |
| T03～T04 | pausing / stopping | 进行中：正在暂停 / 正在停止；保留并列投递决议和未决原因。 |
| T05～T07 | 当前 revision 的 uncertain 投递、未解除 operation、failed 投递 | 需要我处理：发送结果待确认 / 外部操作结果待确认 / 发送失败。 |
| T08～T11 | user_acceptance_required、budget_exhausted、awaiting_input、非系统等待 blocked | 需要我处理：成果确认 / 额度 / 补充 / 执行受阻。 |
| T12 | 有 active coordinator | 进行中：正在执行。 |
| T13～T17 | paused、at_time、retry_after、正常 awaiting_delivery、waiting_capacity | 等待或已计划：已暂停 / 已计划 / 自动重试 / 回执 / 执行资源。 |
| T18～T19 | ready 无 coordinator，其他合法非终态 | 等待或已计划：准备继续 / 等待继续。 |

`paused + blocked/delivery_resolved` 在无 coordinator、operation 和当前失败/不确定投递时按已暂停显示。旧 revision 投递异常保留在历史；单次 Run succeeded 不表示 ongoing Work 已完成，Run failed 也不直接把 Work 归已结束。过期 due_at 仍显示等待执行。冻结 M01～M21 及异常输入的正式表驱动回归见 [`taskPresentation.test.ts`](../../web/src/components/tasks/taskPresentation.test.ts)。

Works 串行追完所有页后才给出精确计数，未完成时明确已加载范围；刷新按 row_version 合并，单对象深链 upsert。所有写命令服从 Work If-Match、原意图 key 和服务端裁决；冲突保留要求草稿。预算比例只用 used.model_total_tokens / limits.model_total_tokens，reserved 单列，增额为显式命令。

具体 Delivery 使用 accepted / not_sent / retry_accepting_duplicate_risk；确认未发送可能触发重发，重复风险须独立确认，accepted 不表示已读。通用未决 operation 仅提供核查/诊断，未新增通用人工对账。Research 保留报告、证据、已发布文件和 required 保存状态；收件箱不伪造服务端未读计数，最近执行最多 100 次。

## HTML Artifact

`ArtifactMessageItems` / Task 成果打开上下文 Inspector，`ArtifactInspector` 提供预览、详情、版本历史，`ArtifactComposer` 修改最近成功版本。Task origin 保存原交付版本和 requirement revision；手工新版不会自动替换原任务验收证据。

版本列表、消息摘要和逐版 GET 按版本 ID 与请求发出序号合并；已完成内容不被旧 running 覆盖，同 Run 的合法 failed→running 重试仍接受。每版一个 poller，最多四个单版本 GET 并发；仅消息行也能接管后台生成恢复。默认版本绑定本次成功加载，明确手选、关闭、切账户及 A→B→A 不被迟到初始化抢占。

HTML 仍通过受限 sandbox / CSP 预览；下载保留 `.html`，空间保存为 `.html.txt` 文本源码副本，不自动授权。Worker 在 failed/cancelled Run 的终态事务内将已领取且 running 的版本收敛为 failed，取消原因 `artifact_cancelled`；未被领取的 queued 意图仍属于 active Work。完成版本不可改写，迟到生成受 fencing 拒绝，再次推进使用新版本。

059 迁移撤销 API 对 `artifact_versions` 的 UPDATE；API 只 SELECT/INSERT，父 `artifacts` 的 UPDATE 保留用于行锁。Worker 保留发布所需 INSERT/UPDATE；账户过滤、组合外键与不可变 provenance 分别承担隔离和结果约束，表级授权不能替代它们。

## 视觉、阅读与会话切换

Paper Flame 使用冷瓷灰与少量焰橙的语义 tokens；评审控制及候选参数不进入产品构建。导航、反馈与动效观察已获准事实，不能提前改变领域状态。请求获准后才执行接力，排队、运行、失败、停止保留真实含义。

四类 Inspector 立即请求，数据就绪与实际抵达分别门控；抵达前显示中性头部。同对象页签、展开、版本保持身份，关闭按钮真实关闭，Escape 作用于最高层，dirty guard 获准前不导航。

会话切换与阅读收纳共用 Mercury 时间轴：omega=8.5×speed，默认 speed=0.35，hostTempo=2；两方向端点均按时间轴收束，不压缩数据等待。粒子固定 320 个身份，以进度和速度可逆推进，真实跨栏抵达对应 Conversation 条目。保留单 Composer、真实消息 DOM、草稿/附件/IME；目标活跃执行优先。渲染 DPR 上限 1.5，低档为 1，超过 180ms 的长帧安全降级；产品不提供速度设置。

阅读收纳只在已确认终态的 active Run，或已完成加载且无 Run 时可用。pending/unknown/queued/running/cancelling、错误、加载和忙状态都不能收纳。提交 guard 获准后先恢复阅读再发送，不额外聚焦或发请求；新消息引用/服务端事实变化自动恢复。手动恢复不拉取数据，保持阅读锚点或贴底；hidden/inert 阶段的滚动不更新阅读事实。收纳状态仅属本地生命周期，不写 URL 或 localStorage。

窄屏（Mercury 使用 960px 边界）、减动、隐藏/恢复、宿主失联、快切、resize、modal、长帧和渲染异常要收敛至真实状态并清理资源；旧 timer/arrival 不得覆盖新对象，空闲不保持 RAF。具体实现位于 `components/mercury`、`components/shell` 及相邻测试。真机 IME/软键盘/读屏、其他浏览器与长期堆分析需要独立验证，不能从本机静态或单元检查推断。

## 执行详情与诊断

桌面 ≥1280px 的 Run Inspector 可拖动左边界：默认宽 460px，最小 360px、最大 880px，主 Canvas 至少 600px，实际可用宽度继续约束上限。分隔条命中宽 12px，键盘步长 16px、Shift 48px、Home/End 到边界；拖动使用 pointer capture。宽度仅在当前会话内存保留，resize 不重建 DOM、查询或页签。关闭命中区 40×40px，退出期间保留宽度；其他对象和窄屏不继承拖宽行为。

标题工具栏刷新 Run 快照；高级诊断还刷新 Trace 并使 Model Input 正文缓存失效，资料/审批等独立资源各自刷新。标题保留抵达身份，页签切换不伪装成新对象打开。

概览原始回复显示完整 Markdown 字符文本，不摘要、不二次渲染、不折叠；具可访问名称，独立可聚焦滚动区最大高 440px。时间线按真实事件相对 created_at、end/sample_at 定位，等待灰、执行橙、成功绿；零时长合并，不补假宽、不移动点解决文字碰撞。跨日显示日期，缺失/倒序/非法时间标未知，不能当 0。活动且可见时按秒更新采样，隐藏停止、终态停止；位置不表示完成百分比。

Run 序号来自账号及真实 Conversation/Work 来源内按 created_at/run_id 排序的授权 PG 查询，不按消息数或前端缓存计数；重试新 Run 有新序号，缺失显示“本次执行”。

用量聚合以 run_budgets 为准；请求尝试以 run_usage_ledger 的 settled/inflight/released 事实计数，可能包含重试/fallback 与发送前释放。Snapshot/Trace 数量不是调用数；单次调用只按真实 model_call_id 关联。Trace 可能只保留最终响应，缺少的用量、关联或耗时明确显示未知，不猜 token 拆分、供应商延迟或任意 90% 告警线。最终结算、当前预留与预算强制边界分别解释。

Trace 节点选择与折叠独立，刷新/调宽保留状态；多根/深树按真实层级截断，详情仍可读完整节点。只有 snapshot_id 可打开 Model Input，不另发输入列表查询。执行中才有运行装饰，终态与减动静止。总跨度用起止差，不累加可能重叠的节点耗时。实现见 `components/run`、`components/trace`、`store/runInspector.ts` 及相邻测试。
