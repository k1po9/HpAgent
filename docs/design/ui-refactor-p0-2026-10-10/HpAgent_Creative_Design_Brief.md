> 历史设计/交接记录：保留当时基线和验证边界；当前实现与操作从[文档导航](../../README.md)进入。

# HpAgent Creative Design Brief — 创意阶段产品能力输入

审计基线：2026-10-10，当前开发工作副本 `main@a47d73b06a993d353b4177e282b69ddd28ffdb36`。本说明用于下一阶段创意探索，**不规定导航、布局、组件、视觉层级或具体交互形式**。现有UI只是业务参考；后端已实现的事实和安全约束才是适配边界。

**F＝源码已核实，未作本轮运行验收；I＝由已实现能力推导的用户目标/设计空间；U＝尚未确认。** 能力全表见 [Capability Map](HpAgent_UI_Capability_Map.md)，状态/安全与差异见 [Interaction Contract](HpAgent_Interaction_Contract.md)，实现追踪见 [Integration Map](HpAgent_Frontend_Integration_Map.md)。

## 1. 产品能够承接的工作

HpAgent让用户提出有限的AI请求，也能接受持续委托；用户可以给委托明确要求、时机、资源权限和预算，并持续核对执行、成果和交付。文件具有长期入口及不可变版本，Agent使用资料必须有明确权限。HTML Artifact可从完成的回答生成并修改成新版本；研究工作另有证据与报告生产链。账户可连接Web和QQ身份，通知去向由已验证来源决定。F：E01–E06、E08，均为源码能力，不保证当前部署全部开关打开或每次模型调用成功。

| 用户希望完成的事（I） | 已有产品能力（F） | 必须让用户理解的真实边界 | 依据 |
| --- | --- | --- | --- |
| “现在帮我解决一个问题，并让我知道进展。” | 多轮Conversation；ReAct/Plan-and-Execute；有限Run、回复/进度/失败诊断与取消 | 同一对话一个活跃chat Run；进度不是已完成正文；观察中断不代表执行中断 | E01、E07 |
| “替我持续推进这件事，我稍后回来核对。” | Work要求版本、即时/指定时刻/每日计划、暂停/恢复/停止/推进、事件历史 | 一次Run与持续Work不同；接受/推进/运行/完成不同；暂停与停止可能等待未决责任 | E02、E07 |
| “到时候提醒我，或定期完成一轮工作。” | deterministic提醒；once/daily计划；账户inbox、合法QQ渠道投递 | daily为ongoing；时间、时区和错过执行规则明确；投递接受不等于已读/验收 | E02、E06 |
| “查资料、形成可核对的研究成果。” | Research来源策略、evidence/report、Markdown/HTML成果、发布文件、必要保存和验收 | 来源策略/网络工具受环境限制；报告、呈现版、长期保存和交付是独立事实；不承诺所有来源已联网验真 | E02、E04、E08；运行效果U |
| “让Agent只用我允许的资料，并能撤回权限。” | 对Conversation/Work分别授权；独立input ref；冻结Run候选、固定版本、撤权阻断与受影响执行观察 | 保存不等于授权；候选不等于已读取；给新资料一般下轮生效，撤权仍对当前访问生效 | E03 |
| “保留资料和产物，以后还能找回并知道来源。” | 长期Workspace、目录入口、检索、血缘/保留原因、下载、可更新entry和历史revision | File与入口不同；名称/路径可变但ID稳定；移除入口不保证释放字节；当前检索不是任意全文语义搜索 | E03 |
| “将回答变成可使用的HTML成果，再提出修改。” | 来源于完成助手消息的Artifact；异步独立构建、版本历史、受限交互预览、下载/源码副本保存 | 新版不覆盖完成旧版；构建、预览、下载、保存、采用和验收分别成立；修改基准严格跨客户端CAS尚缺 | E05、G03/G08 |
| “知道哪里卡住、下一步需要我做什么。” | continuation/reason、失败详情、预算used/reserved、文件审批、投递决议、trace/model-input安全投影 | 预算、权限、待资料、未知副作用/投递不能都用普通重试；批准操作不等于操作完成 | E02、E06、E07 |
| “在Web和QQ之间继续使用我的账户。” | 账户注册/登录恢复，QQ绑定挑战，账户对象与通知目标连续性 | 绑定须真实身份验证；关联不自动合并所有Conversation；群渠道只允许摘要 | E06 |

## 2. 可用的产品对象与关联

| 对象 | 可以围绕它表达的用户意图 | 关键关系 |
| --- | --- | --- |
| Conversation / Message | 提问、提供上下文、查看按顺序的交流、命名和恢复 | Message触发有限chat Run；Conversation可关联多个Work，但关联不是历史/附件/权限导入 |
| Work / Requirement | 委托、修订目标与约束、设计划、控制生命周期、累计预算、验收 | 一个Work多次有限Run；Requirement每次修订不可变，control_epoch防止旧执行提交新要求 |
| Run / Execution / branch | 查看一次执行、所用资源、过程、结果和诊断 | chat与work来源不同；branch是内部受限执行结构，用户没有任意建Agent团队接口 |
| File / Workspace entry / revision / grant | 上传、长期保存、授权、追溯、提交新版、下载 | bytes不可变、入口稳定、版本可追溯；所有权、读写授权、长期保存分别管理 |
| Artifact / Artifact Version | 生成/修改HTML、选择具体版、预览、保存、引用、明确接受 | 与File revision不同；Work原交付绑定具体Artifact版；手工新版本不能自动代替原证据 |
| Approval / operation / delivery / notification | 明确决定有风险动作、核对未知结果、合法投递与收件事实 | 不同责任对象：审批、副作用结果、渠道接受和成果验收不是同一确认 |

F依据：E01–E07及 [Hp领域DTO](../../../web/src/api/types.ts)。这些关系必须准确，设计师可用任何合适方式表达，不需要暴露所有内部字段或技术名词。

## 3. 无法改变的语义与安全约束

1. **账户与身份是真边界。** 所有对象按Account隔离；会话失效结束当前上下文，缓存、草稿、模型输入和下载引用同步清理；已登录业务写操作有CSRF；另一对象/账户的晚到响应不能落到当前界面。F：E06、E09。
2. **用户意图与后端确认要区分。** submitting、网络未知、已接受、执行中、终态是不同事实；同一未知意图复用原幂等key/body/version，确定冲突保留草稿并重新核对。不能用乐观状态代替已执行/已停止。F：E01、E02、E09。
3. **执行、委托与停止不能混为一体。** chat占单一活跃槽，cancelled不可retry；安全failed才可重试。Work失败Run应通过Work继续条件恢复；取消单次Run不能当停止持续Work。pausing/stopping在未知责任未清前不提前结束。F：E01、E02。
4. **资源使用需要独立、具体的权限。** 五种操作与递归分别授权；保存/关联/资源请求不自动发权限；冻结候选及已固定版本不能在本轮随意扩张；撤权阻止后续访问并清理敏感观察缓存。F：E03、E07。
5. **版本和原成果证据不能偷换。** Work修订、预算、长期entry更新都有并发基准；旧epoch执行无权完成新要求；Artifact完成旧版不可覆盖；原交付验收须精确revision与版本。F：E02、E03、E05。
6. **成功有具体条件。** 普通文字不自动构成通用Work完成证据；required保存须已提交；渠道接受不是已读/成果验收；ongoing完成一轮也可能继续active。F：E02、E04、E06。
7. **预算和未知副作用保留责任。** retry/修订/Subagent不清累计用量；增额须显式且不能代替账户日额度/权限；unknown operation没有通用人工解除接口，unknown delivery不能自动重复投递。F：E02、E06、E07。
8. **安全呈现与真实新鲜度必须成立。** HTML预览保持sandbox；诊断按visibility投影；SSE缺口改查权威snapshot，终态覆盖delta；无Artifact/Workspace统一SSE，不能假定所有业务即时更新。F：E05、E07；不同HTML发布来源一致CSP U，见G08。

约束限制的是语义、资格和确认机制，**不要求旧文字、按钮、面板或对话框形式**。例如必须使用户能理解明确增额/重复投递风险并表达决定，但可以重新设计这一过程的交互。

## 4. 创意可以使用的自由空间

I：可以从用户要完成的工作、产物、资源或责任状态出发重新组织产品；Conversation不是后台Work的唯一容器。查询各对象、追踪来源、按权限组合事实、重新表达执行进展和待用户决定的事项，都不依赖现有导航与页面排列。现有任务四桶、三个screen、Inspector、Sidebar、assistant-ui只是本地实现，均可替换。F依据：[shell](../../../web/src/store/shell.ts)、[presentTask](../../../web/src/components/tasks/taskPresentation.ts)、[runtime adapter](../../../web/src/adapters/assistant-ui/runtime.ts)；这是自由边界，不是新视觉方案。

| 可通过前端组合/接入实现（I） | 可依赖的已有事实与边界（F） |
| --- | --- |
| 组合显示关联Work、执行、资料、具体产物、交付和预算 | 现有查询与对象ID；按权限读取；跨对象不是原子事务；观察不同步/部分查询失败须准确表达 |
| 将“上传/保存/使用”作为连续过程 | 已有upload→ready→save→grant分段流程；分别记录结果与只补失败步骤；不自动扩张活跃Run范围 |
| 更完整地表达委托要求、来源策略与计划 | 后端已有constraints/acceptance_criteria/resource_requests/deliverable_policy、SourceStrategy、IANA时区；请求与权限仍分开；自动required保存覆盖不能泛化所有能力 |
| 使用已有evidence、Run冻结资源检索、Artifact input/evidence引用 | 后端端点已存在而Web接入有限；检索不扩大候选；引用不冒充deliverable/采用 |
| 对已有版本、执行和来源作本地比较/筛选 | 历史查询与不可变版；是前端比较，不是服务端merge/分叉；部分历史明细有100条上限 |
| 重构状态理解、异常恢复和诊断入口 | 复用精确status/continuation/reason/资格与安全投影；加载失败、执行失败、预览失败和命令未知分别处理 |

对应实现依据：E01–E09、[Integration Map第2/5节](HpAgent_Frontend_Integration_Map.md)。设计不必保留组件，但工程建议复用ApiClient、领域DTO、命令恢复、SSE序列、权限/save/version业务逻辑及会话清理。

## 5. 不能作为现成能力使用

F，当前未发现所需后端契约：多人协作/分享ACL、跨设备草稿、消息编辑/分叉、Conversation删除归档、通知read/unread、Artifact公开托管/网站发布、自由任意收件人、weekly/任意cron、任意Agent团队、通用未知operation人工解除、原子跨对象流程、客户端Artifact parent CAS。若创意依赖这些，应标为**需后端/服务扩展**，不能用新组件模拟成服务器事实。依据：E01/E02白名单、[当前完整路由](HpAgent_Frontend_Integration_Map.md)、[请求DTO](../../../src/web_api/models.py)、[delegation_contracts](../../../src/agent_workflows/delegation_contracts.py)。

F，已有能力也有条件：文件上传/transform/shell开关；仅明确允许的文件类型，非通用多模态；Web文件模式不等于所有MCP/工具可用；PDF转换定义不等于被注册。U：实际模型效果、QQ投递、网络研究来源、worker依赖/长跑恢复、所有Work审批触发覆盖。本轮源码审计未运行这些链路。依据：E03、E06、E08；不能把“有代码/有测试文件”写成运行验收通过。

特别需要设计阶段知道的差异：G03修改基准不是服务端CAS；G04 Research手工版本限制未见服务层强制；G05输入ref列表含撤销历史，当前UI未消费available；G06输出-only撤权的主动取消覆盖未确认；G10补资料不保证自动清awaiting_input。完整源码与确认建议见 [Interaction差异登记](HpAgent_Interaction_Contract.md)。无需在本轮解决，但创意不能将未确认处画成无条件承诺。

## 6. 创意产出应能回答的问题

这些是产品可用性的判断问题（I），不指定视觉答案：用户能否知道请求是否被接受、谁在持续承担工作、当前在等什么、怎样补资料/控制/恢复；能否知道Agent实际获准使用什么、哪个版本是原成果、什么已保存/已投递/待验收；能否作出明确预算/审批/投递风险决定；网络断开、冲突、撤权或部分失败后能否继续核对并保留未完成意图。

下一阶段每项新交互应标出：依赖的已验证能力ID，前端组合还是后端扩展，所用状态事实及不可省略的确认/恢复。它不需要映射到当前页面。能力ID可直接引用Capability Map的A/C/W/F/H编号；工程契约引用Interaction章节与G编号。

## 7. 关键证据索引

| 编号 | 源码及函数/类型 |
| --- | --- |
| E01 Conversation/Run | [CommandService.create_conversation/send_message/cancel_run/retry_run](../../../src/conversation_domain/commands.py)、[SingleActiveRunAdmission](../../../src/conversation_domain/admission.py)、[QueryService](../../../src/web_api/queries.py) |
| E02 Durable Work | [Requirement/WORK_STATUSES/CONTINUATIONS](../../../src/work_domain/models.py)、[WorkCommandService](../../../src/work_domain/commands.py)、[integration_command/validate_evidence](../../../src/work_domain/integration.py)、[WorkCompletionPolicy.accept](../../../src/work_domain/completion.py)、[generic_result](../../../src/application/work_context.py)、[timing](../../../src/work_domain/timing.py) |
| E03 File/Workspace | [FileService](../../../src/web_domain/file_services.py)、[WorkspaceCatalog](../../../src/workspace/catalog.py)、[ResourcePolicy](../../../src/workspace/resources.py)、[WorkspaceDiscovery](../../../src/workspace/discovery.py) |
| E04 Research | [SourceStrategy](../../../src/research_domain/models.py)、[ResearchQueryService](../../../src/research_domain/services.py)、[ResearchActivities._publish/_save_workspace/complete_research_activity](../../../src/research_activities/runtime.py) |
| E05 Artifact | [ArtifactService.create_artifact/create_version/_enqueue](../../../src/web_artifacts/services.py)、[ArtifactBuildService](../../../src/web_artifacts/build.py)、[WebArtifactGenerator.harden](../../../src/web_artifacts/generator.py)、[PreviewFrame](../../../web/src/components/ArtifactPreview.tsx) |
| E06 身份/Delivery | [AuthService](../../../src/web_api/auth.py)、[IdentityBindingService](../../../src/account/identity_binding_service.py)、[target_from_origin/DeliveryReceiptHandler](../../../src/delivery/service.py)、[Web API me/通知/CSRF路由](../../../src/web_api/app.py) |
| E07 执行/观察/审批 | [RunLifecycleService](../../../src/run_domain/lifecycle.py)、[SSEGateway.stream](../../../src/web_api/sse.py)、[ModelObservabilityQueries](../../../src/web_api/model_observability_queries.py)、[FileActionApprovalService](../../../src/file_domain/approvals.py) |
| E08 实际工具入口 | [create_main_work_tools](../../../src/application/main_agent.py)、[create_execution_sandbox](../../../src/sandbox/sandbox_manager.py)、[FileCapabilityConfig](../../../src/workspace/file_capability_config.py)、[AgentLifecycleWorkflow.run](../../../src/orchestration/agent_lifecycle_workflow.py) |
| E09 客户端业务保护 | [ApiClient](../../../web/src/api/client.ts)、[sessionLifecycle](../../../web/src/store/sessionLifecycle.ts)、[workbench](../../../web/src/store/workbench.ts)、[taskCommand](../../../web/src/components/tasks/taskOperations.ts)、[workspaceOperations](../../../web/src/components/workspace/workspaceOperations.ts)、[versionOperations](../../../web/src/components/workspace/versionOperations.ts)、[artifacts](../../../web/src/store/artifacts.ts) |
