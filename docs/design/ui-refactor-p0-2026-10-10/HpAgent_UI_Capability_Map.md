# HpAgent UI Capability Map — P0 系统能力审计

审计日期：2026-10-10（Asia/Shanghai）。代码基线：当前检出 `main`，HEAD `a47d73b06a993d353b4177e282b69ddd28ffdb36`；开始时工作区干净。当前本地 `origin/main` 指向同一提交，另有 `codex/workspace-v41-final-acceptance` 分支；本次没有切分支、拉取远端或以默认分支名称推断新旧。所有结论限定于该工作副本。

本轮只读源码、契约与测试，新增四份 Markdown 审计文档；未运行应用、真实模型、QQ、数据库或大规模测试。**“已验证”指源码及调用关系已核实，不表示本轮完成运行验收。** API 清单从当前 `create_app` 提取，共 88 个显式 HTTP route decorator（包括健康检查与认证页面），见 Integration Map 附录。

## 1. 阅读规则与系统对象

标记：**F**＝已验证事实；**I**＝合理推断/可组合的设计空间；**U**＝本轮未确认。支持程度：**Web**＝存在实际组件调用链；**API**＝后端存在但 Web 未完整接入；**条件**＝还受配置、权限、运行环境或来源限制。下表 S 编号指向文末源码证据，T 编号指向相关测试源码。API 路径中的 `c/w/r/n/f/a/v` 分别表示 Conversation、Work、Run、Workspace node、File、Artifact、Artifact Version ID。

| 业务对象 | 用户可理解的职责 | 关键不可混淆关系 | 依据 |
| --- | --- | --- | --- |
| Account / Identity / Web session | 账户所有权、入口身份、登录和费用治理 | Web/QQ 可绑定同一账户；浏览器会话不是 Agent 执行会话 | F：S01、S02、S03 |
| Conversation / Message | 一段有顺序的交互及用户/助手消息 | 同一对话最多一个活跃 chat Run；后台 Work 不占此槽位 | F：S04、S05、S06 |
| Work / Requirement | 持续委托、不可变要求版本、继续条件、累计预算 | Work 可关联多个 Conversation；关联不导入全文、附件或授权 | F：S07、S08、S09 |
| Run / Execution | 一次有限执行及其中隔离的执行上下文 | chat snapshot 有 assistant_message；work snapshot 没有，Conversation 为 null；Execution 不是独立用户委托 | F：S04、S06、S10、S27 |
| File / node / destination / revision / grant | 不可变字节、长期入口、可更新目标、历史版本与使用权限 | `file_id` 与 `node_id` 不同；路径不作身份；所有者浏览权不等于 Agent 权限 | F：S15、S16、S17 |
| Artifact / Artifact Version | 可预览成果及具体生产版本 | 生产、交付引用、采用、保存、验收分别记账；手工新版不能改写原验收证据 | F：S09、S21、S22 |
| Notification / Delivery / receipt / acceptance | 通知事实、每个渠道投递及用户验收 | 渠道接受不等于已读，也不等于接受成果 | F：S09、S24 |

## 2. 身份与账户能力

| ID / 能力 | 用户操作 | 输入 | 输出 | 前端入口 | API / SSE | 对象、必须遵守的约束 | 支持与证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A01 注册与登录 | 注册或登录账户 | username、password；注册可选 invite_code | 注册事实、Cookie session；`/me` 账户/身份/CSRF/capabilities | `LoginForm` → `ApiClient.register/login` → `useAuth.check` | `POST /auth/register`、`POST /auth/login`；`GET /api/v1/me` | 密码策略 8–128 字符；注册成功可能未建立会话；登录成功须再查 `/me`；只信 HttpOnly Cookie | F/Web：S01–S03、T01 |
| A02 会话恢复、退出 | 打开应用、重试恢复或退出 | Cookie；退出 CSRF | signedIn/signedOut/error；服务端撤销会话 | `App` / `useAuth.check/signOut/expire` | `GET /me`、`POST /auth/logout`（均在 `/api/v1`） | 401/流过期清空跨领域缓存和请求；重新登录会撤销同 Web identity 既有会话；退出本地清理不证明网络撤销成功 | F/Web：S02、S03、S28、T02 |
| A03 QQ 身份绑定 | 生成码，在 QQ 发绑定命令，核对状态 | challenge；QQ 真实身份 | pending/completed/cancelled/expired、脱敏 QQ 身份 | `QQBindingPanel` → `ApiClient.create/getQqBindingChallenge`；3 秒查询 | `POST /api/v1/identity-bindings/qq/challenges`、`GET .../{challenge_id}` | 绑定由 QQ 入口完成；不是自由填写 QQ 号；没有本轮发现的 Web 自助解绑/换绑 API | F/Web+条件：S01、S03、S29、T01；真实 QQ U |
| A04 跨入口连续使用 | 在 Web/QQ 使用相同账户能力 | 已验证 identity/account | 账户拥有的 Conversation/Work/资源 | Web 查询账户对象；QQ 为服务端入口 | 当前账户 API；QQ ingress，不是 Web 专属接口 | 共享 Account 不代表合并所有对话；QQ 群目标和群工具观察值限制私有信息 | F/条件：S03、S24、S26；部署/真实渠道 U |

## 3. Conversation 与有限执行能力

| ID / 能力 | 用户操作 | 输入 | 输出 | 前端入口 | API / SSE | 对象、必须遵守的约束 | 支持与证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C01 新建、恢复、查阅对话 | 开始对话、选择历史、加载更多 | 可选 title；conversation_id；cursor | Conversation、消息序列、active chat Run | `ConversationSidebar` / `ChatPane` → `useWorkbench.ensureConversation/selectConversation/loadMore*` | `POST/GET /api/v1/conversations`、`GET /conversations/{c}`、`GET .../messages` | 空白本地草稿可延迟建实体；分页按服务端顺序归并；无 Conversation 删除/归档/分享 endpoint | F/Web：S04–S06、S11、T03 |
| C02 改名 | 修改当前对话标题 | title、metadata_version ETag | 新标题与 metadata_version | `ConversationHeader` → `renameActiveConversation` | `PATCH /api/v1/conversations/{c}` + If-Match | 1–200 字符；metadata_version 不随消息数量变化；412 冲突需刷新核对 | F/Web：S01、S05、S11、T03 |
| C03 发送请求与策略选择 | 提交文字，选择 ReAct/Plan-and-Execute | 非空 content、agent_strategy、可选 file_ids、意图 key | 原子接受 user Message、pending assistant Message、queued Run、events_url | `ChatPane` / `HpThread` / runtime → `useWorkbench.sendMessage` → `HpApi.sendMessage` | `POST /api/v1/conversations/{c}/messages` | ≤32,000 字符且 ≤128 KiB UTF-8；附件上限取服务端配置；同对话 queued/running/cancelling 占槽；不能在活跃执行时追加、编辑或分叉消息 | F/Web：S01、S04、S11–S13、T03 |
| C04 附件与既有输入选择 | 上传/移除待发送附件，选已有可用文件 | File；file_id；Conversation | uploading/ready/rejected；绑定后的输入引用 | `ChatPane` → `addAttachments/loadFileCandidates/selectExistingFile/removeAttachment` | `POST /conversations/{c}/uploads`、`PUT /uploads/{f}/content`、`GET .../file-candidates`、`DELETE /files/{f}`（均 `/api/v1`） | 上传开关；ready 才能发；本地最多 10 件，DTO 上限 20，部署默认 10；绑定/授权由后端核验；移除既有选择不删除源文件 | F/Web+条件：S01、S11、S14、S16、T03、T04 |
| C05 跟踪、恢复执行 | 查看状态、进度、回复；刷新/重开 | run_id；Cookie | snapshot、delta、progress、trace、终态及 budget | `useWorkbench.startRunMonitor`、`openRunFeed`、`RunStatus`；`RunInspector` | `GET /api/v1/runs/{r}`、`GET .../events` SSE | 进度是易失提示，不是正文；终态用数据库 snapshot 替换 delta；流缺口永久停止本流拼接，改查 REST；关闭页面不取消 Run | F/Web：S10–S13、S19、T05 |
| C06 取消聊天执行 | 显式停止本次执行 | run_id、意图 key | 200 cancelled 或 202 cancelling snapshot | `RunStatus` / runtime onCancel → `useWorkbench.stopRun` | `POST /api/v1/runs/{r}/cancel` | queued 且未派发可直接终止；正在执行需等待确认；不能把 cancelling 当 cancelled；取消 chat 不撤回已创建 Work | F/Web：S04、S10、S11、S26、T03 |
| C07 安全重试失败聊天 | 重试可重试失败 | source_run_id、意图 key | 新 Run 与新 assistant Message，或复用直接 retry child | `RunStatus` → `useWorkbench.retryRun` | `POST /api/v1/runs/{r}/retry` | **只接受 failed，cancelled 不可重试**；failure policy 和副作用安全由服务端裁决；冻结输入继承；Work Run 返回 use_work_advance | F/Web：S04、S11、S30、T03 |
| C08 查看诊断与审批 | 查历史 Run、轨迹、模型输入；允许/拒绝待审批操作 | run_id / snapshot_id / approval_id、明确决定 | Trace tree、模型快照投影、审批事实、Run budget | `RunLookup/Inspector/Resources/RunApprovals` → `useRunInspector` / `useTraceStore` | `GET /runs/{r}/trace`、`.../model-inputs`、`GET /model-inputs/{id}`、`GET .../file-action-approvals`、`POST /file-action-approvals/{id}/approve\|reject`（均 `/api/v1`） | 模型输入 visibility none/summary/full_safe；审批绑定账户/Run/operation/参数 hash，单次消费、有过期；允许不等于操作完成；历史 chat 在现 UI 只读/返回对话 | F/Web+条件：S01、S18、S20、S25、T06、T07；所有工具触发审批覆盖面 U |

## 4. 持续 Work、研究与交付能力

| ID / 能力 | 用户操作 | 输入 | 输出 | 前端入口 | API / SSE | 对象、必须遵守的约束 | 支持与证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| W01 结构化接受委托 | 创建提醒、研究或通用工作 | title≤200、Requirement（objective≤20,000、完整JSON≤32KiB）；可选来源 Conversation/Message | Work、revision=1、budget、schedule/wakeup、默认 inbox 目标 | `TaskEditor` → `buildRequirement/taskCommand` | `POST /api/v1/works` | capability 服务端白名单；需求不可变；接受不等于开始/完成；非终态 Work 接受数量受配置限制 | F/Web：S07、S08、S23、T08 |
| W02 用自然语言管理委托 | 向 Main 明确委托或要求修改/控制 | 普通聊天；精确 Work ID/版本/稳定 mandate_slot | 同一 WorkCommandService 的提交事实 | C03 → Agent tool execution → `create_main_work_tools` | 复用聊天 API；服务端工具 accept/list/get/revise/control/advance/link_work | 指代歧义必须澄清；Main 工具只装入 chat 上下文；实际模型能否正确选工具本轮未运行 | F/条件：S26、S31；“每次自然语言都可成功”U |
| W03 三类工作执行 | 等提醒、研究证据/报告，或委托通用工具工作 | Requirement.spec、资源、完成条件 | deterministic reminder；fixed research；generic work 的执行结果 | `TaskEditor/TaskOutputs/RunInspector` | Work 命令、Run 查询；Research `GET /api/v1/runs/{r}/research/report\|evidence` | 后端四种 capability 另含 artifact_build；策略注册/版本冻结；通用文字回复不自动算完成证据 | F/Web/部分API：S08、S10、S27、S32、S33；证据列表缺 Web 专用调用 |
| W04 即时、一次、每日计划 | 设立即/指定时间/每天 | timing.kind、IANA timezone、offset due_at 或 HH:MM local_time | 持久 schedule/occurrence/wakeup 和 continuation | `TaskEditor` → `buildRequirement/localToInstant` | `POST /works` 或 `POST /works/{w}/revisions`（均 `/api/v1`） | daily 必须 ongoing；once catch_up，daily latest；DST 重叠 first fold、缺失跳过；Web 新编辑仅 UTC/Asia/Shanghai，其他现值保留只读 | F/Web子集：S07、S08、S23、S34 |
| W05 查询、分类与审计 | 看委托、运行和事件；查精确 ID | work_id、before、event after | Work snapshot、最近最多100次Run、按after分页的事件 | `TaskScreen/Lookup/Inspector/Events`、`useWorks.load/refresh/receive` | `GET /works`、`/works/{w}`、`.../runs`、`.../events`、`.../events/stream`（均 `/api/v1`） | Work event_seq 独立于 chat seq；四桶是本地投影；扫描未完不能称总数精确；全列表扫描不代表明细完整 | F/Web：S07、S23、S35、T08 |
| W06 修订、暂停、恢复、停止 | 修改要求或控制委托 | 完整可编辑 Requirement/change_reason；Work ID、row_version、key | 新 revision/control_epoch、取消协调执行、Work 最新状态 | `TaskEditor/Actions` → `useWorks.command/control`、`taskCommand` | `POST /works/{w}/revisions\|pause\|resume\|stop`（均 `/api/v1`） | If-Match+幂等；暂停不自动恢复；未决副作用保持 pausing/stopping；终态不可修订/恢复；旧回执不完成新 revision | F/Web：S07、S09、S10、S23、T08 |
| W07 推进/再次尝试 | 条件允许时继续 Work | 当前 Work ID/row_version/key | 202：可能 new Run、not_due、waiting_capacity、budget_exhausted | `TaskActions` → `useWorks.control('advance')` | `POST /api/v1/works/{w}/advance` | active、无 coordinator、无未决 operation；awaiting_input/awaiting_delivery 不能直接推进；202 不是承诺已经执行；不同于 chat retry | F/Web：S07、S10、S23 |
| W08 关联对话 | 将委托关联当前 Conversation | work_id、conversation_id、If-Match/key | work_conversations 关系 | `TaskInspector` → `useWorks.link` | `PUT /api/v1/works/{w}/conversations/{c}` | 不授予资料、导入聊天历史或更改通知目标 | F/Web：S07、S23、S26 |
| W09 资料权限与显式输入 | 授权/撤权目录文件；添加/撤销 input ref | node_id、operations、recursive；file_id/purpose/source_message_id | grants/input refs；affected Run、取消意图 | `TaskResources/ResourceManager/GrantEditor/WorkInputs` → `workCommand/grantMissing/revokeRules` | `/works/{w}/resources` GET/POST/DELETE；`.../inputs` GET/POST/DELETE（均 `/api/v1`） | Work 主体独立；input ref 当前仅接受 ready 且 purpose=input；已发布 output 可通过长期 entry grant 再用；新增下一 Run 生效 | F/Web：S09、S16、S17、S36、T04、T09 |
| W10 累计预算与显式增额 | 核对用量/预留，明确提高某项上限 | Work row_version、budget_version、limits | 累计 used/reserved/limits/version；可能恢复 wakeup | `TaskBudgetDialog` → `useWorks.increaseBudget` | `POST /api/v1/works/{w}/budget` | revision/retry/fallback/Subagent 不重置累计预算；不能降低 limits；Work 增额不修改 Account 日额度、权限或 Run 固定预算 | F/Web：S09、S23、S37、T09 |
| W11 精确成果引用与验收 | 看原交付，明确接受指定版本；选择现成 Artifact 作 input/evidence | artifact_version_id、role；验收 requirement_revision | work_artifacts、acceptance、completion receipt | 验收：`TaskOutputs` → `acceptanceEligible/useWorks.control`；input/evidence 引用未找到正式选择入口 | `GET/POST /api/v1/works/{w}/artifacts`、`POST .../accept-result` | 当前 revision/control_epoch、成功执行及持久证据必须匹配；用户验收需 criterion；不能提交 deliverable role 冒充生产/完成 | F/Web验收+API引用：S09、S23、S38 |
| W12 通知目标、收件箱与投递决议 | 查看/设置/停用目标，读取通知，核查失败/未知投递 | 已验证 source_message_id/content_scope；delivery_id/outcome | targets、notifications、delivery_decisions、receipt | `TaskTargets/Inbox/DeliveryDecision` → `useWorks.command/resolveDelivery` | `/works/{w}/notification-targets` GET/PUT/DELETE、`GET /notifications`、`POST .../deliveries/{id}/resolve`（均 `/api/v1`） | 无自由收件人地址；QQ 群只 summary；unknown 不自动重发；not_sent 可能重发；重复风险需独立明确决定；无服务端 unread/read API | F/Web+条件：S01、S09、S24、S38、T09 |
| W13 有界 Subagent 执行可观察 | 查看通用工作中的分支执行 | Work Run ID；分支由 root 发起 | branches 状态、来源、用量/结果引用 | `RunInspector` 渲染 work snapshot.branches | `GET /api/v1/runs/{r}`；内部 `delegate_work` | Generic Work root 每 Run 一次，最多3分支、每支最多2尝试，只读/授权子集/共享预算、禁止递归；没有用户任意创建 Agent 团队 API | F/条件：S10、S27、S39；真实执行 U |

## 5. 文件与长期 Workspace 能力

| ID / 能力 | 用户操作 | 输入 | 输出 | 前端入口 | API / SSE | 对象、必须遵守的约束 | 支持与证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| F01 长期上传与保存 | 上传至账户空间，可另选用于对话 | 浏览器 File、parent_id、name；可选 Conversation 主体 | stored File ready、长期 node、可选 grants | `UploadToWorkspaceDialog` → `startSave/resumeSave` | `POST /api/v1/workspace/uploads`、`PUT /uploads/{f}/content`、`POST /workspace/files` | 三阶段独立；保存后授权失败只补授权；支持 txt/md/log/PDF/DOCX/XLSX/PPTX，禁止假定图片/音视频/任意 MIME；默认128 MiB由配置决定 | F/Web+条件：S14–S17、S36、T04 |
| F02 浏览、检索与占用解释 | 看目录、过滤文件、查来源和保留 | name、summary、content_type、purpose、work_id、source_run_id、日期、after | 树、分页匹配、唯一文件物理空间、retention/trace | `WorkspaceScreen/FileList/FileInspector` → `useWorkspace/query/searchFiles` | `GET /api/v1/workspace`、`.../search\|space`、`.../nodes/{n}/trace`、`.../files/{f}/retention` | summary 为已索引摘要过滤，不能宣称任意全文语义搜索；按 file_id 去重计字节；删除入口不等于释放空间 | F/Web：S15、S17、S40、T04 |
| F03 目录和入口管理 | 新建目录、重命名、移动、移除入口 | parent_id/name；impact preview_token | 稳定 node；冲突 reason；潜在影响 Run | `WorkspaceScreen/WorkspaceMutationDialog` → `workspaceApi` | `POST /workspace/directories`；`GET .../nodes/{n}/impact`；`PATCH/DELETE .../nodes/{n}`（均 `/api/v1`） | 不用路径作身份；root 不变、目录不能循环/非空删除、同级不重名；变更需新预览；并非批量/递归物理删除 | F/Web：S01、S15、S36、T04 |
| F04 查看正文、下载和血缘 | 预览可读文本，下载文件，核对来源 | file_id / node_id | 文本/Markdown、octet-stream 下载、lineage/trace | `FilePreview/FileInspector/FileVersions/RunResources/TaskOutputs` → `readFileText/getFileLineage` 或下载链接 | `GET /api/v1/files/{f}`、`.../content\|lineage` | Web 正文预览仅 ready UTF-8已知大小≤1 MiB文本；Office/PDF上传/处理不等于浏览器已有内嵌预览 | F/Web：S14、S17、S40 |
| F05 保存既有输出与版本管理 | 将发布文件另存；升级为可更新 entry；用 Run 输出提交新 revision | parent/file/name/key；更新 run_id/file_id/expected_revision/sha256 | 新 entry 或 immutable revision、CAS conflict | `SaveToWorkspaceDialog/FileVersions` → `resumeSave/startVersionUpdate/resumeVersionUpdate` | `POST /workspace/files`、`GET .../nodes/{n}/versions`、`POST .../upgrade\|versions`（均 `/api/v1`） | 新版须来自该 Run 的已发布输出；当前 update_content、固定基准、合法 Run 状态再检查；冲突保留 output 可另存；不是直接上传任意字节覆盖 | F/Web：S15、S16、S36、T10 |
| F06 对话授权与撤权 | 让当前/新对话使用文件；管理操作与递归规则；撤附件引用 | Conversation/Work、node、operations、recursive、grant_id | grants、受影响 Run 及 stopping/stopped | `UseInConversationDialog/GrantEditor/ConversationResources` → `grantMissing/revokeRules` | `/api/v1/conversations/{c}/resources` GET/POST/DELETE；`DELETE .../attachments/{f}` | 五种操作独立；递归显式选择；继承从源规则撤；创建对话成功而授权失败复用原对话；撤权必须阻断后续访问并清模型输入缓存 | F/Web：S16、S36、T04、T09 |
| F07 核对本 Run 的资源范围 | 查候选、是否固定/读取、已发布输出 | run_id、候选 after；可用 search 参数 | 冻结候选计数/状态、发布文件 | `RunResources` → `HpApi.listRunResources/listRunPublishedFiles` | `GET /api/v1/runs/{r}/resources\|published-files`；`GET .../workspace/search` | 创建 Run 冻结≤500候选、元数据≤512,000字节（精确代码值）；首次 select固定版本；新增不扩容，撤权仍即时检查；Run search未有正式 Web wrapper | F/Web+API：S16、S17、S19、T04 |
| F08 Agent 文件读取/分析/生成 | 通过已授权请求处理文件，取得发布结果或审批 | 自然语言、冻结输入、可用工具 manifest | 文件信息/正文/分析、新文件及执行/审批记录 | C03/W03；输出/审批通过 RunResources 查看 | Agent Activities/工具，不存在一个通用“转换任意文件”REST endpoint | 只承诺具体已注册工具；Web 文件模式限定工具集；源码明确跳过 convert_file_to_pdf 注册，不能把工具定义存在等同用户可用 | F/条件：S25、S31、S41；部署工具/转换全覆盖 U |

## 6. Artifact 能力

| ID / 能力 | 用户操作 | 输入 | 输出 | 前端入口 | API / SSE | 对象、必须遵守的约束 | 支持与证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| H01 从回复生成 HTML 成果 | 从已完成助手消息提交生成 | source_message_id、instruction≤4000、key | Artifact、queued Version、独立 artifact_build Work，异步完成 | `ArtifactCreateForm/ArtifactMessageItems` → `useArtifacts.createArtifact` | `POST /api/v1/messages/{id}/artifacts`；按版 REST 查询，无 Artifact SSE | 来源必须同账户、assistant/completed/非空；202不是成果已生成；构建有自己的 Work/Run/费用 | F/Web：S21、S22、S42、T11 |
| H02 查看与增量修改版本 | 选版预览/查历史；自然语言提交修改 | artifact_id、instruction≤4000、前端 expectedParent | 新 version/parent，独立构建事实 | `ArtifactInspector/Composer` → `loadArtifact/createVersion` | `GET /artifacts/{a}`、`GET/POST .../versions`、`GET /artifact-versions/{v}`（均 `/api/v1`） | parent 由后端选最近 completed；不可原位改 completed；现 UI 禁止有构建时再改且复核基准，但 POST没有 parent/CAS字段；并发保护缺口见 Interaction G03 | F/Web：S21、S22、S42、T11 |
| H03 交互预览、下载、源码保存 | 运行受限预览，下载 html，存长期文本副本 | completed version.html、版本来源、保存目录 | sandbox iframe、本地 `.html` 下载、`.html.txt`长期副本 | `ArtifactPreview`、`downloadArtifact`、`SaveToWorkspaceDialog` | 下载为前端 Blob；保存复用F01/F05 | iframe仅 allow-scripts；生成器注入限制CSP；预览错误≠生成失败；保存不授权、不验收、不发布公共网站 | F/Web：S22、S36、S43 |
| H04 从任务查原成果与研究输出 | 查询精确交付/报告及相关文件 | work_id/run_id/version_id | 原引用版本、Markdown报告/HTML Artifact、已发布文件、保存状态 | `TaskOutputs` → `useTaskQuery`；Artifact带origin打开 | Work artifacts/Run report/published-files/Artifact GET | 成果可无 Conversation/Message；研究报告真值与HTML呈现分别读取；原任务证据不能被新手工版替代 | F/Web：S09、S32、S38、S42 |

## 7. 后端已有、Web 未完整开放的能力

以下是**现有能力的接入空间**，不要求新增后端契约；仍需核对权限、参数和运行条件。

| 能力差异 | 已核实的事实 | 边界与证据 |
| --- | --- | --- |
| 更完整的 Requirement 编辑 | 后端接受 constraints、acceptance_criteria、resource_requests、deliverable_policy、completion_mode、Research SourceStrategy；现表单只编辑常用字段，保留其余字段 | F：S08、S23、S33；资源请求/保存目标本身不等于授权；自动保存实施主要为Research，不能泛化所有能力 |
| 多时区和持续委托 | 后端 ZoneInfo IANA、deliverable/ongoing；现编辑器仅两时区，daily才自动选择ongoing | F：S08、S23、S34；若客户端可靠处理DST/offset可扩展表单，I |
| 研究证据原始查询 | ResearchQueryService.evidence 存在，TaskOutputs主要查report/files，无evidence调用 | F：S01、S32、S38；展示方式自由，不保证来源全部正确/联网可用 |
| Run内搜索 | `/runs/{r}/workspace/search`存在，Web HpApi未有对应封装 | F：S01、S17、S19；仅冻结候选内过滤 |
| 现成Artifact作Work input/evidence | POST Work artifacts存在，integration分支验证completed/主体/执行条件；未发现正式选择调用 | F：S09、S38；禁止伪造deliverable/采用 |
| 单独取消Work Run | 后端cancel接受work variant；现RunInspector引导回Work控制，没有单次取消按钮调用 | F：S04、S18；取消一次不停止持续Work；不能用chat retry恢复 |

## 8. 不应宣称的现有能力与未确认项

- **F，未发现接口**：Conversation/Message 编辑或分叉、归档/删除对话、公共分享/成果托管、账户协作与多人ACL、服务端草稿同步、通知已读/未读、通用未知外部operation人工解除、任意cron/weekly计划。依据S01完整路由、S12 adapter省略onEdit/onReload、S08 timing白名单。要支持这些语义须扩展后端或另接服务，不能靠视觉组件补出事实。
- **F，限制存在**：Subagent为内部受限机制，不是可由用户任意编辑的多Agent团队；MCP/本地工具注册与Web文件模式互斥条件，不能仅凭README宣称所有MCP都在Web可调用。依据S31、S39。
- **F，客户端局限**：草稿/未知结果意图主要在账户内存，关闭重开组件可恢复不等于浏览器刷新、跨设备或退出后持久恢复。依据S11、S23、S28、S36、S42。
- **U，运行实证**：当前部署开关、entitlement、模型/工具可用性、真实QQ接受回执、Research长期恢复和文档worker依赖，本轮未调用运行服务。README和架构文档也保留真实模型/QQ等验收缺口；不能用源码/测试存在代替上线放行。
- **U，契约差异**：Research Artifact手工版本限制、输出写权限撤销是否立即主动停止全部有关Run、Work上下文触发所有文件审批的覆盖面，详见Interaction Contract G04/G06/G07。

## 9. 源码证据索引

所有S均为本次已读取的当前代码；函数/类型是定位依据，不以文件或变量名推断功能。

| 编号 | 源码、函数/类型/接口 |
| --- | --- |
| S01 | [src/web_api/app.py](../../../src/web_api/app.py#L272)：`create_app`全部HTTP路由、`csrf_guard/idempotency_key/work_version`、错误映射、消息/文件/Artifact请求检查；[models.py](../../../src/web_api/models.py#L9)：`StrictModel`及请求DTO |
| S02 | [src/web_api/auth.py](../../../src/web_api/auth.py#L34)：`AuthService.login/authenticate/revoke` |
| S03 | [web/src/api/client.ts](../../../web/src/api/client.ts#L73)：`ApiClient.me/login/register/request/resolve/reset`；[store/auth.ts](../../../web/src/store/auth.ts#L33)：`useAuth` |
| S04 | [src/conversation_domain/commands.py](../../../src/conversation_domain/commands.py#L73)：`CommandService.create_conversation/send_message/_explicit_file_authority/cancel_run/retry_run` |
| S05 | [src/web_api/queries.py](../../../src/web_api/queries.py)：`QueryService.list_conversations/list_messages/get_conversation/rename_conversation/get_run` |
| S06 | [src/conversation_domain/admission.py](../../../src/conversation_domain/admission.py#L23)：`SingleActiveRunAdmission`；[web/src/api/types.ts](../../../web/src/api/types.ts)：`HpConversation/HpMessage/HpRunSnapshot/HpWork` |
| S07 | [src/work_domain/commands.py](../../../src/work_domain/commands.py#L26)：`WorkCommandService.accept/revise/control/advance/link/records/_claim` |
| S08 | [src/work_domain/models.py](../../../src/work_domain/models.py#L44)：`Requirement.to_dict/from_dict`、`WORK_STATUSES/CONTINUATIONS` |
| S09 | [src/work_domain/integration.py](../../../src/work_domain/integration.py#L183)：`integration_command/projections/validate_evidence`；[completion.py](../../../src/work_domain/completion.py#L10)：`WorkCompletionPolicy.accept` |
| S10 | [src/run_domain/lifecycle.py](../../../src/run_domain/lifecycle.py#L15)：`RunLifecycleService.finish_in_uow/check_work/unresolved_effect/converge_controls` |
| S11 | [web/src/store/workbench.ts](../../../web/src/store/workbench.ts#L234)：`createWorkbenchStore`内发送、切换、上传、取消、重试和监控函数 |
| S12 | [web/src/adapters/assistant-ui/runtime.ts](../../../web/src/adapters/assistant-ui/runtime.ts)：`useHpThreadRuntime`；[types.ts](../../../web/src/adapters/assistant-ui/types.ts)：`toThreadMessageLike` |
| S13 | [web/src/sse/runFeed.ts](../../../web/src/sse/runFeed.ts)：`openRunFeed/StreamSequencer`；[sseClient.ts](../../../web/src/sse/sseClient.ts)：`openSseStream` |
| S14 | [src/web_domain/file_services.py](../../../src/web_domain/file_services.py#L56)：`FileService`、`ALLOWED_DECLARED_TYPES`；[src/web_api/config.py](../../../src/web_api/config.py)：`WebApiSettings`文件开关和上限 |
| S15 | [src/workspace/catalog.py](../../../src/workspace/catalog.py#L44)：`WorkspaceCatalog.initialize/save_file/upgrade_file/update_file/preview_change/move/remove` |
| S16 | [src/workspace/resources.py](../../../src/workspace/resources.py#L27)：`ResourcePolicy.grant/revoke/snapshot_in_uow/_select/check_file/check_dispatch/copy_retry_in_uow` |
| S17 | [src/workspace/discovery.py](../../../src/workspace/discovery.py#L23)：`WorkspaceDiscovery.search/space/trace/retention` |
| S18 | [web/src/components/run/RunInspector.tsx](../../../web/src/components/run/RunInspector.tsx)：`RunInspector/ModelInputs`；[store/runInspector.ts](../../../web/src/store/runInspector.ts#L38)：`createRunInspector.load/loadApprovals/decide` |
| S19 | [src/web_api/sse.py](../../../src/web_api/sse.py#L106)：`SSEGateway.stream`；[web/src/components/run/RunResources.tsx](../../../web/src/components/run/RunResources.tsx)：`RunResources/RunApprovals` |
| S20 | [src/web_api/model_observability_queries.py](../../../src/web_api/model_observability_queries.py#L19)：`ModelObservabilityQueries`；[snapshot_projection.py](../../../src/model_observability/snapshot_projection.py#L75)：`full_safe_projection` |
| S21 | [src/web_artifacts/services.py](../../../src/web_artifacts/services.py#L23)：`ArtifactService.create_artifact/create_version/_enqueue` |
| S22 | [src/web_artifacts/build.py](../../../src/web_artifacts/build.py)：`ArtifactBuildService.execute/_prepare/_complete`；[publisher.py](../../../src/web_artifacts/publisher.py#L10)：`publish`；[generator.py](../../../src/web_artifacts/generator.py#L110)：`WebArtifactGenerator.harden` |
| S23 | [web/src/components/tasks/TaskEditor.tsx](../../../web/src/components/tasks/TaskEditor.tsx)：`TaskEditor/EditorForm`；[taskRequirement.ts](../../../web/src/components/tasks/taskRequirement.ts)：`buildRequirement`；[taskOperations.ts](../../../web/src/components/tasks/taskOperations.ts)：`taskCommand/acquireTaskLock`；[store/works.ts](../../../web/src/store/works.ts)：`useWorks` |
| S24 | [src/delivery/service.py](../../../src/delivery/service.py)：`ensure_inbox/target_from_origin/target_authorized/DeliveryReceiptHandler/unresolved_delivery` |
| S25 | [src/file_domain/approvals.py](../../../src/file_domain/approvals.py#L53)：`FileActionApprovalService.request/decide/consume/authoritative_status` |
| S26 | [src/application/main_agent.py](../../../src/application/main_agent.py#L27)：`create_main_work_tools/MAIN_ROLE_CONTEXT` |
| S27 | [src/run_domain/admission.py](../../../src/run_domain/admission.py#L14)：`admit_work_run`；[src/orchestration/agent_lifecycle_workflow.py](../../../src/orchestration/agent_lifecycle_workflow.py#L73)：`AgentLifecycleWorkflow` |
| S28 | [web/src/store/sessionLifecycle.ts](../../../web/src/store/sessionLifecycle.ts#L20)：`disposeSessionLifecycle`；[conversationUi.ts](../../../web/src/store/conversationUi.ts)：`useConversationUi`；[artifactUi.ts](../../../web/src/store/artifactUi.ts)：`useArtifactUi` |
| S29 | [src/account/identity_binding_service.py](../../../src/account/identity_binding_service.py)：`IdentityBindingService`；[web/src/components/QQBindingPanel.tsx](../../../web/src/components/QQBindingPanel.tsx)：`QQBindingPanel` |
| S30 | [src/web_domain/failures.py](../../../src/web_domain/failures.py)：`is_failure_retryable/NON_RETRYABLE_FAILURE_CODES` |
| S31 | [src/sandbox/sandbox_manager.py](../../../src/sandbox/sandbox_manager.py#L128)：`create_execution_sandbox` |
| S32 | [src/research_domain/services.py](../../../src/research_domain/services.py)：`ResearchQueryService`；[src/research_activities/runtime.py](../../../src/research_activities/runtime.py#L818)：`_publish/_save_workspace/complete_research_activity/_complete`相关发布/保存/终态逻辑 |
| S33 | [src/research_domain/models.py](../../../src/research_domain/models.py#L21)：`SourceStrategy` |
| S34 | [src/work_domain/timing.py](../../../src/work_domain/timing.py)：`daily_on/next_daily/latest_daily/initial_continuation`；[src/orchestration/work_schedule.py](../../../src/orchestration/work_schedule.py#L23)：`WorkScheduleService` |
| S35 | [web/src/components/tasks/taskPresentation.ts](../../../web/src/components/tasks/taskPresentation.ts)：`presentTask/taskType`；[useTaskController.ts](../../../web/src/components/tasks/useTaskController.ts)：`useTaskController`；[sse/workFeed.ts](../../../web/src/sse/workFeed.ts)：`subscribeWork` |
| S36 | [web/src/components/workspace/workspaceOperations.ts](../../../web/src/components/workspace/workspaceOperations.ts)：`startSave/resumeSave/grantMissing/revokeRules/workCommand/observeRevocation`；[versionOperations.ts](../../../web/src/components/workspace/versionOperations.ts)：`startVersionUpdate/resumeVersionUpdate` |
| S37 | [src/resources/work_budget.py](../../../src/resources/work_budget.py)：`WorkBudgetService`；[model_budget_coordinator.py](../../../src/resources/model_budget_coordinator.py)：预算协调；[account_daily_budget.py](../../../src/resources/account_daily_budget.py)：账户日预算 |
| S38 | [web/src/components/tasks/TaskOutputs.tsx](../../../web/src/components/tasks/TaskOutputs.tsx)：`TaskOutputs/ArtifactResult/RunOutput`；[taskActions.ts](../../../web/src/components/tasks/taskActions.ts)：`taskActions/acceptanceEligible/canRetryDelivery`；[TaskDeliveryDecision.tsx](../../../web/src/components/tasks/TaskDeliveryDecision.tsx)：`TaskDeliveryDecision` |
| S39 | [src/agent_workflows/delegation_contracts.py](../../../src/agent_workflows/delegation_contracts.py)：`DELEGATE_MANIFEST/MAX_BRANCHES/MAX_BRANCH_ATTEMPTS`；[src/agent_activities/delegation.py](../../../src/agent_activities/delegation.py)：`DelegationActivities._prepare/require_root_write/branch_projection` |
| S40 | [web/src/store/workspace.ts](../../../web/src/store/workspace.ts)：`useWorkspace`；[FilePreview.tsx](../../../web/src/components/workspace/FilePreview.tsx)：`FilePreview`；[workspacePresentation.ts](../../../web/src/components/workspace/workspacePresentation.ts)：`canPreview` |
| S41 | [src/sandbox/tools/local/file_read.py](../../../src/sandbox/tools/local/file_read.py)：`create_file_read_tools`；[file_analysis.py](../../../src/sandbox/tools/local/file_analysis.py)：`create_file_analysis_tools`；[file_write.py](../../../src/sandbox/tools/local/file_write.py)：`create_file_write_tools`；[src/workspace/file_capability_config.py](../../../src/workspace/file_capability_config.py)：`FileCapabilityConfig` |
| S42 | [web/src/store/artifacts.ts](../../../web/src/store/artifacts.ts#L78)：`createArtifactStore`内`cacheVersion/poll/command/loadArtifact/loadForMessage`；[ArtifactInspector.tsx](../../../web/src/components/artifact/ArtifactInspector.tsx)：`ArtifactInspector` |
| S43 | [web/src/components/ArtifactPreview.tsx](../../../web/src/components/ArtifactPreview.tsx)：`ArtifactPreview/PreviewFrame`；[artifactDownloads.ts](../../../web/src/components/artifact/artifactDownloads.ts)：`downloadArtifact/artifactFilename` |

## 10. 测试证据与验证等级

这里只说明**当前测试源码断言的范围**；本轮未执行这些测试，也不引用历史通过记录作本轮通过证明。

| 编号 | 测试源码与关键断言 |
| --- | --- |
| T01 | [test/web_api/test_identity_self_service.py](../../../test/web_api/test_identity_self_service.py)：`test_register_auto_login_logout_and_database_login`、`test_register_reports_success_when_session_creation_fails`、`test_qq_challenge_updates_me_without_entering_agent` |
| T02 | [web/src/store/sessionIsolation.test.ts](../../../web/src/store/sessionIsolation.test.ts)：切账户丢弃迟到请求、401/403区别、A→B→A防旧响应；[App.recovery.test.tsx](../../../web/src/App.recovery.test.tsx)：恢复入口 |
| T03 | [test/web_api/test_phase_b_api.py](../../../test/web_api/test_phase_b_api.py)：`test_api_003_004_005_send_idempotency_and_busy`、`test_api_007_cancelled_run_cannot_be_retried`、`test_api_rejects_retry_after_uncertain_side_effect`、ETag/排序/并发；[web/src/store/workbench.test.ts](../../../web/src/store/workbench.test.ts)：草稿、双发、附件、重试与恢复 |
| T04 | [test/web_api/test_workspace_direct_upload.py](../../../test/web_api/test_workspace_direct_upload.py)：direct upload/save/download/cross-conversation、冲突reason；[test/web_persistence/test_workspace_v41_p2.py](../../../test/web_persistence/test_workspace_v41_p2.py)：冻结范围、撤权、候选上限；[web/src/components/workspace/WorkspaceCommands.test.tsx](../../../web/src/components/workspace/WorkspaceCommands.test.tsx)：影响预览参数绑定、CAS另存、只补授权 |
| T05 | [test/web_api/test_sse_gateway.py](../../../test/web_api/test_sse_gateway.py)：`test_terminal_committed_during_handshake_is_not_lost`、`test_sse_terminal_snapshot_overrides_deltas`、过期/降级；[web/src/sse/runFeed.test.ts](../../../web/src/sse/runFeed.test.ts)：序列、去重、缺口与未知事件 |
| T06 | [test/web_api/test_model_observability.py](../../../test/web_api/test_model_observability.py)：`test_owned_list_and_visibility_projections_are_current_and_immutable`、`test_trace_api_contains_refs_but_not_canonical_prompt` |
| T07 | [test/web_persistence/test_file_action_approvals.py](../../../test/web_persistence/test_file_action_approvals.py)：`test_approval_is_owned_idempotent_and_single_use`、过期/撤销/越权；[web/src/store/runInspector.test.ts](../../../web/src/store/runInspector.test.ts)：审批恢复 |
| T08 | [test/web_api/test_work_foundation.py](../../../test/web_api/test_work_foundation.py)：`test_work_http_lifecycle_and_cross_account_visibility`；[web/src/components/tasks/taskOperations.test.ts](../../../web/src/components/tasks/taskOperations.test.ts)：未知响应保留key/version、已提交读回失败不重发；[TaskSelfReview.test.tsx](../../../web/src/components/tasks/TaskSelfReview.test.tsx)：终态输出同步与冲突草稿 |
| T09 | [test/web_persistence/test_work_integration.py](../../../test/web_persistence/test_work_integration.py)：`test_reminder_only_completes_after_exact_delivery_receipt`、`test_stop_and_uncertain_delivery_preserve_responsibility`、`test_explicit_input_revoke_blocks_dispatch_and_budget_adjustment_is_versioned`；[test/web_api/test_workspace_research_cancel_chain.py](../../../test/web_api/test_workspace_research_cancel_chain.py)：实际撤权取消链 |
| T10 | [test/web_api/test_workspace_v41_p3.py](../../../test/web_api/test_workspace_v41_p3.py)：`test_workspace_version_api_and_conflict_output`；[test/web_persistence/test_workspace_v41_p3.py](../../../test/web_persistence/test_workspace_v41_p3.py)：发布/版本CAS与权限 |
| T11 | [test/web_persistence/test_web_artifacts.py](../../../test/web_persistence/test_web_artifacts.py)：`test_artifact_admission_replays_without_duplicate_mandates`、`test_artifact_run_cancel_preserves_provenance_and_rejects_late_publish`；[web/src/store/UI6SelfReview.test.ts](../../../web/src/store/UI6SelfReview.test.ts)：终态不可被迟到running覆盖、四并发、后台恢复 |

相关当前契约文档：[HTTP API](../../reference/api.md)、[Durable Work V1](../../architecture/durable-work-v1.md)、[Workspace v4.1](../../architecture/workspace-v4.1.md)、[Web UI](../../architecture/web-ui.md)。这些文档辅助核对；与当前代码不一致时在Interaction Contract单列，旧导航和视觉结构不作为重构要求。
