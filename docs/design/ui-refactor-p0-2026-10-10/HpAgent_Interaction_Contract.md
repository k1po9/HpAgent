> 历史设计/交接记录：保留当时基线和验证边界；当前实现与操作从[文档导航](../../README.md)进入。

# HpAgent Interaction Contract — P0 交互契约与重构边界

基线：2026-10-10，当前检出 `main@a47d73b06a993d353b4177e282b69ddd28ffdb36`。本轮只检查源码、契约和测试断言，未运行应用或测试。**F＝源码已核实；I＝合理推断；U＝未确认。** 完整能力与证据目录见 [Capability Map](HpAgent_UI_Capability_Map.md)，实际调用链和路由见 [Integration Map](HpAgent_Frontend_Integration_Map.md)。以下约束描述业务语义，不要求保留旧布局、文字标签或组件。

## 1. 状态的事实来源

| 层级 | 保存的状态 | 重构必须保留的语义 | 依据 |
| --- | --- | --- | --- |
| 后端持久事实 | Account、Conversation/Message、Run、Work/Requirement、预算、资源授权、不可变文件/版本、Artifact版本、operation、delivery、审批、通知 | 只有后端提交/权威查询能确认接受、执行、完成、停止或投递；前端不能自行补终态 | F：[CommandService](../../../src/conversation_domain/commands.py)、[WorkCommandService](../../../src/work_domain/commands.py)、[RunLifecycleService](../../../src/run_domain/lifecycle.py) |
| 实时传输 | chat snapshot/delta/progress；Work持久event_seq；auth.expired/stream.degraded | delta是临时展示；缺口不能猜补；两种SSE契约不同；终态回读覆盖临时累积 | F：[SSEGateway.stream](../../../src/web_api/sse.py)、[StreamSequencer](../../../web/src/sse/runFeed.ts)、[subscribeWork](../../../web/src/sse/workFeed.ts) |
| 前端业务过程 | 冻结请求意图、幂等key/基准版本、上传/保存分段进度、冲突草稿、部分失败、缓存代次和选择代次 | 未知结果不能当失败重建请求；晚到响应不能污染新选择/新账户；这些是业务逻辑而非装饰 | F：[workbench](../../../web/src/store/workbench.ts)、[taskCommand](../../../web/src/components/tasks/taskOperations.ts)、[resumeSave](../../../web/src/components/workspace/workspaceOperations.ts) |
| 可自由替换的本地呈现 | 选中对象、面板、导航、任务四桶、展开项、宽度、预览状态、编辑器草稿形式 | 可换交互，但对象ID、准确状态、操作资格、草稿保护与恢复能力仍需成立；本地分类不是后端新增状态 | F：[shell](../../../web/src/store/shell.ts)、[presentTask](../../../web/src/components/tasks/taskPresentation.ts)、[conversationUi](../../../web/src/store/conversationUi.ts) |

## 2. 身份、请求和并发契约

### 2.1 登录与会话恢复

初始状态为 `checking`；`/me` 成功进入 `signedIn`，未认证进入 `signedOut`，网络/服务问题进入可重试 `error`。注册成功与会话建立分别返回；不能因 `registered=true` 就认为已登录。登录返回重定向后仍核对 `/me`。账户被停用、绝对/空闲过期或流的 `auth.expired` 会结束当前身份上下文。退出先清本地状态，服务端撤销若网络失败仍不能宣称已确认成功。F：[useAuth](../../../web/src/store/auth.ts)、[ApiClient.login/register/me](../../../web/src/api/client.ts)、[AuthService.authenticate](../../../src/web_api/auth.py)。

登录依赖 HttpOnly Cookie；已登录业务写请求走 CSRF 与 Origin/Referer 校验，服务端所有查询/命令限定当前 Account。403通常表示该对象/能力的权限问题，401才触发账户上下文结束；404可能同时承担不存在与越权隐藏语义，不能将另一账户对象加载到当前账户。`csrf_invalid` 仅刷新 `/me` 后重试一次同一请求，不能把其他403自动刷新重发。F：[csrf_guard/auth_context](../../../src/web_api/app.py)、[ApiClient.request/resolve](../../../web/src/api/client.ts)。

会话改变同步清理 Conversation、Work、Workspace、Artifact、Trace/模型输入、Run Inspector、下载Blob URL、各操作队列并关闭流。请求中止与generation检查共同阻止A→B→A时旧A响应再落地；只用“当前account_id相等”不足。F：[sessionLifecycle](../../../web/src/store/sessionLifecycle.ts)、[sessionIsolation.test.ts](../../../web/src/store/sessionIsolation.test.ts)。

### 2.2 命令类别不能套用同一重试规则

| 命令类别 | 当前后端契约 | 客户端必须处理的恢复语义 | 依据 |
| --- | --- | --- | --- |
| 创建Conversation、发消息、cancel/retry Run、初始化上传、创建Artifact/Version、审批决定 | 路由要求规范UUID `Idempotency-Key`；领域记录/对象约束决定重放和冲突 | 同一意图复用同一key与有效载荷；修改参数即新意图；网络中断不证明未提交 | F：[idempotency_key及相关路由](../../../src/web_api/app.py)、[CommandService](../../../src/conversation_domain/commands.py)、[ArtifactService](../../../src/web_artifacts/services.py) |
| Work接受、修订、控制、资源/输入/目标/预算/验收/投递决议 | 接受需key；已有Work写入还需 `If-Match: "work-{id}-v{row_version}"`；预算另有budget_version；事务内先查重放再锁版本，幂等记录有7天有效期 | 未知结果保留原key/body/If-Match，重放原意图；确定冲突刷新核对后新提交，不能偷偷改基准覆盖别人的修改 | F：[WorkCommandService._claim/_locked](../../../src/work_domain/commands.py)、[integration_command](../../../src/work_domain/integration.py)、[taskOperations](../../../web/src/components/tasks/taskOperations.ts) |
| Conversation改名 | `If-Match: "conversation-{id}-m{metadata_version}"`，与消息序号独立；路由未要求幂等key | 412保留编辑意图并回读；不能因来新消息就认为标题版本变化 | F：[rename_conversation](../../../src/web_api/queries.py)、[rename_conversation路由](../../../src/web_api/app.py) |
| Workspace保存/提交revision | 保存和revision命令需key；revision还需 expected_revision/expected_sha256 和来自指定Run的published file | 同一意图重放；CAS冲突保留已生成文件，可明确另存；不能变成强制覆盖 | F：[WorkspaceCatalog.save_file/update_file](../../../src/workspace/catalog.py)、[versionOperations](../../../web/src/components/workspace/versionOperations.ts) |
| 上传字节PUT | 按File状态/内容检查；已ready再次PUT可能409，不是任意安全盲重放 | 未知结果先GET File；已ready跳过PUT，再完成保存；上传资源创建与字节上传是两步 | F：[FileService.upload_content](../../../src/web_domain/file_services.py)、[resumeSave](../../../web/src/components/workspace/workspaceOperations.ts) |
| Conversation grant/revoke、附件撤销、目录创建/移动/移除、entry升级、File删除、QQ challenge | 当前路由未统一要求幂等key；移动/移除还需新鲜影响预览 | 不应把“API Client能发key”当成服务端支持重放；未知结果先核对实际对象/权限，避免重复创建或错误删除 | F：[当前路由](../../../src/web_api/app.py)、[WorkspaceMutationDialog](../../../web/src/components/workspace/WorkspaceMutationDialog.tsx) |

业务请求DTO采用 `extra="forbid"`，错误有code/message/request_id/retryable/details。422是字段验证；413是体积；429可能是额度/容量；503是能力暂不可用。Work领域版本冲突为409 `work_conflict` 并带current/reason；**缺失Work If-Match为428、格式/ID不匹配为409，但目前通用异常映射code是 `malformed_request`**。不要仅从旧文档或错误名称编造统一冲突类型。F：[StrictModel](../../../src/web_api/models.py)、[http_error/domain_error/work_version](../../../src/web_api/app.py)。

## 3. Conversation与chat Run状态模型

| 阶段 | 权威状态与操作资格 | 失败/取消/恢复行为 | 依据 |
| --- | --- | --- | --- |
| 初始/读取 | 本地空白草稿可尚无Conversation；选择历史时读取detail/messages；加载和空结果不同 | 请求generation与选择generation校验；分页去重、保持服务端排序；加载失败可重试且保留本地草稿 | F：[createWorkbenchStore](../../../web/src/store/workbench.ts)、[QueryService](../../../src/web_api/queries.py) |
| 提交中 | 冻结content/file_ids/agent_strategy/key；后端一事务接受user Message、pending assistant Message、queued Run和Outbox | 网络/5xx保留pendingSend与草稿；`confirmPendingSend`复用原意图，不能重新发不同内容；4xx确认拒绝后解除意图；busy回读活跃Run | F：[sendMessage/confirmPendingSend](../../../web/src/store/workbench.ts)、[CommandService._send_message_in_uow](../../../src/conversation_domain/commands.py) |
| 执行 | `queued → running`；`queued/running/cancelling`都占唯一chat槽；assistant Message保持pending，流式正文是临时前端投影 | SSE临时delta与progress、预算/trace独立更新；无法编辑/追加/分叉当前请求；选另一对话或关闭观察不会取消后端执行 | F：[SingleActiveRunAdmission](../../../src/conversation_domain/admission.py)、[runFeed](../../../web/src/sse/runFeed.ts)、[assistant-ui runtime](../../../web/src/adapters/assistant-ui/runtime.ts) |
| 成功 | `succeeded`、权威assistant Message completed | 使用终态snapshot覆盖临时正文；终态不可被迟到流改回running；释放槽后可再次发送 | F：[RunLifecycleService](../../../src/run_domain/lifecycle.py)、[workbench](../../../web/src/store/workbench.ts) |
| 失败 | `failed`、Message failed、failure.code/details | 只允许服务端判断安全的failed重试；副作用不确定、权限/预算等禁止码不可擅自新发同操作绕过 | F：[retry_run](../../../src/conversation_domain/commands.py)、[is_failure_retryable](../../../src/web_domain/failures.py) |
| 取消 | queued且无workflow可直接cancelled；其他通常先cancelling再cancelled；Message aborted | succeeded/failed不能取消；取消不会撤回已经接受的Work或已产生外部副作用；cancelled不能调用retry | F：[cancel_run/retry_run](../../../src/conversation_domain/commands.py)、[Main Work工具](../../../src/application/main_agent.py) |
| 重试 | failed源Run继承冻结输入/策略/触发消息，产生新assistant Message和新Run；直接retry child可复用 | 不新增同一user Message；撤权或输入不可用仍可拒绝；当前stop/retry每次调用生成key，尚无send同等持久pending意图，见G02 | F：[retry_run](../../../src/conversation_domain/commands.py)、[workbench.stopRun/retryRun](../../../web/src/store/workbench.ts) |

Run来源须使用DTO判别：chat有Conversation/assistant_message；work没有assistant_message，不能在旧聊天模型中伪造消息来装入Work状态。历史Run查询与当前实时跟随可以并存，观察历史对象不能被新事件自动夺走选择。F：[HpRunSnapshot](../../../web/src/api/types.ts)、[RunInspector](../../../web/src/components/run/RunInspector.tsx)、[TraceStore](../../../web/src/components/trace/traceStore.ts)。

## 4. Work状态模型

Work的生命周期、继续条件、单次Run和交付事实是不同轴，不能压成一个“成功/失败”字段。F：[Requirement/WORK_STATUSES/CONTINUATIONS](../../../src/work_domain/models.py)、[presentTask](../../../web/src/components/tasks/taskPresentation.ts)。

输入边界：Work title 1–200字符，objective非空且≤20,000字符，每项constraint非空且≤1,000字符；Requirement完整JSON≤32,768字节，spec/timing/policy等versioned value按 `bounded` 白名单和16,384字节上限验证；schema_version必须为1。填写资源请求或保存目标不授予权限。F：[WorkCommandService.accept](../../../src/work_domain/commands.py)、[Requirement.to_dict/bounded](../../../src/work_domain/models.py)。

| 轴 | 当前有效值/事实 | 交互语义 |
| --- | --- | --- |
| 生命周期 | active、pausing、paused、stopping、stopped、completed | active也可能在等时间/资料/回执或阻塞；pausing/stopping仍承担未决责任；stopped不是completed |
| continuation.kind | ready、at_time、awaiting_input、awaiting_delivery、retry_after、blocked、none | 同时看reason、due_at、operation_ref；到时间不自动算完成，未知operation不能自动重试 |
| 单次执行 | active_coordinator_run_id；Run queued/running/cancelling/succeeded/failed/cancelled | Run成功不必然完成Work；失败/取消可能只阻塞下一次尝试；Work Run不能调用chat retry |
| 要求/控制 | current_requirement_revision、control_epoch、row_version | revision是不可变业务要求；epoch使旧执行失去提交权；row_version是并发写基准 |
| 成果/完成 | evidence、Artifact/File精确版本、save状态、acceptance、delivery receipt、completion_mode | produce/publish/save/deliver/accept分别核实；ongoing完成一轮后仍可active |

| 操作/阶段 | 前置条件及结果 | 恢复、失败与特殊情况 | 依据 |
| --- | --- | --- | --- |
| 读取与接受 | 初始读取列表/精确Work；创建后active、Requirement r1、预算/默认inbox/计划；异步调度 | list按50分页，loadState可scanning/partial/error；扫描未完不得显示假精确总数；接受数量/账户/能力受限 | F：[WorkCommandService.accept/list/get](../../../src/work_domain/commands.py)、[useWorks](../../../web/src/store/works.ts) |
| 编辑与修订 | active/pausing/paused可修订，r+1/epoch+1，取消旧coordinator，重新计划；paused保持paused | 前端编辑前GET基准，保留未编辑字段；409保留草稿并核对新版本；未决operation仍保留，修订不能洗掉未知副作用 | F：[revise](../../../src/work_domain/commands.py)、[TaskEditor](../../../web/src/components/tasks/TaskEditor.tsx) |
| 暂停/恢复 | active才可pause；无执行/未决事实即paused，否则pausing；只有paused可resume→active | 显式暂停须显式恢复；计划不自行恢复暂停；恢复重新计算继续条件，不重置累计预算 | F：[control](../../../src/work_domain/commands.py)、[WorkScheduleService](../../../src/orchestration/work_schedule.py) |
| 停止 | active/pausing/paused可stop；有未决责任为stopping，否则stopped | 等协调执行、operation、delivery收敛；未知投递不能先显示已停止；stopped/completed不可恢复或再修订 | F：[control](../../../src/work_domain/commands.py)、[converge_controls](../../../src/run_domain/lifecycle.py) |
| 推进 | active、无coordinator/未知operation，且非awaiting_input/awaiting_delivery | 202可能为not_due/waiting_capacity/budget_exhausted或真的新Run；读返回snapshot/reason；awaiting_input不能靠advance越过 | F：[advance](../../../src/work_domain/commands.py) |
| 单次失败/取消 | lifecycle记录该Run终态、释放coordinator并设置attempt_failed/attempt_cancelled等继续条件 | Work可保持active blocked；取消Work Run≠stop Work；条件恢复后用Work advance；未知副作用仍阻塞，不可当普通失败重试 | F：[finish_in_uow/unresolved_effect](../../../src/run_domain/lifecycle.py)、[retry_run](../../../src/conversation_domain/commands.py) |
| 补资料/增额 | 独立grant/input；预算版本化且只能显式增额；新增资源下一次冻结 | 资料添加并不统一清除awaiting_input；预算增额只在特定budget_exhausted分支重新wakeup；账户日预算/entitlement另行约束 | F：[integration_command](../../../src/work_domain/integration.py)、[WorkBudgetService](../../../src/resources/work_budget.py) |
| 成功/验收 | 成功Run、当前requirement/epoch、持久完成证据、必要保存与criteria；user_acceptance须精确Artifact Version | 仅普通文字回答会progress_saved/awaiting_input；未提交指定save不算满足required；ongoing接受一轮结果仍active；手改新版不自动替换原证据 | F：[generic_result](../../../src/application/work_context.py)、[WorkCompletionPolicy.accept](../../../src/work_domain/completion.py)、[validate_evidence/accept_result](../../../src/work_domain/integration.py) |
| 定时与并发回调 | immediate/once/daily；daily仅ongoing，IANA timezone；旧计划回调受epoch/版本约束 | once catch_up；daily latest合并积压；DST重叠first fold/缺失跳过；暂停/停止回调跳过；不冲掉awaiting_delivery | F：[timing](../../../src/work_domain/timing.py)、[WorkScheduleService](../../../src/orchestration/work_schedule.py) |

关联Conversation只新增关系，不导入聊天全文、附件、授权或目标；同Account也不能把Conversation grant拿给Work。内部Generic Subagent最多3分支、各2尝试、只读子集、共享预算、禁止递归；它是受限执行结构，没有用户任意组建团队的接口。F：[WorkCommandService.link](../../../src/work_domain/commands.py)、[create_main_work_tools](../../../src/application/main_agent.py)、[delegation_contracts](../../../src/agent_workflows/delegation_contracts.py)。

## 5. 文件、Workspace和资源权限状态模型

| 阶段/过程 | 已验证契约 | 失败/取消/恢复及权限限制 | 依据 |
| --- | --- | --- | --- |
| 初始与加载 | 账户Workspace初始化root及普通默认目录；tree/search/trace/space是REST；前端按generation缓存 | 初始空、loading、error、搜索失效分别处理；默认目录删除不自动重建；目录名称与视觉分组可自由改 | F：[WorkspaceCatalog.initialize](../../../src/workspace/catalog.py)、[useWorkspace](../../../web/src/store/workspace.ts) |
| 上传 | create→uploading，PUT校验长度/hash/类型/UTF-8→ready或rejected；File不可变 | 前端attachment uploading/ready/failed是本地状态；上传未完成/未绑定ready有TTL；移除待发送新上传可清理未绑定文件，绑定后不能直接File delete | F：[FileService](../../../src/web_domain/file_services.py)、[addAttachments/removeAttachment](../../../web/src/store/workbench.ts) |
| 保存并使用 | 准备File→上传确认→保存entry→可选grant，分别记录 | 保存成功授权失败是部分成功，只补grant；相同字节不必重传；目标冲突可换目标和保存key；关闭组件不取消队列，刷新持久恢复未实现 | F：[startSave/resumeSave/changeSaveTarget/grantMissing](../../../web/src/components/workspace/workspaceOperations.ts) |
| 浏览与Agent权限 | 用户拥有File可浏览不等于Agent可读；五种grant操作独立，recursive显式；union授权 | resource_requests/deliverable_policy/Conversation关联/保存都不是授权；输入引用和目录权限分别管理；未授权内容不得写入模型快照 | F：[ResourcePolicy](../../../src/workspace/resources.py)、[Requirement](../../../src/work_domain/models.py) |
| Run冻结与访问 | 最多500候选、512,000字节metadata；候选不是读取；首次select固定版本；materialize/read另有检查 | 新增只影响下次Run；撤权对当前访问/dispatch重新检查，必要时取消；已有可用替代grant仍可保权；不能动态扩容当前候选 | F：[snapshot_in_uow/_select/check_file/check_dispatch](../../../src/workspace/resources.py) |
| 撤权与观察 | 服务端策略事务阻断非法选中输入、写cancel Outbox；前端读回grants并观察受影响Run | 用户意图、撤权成功、Run cancelling、Run终态分别确认；清模型输入缓存；输出写权撤销能阻止commit，是否所有输出-only场景主动停Run见G06 | F：[ResourcePolicy.revoke](../../../src/workspace/resources.py)、[revokeRules/observeRevocation](../../../web/src/components/workspace/workspaceOperations.ts) |
| 移动/重命名/移除 | impact返回潜在影响Run+token；node稳定；禁止root改动/循环/重名/非空目录删除；活跃Work输出目录受保护 | 当前token核对全账户topology/policy版本新鲜度，**不绑定本次node/action/name/parent**；前端fingerprint绑定并重预览；未知结果不盲重发 | F：[preview_change/_preview_token/move/remove](../../../src/workspace/catalog.py)、[WorkspaceMutationDialog](../../../web/src/components/workspace/WorkspaceMutationDialog.tsx) |
| 更新长期entry | 升级为destination r1；新revision来自指定Run已发布输出，校验当前update_content、冻结base、expected revision/hash、Run状态 | 409 workspace_version_conflict带current与output；输出仍可下载/另存；无法靠普通字节上传越过版本CAS；旧revision不可覆盖 | F：[upgrade_file/update_file](../../../src/workspace/catalog.py)、[versionOperations](../../../web/src/components/workspace/versionOperations.ts) |
| 删除、下载、预览 | 删entry不物理销毁bytes，retention检查各业务引用；下载ready且合法发布；文本预览≤1MiB | Office/PDF可上传不意味着内嵌浏览；通用HTML文件按文本看；不宣称删除立即释放空间或已有永久擦除API | F：[WorkspaceDiscovery.retention/space](../../../src/workspace/discovery.py)、[FilePreview/canPreview](../../../web/src/components/workspace/FilePreview.tsx)、[FileService.download/delete](../../../src/web_domain/file_services.py) |

## 6. Artifact、审批与交付状态模型

### 6.1 Artifact

用户从回复主动新建Artifact时，来源须为同账户completed且非空的assistant Message；Research等Work发布成果另有生产链；后端接受后创建Artifact、queued Version以及独立artifact_build Work。版本依次queued/running/completed或failed，HTML生成、持久发布、Work完成与前端预览装载分别处理。Run取消没有Artifact `cancelled`状态：已被该Run认领的running版转failed/artifact_cancelled；尚未认领queued版可保留queued，下一次Work推进继续建立执行。已完成版本不可被旧Run迟到结果覆盖。F：[ArtifactService._enqueue](../../../src/web_artifacts/services.py)、[ArtifactBuildService](../../../src/web_artifacts/build.py)、[RunLifecycleService](../../../src/run_domain/lifecycle.py)、[取消/迟到发布测试](../../../test/web_persistence/test_web_artifacts.py)。

前端按版本REST轮询1/2/3/5秒、最多4并发，后台消息成果也可恢复；completed不被迟到running覆盖，同一版本failed→running可由较新权威查询反映恢复。默认优先最新成功版，用户选版后迟到初始化不能改其选择。生成失败、预览iframe失败、读取版本失败和命令结果未知分别恢复。403/404清相关缓存，401按会话清理。F：[createArtifactStore/cacheVersion/poll/command](../../../web/src/store/artifacts.ts)。

提交修改由服务端选择最新completed parent；客户端当前先复核expectedParent并限制有building时再提交，**服务端未提供客户端parent CAS**（G03）。iframe只允许scripts，不带allow-same-origin；消息校验event.source。生成器限制CSP/HTML大小；研究发布路径与生成器不同，不能泛称所有来源都注入同一CSP（G08）。下载为本地Blob；保存 `.html.txt` 是普通长期文件，既不自动授权，也不等于验收或公开托管。F：[CreateArtifactVersionRequest](../../../src/web_api/models.py)、[ArtifactService.create_version](../../../src/web_artifacts/services.py)、[PreviewFrame](../../../web/src/components/ArtifactPreview.tsx)、[WebArtifactGenerator.harden](../../../src/web_artifacts/generator.py)、[artifactDownloads](../../../web/src/components/artifact/artifactDownloads.ts)。

### 6.2 文件动作审批

审批对象有pending、approved、rejected、expired、cancelled、consumed状态；决定限定账户、Run、operation/tool/参数hash/intent和有效期，批准只能被匹配执行单次消费，不发放永久授权。前端未知响应保留approval key/decision，过期不再重放；批准后仍等待Run的实际执行事实。DTO不暴露原始参数hash/内部intent结构。哪些Work工具路径一定产生审批，本轮未证明（G07）。F：[FileActionApprovalService.request/decide/consume/authoritative_status](../../../src/file_domain/approvals.py)、[useRunInspector.decide](../../../web/src/store/runInspector.ts)、[审批测试](../../../test/web_persistence/test_file_action_approvals.py)。

### 6.3 通知、Delivery与用户验收

目标从已验证消息来源生成；没有自由收件人字段，QQ群只允许summary。inbox Notification与每个Delivery分别持久化；pending/sending/accepted/failed/uncertain/cancelled状态中的未知责任不能用乐观UI抹掉。停用目标可取消pending/failed，sending/uncertain仍须核对。`accepted`人工决议表示确认渠道接受，**不是已读/验收**；`not_sent`对仍有效目标可能恢复pending重发；`retry_accepting_duplicate_risk`要求明确承担重复风险且当前授权有效。旧revision/epoch的回执不得完成新要求，过期fulfillment不得重发。F：[DeliveryReceiptHandler/target_from_origin/target_authorized](../../../src/delivery/service.py)、[integration_command resolve_delivery](../../../src/work_domain/integration.py)、[TaskDeliveryDecision](../../../web/src/components/tasks/TaskDeliveryDecision.tsx)。

收件箱没有后端read/unread契约；当前按100条查询、游标/去重形成列表，本地“看过”不能当服务器事实。用户接受成果则走另一接口，包含当前requirement_revision与原始artifact_version_id并验证完成证据。F：[notification_inbox/accept_work_result](../../../src/web_api/app.py)、[TaskInbox](../../../web/src/components/tasks/TaskInbox.tsx)、[TaskOutputs](../../../web/src/components/tasks/TaskOutputs.tsx)。

## 7. 实时事件与恢复约定

| 通道 | 后端与客户端行为 | 必须保留 |
| --- | --- | --- |
| chat Run SSE | 先订阅/缓冲再读取权威snapshot；envelope有stream_id/event_seq/event_id；终态再查库；客户端重排窗口16，缺口/stream切换永久degrade；Redis缺失/断开也degrade | 不拿progress充正文；去重/排序；terminal以snapshot盖delta；REST轮询兜底1/2/3/5秒；没有可靠delta历史重放承诺 |
| Work SSE | PostgreSQL持久事件按seq、after查询；支持数字Last-Event-ID；前端用after游标，事件后刷新snapshot，错误GET恢复/3秒重连 | 与chat envelope/sequencer不同；按event_seq去重、按row_version拒绝旧snapshot；当前仅最多2条重点Work流+10秒列表查询，不是全部Work即时推送 |
| Artifact/Workspace/通知 | Artifact按版轮询；Workspace写后invalidate/REST刷新；inbox按需REST | 不捏造统一SSE、后台全局实时保证或“离线已完成”；无流时仍有可重试读取和准确的新鲜度语义 |
| auth.expired/流503 | 过期结束会话；连接容量满503、传输失败是观察失败 | 观察失败≠业务失败；关闭流≠取消Run/Work |

F依据：[SSEGateway.stream](../../../src/web_api/sse.py)、[stream_work_events](../../../src/web_api/app.py)、[runFeed](../../../web/src/sse/runFeed.ts)、[workFeed](../../../web/src/sse/workFeed.ts)、[useTaskController](../../../web/src/components/tasks/useTaskController.ts)、[artifacts store](../../../web/src/store/artifacts.ts)。相关断言：[SSE握手/终态测试](../../../test/web_api/test_sse_gateway.py)、[序列测试](../../../web/src/sse/runFeed.test.ts)。本轮未执行。

## 8. 重构边界

| 分类 | 结论 | 依据/性质 |
| --- | --- | --- |
| 1 必须保留业务契约与安全机制 | 账户隔离/会话清理、CSRF、幂等意图、版本并发、单活跃chat槽、Run/Work区分、冻结输入与实时撤权、分段保存/授权、审批单次消费、epoch防旧执行、精确成果验收、预算累计、投递未知责任、sandbox | F：以上各节后端强制项；客户端generation/草稿/影响参数复核等保护虽非全部后端强制，也需保留等价行为 |
| 2 建议复用业务逻辑 | ApiClient/领域DTO、workbench业务命令与监控、Work merge/命令恢复、taskActions资格、resource/save/version队列、Artifact缓存/轮询、trace visibility、sessionLifecycle | I：复用可降低丢失已核实契约的风险；可重写，但需证明等价；目录在components下不代表纯UI |
| 3 可自由替换 | AppShell/Surface/Sidebar/Inspector布局、导航、组件结构、CSS、任务四桶、assistant-ui依赖、状态展示形式、消息/成果的排列与视觉层级 | F/I：[shell](../../../web/src/store/shell.ts)、[runtime adapter](../../../web/src/adapters/assistant-ui/runtime.ts)、[presentTask](../../../web/src/components/tasks/taskPresentation.ts)只是投影；不能把投影当实体状态 |
| 4 纯前端组合或接入已有API | 按权限组合Conversation/Work/Run/文件/成果；本地筛选/聚合/诊断；分步“保存并使用”；现有Requirement更多字段/时区；研究evidence或Run范围检索；比较已有版本 | I：已有独立查询/命令可组合；不是原子事务，须保留部分成功/恢复；版本比较不是后端合并；能力依据Capability Map第7节 |
| 5 必须扩展后端/服务 | 共享ACL/多人协作、跨设备草稿、通知read/unread、对话编辑/分叉/删除归档、成果公共托管、自由外部收件人/新计划种类、任意Agent团队、任意operation人工解除、客户端Artifact parent CAS、原子跨对象提交 | F：当前API/DTO无这些语义；I：要作服务端事实需新契约；既有工具/部署扩展也需运行验收。不能只增按钮后宣称支持 |

## 9. 差异与未确认登记

这些不是本轮实施任务，也不是对运行缺陷的未经验证断言；给后续设计/工程适配提供明确边界。

| ID | 状态及结论 | 证据与下一步核实 |
| --- | --- | --- |
| G01 能力/限额投影 | F：消息DTO附件≤20，前端10，部署默认10；metadata代码512,000字节；后端 `/me`含file_transform，前端MeResponse未列该字段；新UI不可硬编码为全部可用 | [models.py](../../../src/web_api/models.py)、[config.py](../../../src/web_api/config.py)、[MeResponse](../../../web/src/api/client.ts)、[resources.py](../../../src/workspace/resources.py)；U实际配置 |
| G02 恢复耐久性 | F：大多数草稿/未知命令队列在账户内存；组件重开可恢复不等于刷新/跨设备；chat cancel/retry每次调用新key，没有send式pending意图 | [workbench](../../../web/src/store/workbench.ts)、[taskOperations](../../../web/src/components/tasks/taskOperations.ts)、[workspaceOperations](../../../web/src/components/workspace/workspaceOperations.ts)；若设计要求跨刷新耐久，应明确定义持久策略与确认查询 |
| G03 Artifact修改基准 | F：客户端expectedParent只在GET复核时存在，POST只有instruction；后端锁Artifact分配版本，但不校验客户端parent，也不统一拒绝已有building；I存在跨标签页GET→POST竞争窗口 | [CreateArtifactVersionRequest](../../../src/web_api/models.py)、[ArtifactService.create_version](../../../src/web_artifacts/services.py)、[artifact store command](../../../web/src/store/artifacts.ts)；若承诺严格基于用户所见版本修改，需后端CAS |
| G04 Research手工版本 | F：路由有artifact_research_version_unsupported错误映射，但当前create_version未见对应来源/kind拒绝分支；旧文档的限制不能当已执行契约。U产品是否应禁止/放开 | [create_artifact_version路由](../../../src/web_api/app.py)、[ArtifactService.create_version](../../../src/web_artifacts/services.py)；无论是否允许，原Work验收仍绑定原版本；补契约/运行测试再作承诺 |
| G05 Work输入有效性 | F：GET inputs返回含已撤销记录的所有ref；当前WorkInputs的窄类型与map未读取available，因此展示不能证明全部有效 | [work_inputs](../../../src/web_api/app.py)、[WorkInputs](../../../web/src/components/tasks/TaskInputs.tsx)、[ResourcePolicy.snapshot_in_uow](../../../src/workspace/resources.py)；新投影应明确有效/历史语义，运行呈现未验收 |
| G06 输出授权撤销 | F：commit时重新核对update_content；U输出-only授权撤销是否覆盖所有主动取消链，不能泛称撤权一定立即停全部Run | [ResourcePolicy.revoke/check_dispatch](../../../src/workspace/resources.py)、[WorkspaceCatalog.update_file](../../../src/workspace/catalog.py)；应补针对输出-only的调用/运行断言 |
| G07 工具/审批覆盖 | F：文件/transform/shell受开关和sandbox配置，Web文件模式显式跳过convert_file_to_pdf；审批API存在。U所有Work工具何时触发审批、worker依赖和真实模型调用 | [create_execution_sandbox](../../../src/sandbox/sandbox_manager.py)、[FileCapabilityConfig](../../../src/workspace/file_capability_config.py)、[approvals](../../../src/file_domain/approvals.py)；不得把工具文件存在等同开放能力 |
| G08 HTML发布来源 | F：生成器harden注入CSP；Research `_publish`转义内容后直接publish，通用publisher不统一注入harden；iframe sandbox覆盖当前预览。U能否宣称全部下载HTML一致CSP | [generator.harden](../../../src/web_artifacts/generator.py)、[ResearchActivities._publish](../../../src/research_activities/runtime.py#L818)、[publisher.publish](../../../src/web_artifacts/publisher.py)、[PreviewFrame](../../../web/src/components/ArtifactPreview.tsx)；保留各来源安全处理，统一承诺前核验全部发布路径 |
| G09 HTTP错误差异 | F：缺Work If-Match 428/格式409映射malformed_request；真正领域WorkConflict才work_conflict；不能照旧描述只按code处理 | [work_version/http_error](../../../src/web_api/app.py)；后续可决定是否统一错误契约，本轮只记录 |
| G10 资料补全后的继续 | F：input/grant分支未统一清awaiting_input，advance拒绝该kind；I某些流程需修订或特定领域回执才能继续 | [integration_command](../../../src/work_domain/integration.py)、[advance](../../../src/work_domain/commands.py)、[generic_result](../../../src/application/work_context.py)；“补资料即自动继续”需后端显式语义 |
| G11 部署与真实链路 | U：功能开关/账号tier/真实QQ回执、模型工具选择、Research网络源、Temporal/Redis/文档worker恢复与长时间运行 | 本轮源码审计不能替代环境验收；[README](../../../README.md)、[api.md](../../reference/api.md)为辅助；下一阶段实现验收按真实部署核对 |

## 10. 后续工程适配的验收输入

以下是验收场景，不是要求本轮运行测试：重复发送/双击只接受一个意图；未知响应重放原key/body/version；冲突保留草稿；切账户/快速切对象不落旧数据；SSE缺口及终态覆盖；cancel等待后端终态；Work pause/stop保留未知责任；撤权阻断后续读取且清模型缓存；保存成功授权失败只补授权；版本CAS冲突可保留/另存输出；Artifact迟到running不覆盖completed；预览失败不误报生产失败；原版验收不偷换手改版；delivery未知不自动重复投递；预算增额不清累计或日额度。

F对应断言源码：[test_phase_b_api](../../../test/web_api/test_phase_b_api.py)、[test_sse_gateway](../../../test/web_api/test_sse_gateway.py)、[test_work_integration](../../../test/web_persistence/test_work_integration.py)、[test_workspace_v41_p3](../../../test/web_persistence/test_workspace_v41_p3.py)、[TaskOperations tests](../../../web/src/components/tasks/taskOperations.test.ts)、[WorkspaceCommands tests](../../../web/src/components/workspace/WorkspaceCommands.test.tsx)、[UI6SelfReview](../../../web/src/store/UI6SelfReview.test.ts)。可复用这些业务断言，视觉快照/旧导航断言应随新方案调整。
