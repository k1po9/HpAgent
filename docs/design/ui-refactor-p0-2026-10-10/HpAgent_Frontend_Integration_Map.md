> 历史设计/交接记录：保留当时基线和验证边界；当前实现与操作从[文档导航](../../README.md)进入。

# HpAgent Frontend Integration Map — P0 前后端映射

基线：当前检出 `main@a47d73b06a993d353b4177e282b69ddd28ffdb36`，2026-10-10。**F＝已逐段检查的源码调用关系；I＝设计/适配推断；U＝未运行确认。** 不把组件名、旧布局或注释当作后端能力；例如adapter注释提到cancelled重试，实际 `CommandService.retry_run`拒绝cancelled。未修改代码、未执行应用或测试。能力及边界见 [Capability Map](HpAgent_UI_Capability_Map.md) 与 [Interaction Contract](HpAgent_Interaction_Contract.md)。

## 1. 模块职责与可复用层

| 层/模块 | 当前职责与实际接入 | 重构判断及源码 |
| --- | --- | --- |
| API传输 | 同源Cookie、JSON/error envelope、CSRF刷新一次、调用方传幂等key、AbortSignal及会话generation、认证事件 | F：[ApiClient](../../../web/src/api/client.ts)；建议复用/等价替代，不能由新组件各自实现不一致重试 |
| 领域DTO和端点封装 | HpConversation/Message、chat/work判别Run、File/Workspace/Artifact/Work；HpApi封装对话、Run、文件、资源、Artifact。Work多数直接api.request，非统一HpApi facade | F：[types.ts](../../../web/src/api/types.ts)、[HpApi](../../../web/src/api/resources.ts)；Dto属业务，assistant-ui类型只在adapter |
| 会话 | 检查/过期/退出与全域清理；QQ绑定轮询 | F：[auth](../../../web/src/store/auth.ts)、[sessionLifecycle](../../../web/src/store/sessionLifecycle.ts)、[QQBindingPanel](../../../web/src/components/QQBindingPanel.tsx) |
| Conversation/Run业务 | create/select/rename、分页、附件、发送意图、cancel/retry、snapshot/delta/终态协调、poll fallback | F：[createWorkbenchStore](../../../web/src/store/workbench.ts)；承载业务过程与缓存，应拆分评估后复用，不视为旧页面布局 |
| 渲染adapter | Hp Message → assistant-ui ThreadMessageLike；onNew/onCancel回调；不接onEdit/onReload | F：[runtime](../../../web/src/adapters/assistant-ui/runtime.ts)、[toThreadMessageLike](../../../web/src/adapters/assistant-ui/types.ts)；可替换库/投影，保留命令语义 |
| Work业务 | list50连续扫描、row_version归并、按ID刷新、event seq；命令lock/unknown意图；动作资格、Requirement组装 | F：[works](../../../web/src/store/works.ts)、[taskOperations](../../../web/src/components/tasks/taskOperations.ts)、[taskActions](../../../web/src/components/tasks/taskActions.ts)、[taskRequirement](../../../web/src/components/tasks/taskRequirement.ts)；在components目录的业务函数仍建议复用 |
| Work查询/调度投影 | keyed查询/部分失败；可见时10秒列表同步和最多2条Work SSE；四桶投影与排序 | F：[useTaskQuery](../../../web/src/components/tasks/useTaskQuery.ts)、[useTaskController](../../../web/src/components/tasks/useTaskController.ts)、[presentTask](../../../web/src/components/tasks/taskPresentation.ts)；四桶/排序是可替换呈现 |
| Workspace业务 | tree/search/缓存generation；save分段恢复、grant union/readback、撤权后观察、Work命令共享lock、revision CAS队列 | F：[workspace](../../../web/src/store/workspace.ts)、[workspaceOperations](../../../web/src/components/workspace/workspaceOperations.ts)、[versionOperations](../../../web/src/components/workspace/versionOperations.ts)；保存过程可与任意新交互结合 |
| Artifact业务 | 按版缓存、请求dispatch序号防回退、后台恢复poll/concurrency、命令unknown恢复和客户端parent复核 | F：[createArtifactStore](../../../web/src/store/artifacts.ts)；前端parent复核并非后端CAS，见Interaction G03 |
| 诊断与权限投影 | Run snapshot/审批、trace tree/model-input分级、历史选择与live following分开 | F：[runInspector](../../../web/src/store/runInspector.ts)、[traceStore](../../../web/src/components/trace/traceStore.ts)；诊断读取失败不能改业务终态 |
| 本地UI状态 | draft/selected version/route/inspector/origin/dirty guard等账户内存 | F：[conversationUi](../../../web/src/store/conversationUi.ts)、[artifactUi](../../../web/src/store/artifactUi.ts)、[shell](../../../web/src/store/shell.ts)；自由改结构，保留对象ID、来源、未提交内容保护与会话隔离 |

## 2. 实际关键链路

表中HTTP业务路径默认前缀 `/api/v1`；认证 `/auth/login|register` 例外。每行按“用户→UI→业务层→传输→后端→事实/缓存→UI”写出；是调用关系，不是建议保留对应组件。

| 链路 | 已核实的调用顺序与状态更新 | 源码依据与关键边界 |
| --- | --- | --- |
| L01 登录/注册/退出 | 输入账户 → LoginForm → ApiClient.login/register → POST认证、GET me → AuthService/identity self-service → useAuth signedIn/恢复或未建立session → App；退出→signOut→logout+expire→sessionLifecycle各域reset | F：[LoginForm](../../../web/src/components/LoginForm.tsx)、[client](../../../web/src/api/client.ts)、[auth](../../../src/web_api/auth.py)、[app认证路由](../../../src/web_api/app.py)；注册事实不等于登录事实 |
| L02 QQ绑定 | QQBindingPanel创建 → ApiClient.createQqBindingChallenge → POST identity-bindings/qq/challenges → IdentityBindingService；3秒GET challenge，QQ入口完成验证 → completed → me刷新身份 → panel | F：[QQBindingPanel](../../../web/src/components/QQBindingPanel.tsx)、[IdentityBindingService](../../../src/account/identity_binding_service.py)；Web不直接填写收件身份，真实QQ U |
| L03 新建/恢复/改名 | Sidebar新建本地draft；第一次send/upload前 ChatPane.ensure → workbench.ensureConversation → HpApi.createConversation → CommandService.create_conversation →实体ID/草稿迁移；选择→selectConversation→detail/messages并行GET→generation检查/归并；TitleEditor→renameActiveConversation→HpApi PATCH+ETag→QueryService.rename_conversation→新metadata_version | F：[ChatPane](../../../web/src/components/ChatPane.tsx)、[ConversationSidebar](../../../web/src/components/ConversationSidebar.tsx)、[ConversationHeader](../../../web/src/components/conversation/ConversationHeader.tsx)、[workbench](../../../web/src/store/workbench.ts)、[QueryService](../../../src/web_api/queries.py) |
| L04 发消息/流式执行 | HpThread onNew → useHpThreadRuntime.onSend → ChatPane send callback → workbench.sendMessage冻结意图 → HpApi.sendMessage → POST conversations/{c}/messages → CommandService._send_message_in_uow → user/assistant/Run/Outbox → workbench替换临时消息/startRunMonitor → openRunFeed → SSEGateway snapshot/delta/progress → 消息投影 → HpThread | F：[HpThread](../../../web/src/adapters/assistant-ui/HpThread.tsx)、[runtime](../../../web/src/adapters/assistant-ui/runtime.ts)、[workbench](../../../web/src/store/workbench.ts)、[CommandService](../../../src/conversation_domain/commands.py)、[SSEGateway](../../../src/web_api/sse.py)；后端busy是跨标签页裁决 |
| L05 取消/失败重试 | RunStatus或runtime onCancel → stopRun → HpApi.cancelRun → POST runs/{r}/cancel → CommandService.cancel_run → snapshot immediate cancelled或cancelling+Outbox → monitor终态；RunStatus retry → retryRun → POST retry → frozen-input retry child/newassistant → startRunMonitor | F：[RunStatus](../../../web/src/components/RunStatus.tsx)、[workbench](../../../web/src/store/workbench.ts)、[CommandService.cancel_run/retry_run](../../../src/conversation_domain/commands.py)；只重试安全failed，取消不能当撤回Work |
| L06 对话附件 | ChatPane选文件 → ensureConversation/addAttachments → HpApi.createUpload → POST conversation uploads → FileService上传记录；PUT内容→ready →本地ready；sendMessage收集ready IDs→后端绑定/冻结；选既有→listFileCandidates/selectExistingFile → send再次授权验证 | F：[workbench.addAttachments/sendMessage](../../../web/src/store/workbench.ts)、[HpApi.createUpload/uploadContent/listFileCandidates](../../../web/src/api/resources.ts)、[FileService](../../../src/web_domain/file_services.py)、[CommandService._explicit_file_authority](../../../src/conversation_domain/commands.py) |
| L07 Work接受/修订 | TaskEditor → buildRequirement保留隐藏字段 → taskCommand(owner,key/body/If-Match) → ApiClient.request POST works或revisions → WorkCommandService.accept/revise → Requirement/Work/预算/计划/事件 → upsert(snapshot)，无snapshot回读 →业务消费者更新 | F：[TaskEditor](../../../web/src/components/tasks/TaskEditor.tsx)、[taskRequirement](../../../web/src/components/tasks/taskRequirement.ts)、[taskOperations](../../../web/src/components/tasks/taskOperations.ts)、[WorkCommandService](../../../src/work_domain/commands.py)；修订前GET精确基准，非列表猜测 |
| L08 Work自然语言入口 | L04 chat执行 → create_execution_sandbox在chat注册create_main_work_tools → accept/revise/control/advance/link_work → 同一WorkCommandService →持久Work →后续Web useWorks读取；mandate_slot/source message去重防chatretry重复委托 | F：[sandbox_manager](../../../src/sandbox/sandbox_manager.py)、[create_main_work_tools](../../../src/application/main_agent.py)、[WorkCommandService](../../../src/work_domain/commands.py)；不是由UI解析聊天文本建假任务，实际模型选工具 U |
| L09 Work观察/控制 | AppShell一次挂载useTaskController → useWorks.load按50扫描；最多2个subscribeWork(after) → Work事件stream → useWorks.receive seq去重+refresh→snapshot按row_version归并→TaskScreen/Inspector；TaskActions→taskActions资格→useWorks.control→command/taskCommand→HTTP→control/advance→权威snapshot | F：[AppShell](../../../web/src/components/shell/AppShell.tsx)、[controller](../../../web/src/components/tasks/useTaskController.ts)、[works](../../../web/src/store/works.ts)、[workFeed](../../../web/src/sse/workFeed.ts)、[WorkCommandService](../../../src/work_domain/commands.py)；四桶不是后端状态，202需读reason |
| L10 Work成果/研究/验收 | TaskOutputs → useTaskQuery GET works/{w}/runs；RunOutput独立GET research/report与published-files → ResearchQueryService/File投影 → Markdown报告/文件；ArtifactResult检查acceptanceEligible → useWorks.control('accept-result',原version) → integration.validate_evidence/WorkCompletionPolicy →snapshot/acceptance/完成状态 | F：[TaskOutputs](../../../web/src/components/tasks/TaskOutputs.tsx)、[taskActions](../../../web/src/components/tasks/taskActions.ts)、[ResearchQueryService](../../../src/research_domain/services.py)、[integration](../../../src/work_domain/integration.py)、[completion](../../../src/work_domain/completion.py)；query失败分别重试，不把新手改版当原交付 |
| L11 目标/投递/预算 | TaskTargets → useTaskQuery GET targets；useWorks.command PUT/DELETE目标 → integration+target_from_origin；TaskInbox→GET notifications；TaskDeliveryDecision→resolveDelivery→integration/DeliveryReceiptHandler；TaskBudgetDialog→increaseBudget含budget_version→integration→used/reserved保持 →upsert | F：[TaskTargets](../../../web/src/components/tasks/TaskTargets.tsx)、[TaskInbox](../../../web/src/components/tasks/TaskInbox.tsx)、[TaskDeliveryDecision](../../../web/src/components/tasks/TaskDeliveryDecision.tsx)、[TaskBudgetDialog](../../../web/src/components/tasks/TaskBudgetDialog.tsx)、[integration](../../../src/work_domain/integration.py)、[delivery](../../../src/delivery/service.py) |
| L12 Workspace浏览/搜索 | WorkspaceScreen/FileList → useWorkspace.load/searchFiles → workspaceApi(HpApi) GET tree/search → WorkspaceCatalog/WorkspaceDiscovery →缓存+revision/generation→列表；FileInspector/useWorkspaceQuery读metadata/trace/retention/space →事实展示；FilePreview→readFileText→content下载→只解析可预览文本 | F：[workspace](../../../web/src/store/workspace.ts)、[FileInspector](../../../web/src/components/workspace/FileInspector.tsx)、[FilePreview](../../../web/src/components/workspace/FilePreview.tsx)、[WorkspaceDiscovery](../../../src/workspace/discovery.py)；路径展示不当对象身份 |
| L13 长期保存并使用 | UploadToWorkspaceDialog/SaveToWorkspaceDialog → startSave/resumeSave → createWorkspaceUpload(key)/GET File/必要PUT →saveWorkspaceFile(key)→FileService+WorkspaceCatalog→node；可选grantMissing→GET已有grants/只补缺失→ResourcePolicy →分别更新完成/部分错误+invalidate | F：[UploadToWorkspaceDialog](../../../web/src/components/workspace/UploadToWorkspaceDialog.tsx)、[SaveToWorkspaceDialog](../../../web/src/components/workspace/SaveToWorkspaceDialog.tsx)、[workspaceOperations](../../../web/src/components/workspace/workspaceOperations.ts)、[FileService](../../../src/web_domain/file_services.py)、[WorkspaceCatalog](../../../src/workspace/catalog.py)；当前组件关闭仍在内存运行，非刷新耐久 |
| L14 授权/撤权/Work inputs | ResourceManager/GrantEditor/UseInConversationDialog → grantMissing/revokeRules/listGrants；Conversation经HpApi，Work经workCommand取得版本并共享task lock →ResourcePolicy/Work integration →grants/affected_run_ids→permissionsChanged清资源和模型快照缓存、observeRevocation轮询Run；WorkInputs→inputs API →ref available状态 | F：[ResourceManager](../../../web/src/components/workspace/ResourceManager.tsx)、[workspaceOperations](../../../web/src/components/workspace/workspaceOperations.ts)、[WorkInputs](../../../web/src/components/tasks/TaskInputs.tsx)、[ResourcePolicy](../../../src/workspace/resources.py)、[integration](../../../src/work_domain/integration.py)；当前WorkInputs显示未消费available，见G05 |
| L15 entry移动/移除/更新 | WorkspaceMutationDialog→GET node impact→本地params fingerprint与token确认→HpApi PATCH/DELETE→Catalog.move/remove→invalidate；FileVersions→upgradeWorkspaceFile；RunResources提供published output→startVersionUpdate/resumeVersionUpdate冻结node/run/file/revision/hash/key→POST node versions→Catalog.update_file/CAS→成功invalidate或冲突保留输出 | F：[WorkspaceMutationDialog](../../../web/src/components/workspace/WorkspaceMutationDialog.tsx)、[FileVersions](../../../web/src/components/workspace/FileVersions.tsx)、[RunResources](../../../web/src/components/run/RunResources.tsx)、[versionOperations](../../../web/src/components/workspace/versionOperations.ts)、[catalog](../../../src/workspace/catalog.py)；preview token仅topology/policy新鲜度 |
| L16 Artifact生成/修改/恢复 | ArtifactMessageItems/CreateForm → useArtifacts.createArtifact → HpApi.createArtifact → POST messages/{m}/artifacts →ArtifactService.create_artifact/_enqueue →独立Work queued版；ArtifactComposer→createVersion→client复核→POST versions→create_version；GET按版poll→cacheVersion→ArtifactInspector/消息成果列表 | F：[ArtifactCreateForm](../../../web/src/components/artifact/ArtifactCreateForm.tsx)、[ArtifactComposer](../../../web/src/components/artifact/ArtifactComposer.tsx)、[artifacts](../../../web/src/store/artifacts.ts)、[ArtifactService](../../../src/web_artifacts/services.py)；没有Artifact SSE/后端parent CAS |
| L17 Artifact预览/下载/保存 | completed版html→ArtifactPreview.PreviewFrame sandbox iframe srcDoc/event.source核对→局部preview错误；downloadArtifact→Blob `.html`；Inspector保存→SaveToWorkspaceDialog传html/file_name→L13 text/plain `.html.txt`上传保存 | F：[ArtifactPreview](../../../web/src/components/ArtifactPreview.tsx)、[artifactDownloads](../../../web/src/components/artifact/artifactDownloads.ts)、[ArtifactInspector](../../../web/src/components/artifact/ArtifactInspector.tsx)、[resumeSave](../../../web/src/components/workspace/workspaceOperations.ts)；预览、下载、保存不是接受成果/托管 |
| L18 Run诊断/审批 | RunLookup/Inspector选择 → useRunInspector.load → HpApi.getRun →QueryService.get_run →snapshot；当前chat可取workbench live，其余REST可见时poll；RunTraceContent→TraceStore.loadTrace/loadModelInput→trace/model-input APIs→安全投影；RunApprovals→loadApprovals/decide(持key)→approval GET/approve/reject→FileActionApprovalService→Outbox决定信号→Run继续/拒绝 | F：[RunInspector](../../../web/src/components/run/RunInspector.tsx)、[runInspector store](../../../web/src/store/runInspector.ts)、[traceStore](../../../web/src/components/trace/traceStore.ts)、[RunApprovals](../../../web/src/components/run/RunResources.tsx)、[ModelObservabilityQueries](../../../src/web_api/model_observability_queries.py)、[FileActionApprovalService](../../../src/file_domain/approvals.py)；审批成功仍等执行事实 |

## 3. 后端异步执行与前端回流

F：chat接受事务产生 `start_run` Outbox；Work接受先持久wakeup/schedule，由 `WorkScheduleService.dispatch_due` 或显式advance调用 `admit_work_run` 建立有限Run及Outbox。`WebOutboxDispatcher`消费、`TemporalOutboxDispatcher`派发，`TemporalClientAdapter.start_web_run`以确定workflow ID启动 `AgentLifecycleWorkflow`，模糊Start恢复同一Workflow，拒绝重复ID复用。

依据：[commands](../../../src/conversation_domain/commands.py)、[WorkScheduleService.dispatch_due](../../../src/orchestration/work_schedule.py)、[admit_work_run](../../../src/run_domain/admission.py)、[OutboxService](../../../src/web_domain/outbox.py)、[web_dispatcher](../../../src/orchestration/web_dispatcher.py)。

F：Lifecycle prepare/加载冻结策略后分派：deterministic→reminder activity；fixed_workflow→ResearchReportWorkflow或artifact_html build；其他→AgentRunWorkflow子workflow→finalize。终态经RunLifecycleService持久化：chat走 `project_terminal` 更新assistant Message/输出关联和终态发布；work更新Work checkpoint/continuation/evidence，不造chat Message。Research `_publish/_save_workspace/_complete`、ArtifactBuild `_complete`、DeliveryReceiptHandler各自提交对应事实，最终由查询/SSE回读供前端呈现。

依据：[AgentLifecycleWorkflow.run](../../../src/orchestration/agent_lifecycle_workflow.py#L73)、[RunLifecycleService](../../../src/run_domain/lifecycle.py)、[project_terminal](../../../src/conversation_domain/run_projection.py)、[ResearchActivities](../../../src/research_activities/runtime.py)、[ArtifactBuildService](../../../src/web_artifacts/build.py)、[DeliveryReceiptHandler](../../../src/delivery/service.py)。U：本轮没有启动这些worker或验证实际部署恢复。

## 4. 更新机制和防迟到策略

| 业务 | 更新入口 | 客户端状态落点 | 丢流/失败恢复与隔离 |
| --- | --- | --- | --- |
| chat Run | openRunFeed→openSseStream→SSEGateway；run.snapshot、message.delta、run.progress、trace.event、run.succeeded/failed/cancelled等已知事件 | workbench当前Run/Message临时正文；TraceStore独立following；budget GET仅刷新预算 | StreamSequencer stream/seq去重重排、gap永久degrade；confirmRun REST终态替换；startPolling 1/2/3/5秒；选择与account generation |
| Work | subscribeWork GET events/stream?after；GET works/{w}；列表轮询 | useWorks.receive cursor/event窗口→refresh，upsert按row_version；useTaskQuery按revision失效 | 3秒重连+GET恢复；控制器visible/route gating；API/Store generation；列表扫描暂停/部分结果可读 |
| Artifact | GET messages artifacts/Artifact/versions/单version；poll按版 | versionsByArtifactId、version cache、poll/command query；artifactUi选择/草稿 | 请求dispatch序号每版拒旧、completed不可回退；单版一poll/最多4并发；401清全域，403/404purge |
| Workspace | tree/search以及metadata/trace/retention/resources的REST | useWorkspace tree/search/cache/revision；操作queue单独保存 | 写后invalidate；搜索stale/generation；query key、账户与对象代次；保存分段恢复；撤权后GET Run观察 |
| 诊断/审批 | Inspector snapshot、trace/modelinput REST；审批可见时5秒检查 | runInspector与TraceStore独立选择/cache/approval intent | snapshot默认约2秒，错误退避最长10秒；可见性暂停；模型输入visibility变化清缓存；历史观察不由live事件抢选择 |
| 通知/交付 | inbox REST；Work snapshot内deliveries；命令后刷新 | TaskInbox本地分页；Work deliveries/targets projection | 查询错误独立重试；无未读SSE/mark-read；投递未知需显式resolve |

F依据：[runFeed](../../../web/src/sse/runFeed.ts)、[workFeed](../../../web/src/sse/workFeed.ts)、[workbench](../../../web/src/store/workbench.ts)、[works](../../../web/src/store/works.ts)、[artifacts](../../../web/src/store/artifacts.ts)、[workspace](../../../web/src/store/workspace.ts)、[RunInspector](../../../web/src/components/run/RunInspector.tsx)、[RunApprovals](../../../web/src/components/run/RunResources.tsx)、[TaskInbox](../../../web/src/components/tasks/TaskInbox.tsx)。事件名称/载荷以runFeed实际解析分支和服务端envelope为准；不是所有后端事件都有UI渲染。

## 5. 接入空白与易误判的封装

| 事项 | 当前事实 | 适配判断 |
| --- | --- | --- |
| Work API facade | 工作接受/控制/预算/目标/inputs主要直接ApiClient.request；旧HpApi.listResearchWorks并不只筛Research | F：[resources](../../../web/src/api/resources.ts)、[works](../../../web/src/store/works.ts)；不能按方法名判断后端范围 |
| Research evidence | 后端GET evidence；现TaskOutputs实际读取report/published-files，未找到evidence调用；已有HpApi.getResearchReport不等于当前TaskOutputs使用该wrapper | F：[ResearchQueryService](../../../src/research_domain/services.py)、[TaskOutputs](../../../web/src/components/tasks/TaskOutputs.tsx)；I可新增前端查询/展现 |
| Run范围Workspace search | 后端和ResourcePolicy已有冻结候选搜索，当前HpApi未封装对应Run search | F：[search_run_workspace](../../../src/web_api/app.py)、[WorkspaceDiscovery](../../../src/workspace/discovery.py)；I可接现有API，不扩当前Run候选 |
| Work Artifact input/evidence | POST works/{w}/artifacts真实验证completed/role/执行条件；未见正式Web选择调用 | F：[integration_command](../../../src/work_domain/integration.py)；I可接入，但不等于采用/验收 |
| 单独cancel Work Run | CommandService.cancel_run接受work variant；当前RunInspector未接Work Run单次cancel按钮，引导Work控制；retry端点明确拒绝work | F：[commands](../../../src/conversation_domain/commands.py)、[RunInspector](../../../web/src/components/run/RunInspector.tsx)；I可组合，但需解释单次取消与Work stop差异 |
| Requirement字段/时区 | 后端完整Requirement/SourceStrategy/IANA；表单常用字段与两时区，未编辑字段保留；普通表单artifact_build不可改 | F：[models](../../../src/work_domain/models.py)、[TaskEditor](../../../web/src/components/tasks/TaskEditor.tsx)、[taskRequirement](../../../web/src/components/tasks/taskRequirement.ts)；I更多字段可前端接入，时间转换/授权单独正确处理 |
| 文件工具范围 | tools定义、file_runtime注册、sandbox对Web manifest选择及部署worker三层不同；源码跳过PDF转换工具注册 | F：[sandbox_manager](../../../src/sandbox/sandbox_manager.py)、[file_runtime registry](../../../src/file_runtime/registry.py)；U真实可用集合，不能由文件名推用户能力 |
| client-only保护 | Artifact parent核对、影响预览参数fingerprint、dirty草稿离开保护不是后端强制的同一个原子契约 | F：[artifacts](../../../web/src/store/artifacts.ts)、[WorkspaceMutationDialog](../../../web/src/components/workspace/WorkspaceMutationDialog.tsx)、[useInspectorFlow](../../../web/src/components/shell/useInspectorFlow.ts)；重构保留等价保护，严格跨客户端保证须扩契约 |

## 6. 核对范围与测试证据

主要路由已与服务端命令/查询、前端wrapper或直接request、Store/Hook、事件处理及调用组件交叉核对。相关测试的存在/断言增强源码结论，**不表示本轮测试通过**：

- Conversation admission/idempotency/cancel/retry/ETag：[test_phase_b_api](../../../test/web_api/test_phase_b_api.py)、[workbench.test](../../../web/src/store/workbench.test.ts)。
- session、SSE、选择迟到/恢复：[sessionIsolation](../../../web/src/store/sessionIsolation.test.ts)、[sse_gateway](../../../test/web_api/test_sse_gateway.py)、[runFeed.test](../../../web/src/sse/runFeed.test.ts)。
- Work流程/结果/投递/命令恢复：[work_foundation](../../../test/web_api/test_work_foundation.py)、[work_integration](../../../test/web_persistence/test_work_integration.py)、[taskOperations.test](../../../web/src/components/tasks/taskOperations.test.ts)、[TaskIntegration](../../../web/src/components/tasks/TaskIntegration.test.tsx)。
- 文件/授权/CAS/部分成功：[workspace_direct_upload](../../../test/web_api/test_workspace_direct_upload.py)、[workspace_v41_p2](../../../test/web_persistence/test_workspace_v41_p2.py)、[workspace_v41_p3](../../../test/web_persistence/test_workspace_v41_p3.py)、[WorkspaceCommands](../../../web/src/components/workspace/WorkspaceCommands.test.tsx)。
- Artifact/审批/安全投影：[web_artifacts](../../../test/web_persistence/test_web_artifacts.py)、[UI6SelfReview](../../../web/src/store/UI6SelfReview.test.ts)、[file_action_approvals](../../../test/web_persistence/test_file_action_approvals.py)、[model_observability](../../../test/web_api/test_model_observability.py)。

API与架构文档辅助核对：[HTTP API](../../reference/api.md)、[Durable Work](../../architecture/durable-work-v1.md)、[Workspace](../../architecture/workspace-v4.1.md)、[Web UI](../../architecture/web-ui.md)。代码不一致项列于Interaction G01–G11，不将旧视觉验收当业务契约。

## 附录：当前显式HTTP路由清单

以下直接由当前 `src/web_api/app.py` 的AST route decorator提取，88条；不导入/运行应用，不包含框架自动OpenAPI等路由。方法、路径、函数和行号可逐条回源。路由存在与前端已开放不同：前端真实接入见L01–L18和第5节；文件能力/权限仍可能令路由不可用。GET events为SSE入口，其他GET通常为REST。

| 方法 | 路径 | 路由函数/源码 |
| --- | --- | --- |
| GET | `/health/live` | [live](../../../src/web_api/app.py#L523) |
| GET | `/health/ready` | [ready](../../../src/web_api/app.py#L527) |
| POST | `/auth/login` | [login](../../../src/web_api/app.py#L533) |
| POST | `/auth/register` | [register](../../../src/web_api/app.py#L553) |
| GET | `/auth/login` | [login_entry](../../../src/web_api/app.py#L613) |
| GET | `/api/v1/me` | [me](../../../src/web_api/app.py#L622) |
| POST | `/api/v1/identity-bindings/qq/challenges` | [create_qq_binding_challenge](../../../src/web_api/app.py#L642) |
| GET | `/api/v1/identity-bindings/qq/challenges/{challenge_id}` | [get_qq_binding_challenge](../../../src/web_api/app.py#L656) |
| POST | `/api/v1/auth/logout` | [logout](../../../src/web_api/app.py#L680) |
| POST | `/api/v1/conversations` | [create_conversation](../../../src/web_api/app.py#L693) |
| GET | `/api/v1/works` | [list_works](../../../src/web_api/app.py#L723) |
| GET | `/api/v1/works/{work_id}` | [get_work](../../../src/web_api/app.py#L728) |
| POST | `/api/v1/works` | [accept_work](../../../src/web_api/app.py#L741) |
| POST | `/api/v1/works/{work_id}/revisions` | [revise_work](../../../src/web_api/app.py#L751) |
| POST | `/api/v1/works/{work_id}/pause` | [pause_work](../../../src/web_api/app.py#L758) |
| POST | `/api/v1/works/{work_id}/resume` | [resume_work](../../../src/web_api/app.py#L764) |
| POST | `/api/v1/works/{work_id}/stop` | [stop_work](../../../src/web_api/app.py#L770) |
| POST | `/api/v1/works/{work_id}/advance` | [advance_work](../../../src/web_api/app.py#L776) |
| PUT | `/api/v1/works/{work_id}/conversations/{conversation_id}` | [link_work](../../../src/web_api/app.py#L782) |
| GET | `/api/v1/works/{work_id}/events` | [work_events](../../../src/web_api/app.py#L788) |
| GET | `/api/v1/works/{work_id}/runs` | [work_runs](../../../src/web_api/app.py#L793) |
| GET | `/api/v1/works/{work_id}/artifacts` | [work_artifacts](../../../src/web_api/app.py#L797) |
| POST | `/api/v1/works/{work_id}/artifacts` | [reference_work_artifact](../../../src/web_api/app.py#L801) |
| GET | `/api/v1/works/{work_id}/notification-targets` | [work_targets](../../../src/web_api/app.py#L807) |
| PUT | `/api/v1/works/{work_id}/notification-targets` | [set_work_target](../../../src/web_api/app.py#L814) |
| DELETE | `/api/v1/works/{work_id}/notification-targets/{target_id}` | [disable_work_target](../../../src/web_api/app.py#L819) |
| POST | `/api/v1/works/{work_id}/budget` | [increase_work_budget](../../../src/web_api/app.py#L824) |
| GET | `/api/v1/works/{work_id}/inputs` | [work_inputs](../../../src/web_api/app.py#L829) |
| POST | `/api/v1/works/{work_id}/inputs` | [add_work_input](../../../src/web_api/app.py#L836) |
| DELETE | `/api/v1/works/{work_id}/inputs/{ref_id}` | [revoke_work_input](../../../src/web_api/app.py#L841) |
| POST | `/api/v1/works/{work_id}/accept-result` | [accept_work_result](../../../src/web_api/app.py#L846) |
| GET | `/api/v1/notifications` | [notification_inbox](../../../src/web_api/app.py#L851) |
| POST | `/api/v1/works/{work_id}/deliveries/{delivery_id}/resolve` | [resolve_work_delivery](../../../src/web_api/app.py#L858) |
| GET | `/api/v1/works/{work_id}/events/stream` | [stream_work_events](../../../src/web_api/app.py#L864) |
| GET | `/api/v1/works/{work_id}/resources` | [work_resources](../../../src/web_api/app.py#L889) |
| POST | `/api/v1/works/{work_id}/resources` | [grant_work_resource](../../../src/web_api/app.py#L893) |
| DELETE | `/api/v1/works/{work_id}/resources/{grant_id}` | [revoke_work_resource](../../../src/web_api/app.py#L899) |
| GET | `/api/v1/runs/{run_id}/research/evidence` | [research_evidence](../../../src/web_api/app.py#L905) |
| GET | `/api/v1/runs/{run_id}/research/report` | [research_report](../../../src/web_api/app.py#L909) |
| GET | `/api/v1/conversations/{conversation_id}/file-candidates` | [list_file_candidates](../../../src/web_api/app.py#L913) |
| POST | `/api/v1/conversations/{conversation_id}/uploads` | [create_upload](../../../src/web_api/app.py#L923) |
| POST | `/api/v1/workspace/uploads` | [create_workspace_upload](../../../src/web_api/app.py#L942) |
| PUT | `/api/v1/uploads/{file_id}/content` | [upload_content](../../../src/web_api/app.py#L959) |
| GET | `/api/v1/files/{file_id}` | [get_file](../../../src/web_api/app.py#L971) |
| GET | `/api/v1/workspace` | [get_workspace](../../../src/web_api/app.py#L1004) |
| GET | `/api/v1/workspace/search` | [search_workspace](../../../src/web_api/app.py#L1008) |
| GET | `/api/v1/runs/{run_id}/workspace/search` | [search_run_workspace](../../../src/web_api/app.py#L1023) |
| GET | `/api/v1/workspace/space` | [workspace_space](../../../src/web_api/app.py#L1039) |
| GET | `/api/v1/workspace/nodes/{node_id}/trace` | [workspace_trace](../../../src/web_api/app.py#L1043) |
| GET | `/api/v1/workspace/files/{file_id}/retention` | [workspace_retention](../../../src/web_api/app.py#L1048) |
| GET | `/api/v1/conversations/{conversation_id}/resources` | [conversation_resources](../../../src/web_api/app.py#L1053) |
| POST | `/api/v1/conversations/{conversation_id}/resources` | [grant_conversation_resource](../../../src/web_api/app.py#L1060) |
| DELETE | `/api/v1/conversations/{conversation_id}/resources/{grant_id}` | [revoke_conversation_resource](../../../src/web_api/app.py#L1071) |
| DELETE | `/api/v1/conversations/{conversation_id}/attachments/{file_id}` | [revoke_conversation_attachment](../../../src/web_api/app.py#L1097) |
| GET | `/api/v1/runs/{run_id}/resources` | [run_resources](../../../src/web_api/app.py#L1107) |
| POST | `/api/v1/workspace/directories` | [create_workspace_directory](../../../src/web_api/app.py#L1116) |
| POST | `/api/v1/workspace/files` | [save_workspace_file](../../../src/web_api/app.py#L1126) |
| GET | `/api/v1/workspace/nodes/{node_id}/versions` | [workspace_versions](../../../src/web_api/app.py#L1136) |
| GET | `/api/v1/runs/{run_id}/published-files` | [run_published_files](../../../src/web_api/app.py#L1141) |
| POST | `/api/v1/workspace/nodes/{node_id}/upgrade` | [upgrade_workspace_file](../../../src/web_api/app.py#L1158) |
| POST | `/api/v1/workspace/nodes/{node_id}/versions` | [update_workspace_file](../../../src/web_api/app.py#L1164) |
| GET | `/api/v1/workspace/nodes/{node_id}/impact` | [workspace_node_impact](../../../src/web_api/app.py#L1181) |
| PATCH | `/api/v1/workspace/nodes/{node_id}` | [move_workspace_node](../../../src/web_api/app.py#L1186) |
| DELETE | `/api/v1/workspace/nodes/{node_id}` | [remove_workspace_node](../../../src/web_api/app.py#L1197) |
| DELETE | `/api/v1/files/{file_id}` | [delete_file](../../../src/web_api/app.py#L1210) |
| GET | `/api/v1/files/{file_id}/lineage` | [get_file_lineage](../../../src/web_api/app.py#L1220) |
| GET | `/api/v1/runs/{run_id}/file-action-approvals` | [list_file_action_approvals](../../../src/web_api/app.py#L1229) |
| POST | `/api/v1/file-action-approvals/{approval_id}/approve` | [approve_file_action](../../../src/web_api/app.py#L1237) |
| POST | `/api/v1/file-action-approvals/{approval_id}/reject` | [reject_file_action](../../../src/web_api/app.py#L1251) |
| GET | `/api/v1/files/{file_id}/content` | [download_file](../../../src/web_api/app.py#L1265) |
| GET | `/api/v1/conversations` | [list_conversations](../../../src/web_api/app.py#L1291) |
| GET | `/api/v1/conversations/{conversation_id}` | [get_conversation](../../../src/web_api/app.py#L1295) |
| PATCH | `/api/v1/conversations/{conversation_id}` | [rename_conversation](../../../src/web_api/app.py#L1302) |
| GET | `/api/v1/conversations/{conversation_id}/messages` | [list_messages](../../../src/web_api/app.py#L1318) |
| POST | `/api/v1/conversations/{conversation_id}/messages` | [send_message](../../../src/web_api/app.py#L1322) |
| GET | `/api/v1/runs/{run_id}` | [get_run](../../../src/web_api/app.py#L1358) |
| GET | `/api/v1/runs/{run_id}/trace` | [get_run_trace](../../../src/web_api/app.py#L1362) |
| GET | `/api/v1/runs/{run_id}/model-inputs` | [list_run_model_inputs](../../../src/web_api/app.py#L1375) |
| GET | `/api/v1/model-inputs/{snapshot_id}` | [get_model_input](../../../src/web_api/app.py#L1383) |
| POST | `/api/v1/messages/{message_id}/artifacts` | [create_artifact](../../../src/web_api/app.py#L1391) |
| GET | `/api/v1/messages/{message_id}/artifacts` | [list_message_artifacts](../../../src/web_api/app.py#L1412) |
| GET | `/api/v1/artifacts/{artifact_id}` | [get_artifact](../../../src/web_api/app.py#L1417) |
| GET | `/api/v1/artifacts/{artifact_id}/versions` | [list_artifact_versions](../../../src/web_api/app.py#L1422) |
| POST | `/api/v1/artifacts/{artifact_id}/versions` | [create_artifact_version](../../../src/web_api/app.py#L1427) |
| GET | `/api/v1/artifact-versions/{artifact_version_id}` | [get_artifact_version](../../../src/web_api/app.py#L1451) |
| GET | `/api/v1/runs/{run_id}/events` | [run_events](../../../src/web_api/app.py#L1458) |
| POST | `/api/v1/runs/{run_id}/cancel` | [cancel_run](../../../src/web_api/app.py#L1512) |
| POST | `/api/v1/runs/{run_id}/retry` | [retry_run](../../../src/web_api/app.py#L1523) |
