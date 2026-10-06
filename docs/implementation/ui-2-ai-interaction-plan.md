# UI-2 AI 主要交互实施计划

> 状态：待实施计划，不是完成报告。日期：2026-10-07（Asia/Shanghai）。
> 依据：用户提供的《HpAgent 前端 UI 重构方案 v1.0》（2026-10-06）及当前项目源码。
> 本次只生成计划文档；未修改业务代码、安装依赖、启动服务、执行业务写操作或运行应用测试。

## 1. 基准、目标与边界

### 1.1 核验基准

| 项目 | 本次基准 |
| --- | --- |
| 项目 | `/home/hp/workspace/HpAgent_web` |
| 当前 HEAD | `cc4086273a92ea269abe863b5a054bf5026dcbb1` |
| 最近提交 | `feat(web): implement UI-1 shell and session lifecycle` |
| 初始工作区 | `git status --short` 无输出，干净 |
| 方案文件 | `D:/HuaweiMoveData/Users/黄培/Desktop/ui/HpAgent_前端_UI_重构方案_v1.0.md` |
| WSL 读取路径 | `/mnt/d/HuaweiMoveData/Users/黄培/Desktop/ui/HpAgent_前端_UI_重构方案_v1.0.md` |
| 与方案原基准的差异 | 原方案基于 `23ad691`；当前已有 UI-1，不重新实现 Pre-Shell、三入口、hash 路由和 InspectorHost |
| 证据范围 | 静态阅读源码及测试定义；未复跑 UI-1 验收，不能据提交名称认定所有目标已通过 |

附件中的后续实施指令作为设计输入；本次用户请求是制定 UI-2 计划，并不授权直接执行 UI-2 或附件中其他阶段。以下新增文件名、状态及方法均为实施建议，现状以第 2 节为准。后续执行时重新记录 HEAD 与 dirty diff，并核对本文证据是否变化。

### 1.2 UI-2 交付目标

完成“选择或创建对话 → 输入 → 添加当前轮附件或授权长期资料 → 快速/深度执行 → 阅读流式回复与历史 → 切换后恢复上下文”的 AI 主链路。保留现有 Markdown、文件下载、保存、HTML 生成及诊断入口。

直接对应重构方案 §4.1–4.2、§9、§10、§12、§13 和 §14.2 UI-2 行；遵循 D01–D04、D07–D10。本阶段不实现 Task 四桶及 M01–M21，不把它们列为 UI-2 新交付。FE-B1/FE-B3 已有 UI-1 接线继续回归；FE-B6 仅补本阶段实际使用的 DTO，Work 字段留给 UI-5。

### 1.3 范围归属

| 本阶段完成 | 保留与后续移交 |
| --- | --- |
| AI Sidebar 分组、已加载标题过滤、分页、错误恢复 | 不新增全文搜索接口或第四一级入口 |
| Conversation Header、标题修改、无对话 Composer | 不新增删除/归档对话能力 |
| Composer 布局、快速/深度、IME、草稿、单次提交及恢复 | 保持既有 send/stop/retry、Run 状态和 SSE 协议 |
| 当前轮上传/已有附件选择、长期资料 Chips 与读取选择器 | UI-4 承担完整 Tree/File Inspector/GrantEditor 与空间 controller 重构 |
| 历史消息锚点、切对话草稿/滚动恢复 | UI-3 承担 ExecutionBlock、Run Inspector、审批及完整诊断布局 |
| AI 局部响应式与无障碍 | UI-7 统一全站动效与清理旧页壳 |
| 原 HTML/下载/保存/Trace 入口回归 | UI-6 承担 Artifact 版本与保存流程重构 |

UI-2 需要对 `workbench.ts` 做分页、命令上下文、草稿交接所必需的局部修改，并调整 `AppShell.tsx` 的 AI 挂载与导航；这是主交互接线范围，不借此重写领域 store。无需新增后端接口、迁移数据库或引入路由/动画/查询框架。

## 2. 当前代码与差距

以下链接相对本文所在目录，锚点对应本次 HEAD。

| 编号 | 已核验现状 | UI-2 决策 |
| --- | --- | --- |
| C01 | [AppShell](../../web/src/components/shell/AppShell.tsx#L61) 已统一三入口；AI section 通过 hidden 保留，但无 activeConversationId 时渲染 EmptySelection，`#/ai` 分支调用 Workbench reset 后只补回 conversations / conversationsLoaded | 保留隐藏切页机制；改为稳定 AI 容器及可输入空态；普通离开对话与账户 reset 分开，避免新增分页/草稿状态被误清空 |
| C02 | [ConversationSidebar](../../web/src/components/ConversationSidebar.tsx#L1) 仅平铺列表、新建与选择，无分组/过滤/分页/错误栏 | 在原组件扩展，不新建第二份会话列表 |
| C03 | [listConversations wrapper](../../web/src/api/resources.ts#L36) 已支持 cursor、每页 30；[store](../../web/src/store/workbench.ts#L481) 只请求首批并丢弃 next_cursor / has_more | 缺口在 store 和视图，不是后端缺少分页 |
| C04 | [Conversation DTO](../../web/src/api/types.ts#L11) 已有 updated_at / metadata_version；[rename wrapper](../../web/src/api/resources.ts#L376) 已存在，但当前 AI 未接入 | 日期分组使用真实 updated_at，补标题编辑与版本冲突处理 |
| C05 | [ChatPane](../../web/src/components/ChatPane.tsx#L1) 聚合资料文本摘要、RunStatus、模式 select、Trace、已有文件列表；模式文案是“对话（ReAct）/计划执行” | 拆出 Composer 上下文与 Header；运行状态暂留原组件，模式移至底部工具栏 |
| C06 | [HpThread](../../web/src/adapters/assistant-ui/HpThread.tsx#L139) 创建外部 store runtime；消息支持 Markdown/GFM、附件、保存与 HTML 动作；Viewport 开启 autoScroll | 保留 adapter 和消息转换；增加锚点/底部跟随行为验证，不假定 autoScroll 已满足全部场景 |
| C07 | [runtime.onNew](../../web/src/adapters/assistant-ui/runtime.ts#L64) 等待 onSend，但未处理 boolean 结果；ComposerPrimitive 管理输入 | 当前源码不能证明失败时输入必定保留，需验证已安装 assistant-ui API 并显式接管清空时机 |
| C08 | [sendMessage](../../web/src/store/workbench.ts#L760) 有 sending 锁、临时消息替换和上下文 fence；每次调用都新建 key；失败移除临时消息 | 保留成熟发送逻辑；同一未决意图保留 key 和冻结参数，避免网络失败后重复创建消息 |
| C09 | [selectConversation](../../web/src/store/workbench.ts#L538) 关闭旧 feed、清 messages/attachments；消息分页已有 cursor 和 message_id 去重 | 补按对话 UI 草稿及滚动锚点；不重复实现消息分页协议 |
| C10 | [上传/候选](../../web/src/store/workbench.ts#L625) 已有上传状态、10 个附件限制、ready 门控；候选追加无显式加载锁/去重；移除新上传调用 deleteFile，已有文件只移除选择 | 复用附件生命周期；补候选请求状态、竞态与去重；附件恢复策略见 §4.4 |
| C11 | [文件输入](../../web/src/adapters/assistant-ui/HpThread.tsx#L192) accept 主要面向文本；[FileService](../../src/web_domain/file_services.py#L42) 已接受 PDF/DOCX/XLSX/PPTX 等；[createUpload](../../web/src/api/resources.ts#L421) 空 MIME 默认 text/plain | 对齐选择提示和已支持文档类型，明确 MIME 归一化规则，不宣称支持 image/* 或 text/html |
| C12 | [资料弹窗](../../web/src/components/shell/AppShell.tsx#L268) 已通过 Shell modal 挂载 WorkspacePanel authority；关闭时 refresh；原面板已有授权/撤销 | UI-2 复用原高级管理入口，仅提取 AI 读取选择与查询失效能力，不能退回一级资料页 |
| C13 | [sessionLifecycle](../../web/src/store/sessionLifecycle.ts#L12) 已同步清理 stores；[隔离测试](../../web/src/store/sessionIsolation.test.ts#L49) 定义了旧发送/上传/Model Input 迟到隔离 | 新增草稿、资源查询、分页与提交意图均接同一清理边界 |
| C14 | [App 恢复测试](../../web/src/App.recovery.test.tsx#L167) 已覆盖同对话重选不重建 runtime；[App 测试](../../web/src/App.test.tsx#L288) 覆盖切页 feed / 草稿存活 | 在原断言上扩展，不能因新布局删除关键行为断言 |

标题修改特别说明：后端 [PATCH 路由](../../src/web_api/app.py#L1300) 读取 If-Match，并不消费 Idempotency-Key。wrapper 接收 key 不代表服务端实现了该命令的幂等重放；本阶段依赖 metadata_version 的乐观并发控制，响应不确定时先 GET 核对。

## 3. 目标结构与状态所有权

### 3.1 组件调整

```text
AppShell（保留路由、账户、唯一 InspectorHost）
├─ ConversationSidebar（增强原组件）
└─ ChatPane（保留为 AI controller / 稳定容器）
   ├─ ConversationHeader（新增：标题、编辑、当前对话资料）
   ├─ RunStatus / Trace 入口（暂保留，UI-3 接管）
   └─ HpThread（原 external-store runtime）
      ├─ MessageViewport（消息、历史加载、滚动锚点）
      └─ Composer 展示区域
         ├─ ConversationResourceChips（新增：长期授权）
         ├─ AttachmentTray（从原 HpThread 提取）
         ├─ 输入框
         └─ 工具栏：附件 / 使用资料 / 快速·深度 / 发送·停止
Shell modal / 现有 Surface
├─ ConversationResourcePicker（新增：读取授权最小闭环）
└─ WorkspacePanel authority（保留高级资料管理）
```

具体拆分文件数可随实现调整，组件名不构成新的架构层。Header 不重复显示 Shell 的“AI”与同级标题；Shell 保留移动侧栏按钮和页面语义标题，ConversationHeader 承担对话操作。

### 3.2 状态职责

| 状态 | 所有者与约束 |
| --- | --- |
| conversations、分页游标、加载/错误、activeConversation 元数据 | Workbench；元数据可来自 detail，不要求所选深链对话位于首批列表 |
| messages、activeRun、attachments、发送/停止、feed | Workbench 单一权威；assistant-ui 只消费映射，不维护第二份服务器消息 |
| 草稿、选中的执行模式、滚动锚点、底部跟随标记 | 建议新增 `store/conversationUi.ts`，按 accountId + conversationId（含空态键）保存内存 UI 状态；接 sessionLifecycle reset，不写 URL/localStorage |
| pending create / send 的 key、冻结 payload、命令阶段 | Workbench 内部命令状态；与 UI 草稿分开，不放 Shell；已提交后 UI 切走不改变原目标 |
| 资料 grants / tree、加载错误及失效序号 | 建议 `components/conversation/useConversationResources.ts`，明确是服务器查询状态；带 account generation + 对象 request token |
| route、resources modal、侧栏开闭、Inspector | 继续 useShell；不再新增独立“打开资料页”状态 |

不复制 messages/Run/HTML 进 UI 缓存。草稿缓存采用有界策略，例如仅保留最近 30 个无未决操作的对话位置记录；非空草稿不能静默淘汰，应保留至会话结束或明确告知用户。账户退出清空全部缓存。

## 4. 关键交互和契约

### 4.1 对话列表、分页与标题

- Sidebar 按本地日历日期分“今天 / 昨天 / 近 7 天 / 更早”，使用 updated_at；同组按服务端稳定顺序，不能从 UUID 推算日期。跨午夜或页面重新可见时重新分组。
- 标题过滤文案明确“筛选已加载对话”；保留“加载更多”，不把局部空结果写成全局无对话。不显示没有完整来源的全量计数。
- Workbench 增加 conversationCursor、hasMoreConversations、loadingMoreConversations、分页错误及 loadMoreConversations。列表刷新与追加使用同一查询世代：刷新使旧追加失效，追加串行，按 conversation_id 合并去重。
- 初始失败显示重试；追加失败保留已有项与当前选择；cursor_expired / invalid_cursor 提供从首批刷新，保留选中对象和草稿，不无限循环请求。更新排序后的 cursor 分页不保证历史快照一致性，因此不宣称列表已实时完整。
- 新建/改名后 upsert 真实 conversation，并重建或失效旧分页链；深链通过 detail 加载，不因未命中列表而报不存在。
- Header 标题修改沿用 `renameConversation`，If-Match 格式为 `"conversation-{id}-m{metadata_version}"`。标题长度/归一化遵循后端 `_normalize_title`；412 version_conflict、428 缺少前提、422 校验失败分别反馈，冲突保留输入、刷新版本后由用户重新确认。
- 改名响应丢失先读取 detail 比较目标标题；不能凭 wrapper 的 key 盲重试。迟到成功可更新原对象缓存，但不能覆盖另一个对话的编辑框或导航。

### 4.2 空态与首次发送

`#/ai` 展示 HpAgent 空态和完整 Composer，输入文字本身不创建 Conversation。建议 Sidebar“新对话”进入这个空态，已有“新建后立即得到空对象”的测试需改为验证首次实际操作时创建。

首次发送采用明确的 `ensureConversation → sendMessage` 流程：

1. 捕获当前账户、空态草稿版本及导航 token，锁住一次创建/发送意图。
2. 通过现有 POST conversations 创建真实对象，同一未决创建使用相同 key。
3. 创建成功立即保存 conversation_id，迁移该空态草稿上下文；用户仍在原空态时 replace 为规范的 `#/ai/{id}`，避免返回到一次性中间空态。
4. 仅在原意图仍有效时发送冻结内容；若创建期间用户切换对话/一级入口，则不自动发送、不抢导航，已创建对象留在列表，可继续原草稿。
5. 创建失败保留草稿；创建成功发送失败只重试发送，不重复创建 Conversation。

上传与授权要求真实 conversation_id。空态点击附件或确认“使用资料”时可先 ensureConversation，并在 UI 提示将为新对话准备资料；不自动发送。创建锁与首次发送共用，防止上传、授权、发送各自创建一个对象。关闭选择器不撤销已成功创建的对象。

### 4.3 Composer 状态、提交与 IME

| 状态 | 可编辑 | 发送/停止与反馈 |
| --- | --- | --- |
| 空闲 | 文本、附件、长期资料 | 非空文本且附件全 ready 才可发送；不新增纯附件空消息语义 |
| 附件 uploading / failed | 保留文本，可移除附件 | 发送禁用，显示具体失败；不得提交非 ready file_ids |
| creating / sending | 保留输入；冻结本次提交，避免编辑覆盖 | 防连点；不提前清空草稿或附件 |
| queued / running | 可编辑下一条文本草稿 | 禁止发送；策略锁定；固定图标位显示停止 |
| cancelling / stopping | 保留下一条文本 | 显示正在停止，禁用重复停止及发送 |
| succeeded / failed / cancelled | 恢复编辑和发送 | 清晰结束态；Run 重试仍由现有安全资格判断，cancelled 不普遍提供重试 |
| 发送结果未知 / 连接中断 | 保留输入与意图记录 | 区分 POST 结果未知与已知 Run feed degraded，不误报业务失败 |

快速映射 `react`；深度映射 `plan_and_execute`，仅 durable_agent 能力开放时可选。运行中展示 activeRun.agent_strategy，不把下一次偏好冒充当前策略。file_upload 控制附件入口；资料管理按既有授权 API 的真实权限反馈，不能无依据等同于上传能力。

发送恢复的最小设计：

- 为每次明确提交冻结 `{conversationId, content, fileIds, agentStrategy, key}`，保持现有临时消息替换、message_id 去重与 SSE 真相来源。
- 网络失败或响应丢失先读取对话/运行快照；无法确认时只允许用原 key 和相同 payload 重新确认。不能以“文本相同”认定已发送，也不能生成新 key 再盲发。
- 后端重放返回的 user_message/assistant_message 与当前消息按 ID 合并；同一 Run 不重复启动 feed。
- 明确收到校验拒绝后允许修改参数创建新意图；结果未知时先核对原意图，不悄悄将变更参数绑定旧 key。`conversation_busy` 保留输入并同步已有运行。
- 成功仅清除此次提交对应的草稿版本；若保留了后续输入，不得被旧成功响应清空。切会话后收到结果只更新原命令上下文，账户 reset 后完全丢弃。
- 验证 assistant-ui 当前版本的 composer 控制接口，显式处理 onSend 的成功/失败；不用启用 onEdit/onReload 的方式绕过 store。测试必须覆盖浏览器输入框真实内容，不能只断言 store 返回 false。

IME composition 期间 Enter 不发送；compositionend 后正常 Enter 发送，Shift+Enter 换行。保留原键盘习惯并覆盖中文输入法事件边界。图标按钮具有 accessible name、tooltip、禁用原因；错误就地关联输入，流式 token 不逐字 aria-live 播报。

### 4.4 当前轮附件

上传仍使用 `createUpload → PUT content → send file_ids`。附件按钮可打开“本地上传 / 选择已有文件”，已有候选维持 next_before 分页、ready 校验、最多 10 个和 file_id 去重；补首次/追加 loading、error 与请求 token，避免打开多次或快速切对话交叉追加。

文件选择提示对齐现有服务端白名单：文本及 PDF、DOCX、XLSX、PPTX。空 MIME 的已知 Office/PDF 后缀映射到受支持 MIME；文本仅做明确、安全的映射，未知类型由服务端校验，不能把任意二进制改成 text/plain。JSON/CSV 等不能只凭 input accept 声称对应浏览器 MIME 都受支持。服务端数量/大小限制仍是最终约束，不虚构 capabilities 中不存在的动态限额。

移除新上传附件沿用 deleteFile 行为；移除已有候选只取消当前选择，不删除原文件、不撤销长期授权。运行期间继续保留文本草稿；附件增删策略与现有 store 门控一致，不展示点击后静默无效的入口。

本阶段默认仅保证文本/策略/滚动跨对话恢复；当前 store 会清空待发附件，因此必须在切换前处理：存在 uploading/failed/ready 待发附件时提示“继续当前对话 / 放弃本轮附件并切换”，不要静默丢弃。放弃时复用当前附件清理流程，迟到上传不得回灌；已有文件不删除。若后续选择实现附件跨对话恢复，应单独增加按对话的上传任务所有权和 ready 复核测试，不能仅把全局 attachments 数组复制到 UI 缓存。

浏览器前进/后退同样走受保护的切换入口；需要确认时暂时维持旧上下文并规范化地址，确认后再应用目标路由，避免 URL 与当前附件归属不一致。退出/认证失效不等待此确认，直接清理本地状态。

### 4.5 长期资料入口与 Chips

- 长期资料 Chips 位于当前轮附件上方，显示真实 node 名称、读取权限及递归范围；loading/error 与“暂无资料”不同。
- “使用资料”在当前 AI 上下文打开轻量选择器，复用 `getWorkspace` 与 Conversation resources API；确认默认仅 `list_metadata + read_content`，目录递归需显式可见。
- 授权完成并重新读取 grants 后显示已生效 Chip，提示“新增资料下一轮可用”；不改变正在执行 Run 已冻结候选，不自动发送。
- 同 node 多条 operation grant 可以聚合展示，但必须保留实际 grant_ids。Chip 的移除含义是撤销授权，确认显示将撤销的具体规则和可能停止执行的影响；不能只删除本地 Chip。
- 聚合移除可能包含多次 DELETE：逐条跟踪结果，部分失败重新读取 grants，只重试仍存在的规则；反馈 affected_runs 的 stopping/stopped，不乐观宣告全部停止。
- grant 没有通用命令 key 重放契约；响应丢失先读当前 grants 再补缺失操作。读回失败标“待确认”，不显示已生效。
- “管理资料”继续使用现有 authority 弹窗；修改/创建/删除权限不随读取默认选中。复用原高级能力，不在 UI-2 重做全套 GrantEditor。
- 读取选择器与旧管理弹窗共享一个失效通知入口；成功授权/撤销后立即刷新当前主体，不仅依赖关闭弹窗的 refresh。切对话/切账户时旧查询和 mutation 的回调不能改新主体的 Chips。
- 小屏一次一个 modal；从选择器进入管理时替换内容或有明确返回，不叠加平行 Inspector。

File Inspector“在对话中使用”的完整跨对象入口归 UI-4；本阶段预留可指定 conversationId 的授权控制函数，不提前重构 File Inspector。

### 4.6 消息、滚动与生命周期

按对话保存 `{anchorMessageId, offset, atBottom}`。加载更早消息前记录首个可见消息和相对偏移；prepend 完成后恢复同一锚点。若用户在加载期间主动滚动，以最新交互为准，旧恢复请求不得抢位置。

切换对话先保存草稿/锚点，再沿用 selectConversation 关闭原前端 feed 并读取新快照；不会发 cancelRun。返回旧对话后按需串行加载有限历史页以定位锚点；可采用最多 5 页的恢复预算，找不到时明确提供继续加载，不无界拉完整历史、不假称位置已恢复。

用户在底部时才跟随流式输出；阅读历史时显示“有新消息 / 回到底部”。滚动容器保持一个，Composer 在 MainCanvas 底部；打开资料、Inspector、切到空间/任务再返回，不给 runtime 添加 route/requestToken key，不重建聊天实例。

AppShell 普通 `#/ai` 空态切换使用专用 clearActiveConversation/leaveConversation 接线，保留会话列表分页和草稿缓存；账户 reset 继续全清。无效深链显示“对象不可用”和重试/返回，不冒充可向未知对话发送的空态。loadingMessages 不应永久丢失原 UI 草稿，须由缓存明确恢复。

## 5. 实施工作包与依赖

建议按以下顺序形成可独立审阅的提交；是同一 UI-2 阶段内的工作包，不表示当前已实施。

| 工作包 | 主要文件/产物 | 完成条件与依赖 |
| --- | --- | --- |
| P0 基线与契约确认 | 现有测试、安装版本的 assistant-ui 声明、UI-1 接线 | 记录基线检查结果；确认 composer 控制、ETag、cursor、MIME；先于所有修改 |
| P1 查询与命令补全 | `store/workbench.ts`、`api/resources.ts`、必要 DTO 与 store 测试 | 分页/去重、独立会话元数据、ensureConversation 返回真实结果、未决 key 生命周期可测；依赖 P0 |
| P2 Sidebar 与 Header | 原 Sidebar、AppShell、新 ConversationHeader、相关样式 | 日期分组/局部过滤/加载更多/改名/错误恢复；依赖 P1 |
| P3 稳定 Composer 与草稿 | ChatPane、HpThread、runtime、新 conversationUi、sessionLifecycle | 无对话直接输入、首次创建再发送、快速/深度、IME、失败留稿、切对话恢复；依赖 P1，集成 P2 |
| P4 附件与长期资料 | AttachmentTray、候选选择、ResourcePicker/Chips/query hook；WorkspacePanel 最小回调 | 上传与长期授权两条链清楚，部分失败可恢复、不跳一级页；依赖 P3 |
| P5 消息滚动与布局 | HpThread viewport、ChatPane、AI CSS、AppShell 最小布局调整 | 历史锚点、只在底部跟随、导航/Inspector 不重建、移动输入不遮挡；依赖 P3/P4 |
| P6 回归与交接 | 新增/扩展测试、截图、UI-2 验收报告、文档索引 | 第 6 节退出门槛通过；保留 UI-3/4/6 待办清单，依赖 P1–P5 |

建议修改清单：`web/src/components/{ChatPane,ConversationSidebar}.tsx`、`web/src/components/shell/AppShell.tsx`、`web/src/adapters/assistant-ui/{HpThread,runtime}.tsx/ts`、`web/src/store/{workbench,sessionLifecycle}.ts`、`web/src/api/{resources,types}.ts`（按实际需要）、`web/src/styles.css`；新增 AI 小组件集中于 `web/src/components/conversation/`，草稿状态集中于 `web/src/store/conversationUi.ts`。

不计划修改 `src/` 后端、Run/Work 状态机、SSE event 协议或 Artifact 构建协议。阅读后端契约不等于修改后端。若实际实现发现必须改变契约，记录独立 BE 项并说明具体受阻体验，不扩大 UI-2 范围。

## 6. 测试与验收矩阵

以下是后续必须执行的计划，不是本次测试结果。新增用例验证行为及竞态，不测试纯组件拆分或 CSS 实现细节。

| 编号 | 场景与预期 | 测试落点 |
| --- | --- | --- |
| U2-01 | 超过 30 个对话可继续加载；重叠页不重复；刷新与追加乱序不回灌；失败保留已有项 | workbench.test + Sidebar 测试 |
| U2-02 | 本地日期边界、长标题、已加载标题过滤、深链不在首批也可打开 | Sidebar/Header 测试 + conversation E2E |
| U2-03 | 改名成功同步 Header/列表；412 保留输入；响应丢失读回；A→B 迟到不串标题 | Header/API 集成测试 |
| U2-04 | 空态首次发送只创建一个对话和一条消息；连点/StrictMode 不重复；创建后发送失败不再创建 | workbench + App + E2E |
| U2-05 | 创建期间切页/切对话不抢导航、不隐式发送；空态授权/上传复用同一创建结果 | App.recovery + E2E |
| U2-06 | POST 失败/响应丢失保留真实输入框；原 key 原 payload 重放；临时/服务器消息不重复 | Composer/App + workbench + 请求故障注入 |
| U2-07 | 中文 composition Enter 不发送；正常 Enter 一次发送；Shift+Enter 换行 | Composer 组件测试 + 浏览器事件测试 + 人工 IME |
| U2-08 | 快速/深度参数正确；能力关闭无深度入口；running/cancelling 锁策略与发送，下一条文本仍可编辑 | ChatPane/workbench 测试 |
| U2-09 | 上传 ready 前禁发；失败可移除；已有候选去重/分页/跨对象竞态；文档 MIME 正确 | workbench + conversation E2E |
| U2-10 | 待发附件切换前确认；放弃不删除已有文件；迟到上传不污染新对话 | App.recovery + sessionIsolation |
| U2-11 | 资料在当前 AI 弹窗操作；只默认读取；递归可见；grant 失败不显示已授权 | 新 resources 测试 + workspace-p1/p3 E2E |
| U2-12 | 撤销聚合 grant 部分失败真实反馈；受影响 Run 状态如实显示；新授权仅下一轮生效 | resources 测试 + 既有权限后端契约 |
| U2-13 | 历史超过 50 条、页重叠不重复；prepend 不跳；离底部不追随 token；锚点找不到有退路 | workbench + 浏览器滚动测试 |
| U2-14 | A→B→A 草稿/位置正确；空间/任务往返、资料/Inspector 开闭不重建 runtime、不增加 feed | App.recovery/App + 导航 E2E |
| U2-15 | 退出/401/换账户清草稿、分页、grants、pending intent；同 ID 迟到响应无效 | sessionIsolation + auth/multi-tab E2E |
| U2-16 | SSE 重复/乱序/gap/degraded/终态权威不回归；stop/retry 安全判断不变 | 原 runFeed/sseClient/workbench 测试 + disconnect/stop-retry E2E |
| U2-17 | Markdown/GFM/代码块、消息附件下载/保存、HTML 动作、Trace 上下文入口仍可达 | types/App + markdown/artifact/manual-repair E2E |
| U2-18 | 键盘焦点可达、资料关闭回焦、200% 文本、长中英文、reduced-motion、软键盘不遮 Composer | a11y + 多视口截图 + 人工键盘/移动验收 |

布局至少覆盖 360×800、390×844、768×1024、1024×768、1280×800、1440×900、1920×1080。截图包括空态、流式中、长历史、附件失败、资料弹窗及错误、Inspector 打开。电脑模拟 viewport 不等于实机软键盘已验收，应分开记录。

### 6.1 命令与环境

在项目 `web/` 执行；P0 和各工作包先跑受影响测试，最终执行：

```bash
npm run typecheck
npm run lint
npm run build
npm test
```

受影响 E2E 至少包含以下现有测试；新增 UI-2 场景可集中在 `e2e/ui-2-ai.spec.ts`，按实际文件名追加：

```bash
npm run test:e2e -- e2e/conversation.spec.ts e2e/stop-retry.spec.ts e2e/disconnect.spec.ts e2e/auth.spec.ts e2e/multi-tab.spec.ts e2e/markdown.spec.ts e2e/a11y.spec.ts e2e/workspace-p1.spec.ts e2e/workspace-p3.spec.ts e2e/artifact.spec.ts e2e/manual-repair.spec.ts
```

先按 [测试指南](../development/testing.md) 配置隔离 PostgreSQL 三角色 DSN、Redis DB、文件目录和端口。当前 Playwright 为真实 API + PostgreSQL + Redis + Fake Executor，`workers=1`，不能并行共享账号；不得直接采用会清理业务库的默认配置。权限接线还需运行受影响 `test/web_api/test_workspace_v41_p*.py` 契约场景；具体文件按授权/撤销涉及范围选择，报告列明实际命令。

长期命令使用工具支持的长等待，避免只为查询进度反复轮询；不要因为等待超时就重复启动同一测试。保留退出码、失败原因、测试环境及未覆盖范围。Fake Executor、模拟网络和真实模型覆盖分别记录。

### 6.2 退出门槛与交付物

1. U2-01–U2-18 均有明确通过证据或未通过原因；关键发送、隔离、权限、IME、分页、恢复链路不能标为“待后续阶段补齐”后宣布完成。
2. 前端四项检查通过；受影响 E2E 和权限契约验证完成。历史失败单独记录，不以减少断言掩盖失败。
3. 新旧能力映射齐全：新建/选对话、send/stop/安全 retry、历史、附件、资料、下载/保存、HTML、Trace 均有可达入口。
4. 提交 UI-2 验收报告：实际 HEAD、改动文件、对应 D/FE-B/U2 编号、命令/结果、桌面与移动截图、成功/失败恢复/迟到响应三类操作记录、剩余环境限制。
5. 交接 UI-3：RunStatus/Trace 暂留的位置与回调；UI-4：ResourcePicker 查询、grants 失效与高级权限入口；UI-6：消息 HTML/保存接口保持情况。

## 7. 风险、回退与发布约束

| 风险 | 预防与验收重点 |
| --- | --- |
| assistant-ui 在失败前已清 Composer | P0 核实当前依赖 API，P3 测真实输入值；必要时局部受控 Composer，仍复用同一 runtime |
| 新空态与原 Shell reset 互相清状态 | 单独 leaveConversation；账户 reset 与导航生命周期分开；覆盖首次创建及 A→B→A |
| 幂等记录被切页丢弃造成重复消息 | 记录归命令层，绑定账户/原对象/冻结 payload；结果未知先恢复，不用新 key 盲发 |
| 附件与长期资料混为一体 | 两套视觉分区、不同 API/移除语义、独立 loading/error，覆盖 partial success |
| UI-2 过早重做空间/诊断 | 保留既有 authority、RunStatus、Inspector bridge，新增仅 AI 必需部分并记录移交 |
| 新布局导致双滚动、失焦或重复 feed | 稳定挂载、单 viewport、移动 Surface 与底部 safe-area；浏览器验证连接数和锚点 |

每个工作包保持旧领域命令与 API，不删除尚未迁移能力。回退按 UI-2 提交逆序恢复前端实现，保留现有 UI-1 Shell；本次当前代码没有需要假定存在的全局新旧 Shell 切换开关。如采用临时开发开关，仅切视图且共用 store，并登记 UI-7 清理。

回退不删除服务器对话/消息/授权/文件，不取消用户后台工作，不逆写 Run/Work 状态。UI-2 完成只表示 AI 阶段通过，不自动代表整个新 Shell 可向真实用户发布；全量切换仍遵循原方案 §14.4 的原能力入口完整性门槛。
