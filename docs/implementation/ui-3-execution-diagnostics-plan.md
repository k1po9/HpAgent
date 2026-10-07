# UI-3 执行与诊断实施计划

> 状态：已实施；最终验证及能力边界见 [UI-3 实施报告](ui-3-execution-diagnostics-report.md)。日期：2026-10-07（Asia/Shanghai）。
> 依据：《HpAgent 前端 UI 重构方案 v1.0》及当前项目源码。
> 以下正文保留制定计划时的基准与措辞；其中“本次未实施”“仅制定计划”等描述属于原计划阶段。实际实现、测试与未覆盖项以实施报告为准。

## 1. 基准与交付边界

| 项目 | 核验基准 |
| --- | --- |
| 项目 | `/home/hp/workspace/HpAgent_web` |
| 当前 HEAD | `325a5d7d1282834dc7cd9998f05674119e23b81e` |
| 当前提交 | `feat(web): implement UI-2 interactions and fix concurrent operation recovery` |
| 前置提交 | UI-1：`cc40862`；UI-2：`325a5d7` |
| 初始工作区 | `git status --short` 无输出 |
| 用户方案 | `D:/HuaweiMoveData/Users/黄培/Desktop/ui/HpAgent_前端_UI_重构方案_v1.0.md` |
| 实际读取路径 | `/mnt/d/HuaweiMoveData/Users/黄培/Desktop/ui/HpAgent_前端_UI_重构方案_v1.0.md` |
| 对照章节 | §4.3–4.4、§8–10、§11.1–11.2、§12–14；遵循 D01/D03/D07/D08/D10，重点处理 FE-B3、回归 FE-B1 |
| 前置交接 | [UI-2 实施报告](ui-2-ai-interaction-report.md)；其中测试结果是历史证据，本次未复跑 |

方案原基准为 `23ad691`，当前已包含两个阶段的实现。实施前应重新记录 HEAD、dirty diff 与相关代码差异，不重复建设 Pre-Shell、三入口、Composer、会话分页和账户 reset。附件中的执行措辞仅作为设计依据；用户本次要求制定计划，并未要求执行整个 UI 重构。

UI-3 的完成目标：用户能在消息位置看到真实执行状态，从当前或历史消息、已有任务上下文进入同一个 Run Inspector，查看执行、资料与输出、审批、Trace 和受权限控制的 Model Input；全过程保持聊天流、预算刷新、账户隔离和原有安全重试语义。

| 本阶段实施 | 留给其他阶段 |
| --- | --- |
| ExecutionBlock、状态展示与安全动作接线 | UI-2 Composer/资料/草稿逻辑保持并回归 |
| Run Inspector 三页签、独立 Chat/Work Run 读取 | UI-5 Task 四桶、完整 TaskInspector、Work 控制重构 |
| 文件操作审批列表、允许/拒绝、幂等恢复 | 不新增通用外部副作用人工对账，仍属 BE-X1 |
| Trace/Model Input 按选中 Run 加载，事件和请求隔离 | 不新增诊断一级入口或第二条 Chat SSE |
| Run 资料分页、已发布文件下载与现有保存入口 | UI-4 完整 FileInspector、权限编辑和历史反查增强 |
| Run 相关响应式、焦点、错误与回归 | UI-6 Artifact 迭代闭环；UI-7 全站统一和剩余旧壳清理 |

无需新增产品依赖、数据库迁移或后端状态机变更。若要求终态 Run 的完整候选资料历史，须登记独立后端增强，不能将下面的受限查询包装成完整回放。

## 2. 当前代码核验与实施差距

下列链接以本文目录为起点；行号对应本次 HEAD，后续修改后应更新证据。

| ID | 当前事实与证据 | 实施决策 |
| --- | --- | --- |
| C01 | [ChatPane](../../web/src/components/ChatPane.tsx) 将 RunStatus 放在整个消息区上方，并单独提供 Trace 按钮 | 把执行展示移到所属 assistant message；旧 Trace 入口迁为“执行详情”或高级页签入口 |
| C02 | [RunStatus](../../web/src/components/RunStatus.tsx) 已复用停止/重试资格、阶段标签、60 秒排队提示；全部终态使用勾号，并在主区显示 Token/请求数 | 保留资格函数和真实排队时间；分开完成、失败、停止图标；技术指标移高级区 |
| C03 | [HpThread](../../web/src/adapters/assistant-ui/HpThread.tsx) 的消息视图通过 assistant-ui message.id 渲染；[adapter](../../web/src/adapters/assistant-ui/types.ts) 未传 produced_by_run_id | 以原始 HpMessage 构建 messageId→Run 引用映射，不从渲染文本或消息状态猜 Run |
| C04 | [InspectorHost](../../web/src/components/shell/InspectorHost.tsx) 的 Run 分支只 GET snapshot、显示 ID/status，然后 selectRun 并嵌入 TracePanel | 替换为专用 RunInspector，保留唯一 Host/Surface，避免 Host 和子组件重复 GET |
| C05 | [DTO](../../web/src/api/types.ts#L134) 已有 HpChatRunSnapshot/HpWorkRunSnapshot 联合类型；[get_run](../../src/web_api/queries.py#L288) 对 Work 不返回 assistant_message | 继续以 source_kind 分支；Work snapshot 不写入 Workbench.activeRun，不做强制类型转换 |
| C06 | [traceStore](../../web/src/components/trace/traceStore.ts) 已有 generation、open 时 followRun 不切换、Model Input 迟到拦截 | 原方案提到的隔离缺口已有部分修复；本阶段明确分离选中对象与直播事件，补测试，不宣称当前完全无保护 |
| C07 | traceStore.loadTrace 用返回快照整体替换 nodes；同一次 GET 等待期间的 live event 可能被覆盖 | 增加快照与在途事件协调；不能只比较 runId 或合并节点数量 |
| C08 | [Workbench onTrace/onTerminal](../../web/src/store/workbench.ts#L429) 在 applyEvent 后读取选中 Trace 的 nodes 判断是否刷新预算；终态无条件 loadTrace | 预算刷新改由当前 feed 的事件事实判断；终态只刷新匹配且已激活的诊断对象，不请求别的历史 Run |
| C09 | [resources wrapper](../../web/src/api/resources.ts#L233) 已有 Run candidates 分页、published-files、getRunTrace/getModelInput；无审批和 Run Model Input 列表 wrapper | 补已有接口的类型/封装，复用 ApiClient；不是后端缺少接口 |
| C10 | [审批 DTO](../../web/src/api/types.ts#L64) 缺时间字段；[审批服务](../../src/file_domain/approvals.py) 已返回 requested_at/expires_at/decided_at/consumed_at，并提供幂等决策 | 补真实 DTO，接审批及结果不确定恢复，不把允许当作操作已执行 |
| C11 | [ResourcePolicy.candidates](../../src/workspace/resources.py#L242) 要求 queued/running、ready snapshot、当前 list_metadata 授权；终态和撤权可能返回 404 | 将此作为局部资料查询限制；Run 存在不等于候选接口始终可用 |
| C12 | [Model Input 查询](../../src/web_api/model_observability_queries.py) 依据当前 entitlement 返回 none/summary/full_safe；Trace 路由可因没有 tree 返回 404 | Model Input 按服务端权限展示；没有 Trace 不等于 Run 不存在，不阻断其余页签 |
| C13 | [sessionLifecycle](../../web/src/store/sessionLifecycle.ts) 已统一 reset，ApiClient 可 abort 请求；[shell](../../web/src/store/shell.ts) Inspector 仅 kind/objectId/versionId | 新查询和命令状态接同一会话边界；为 Run 增最小 origin/tab/focus 描述，不复制服务器数据 |
| C14 | [WorkPanel](../../web/src/components/WorkPanel.tsx) 已有 active_coordinator_run_id 展示、Work 控制，但没有完整通用 Run Inspector 接线 | 仅补真实 Run 引用的详情入口及返回任务定位，不提前重构任务中心 |

## 3. 目标结构与状态所有权

```text
AppShell / Surface / InspectorHost（继续唯一可见面板）
├─ ChatPane → HpThread → assistant message
│  └─ ExecutionBlock（真实 Run 引用、当前状态、详情入口）
└─ RunInspector
   ├─ 概览：RunSummary、可信结果、已有步骤/分支、所属上下文
   ├─ 使用资料与输出：候选分页、已发布文件、RunApprovals
   └─ 高级诊断：预算细节、TraceTree / TraceDetail、Model Input
```

建议新增 `components/run/ExecutionBlock.tsx`、`RunInspector.tsx`、`RunSummary.tsx`、`RunResources.tsx`、`RunApprovals.tsx`、`runPresentation.ts`；查询可集中于 `store/runInspector.ts`，避免散落在三页签中的独立定时器。具体文件数允许调整，但以下所有权不可改变。

| 数据/行为 | 唯一所有者 |
| --- | --- |
| 当前 Chat Run、messages、progress、degraded、feed、send/stop/retry | Workbench；ExecutionBlock 纯消费，运行时不重建 |
| Inspector 对象、origin、tab、内部返回、焦点位置 | Shell；tab 可先存内存，刷新默认概览，不为此扩展 URL 协议 |
| 历史 Chat/Work snapshot、候选/输出/审批查询结果 | Run Inspector 查询状态；标明是服务器缓存，键含账户会话世代和 runId |
| Trace tree、选中节点、Model Input 查询缓存 | Trace store；运行对象由 Shell 的当前 Run 决定，节点选择留在 Trace store |
| 审批未决命令 | 按 session generation + approvalId 存冻结 decision/key；与可见面板生命周期分离 |
| Work 主控制 | 既有 Works 命令与 WorkPanel，不新增 Run→Work 状态推断 |

Shell 中 sourceKind/origin 只作导航提示，以 GET 返回作为权威。origin 建议记录 conversationId/messageId 或 workId、returnFocusKey；仅内存保存，不在 URL 写诊断正文、权限或命令参数。直接深链没有 origin 时通过 snapshot 恢复所属入口，无法定位时保留关闭/返回页面动作。

当前活动 Chat Run 与 Inspector 同 runId 时直接复用 Workbench 快照，不再开 snapshot 轮询；只有选中历史 Chat/Work Run 才独立 GET。当前对话切走但面板仍有效时重新判断数据来源，确保从复用态切换到独立查询态；不能冻结旧对象或复用新对话 Run。

## 4. 交互与数据契约

### 4.1 ExecutionBlock：消息绑定与真实状态

1. 从原始 messages 建立 messageId→produced_by_run_id 映射。当前 activeRun 仅绑定匹配的 assistant message；不得把“最后一条消息”默认认作该 Run。
2. 当前 Run 在该位置显示阶段/停止/详情；终态收敛为结果摘要。消息尚未出现时，可在消息区末尾显示唯一临时执行占位，真实消息出现后迁入，不追加 HpMessage。
3. 历史消息只有 run_id 而无已加载 snapshot 时显示“查看执行详情”，不根据 Message.completed 宣称 Run succeeded。用户点击才 GET，禁止每行自动查询。
4. 消息文本继续来自 Workbench；ExecutionBlock 不写 Message.content、不添加逐 token 进度文本、不影响 Markdown/文件/Artifact 动作。
5. 重试会产生新 Run，按真实 produced_by_run_id 和返回快照更新绑定；原失败记录不能被本地“改成成功”。

| 信号 | 主文案与表现 | 动作 |
| --- | --- | --- |
| queued | 正在等待执行；基于 created_at 显示真实等待，非法时间不造计时 | 停止、详情 |
| running | 正在执行；有 progress 时优先可信 summary，否则 phaseLabel | 停止、详情 |
| cancelling / 停止请求中 | 正在停止；不是终态 | 详情，禁重复停止 |
| succeeded | 已完成，成功图标 | 查看实际结果、详情 |
| failed | 未完成与简短失败原因，失败图标 | 仅既有 isRetryableRun 允许时重试，否则详情核查 |
| cancelled | 已停止，独立停止图标 | 详情；不普遍开放 retry |
| degraded | 连接中断，正在同步执行状态 | 保留已知状态；不能声称后台仍正常推进或把网络失败当业务失败 |

复用 `isCancellableRunStatus`、`isRetryableRun`、`isTerminalRunStatus`、`phaseLabel`。不预置完整步骤；单个 progress.phase 不证明前面步骤完成。Run 状态与 Trace 状态（completed/succeeded）分别映射，未知诊断状态不能默认打成功勾。

消息主区移除 Run ID、Token、Provider 等调试细节。概览预算可显示明确的摘要，高级诊断保留 used/reserved、usage_state/quality、model_calls/by_source 等已有字段；缺失不补零。

历史 Chat Run 的停止/重试入口先定位其所属 Conversation，经现有快照加载确认 activeRun 和动作资格后交给 Workbench。不能让只操作 activeRun 的旧方法误作用于另一 Run，也不为历史操作塞入伪造 activeRun。当前 Run 的 Inspector 可直接复用同一命令锁。

### 4.2 Run Inspector：独立快照与渐进展示

三个页签为“概览 / 使用资料与输出 / 高级诊断”，默认概览。Run 加载成功后页签各自管理 loading/empty/unavailable/error/ready，附属查询失败不替换整个对象正文。

- 概览：真实状态、可用的时间/失败/结果摘要；Chat 与 Work 分型。Work 的 result_json/branches/published_file 按字段存在展示；无结构化步骤只显示结果或暂无，不把任意 JSON 转成已完成计划。
- Work DTO 按 `QueryService.get_run → work_domain.persistence.dto` 实际返回补本页使用的 version/timestamps/failure 字段，读真实字段名，不能照搬 Chat failure 嵌套结构。fixture 应来自真实投影或契约测试。
- Work Run 明确属于某 work_id 和 requirement_revision；Run succeeded 不等于 Work completed。主按钮为“查看所属任务”，回既有任务页/WorkPanel 的该对象处理 pause/stop/advance；UI-3 必须补可见定位，不能只打开尚无控制的 Task 占位详情。
- 现有 WorkPanel 可为真实 active_coordinator_run_id 增加执行详情按钮。历史 Run 从已有列表/真实引用接入；若需调用已有 Work runs wrapper，只做按需列表，完整 Task 历史布局留 UI-5。
- 通用外部 side_effect_uncertain 显示核查指引与真实 operation 引用（有则显示），不把文件审批、advance 或停止包装为通用清除不确定结果。
- Run 本体 403/404 清除该对象敏感数据和动作，统一“对象不可用”；网络错误保留旧数据并标“待同步”。401 走统一会话失效。

面板自有轮询建议：仅可见且非终态的独立 snapshot，递归 timeout 2 秒起、失败退避至 10 秒，不重叠请求；浏览器隐藏暂停，重新可见立即同步一次。终态停止 snapshot 周期查询，手动刷新仍可用。关闭或切对象只取消视图请求和计时器，不取消后台工作。

### 4.3 使用资料与输出：如实呈现接口边界

候选列表调用既有 `listRunResources(runId, after)`，展示 name/logical_name、类型/大小（有来源时）、fixed/read；候选不等于已读取，历史访问不等于当前仍获授权。追加串行、按 node_id 去重，刷新使旧游标追加失效，失败保留已加载项和重试入口。

必须处理当前后端的限制：

- `/runs/{id}/resources` 只支持 queued/running 且 ready 的资源快照，还受当前授权过滤；queued 尚未 ready、cancelling、终态或授权变化都可能不能读取。
- 返回 404 时先根据已加载 Run 本体区分局部不可用，文案为“当前无法获取候选资料；不代表本次未使用资料”，不能写“未使用任何文件”或把整个 Run 判不存在。
- 对终态默认说明“当前接口不提供完整历史候选回放”，仍可读取输出与 Trace 中实际存在的引用，不扫描全空间拼接历史，不用当前 Conversation grants 冒充 Run 冻结输入。
- 若会话内此前读到列表，只可标记为“先前快照，当前未验证”；权限拒绝时清理缓存。刷新后不承诺恢复这份快照。
- 本限制记录为 UI-3 API Gap（历史候选读取），不冒用基线的 BE-W 编号；如果未来要求完整历史资料页，另立后端方案。

输出复用 `listRunPublishedFiles`。返回只有 file_id/name/sha256，不能把 file_id 填入要求 node_id 的 File Inspector。下载走已有 content 接口；需要保存时按需 GET file metadata，复用现有 onSaveFile/SaveWorkspaceDialog，确保来源是选中 Run。已保存且有真实 node_id 时才允许打开 File Inspector；不在 UI-3 发明 ID 对应关系。

### 4.4 文件操作审批

补 `listRunFileApprovals(runId)`、`decideFileApproval(approvalId, decision, key)`，对应既有 GET 和 approve/reject POST，body 为 `{}`，返回 `{ approval }`。复用 HpFileApproval 并补后端已返回时间字段。

审批区域提供 action_summary、状态、到期时间与“允许 / 拒绝”；tool_name/operation_id 放详情。pending 才显示决策按钮；approved 表示已授权，consumed 也不证明最终业务成功；rejected/expired/cancelled/consumed 都不能再次审批。本地到期时间仅禁用并提示重新同步，最终由服务端裁决。

执行流程：

1. 点击时冻结 account generation、runId、approvalId、decision 和一次意图 key；同步加锁，允许/拒绝共享该 approval 的锁。
2. 通过 ApiClient 发送，CSRF 刷新重放保持同 key；不依赖 optimistic 状态假定服务端接受。
3. 成功更新原 approval，再刷新该 Run 审批及必要快照；提示“已允许，等待执行状态更新”或“已拒绝”，不宣称操作完成。
4. 响应丢失保留未知结果与原 key，先 GET 核对；仍 pending 且有效时可由用户重放原决策。未知期间不开放相反决策；关闭重开仍保持命令锁和 key。
5. `409 approval_not_pending` 刷新并展示已处理/过期；不得旋转 key 强行重试。GET 若仍报 pending 但 expires_at 已过，只提示过期/待同步，不循环提交。
6. 切 Run 时结果只落原对象，不覆盖新面板；退出清锁并使迟到结果失效。账户重新登录后以 GET 为准重建状态。

资料页打开后按需查询审批；存在 pending 或 Run 非终态时可在可见期间限频刷新（建议 5 秒，不重叠），审批全部结束且 Run 终态后停止。概览提供明确“查看操作审批”入口；ExecutionBlock 只显示已经查询得到的 pending 提示，不根据 running 猜审批，也不为每条历史消息轮询。

### 4.5 Trace 与 Model Input：选中对象、直播和快照

本阶段迁移 Run 相关调用到 Shell.openInspector；TracePanel 成为高级页签内容，不再自己决定面板开关。traceStore.open/setOpen/followRun 的 bridge 若仍有调用，只保留单向兼容并列清调用点；不能让 Trace 关闭反向卸载其他 Inspector。

事件规则：

- 选中历史 B 时，Chat A 的 trace.event 不得改变 B 的 runId、节点、selectedNodeId 或 Model Input；高级页签激活才应用匹配对象的事件。
- 关闭高级页签停止其查询/刷新；重新进入 GET 当前树，弥补不可见期间的事件。无需为后台 A 保存无限树或新建 SSE。
- **预算独立于可见 Trace**：Workbench 在 feed 层依据 event.nodeType 或该 feed 自己的有界 nodeId→type 记录识别 LLM 开始/结束，保留原预算刷新节流；不再读取选中 TraceStore.nodes。feed 结束/reset 清理此记录。
- **快照/事件竞态**：发起 Trace GET 时记录请求 token 并缓冲同对象在途事件，返回后以 snapshot 为底、按事件顺序重放。已终态节点不能被较旧 start 降为 running，metadata 合并不能丢失 token_usage/snapshot_id；不能仅将旧 GET 整树覆盖。缓冲设上限，溢出标待同步并重取，不无限占内存。
- 同对象手动刷新也递增请求 token；账户 generation、对象选择世代、请求 token 共同拦截 A→B→A 以及卸载后回流。
- 当前 Chat 终态只通知匹配且激活的高级页签同步；历史/Work 非终态高级页签可限频 GET Trace，终态再同步一次并停止周期查询。不得刷新另一个历史选中对象。

Trace 404 可能只是没有持久化 tree，展示“暂无可用诊断记录”与手动刷新；不伪造步骤。网络错误保留可信旧树并提示未同步，不能继续显示“实时事件仍会继续”这种对历史/Work 不成立的承诺。

Model Input 保留节点 snapshot_id 入口，并补已有 `GET /runs/{id}/model-inputs` 列表 wrapper，支持没有 Trace 节点但已有模型快照的 Run。列表按需加载；none 的 minimal projection 与 summary/full_safe 必须分型，不能假设每项都有 provider/model/message_count。详情仅用户点击时 GET。

| 服务端结果 | 前端行为 |
| --- | --- |
| none / model_input_unavailable | 展示当前不可查看，清理对应已缓存正文，不从 metadata 拼出 prompt |
| summary | 展示返回的安全元数据，不显示 provider_request_body |
| full_safe | 仅展示返回的脱敏 provider_request_body，沿用文本/pre 渲染 |
| 网络失败 | 局部重试；不得把错误当成无调用记录 |
| 403/404、账户变化或权限降级 | 清理受影响正文；重开重新校验，不用旧 full_safe 缓存继续显示 |

不从 `/me.capabilities` 虚构 prompt_visibility；不把 Model Input 写入 localStorage、URL、普通日志或截图证据。相同会话中权限改变无推送时不能承诺瞬时撤回；在重新进入/刷新及服务端拒绝时失效缓存。

### 4.6 导航、焦点和 UI-2 保护

兄弟 Run 点击替换当前选择；Task→Run→有真实 node_id 的 File 使用现有有界返回栈。浏览器返回恢复路由，刷新重新验证对象；旧 `#diagnostics` 继续按已有上下文回收，不新增一级菜单。

保留 Surface 的桌面区域与 <1280px modal 行为。打开将焦点移入命名标题/首个控件；关闭返回原消息/任务按钮，触发点已不在 DOM 时回消息区/列表标题。高级页签 Tabs、审批按钮和重试必须可键盘操作；状态适度 aria-live，不逐 token 播报。

切 Inspector、页签及开关资料不重建 assistant runtime，不重复发送/订阅，不丢草稿、附件和历史滚动锚点。保留 UI-2 的每对话 submission token、未决发送同 key 确认、草稿 revision 和 IME 保护；所有相关旧测试继续保留语义断言。

## 5. 分步实施与退出条件

按以下顺序实施，估算为单人约 8–11 个工作日（含测试和文档，不含外部环境等待），不是交付日期承诺。

| 步骤 | 主要改动 | 依赖与退出条件 | 估算 |
| --- | --- | --- | --- |
| P0 契约复核 | 记录新 HEAD；复核 Run/审批/Model Input/候选真实返回；准备 Chat/Work/无 Trace/终态资料受限 fixture | 明确类型与接口限制；不把未运行测试当通过 | 0.5 天 |
| P1 查询与类型 | 补审批/列表 wrapper、时间 DTO；Run 查询状态、请求 token、会话 reset、单对象去重 | A→B→A、账户 reset、Chat/Work 分型及 403/404 用例通过 | 1–1.5 天 |
| P2 ExecutionBlock | RunStatus 拆分、消息映射、真实状态、详情入口、预算信息迁移 | 六种 Run 状态、degraded、无假步骤、安全重试；runtime/草稿/锚点不回归 | 1–1.5 天 |
| P3 Run Inspector | Host 专用分支、三页签、snapshot 复用/轮询、资料分页/输出、Work 入口与定位 | 一个可见 Host、无重复 Chat feed；历史资料不可用不伪装为空 | 1.5–2 天 |
| P4 审批闭环 | 冻结意图/同 key、互斥决策、读回恢复、跨对象锁 | 连点、结果未知、409、到期、切走重开和账户切换用例通过 | 1 天 |
| P5 诊断隔离 | Trace 选中/直播解耦、预算刷新修复、快照重放、Model Input 列表与权限 | 历史选择稳定、在途事件不丢、预算不依赖面板、权限拒绝不留正文 | 1.5–2 天 |
| P6 验收交接 | 定向→全量前端检查、受影响 E2E/后端契约、尺寸和焦点、报告 | §6 全部有证据，阻断项已解决，未覆盖项明确 | 1.5–2 天 |

UI-3 可局部触及 `workbench.ts`（预算事件和终态诊断通知）、`HpThread`（展示映射）、Shell（最小上下文）、`WorkPanel`（Run 入口和对象定位）、App 的保存回调；这些是迁移接线，不重写对应领域模块。

删除原 RunStatus/Trace 按钮前，必须确认停止/重试、排队提示、失败原因、预算详情、Trace/Model Input 都有新入口。Artifact/File/Task 的其他 bridge 和旧页壳继续交给对应后续阶段，不在此批次全局清理 CSS 或 TestPages。

## 6. 验收矩阵与执行命令

### 6.1 UI-3 必须覆盖的场景

| 编号 | 场景 | 可判定的验收结果 |
| --- | --- | --- |
| U3-01 | 当前消息、空 assistant 占位、重试 Run、历史消息 | 执行块只绑定真实 ID；无多余消息、无逐行 GET、历史未知状态不猜测 |
| U3-02 | queued/running/cancelling/succeeded/failed/cancelled | 文案/图标准确；无伪步骤；stop/retry 符合既有资格且共用锁 |
| U3-03 | 重复 event、乱序、gap、stream_id 改变、degraded 后 delta、终态覆盖 | 复用原 SSE 约束；断线不继续错误拼字，不把断线写成失败 |
| U3-04 | AI→空间→任务→AI，开关 Inspector，StrictMode | 同一 Chat 最多原有一条 feed，runtime 不重建，草稿/锚点保留 |
| U3-05 | Work Run 深链，conversation_id=null，无 assistant_message | 独立加载，Workbench 无污染；Run 结束不改 Work 状态；控制回任务 |
| U3-06 | 历史 Chat Run 动作，当前对话有另一活动 Run | 不对错误 activeRun 执行命令；先定位/刷新再验证资格 |
| U3-07 | A→B→A、并发刷新、慢请求、卸载、账户 A→B/同账号重新登录 | 旧 snapshot/Trace/Model Input/审批结果不回灌，锁只释放自己的意图 |
| U3-08 | 当前 Chat A 直播，查看历史/Work B | B 的 runId/节点/选择不变；A 的预算仍按原规则刷新 |
| U3-09 | Trace GET 期间到达 start/end、终态、metadata 更新 | 最新事件不被快照覆盖；终态节点不倒退，数据不足不补编 |
| U3-10 | Run 没有 Trace、Trace 404/网络失败、列表有 Model Input | 概览仍可用，诊断局部状态准确；Model Input 不依赖树一定存在 |
| U3-11 | Model Input none/summary/full_safe/unavailable、权限变化 | 严格按真实响应分型；拒绝和账户退出后无旧正文 |
| U3-12 | candidate/fixed/read、分页、追加迟到、撤权 | 三者不混淆，分页无重复/漏加载声明，旧追加失效 |
| U3-13 | queued 无 ready snapshot、终态或 cancelling 的 resources 404 | 不把 Run 整体判不存在，不声称从未使用资料；历史能力限制可见 |
| U3-14 | published-files、下载、按需元数据、保存回调 | file_id 不当 node_id；保存目标与当前选中变化隔离，不自动授权 |
| U3-15 | 审批六状态、允许/拒绝连点、到期、409 | 只允许 pending 有效意图；approved/consumed 不显示业务成功 |
| U3-16 | 审批 POST 成功但响应丢失、CSRF 重放、切走重开 | 保留同 key/decision，读回恢复；无相反决策并发、无重复操作 |
| U3-17 | 无 pending、非终态变终态、tab/页面隐藏、关闭 | 轮询有界不重叠，终态/不可见停周期请求；重新可见可同步 |
| U3-18 | 浏览器返回、旧 hash、Task→Run→File、缺失对象 | 单 Host、正确返回、对象权限重新校验、焦点合理恢复 |
| U3-19 | 桌面/移动、长标题、长 JSON、200% 放大、reduced-motion | 无面板溢出/遮挡；键盘 Tabs/审批/关闭可用；动画不影响状态 |
| U3-20 | UI-2 既有交互 | IME、分页、附件、授权/撤销部分成功、A/B 并发发送和同 key 恢复保持 |

### 6.2 测试落点

新增建议：`components/run/*.test.tsx`、`runPresentation.test.ts`、`store/runInspector.test.ts`、`api/resources.test.ts`、`e2e/ui-3-execution.spec.ts`。测试关注可观察行为、网络次数、原命令目标和迟到隔离，不为组件拆文件本身写镜像测试。

扩展既有 `traceStore.test.ts`、`TraceDetail.test.tsx`、`workbench.test.ts`、`sessionIsolation.test.ts`、`shell.test.ts`、`App.test.tsx`、`App.recovery.test.tsx`、`HpThread.ui2.test.tsx`。迁移 RunStatus 测试时将安全重试、取消、预算语义分别放到新对应层，不直接删除断言。

在 `web/` 执行（新增测试文件实现后再加入实际命令）：

```bash
npx vitest run src/sse/runFeed.test.ts src/sse/sseClient.test.ts src/store/workbench.test.ts src/store/sessionIsolation.test.ts src/components/trace/traceStore.test.ts src/components/trace/TraceDetail.test.tsx src/App.test.tsx src/App.recovery.test.tsx
npm run typecheck
npm run lint
npm run build
npm test
npm run test:e2e -- e2e/ui-3-execution.spec.ts e2e/disconnect.spec.ts e2e/stop-retry.spec.ts e2e/conversation.spec.ts e2e/ui-2-ai.spec.ts e2e/auth.spec.ts e2e/multi-tab.spec.ts e2e/a11y.spec.ts
```

保存回调及资源链触及时加跑 `workspace-p1.spec.ts`、`workspace-p3.spec.ts`、`artifact.spec.ts`。真实 API 用于正常状态链；不易由 Fake Executor 触发的 Trace/Model Input/审批响应丢失场景可用明确标注的浏览器网络夹具，但不得把 mock 通过记为真实持久化审批通过。

按 [测试指南](../development/testing.md) 配好专用数据库、三角色 DSN、Redis DB、文件目录、端口之后，在项目根执行聚焦后端契约：

```bash
PYTHONPATH=src python3 -m pytest test/test_file_action_approval_contract.py test/web_persistence/test_file_action_approvals.py test/web_api/test_model_observability.py test/web_api/test_work_foundation.py test/web_api/test_phase_b_api.py -q
```

资料与输出接线另复用 `test/web_api/test_workspace_v41_p1.py` / `test/web_persistence/test_workspace_v41_p1.py` 的候选/读取场景。审批真实消费/唤醒需要验证 Temporal 时，补 `test/test_tool_execution_approval_temporal.py` 的隔离环境运行；未运行则明确只验证 API/持久化决策，未证明真实工具执行恢复。

Playwright 保持 workers=1、同账号串行；依照既有指南，数据库 fixture 可能清表，不能沿用业务库默认 DSN。每批记录命令、环境范围、退出码和实际结果；本次计划不提供预填通过数量。长命令等待遵循会话执行约束，不为“仍在运行”频繁轮询。

### 6.3 视觉与人工证据

至少覆盖 360×800、390×844、768×1024、1024×768、1280×800、1440×900、1920×1080。截图包含执行中/终态/断线、Run 三页签、审批 pending/错误、无 Trace、对象不可用、长标题；至少桌面和手机各有开关 Inspector 流程。

人工核验焦点返回、modal inert、Esc 层级、Tabs/Tree 键盘、200% 文本缩放与 reduced-motion；继续标注实机软键盘/真实 IME/完整 WCAG 目标是否覆盖。Model Input 截图只用合成脱敏数据，证据不包含真实 prompt 或凭据。

## 7. 风险、完成定义与交接

| 风险 | 处理及发布门槛 |
| --- | --- |
| 查看历史 Trace 影响当前 Run 预算 | P5 必须解除 Workbench→选中节点依赖；U3-08 不通过不得交付 |
| 终态资源接口不支持完整回放 | 使用明确局部不可用文案；记录后端增强项；不能宣称完整历史资料验收通过 |
| Work Run 被当 Chat snapshot | 分型和负向 fixture；不得出现 assistant_message 非空断言或 Work 写入 activeRun |
| 快照覆盖 SSE 新事件 | token + 有界重放/单调终态测试；不以最终节点数相同代替内容断言 |
| 审批意图丢失或允许/拒绝交叉提交 | 命令生命周期独立于面板、同 key、读回；结果不确定时禁相反决策 |
| UI-2 稳定性回退 | 每对话锁、草稿 revision、资源恢复及 runtime 测试继续执行 |
| 新旧 Trace 开关互相驱动 | Shell 单一权威；列出迁移调用点，删除本阶段已无消费者的 bridge |

完成定义：U3-01–U3-20 有可复验结果；全量前端检查和受影响 E2E 通过；后端状态前提有契约证据；没有未解决的严重对象串用、重复命令、权限数据残留或重复 feed；能力缩限与环境未覆盖项明确记录。UI-3 通过不等于整个新 Shell 已达到 UI-8 的正式发布门槛。

建议交付文件：

- `docs/implementation/ui-3-execution-diagnostics-report.md`：基准、变更清单、D/FE-B/U3 对应关系、执行结果和限制。
- `artifacts/product-acceptance/ui-3/README.md`：脱敏日志、截图、成功/失败恢复/迟到响应三类操作记录，以及真实 API/网络夹具/Temporal 覆盖区分。
- 更新本目录索引；交给 UI-4 的资料查询限制、UI-5 的 Work 导航与控制边界、UI-6 的输出保存/Artifact 引用边界逐项列出。

回退以对应前端提交或视图接线为单位，保留同一套 store/API；不回退已作出的服务器审批、不清空对象、不修改 Work 状态。原 UI-2 的入口仅可在明确开发回退时恢复，不长期并排维护两个诊断面板。
