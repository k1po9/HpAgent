# 能力边界

## Account 与模型访问治理

Account 是模型访问的主体。`account_entitlements` 保存账号自己的 `model_access_tier`、`daily_token_limit`、`prompt_visibility`、有效期和版本；账号停用、entitlement 缺失或过期时，模型访问不可用。Web 自助注册可不填邀请码，获得代码定义的默认 entitlement；也可使用管理员预先创建的邀请凭证，将其 `entitlement_profile` 复制到新账号。邀请码只参与注册时的 provision，后续模型访问读取账号 entitlement，而不是反复读取邀请码。注册和额度细节见[账号治理运维说明](../operations/account-governance.md)。

模型调用经受治理的 Provider 路径执行：先检查账号 entitlement 与端点 tier，准备 Provider 请求并冻结 Model Input Snapshot，再由 `ModelBudgetCoordinator` 在同一个 PostgreSQL 事务中原子预留 Account UTC 日额度、累计 Work budget（若有）与 Run budget；随后才向 Provider 发送请求，并结算或释放预留。Snapshot 冻结不属于预算预留事务。`daily_token_limit` 为 `NULL` 时不设 Account 每日上限，Run budget 仍独立生效。Model Input 查询按账号的 `prompt_visibility` 投影为最小元数据、摘要或已保存的 Provider 请求体；凭据与 Secret 不应写入模型输入。

## Agent

Agent 能力由 Context Assembly、Brain/Model 决策循环和持久化策略 Workflow 组成。ReAct 与 Plan-and-Execute 共享同一套 Lifecycle、Actions、Memory、Workspace 和结果提交路径。

模型角色与阶段提示词集中在 [agents.yaml](../../config/prompts/agents.yaml)：`main` 面向交互，`worker` 执行 Generic Work，`subagent` 负责只读调查分支；`planning`、`evaluation`、`step_execution` 为规划执行阶段，`research` 与 `html` 是专业生成能力，`handoff` 是委托提交后的交接提示。Research/HTML 不是额外的 Work 管理 agent；文档转换等确定性工具没有模型角色提示词。

这些提示词均用中文表述，工具名、JSON 字段和枚举值保留协议拼写。可以直接编辑配置；进程首次读取后缓存，修改后需重启相关应用进程，已有持久化上下文与模型输入快照不回写。角色配置从仓库/应用根目录下 `config/prompts/agents.yaml` 读取，缺失、无效或缺少所需键时报错，不静默使用 Python 内置角色文本。渠道身份、工具纪律、环境、记忆及工具摘要仍分别维护在同目录已有 YAML 中；工具参数 schema 与权限、预算、验收校验仍由代码控制。

新建 Main Chat Run 在不可变 `input_snapshot.main_protocol=tool_first_v1` 中冻结协议。每轮决策必须产生一个实际结构化工具动作；普通回答通过 `respond_directly(answer, command_refs?)` 结束，其他动作执行后继续 ReAct。Provider 请求在预算预留与快照之前冻结 required tool choice 与单动作限制，服务端再次校验；无工具、伪工具文本或并行批次不执行动作、不作为最终回复，按原轮次预算修正，强制最终轮只提供收尾工具，违规失败。历史 Run 未冻结该协议时保留原行为，避免改变旧 History。

`respond_directly` 使用原 Execution operation、transcript CAS、fence 与预算持久化收尾回执并支持响应丢失重放。`response_terminated` 只证明交互结束。业务成功事实必须来自本 Run 已观察、同账户、已提交且成功的 `idempotency_commands`；`command_refs` 不能引用未观察或其他 Run 的命令。服务端生成独立的已核验命令事实，口头承诺、回答动作及模型编造 ID 均不建立 Work。真正委托仍只能经 `accept_work`，查询或普通回答不强制创建 Work。

回复中的显式业务成功声明按有限的对象、动作和状态规则识别，并要求真实回执支持该类操作；接受 Work 不证明文件已保存、消息已发送或任务已完成。普通记忆来源描述、否定、条件及转述不直接判作成功声明。未获支持的声明返回“无法核实”提示，先查询任务或文件状态再决定是否重发；这层文本检查不是完整语义证明，也不改变数据库业务状态。

## Work 与执行策略

Work 接受持续委托，固定不可变 requirement revision、checkpoint 与 continuation。提醒、研究、通用工作和成果构建通过服务端 StrategyRegistry 选择注册 executor；不支持的能力直接拒绝。Main 使用 Work 管理工具，Work Agent 不获得这些工具；工作上下文不加载关联聊天全文。Generic Work root 可一次委派最多三个只读分支，共用 Run / Work 预算。完成由持久证据与 WorkCompletionPolicy 判断。详见 [Work](work.md)。

## 可组合能力基础契约

[capability_contracts.py](../../src/run_domain/capability_contracts.py) 定义显式版本化的 CapabilityDefinition、CapabilityResult、ResultRef、Check、CapabilityObservation 与 AcceptancePredicate。静态 CapabilityRegistry 与顶层 StrategyRegistry 分离，注册时冻结定义副本；发现只按 surface / 依赖筛选，不是授权。定义使用封闭、自包含 JSON Schema，调用登记和结果提交分别校验输入、输出；不允许远程 schema 引用。

[capabilities.py](../../src/run_domain/capabilities.py) 的 CapabilityFacts 是服务端事务接口，要求调用方提供 UoW 与有效 Execution fence。`create` 以已存在的 root tool Operation 为 invocation ID，固定 definition（含实现版本）、规范化 inputs、resource_scope、limits 和 requirement revision/control epoch，原子创建同 account / Run 的 capability Execution。同身份同描述去重，不同描述冲突；不同 Operation 可以多次调用同一能力。资源上限包含 node_ids、file_ids、tool_names、result_ids，空集合须显式传入。定义与调用限制不替代真实调用的预算预留或容量票据。

`publish_result` 从当前 fence 填充并核对结果 provenance（invocation_id / execution_id / operation_id），即使没有输出也保留准确生产者；原子完成子生产 Operation、回执、结果、引用和检查记录，并设置子 Execution 的 result_ref；不完成 caller Operation、Execution、Run 或 Work。`read_result` 仅供同 Run 的 caller / producer 查询；`read_ref` 返回 ResultRef 值对象，解析准确 file_id 或 Artifact Version，并检查当前执行身份、显式输入或采用关系及当前资源权限。结果元数据接口不直接返回文件字节。`adopt` 由 root 将获准旧结果固定到当前 Execution、revision 和 control epoch，读取仍重验；采用不改写生产者，也不代表 Workspace 保存。

检查记录固定 verifier key/version、subject_ref、verdict（passed / failed / not_checked）和 evidence_ref；证据必须引用同一结果的 check_evidence 输出。检查由可信实现提供，基础服务只验证登记与关联，不执行专业检查。AcceptancePredicate 固定 output_exists / check_passed / workspace_saved / user_accepted / delivery_accepted 的字段；`check_passed` 固定 verifier key/version；`workspace_saved` 的 target_id 为 Workspace node_id，operation_id 固定保存/版本操作，target_revision 可固定 destination revision；user/delivery 的 target_id 指向各自可信回执。Generic Work 已接入前三类准确成果谓词，用户/渠道仍通过各自可信回执验收；固定专业父适配器保留原有专用证据。

`CapabilityObservation` 固定 schema_version / invocation_id / producing_operation_id / producer_result_ref，放在父 Operation result_payload 和 tool_result 事件 payload 的 `capability_observation` 字段。子生产引用为 `capability-result:{invocation_id}`，父 Operation 使用自己的唯一引用（例如 `capability-observation:{invocation_id}`）；不能复用生产引用。`record_observation` 要求父事件及 caller Operation 已在同一 UoW 中写入，且两处指针与精确 producer 一致。Harness 的持久调用边界见下节；Research、HTML 与文字发布已注册局部能力。专业快捷入口和通用调用的边界见下文。

## 通用持久调用与 Harness

[CapabilityInvocationWorkflow](../../src/agent_workflows/capability.py) 接入共享 ToolExecutionWorkflow。服务端 [CapabilityBinding](../../src/agent_activities/capabilities.py) 将准确契约版本绑定到固定 Workflow 和输入资源范围解析器；`capability__` 前缀保留给复杂能力。模型只填写有类型的业务参数，不提供 Execution、fence、资源上限或 Workflow 名称。可选字符串的 null 及要求非空/UUID 字段的空串在作用域解析、校验和冻结前规范化为缺省；必填字段和两个非空互斥来源仍严格拒绝。生产 [compose_capabilities](../../src/agent_activities/composition.py) 是 Harness、固定专业入口和隔离 Fake 执行器共用的目录，注册 `research` v1（`capability__research__v1`）与 `html` v1（`capability__html__v1`），另有 Work 专用 `text` v1（`capability__text__v1`），分别固定到 ResearchCapabilityWorkflow / HtmlCapabilityWorkflow / TextCapabilityWorkflow；服务可用性显式传入，重试与时限读取同一能力定义。确定性扩展能力仅位于测试支持代码。`delegate_work` 仍走独立的只读分支边界。

发现按 surface 与服务依赖筛选，仅 root 获得复杂能力工具。执行时再次检查当前账号、Run、Work revision/control epoch、父 Execution、输入 schema、冻结定义和显式资源范围。输入读取适配器使用 `invocation_context` 检查当前子 fence、定义实现版本和资源权限；专业实现每次使用资源和发出模型请求仍须重验，不可把登记视为永久授权。

父登记 segment 结束后，以子 Execution UUID 固定 Child Workflow 身份；等待不持父 lease、Workspace 锁或物理容量票据。专业 Workflow 自行将短 Activity 划分为独立 segment，重试次数与总时限来自冻结定义；持久审批/输入等待复用 DurableWait 与权威 probe，Signal 不代表批准。能力只允许 read_only / candidate_output，不因此获得长期修改、外发或审批权限；已有 Atomic Tool 的文件审批路径保持独立。

准入串行核对父侧单个活动复杂调用，以及账号/全局子 Execution 数量（`CAPABILITY_ACCOUNT_EXECUTIONS` 默认 4，`CAPABILITY_GLOBAL_EXECUTIONS` 默认 32）；容量不足产生持久失败观察。预算准入只检查 Run / Work 剩余余量，不预收整次能力费用。实际模型请求仍走 Snapshot 后 Account/day + Work + Run 原子预留，工具仍走既有账本和容量服务。迁移 061 从同一 `run_usage_ledger` 按 producing Execution 限制 model_calls / tool_calls；重试原 Operation 去重，不增加第二份费用账本。

可信阶段先登记生产 Operation、预留实际用量，再调用 `commit_result` 原子提交 typed result、回执、引用/检查和可选的非模型实测用量结算；模型结算仍由 ModelBudgetCoordinator 同时处理 Account/day、Work 和 Run。旧 attempt 只保留原回执，不能借新 fence 发布。Workflow 返回不等于业务成功：关闭子 Execution 必须检查已提交结果；父恢复发现已有结果时只补关闭与观察。`uncertain` 与取消结果进入控制路径，不能当普通成功观察自动重发。

父观察取得新的 segment，按当前授权读取结果，在同一事务内登记父新 attempt、完成 caller Operation、追加配对 tool_result 和 CapabilityObservation。transcript CAS 冲突整笔回滚；响应丢失重读同一 Operation。取消等待 Child Workflow 确认后关闭子 Execution；父暂停、修订、停止或终结后，结果只能留在原生产者，不能推进新 revision。专业实现必须在取消清理完成后确认结束，不能丢弃未知外部副作用。

## Actions、Tools、MCP 与 Sandbox

模型工具分组和角色分配由 [agent_tools.yaml](../../config/agent_tools.yaml) 驱动，与 [角色提示词](../../config/prompts/agents.yaml) 配套维护。Main / Generic Work Worker 在注册本地、文件、MCP、Skill 工具时按角色过滤；根执行追加的联网、Research、HTML、文字发布、结果管理和委派工具也读取同一分配。未分配的工具不能通过手工构造调用绕过：普通工具不进入执行注册表，专用工具在执行或创建能力实例前重新检查。配置不会启用原本关闭的工具、补齐缺失服务或扩大资源授权。

只读子 agent 的 `grant=parent_read_only_subset` 是执行约束：父侧从当轮已登记且本角色允许的只读工具中选择 `branches[].tool_names`，服务器检查父侧清单并固定到子 Execution 的 `resource_scope`；子注册表在检索前再取父侧名单、子角色工具组和可信只读元数据的交集。工具执行仍检查分支范围与当前资源权限。共享 `web_search/web_fetch` 当前只支持根执行，不在可委派清单中；Research/HTML/规划/评估模型没有自主工具列表，不能通过修改配置赋予工具循环。

本轮模型输入附带服务器计算的实际工具名与 `delegatable_tools`，后者仅在提供 `delegate_work` 时展示。角色提示词中的工具指导不代表本轮可用；工具描述和参数 schema 仍来自可信注册定义，不从 YAML 接受任意实现或 Workflow 名称。配置操作与生效边界见[配置参考](../reference/configuration.md#agent-工具分配)。

Actions 通过统一调用契约暴露本地工具和已配置的 MCP Server。Tool Retrieval 选择数量受限的相关工具，默认上限为 12（关闭语义检索后仍生效）。Main 管理工具与文件 front door 属于保留入口；配置上限容不下保留入口时，工具选择必须抛出配置错误并阻止模型分发，不能静默降为无工具请求。Side-effect Metadata、稳定 Operation ID、Budget 和 Reconciliation 共同保护可重试执行。Shell 类操作可在 Sandbox 和账号级 Workspace Isolation 中运行。

## Memory

Chat bootstrap 在查询改写后由 Context Assembly 从 Hindsight 召回长期记忆，不是每轮模型调用都重新召回。成功 Chat Run 会异步保留，并附带 Account、Conversation、Run 和 Source 元数据。Hindsight 1.1.0 与官方 Python SDK 1.1.0 固定使用；Compose 镜像锁定 digest。私聊使用 `scope:private`，群聊使用 `scope:group` + 当前路由 `group:<context_key>`，Recall 固定非空 `all_strict`，Retain 同时设置对应 `observation_scopes`。未知范围拒绝。来源时间取触发消息的固定 `created_at`，metadata 去除 null 并统一字符串值。Observation 只在完整来源事实均属于当前范围时注入。定时全 Bank Reflect 已停用，归纳由原生 Consolidation 完成；Metrics Workflow 保留。旧 Reflect Workflow/Activity 的无 I/O 处理仅用于已有 Temporal history 收尾，不重新启用全 Bank 推理。组合入口由默认关闭的 `hindsight.recall_enabled` 控制，空目标与切换检查后显式开放；未登记来源及含此类来源的 Observation 不注入，不以新 tags 自动授权旧数据。本轮采用空目标启用，不提供旧数据迁移/自动清空；完整应用 SQL 迁移链继续保留。治理命令通过现有 Memory Worker 推进，接口和范围见[API](../reference/api.md#长期记忆治理)。

质量配置由 `config/hindsight.yaml` 与 `config/prompts/memory.yaml` 加载。Adapter 返回原生响应预算内的
候选，ContextAssembly 在本地来源授权后应用最终 top_n，并只绑定所选结果的新增文档依赖。
提取、Consolidation、Main 使用记忆分别使用 retain_mission、observations_mission、recall_instruction；
既有 Bank 的策略变化需要显式同步，不能靠下次 Retain 自动覆盖。详情和参数语义见
[质量配置](../reference/configuration.md#hindsight-110-质量参数)。

成功 Chat Run 的原 `retain_memory` 终态 Outbox 保持不变。消费者在同一事务中登记
`memory_sources`、冻结 `memory_actions`（请求 hash、固定来源时间及 UUID operation_id）、
创建仅引用 action_id 的 `memory_action` 事件并完成原事件。逻辑来源与 action_key 唯一；
同 key 的不同 payload 拒绝。来源、动作及 Run/Work/ResultRef/parent 引用使用同账户组合外键；`accounts.memory_epoch`
仅作为失效记录的递增索引；撤回范围保存在动作上，不能因账户序号变化直接拒绝全部执行。
Worker 可读写两表；API 可写治理意图及必要的来源阻断字段，只能读取来源和动作审计列，无冻结请求正文读取权。

准入位于 Retention Service：普通内容自动处理，明确不记住/凭据拒绝，高影响断言等待证据，
分别登记 blocked/quarantined 来源且不上传正文；未知路由范围不提交。只使用触发用户消息与
最终助手回复，并冻结角色说明，助手建议保持未确认归属。此规则是轻量确定性准入，
不宣称覆盖任意自然语言秘密识别或真实模型事实判真。

每次动作推进先查询原 Operation。不存在时仅使用已冻结的原 UUID/原请求异步提交；
发送意图在 HTTP 前提交，丢 ack 标 uncertain 并先对账；原生 failed Operation 在有限故障预算内
调用 retry，不替换身份。pending/processing 重排不计故障、不受 Outbox 投递次数限制。
429/暂时不可用递增 failure_count、退避上限 60 秒并等待恢复；永久 4xx 停止自动处理。
completed 后检查文档身份、Bank、tags 与 observation_scopes，验证动作及完成事件同事务提交。
Outbox 当前投递序号同时作租约 fence；旧 lease 回执和旧 generation 不得使新来源可读。
同来源 current_action_id 控制串行推进，manual_curation 禁止自动重写。

Recall 还须用本地来源表验证当前账户、scope、active、generation 及已 verified 动作；
未同步、阻断和未映射历史来源不注入。Observation 所有来源都须通过，否则只保留可验证 facts。
原生 accepted 只表示提交，Retain verified 不表示全部归纳完成。治理 API、删除协调与上下文失效
已实现；这不证明业务部署已切换。Recall 默认关闭，按[空目标启用步骤](../operations/deployment.md#记忆核心空库启用)单独开放。


单 fact 纠错、失效与恢复调用原生 PATCH；验证目标属于本账户、来源文档及可信 scope，
并检查旧 Observation 已移除。维护期间只暂停受影响 scope 的长期 Recall；人工维护后
保留 `manual_curation`，自动 Retain/replace 不覆盖它。只有被本地已验证失效的 fact 可恢复；
`forgotten` 来源不可恢复。内部来源映射修正从持久化 origin 推导新范围，通过 Document PATCH
迁移范围，并在提交前记录旧、新 scope 的并集；来源历史不足时记录 account 保护。

来源删除先同事务提交来源及已登记晋升副本的 tombstone、generation、失效范围及 Outbox。
Worker 对账所有已登记 Retain 及子 Operation 的终态，在来源级发送锁排除活跃发送者后删除
Document，核对原事实、归档事实和旧派生引用，再清理终态 Operation 载荷及本地冻结正文。
清理进度先保存无正文的 ID，崩溃重放不重新建立已经清理的 Operation。
发送超时、发送中崩溃或升级前旧发送者资格未证实时维持 uncertain 和去重记录；
Operation completed 或 Document 404 都不能独立证明旧请求不会再到达。
本期没有提供自动消除该不确定性的决议，也不承诺全局语义删除、聊天/文件/备份删除。切换前必须停止不带发送资格检查的旧写入进程并排空/对账，不能混跑旧 Worker；新迁移或新建动作本身不证明旧进程已经退出。

Execution 保存实际读取的 scopes、文档引用和已检查序号，子 Execution 继承该标记；
旧助手消息的依赖在历史复用前复验。Snapshot、dispatch、执行 fence 和最终成功提交分别检查
后续失效记录，只有范围相交、account 标记或所需历史缺失才报 `memory_context_revoked`。
已发送内容无法收回，但受影响的迟到响应不能继续工具、发布或 final/fallback。
普通新 Retain、restore、只读查询及正常轮询/重试不递增失效序号。
资源撤权沿现有资源策略复验，同事务将该主体已有 Run 的 `memory_retention_blocked` 单调置为 true，
并记录已知来源的受影响 scope、隔离相关 Conversation 来源。尚未消费的 Retain 事件随后只能登记
quarantined 来源，不能上传；登记和最终发送资格检查与撤权按账户锁排序，读取许可也检查 Run 标记。
已取得发送资格的请求属于在途，其结果不能使已撤权来源重新可用。重新授权只允许新 Run，
无资源依赖证明的旧来源不自动恢复。来源已为 forgotten 时，保留删除 tombstone、generation 和
current_action_id，继续原删除动作的远端对账和载荷清理；原删除失效记录继续生效。不新增依赖图，
不启用 Work 长期 Recall/Retain；完整管理 UI、旧数据迁移和全局语义删除均不属于当前交付。

## File 与 Workspace

File 能力负责上传、校验、不可变对象、Run 绑定和生成输出。账号级长期文件 Workspace 用稳定目录与 entry ID 组织对象；Conversation/Work 授权控制 Agent 发现与读取，Run 冻结候选并在选择时固定版本。发布输出与保存长期入口是两次独立操作。PostgreSQL 是目录、授权、修订、操作与保留引用的权威；TenantFileStore 保存字节，RunFileWorkspace 仅按需物化执行副本。Git Repository 是代码任务的独立能力，不参与普通文件保存与 GC。详见 [Workspace](workspace.md)。

## Research

标准组合的新调用使用 `research-web-v3`，将阶段阈值、检索选择/等待策略、合成 prompt/hash
及生成参数固定在现有 `descriptor.definition.execution_policy`；业务 policy 可与当前配置不同，
实现模板与版本须明确受支持。`research-web-v2` 的准确旧定义仍可恢复，不补填缺失策略，
阶段参数保留旧实例读取语义。公开 v1 输入/输出 schema 与工具名保持不变。
固定父入口与 Harness 恢复均复用原调用描述和子 Execution，不按当前候选重建 scope；
每次使用和模型请求仍复验当前权限与 fence。流程图、提取/历史/检查版本固定，仍最多三轮，
不将原诊断 max_iterations 变成新轮数开关。详见[持久策略配置](../reference/configuration.md#持久策略来源与生效规则)。


`research` v1 是固定局部能力：[定义](../../src/research_domain/capability.py)、[实现](../../src/research_activities/runtime.py)、[编排](../../src/orchestration/research_workflow.py)。规划仅从已登记 invocation 的不可变输入读取 objective 与 source_strategy，不回退读取父 Work 的目标；不获得长期写入或通知权限，不支持从 uploaded_files 隐式读取资料。每次调用以父 tool Operation 的 invocation_id 为研究实例，拥有独立 capability Execution；plan、阶段、iteration、source/evidence、claim/citation、报告和历史基线均按实例隔离。Run 只提供归属、现有预算和查询关联，同 Run 可顺序研究多次。迁移 062–064 保留旧行的 Run 查询键，新执行必须带已登记的 invocation 身份。

具有显式 main 区域和定义列表的网页保留主文档及 API 签名，过滤脚本和导航；其他网页继续使用通用正文提取，避免只留下定义说明却丢失被说明的名称。

每个来源最多保留六个不重叠、去重的精确文本区间，每个区间不超过 1,200 字符、单来源不超过 7,200 UTF-8 字节；研究实例累计最多 24 个区间、24,000 字节。确定性提取优先保留完整段落、定义项和句子，以具名/代码概念、定义章节及限定措辞排序，并为临近目标定义之前的共同限制保留有界前言。超长段落按句子切分；无法在字符或剩余字节上限内完整容纳的单句/代码块跳过，不截断为事实片段。段落尾空白不再吞并后续章节；剩余预算只容纳完整单元。定位保留原文 Unicode 字符偏移、source locator 和由来源、内容引用及区间派生的稳定证据身份，提取器版本为 `research-passages-v2`。具名/代码概念缺口会阻止充分判定；词语命中仅是覆盖启发式，不证明事实正确，也不是完整语义检索。局部输出为不可变 Markdown 文件和检查证据 JSON，各自建立 ResultRef 与 GC 引用。`research.basic` v1 检查标题/非空正文/结构化 claims、每个 claim 的引用身份及来源精确定位、必需输出；引用缺失或无效返回 `insufficient`，保留草稿和 gaps。弱来源仅警告；检查不证明所有事实正确，也不包含浏览器功能验证。Markdown 是独立可读取的结果，不要求存在 HTML。

真实 ResourcePool 合成器把已收到的响应正文、模型关联与用量作为现有 `research_stage_results` / Operation 回执提交，再做结构及引用校验；同 Operation 重入复用该回执，不再次请求模型。已开始而没有可读取响应的 Operation 保持 `research_synthesis_uncertain`，不自动换 ID 重发。JSON、字段形状、无 claims、空引用、未知证据分别记录稳定原因，整个无效报告转为带 gaps 的不足草稿，不筛掉坏 claim 后保留原正文作为成功报告；原始响应保留在受限存储中。每个 invocation 仍只有一次合成调用，父 Agent 可根据不足结果另行决策。合成提示要求每条 claim 聚焦同一对象的一条连贯规则，保留条件、结果、例外及属性/方法等对象类型，不以无关 API 罗列充当覆盖；交付 Markdown 仍逐字使用登记 claim 并附引用，不采用模型的自由叙述。该约束是生成指导，不是语义裁判。

顶层 `research_report` 由 [ResearchParentActivities](../../src/research_activities/parent.py) 薄适配：共用登记容量/预算准入和同一子 Workflow，之后执行父侧 Workspace 保存与验收。确定性阅读视图是可选父侧展示，失败不否定已提交研究。必需保存失败保留报告，父 Run/Work 不得成功；Research 子能力本身不完成 Run/Work、不长期保存、不发送通知。Research 的旧 evidence_types 验收增加精确 Markdown 与成功基础检查事实，通用 typed predicate 与跨 Run 采用使用下述父侧成果服务。已有用户验收命令仍以 Artifact Version 为目标；阅读视图存在时保留该兼容证据，Markdown 可通过准确 ResultRef 采用；产品中的选择与展示入口见[Web 契约](web-ui.md)。

历史仅从父固定候选与本次调用显式 node 范围中选择同 Work、先前成功 Run 的获准报告；冻结 invocation、file_id、requirement revision、snapshot/hash，之后不追随最新版本，使用前仍检查当前 read_content。报告正文、证据与准确 invocation 可通过现有 Research 查询读取；访问仍按账号过滤。

## HTML 与父侧成果采用

`html` v1 使用 [HtmlCapabilityWorkflow](../../src/agent_workflows/html.py) 与 [HtmlActivities](../../src/web_artifacts/local.py)。输入二选一：有界 `text` 或准确 `source_ref`；修改可带 `previous_ref` 和 instruction。ResultRef 必须由父侧当前可读，并显式列入子调用 scope；旧版本不追随最新。输出是新的 Artifact Version 和检查证据 JSON，生产 invocation / Execution / Operation 保持独立；同 Run 可多次调用。已有不可变 HTML 文件或完整结果可恢复，避免响应丢失后重复生成；局部失败返回 insufficient；形状不完整或输出过大属于可修正的 transient 反馈，不伪装为缺少用户输入，也不会在同一生产 Operation 中自动重发模型。父可自行决定是否用已有来源发起新的 HTML 调用，不重做成功的 Research。

HTML 模型输入以 JSON 区分事实来源 Markdown、展示要求和旧版 HTML；旧版仅供复用设计，来源事实优先。自由 HTML/CSS/JavaScript 设计不受固定模板约束。真实生成器额外检查来源 `[E:…]` 引用标记在 HTML 文本中是否仍存在，忽略脚本、样式、模板、head、注释及属性；缺失返回 `artifact_missing_citations`，沿原错误路径反馈，不在生成器内重发。标记存在不证明可见性、引用位置、链接、条件完整性或语义蕴含，也不能发现带同一标记的错误断言；无此格式的普通 Markdown 不增加该检查。

`html.basic` v1 只承诺完整 HTML、字节大小和受限预览策略；证据标注浏览器交互未检查。生成器和 publisher 由直接 Chat、Generic Work 和显式 Artifact 命令共用。HTML 使用原模型预算协调器，输出字节使用 Run/Work 现有预算；不另建调用账本。子能力只创建候选，不保存 Workspace、不完成父 Run/Work。

[ResultAdoptionService](../../src/run_domain/acceptance.py) 在当前 root fence 下执行采用、保存和证据求值。Harness 的 `result__adopt`、`result__save`、`result__evidence` 不依赖 native/MCP 工具模式。显式选定的旧 Artifact 输入在 Run admission 固定 `result_ref_id` 元数据，便于父模型发现准确旧结果；该元数据既不代替显式采用，也不授予文件读取权限。采用记录绑定当前 Run/Execution、Work revision/epoch，引用固定版本，原生产者不改写；账户所有权本身不授予 Agent 读取权，跨 Run 每次读取、模型输入和保存仍检查现有授权。Workspace 新建和 CAS 更新共用原保存内核，不复制正文或给兄弟能力扩大输入集合。保存回执返回准确 subject_ref、node_id、保存/版本 operation_id 和 workspace_saved_predicate；该谓词只是父侧后续求值输入，不自动创建验收证据，使用时仍重新核对当前权限、fence、criterion 和持久保存事实。

文字发布由 [TextActivities](../../src/agent_activities/text_result.py) 接收有界 title/body 和可选 format（默认 markdown，可选 docx），经原 OutputPublisher 发布不可变文件，按稳定 invocation/operation 重放，原子提交 CapabilityResult / ResultRef 与字节预算结算。不调用模型、不要求 HTML 或 Workspace 长期保存。格式能力保留；原生 DOCX 工具也未禁用，任意工具生成文件并不等于满足交付格式。

有结构化交付目标时，发布后读取已持久化的准确字节，检查 SHA256、MIME、文件名与 UTF-8 Markdown 或 OOXML DOCX 结构，发布不可变 check_evidence，登记 `file.format.markdown` / `file.format.docx` v1 Check，subject_ref 固定该成果。Markdown 没有唯一语法签名，因此格式检查只承诺可读 UTF-8 文本及正确元数据，不证明内容质量。格式失败不抹去文件存在事实；父侧仍可登记 output_exists，不能登记通过格式证据。结果、检查、观察和证据分别沿原幂等及恢复路径提交；父侧观察与 evidence 原子提交。旧的未限定格式 Markdown 保留原发布和自动存在性登记，不增加额外格式门槛。仅允许已审查的 text/v1 增量格式升级恢复旧冻结描述，其余实现、限制与资源定义变化仍拒绝。

轻量后台查询由 Agent 自主使用共享 web_search/web_fetch，观察进入后续模型决策；只有明确的专业研究需求才选择 Research。

Generic Work 的 evidence 只接受 `output_exists`、`check_passed`、`workspace_saved`，ref 指向服务器登记的 capability_acceptance_evidence。谓词沿用 AcceptancePredicate v1：检查必须匹配同一 subject_ref 与准确 verifier/version；保存必须匹配同一不可变文件、目标和操作，必需保存另核对 deliverable_policy。成功工具回执不能代替这些条件。用户验收、渠道接受仍走既有独立可信回执，不由模型生成；固定 Research/Artifact 父适配器保留原专用验收接口。

显式 Artifact API 创建有限 Chat Run，使用正常同 Conversation 单 active 槽和独立预算，不再自动新建 artifact_build Work。已有显式 artifact_build Work 仍调用同一 HTML 能力；需要持续委托时由 Work 承担。版本保留准确 parent、生产者和不可变 file_id；取消/失败关闭未完成版本，重试产生新版本。迁移 065–068 增加 typed evidence、通用必需保存与最小权限的 queued HTML 取消函数，不修改旧迁移。

## Heavy Document

Heavy Document Normalization 是 File 能力中高开销的执行分支。独立 Temporal Activity Worker 负责转换与规范化文档、记录幂等 Operation、结算 Budget 并发布规范化结果；它不是第二套文件系统。

## Provider 请求与通知

Provider serializer v2 显式规范化 OpenAI / Anthropic transcript，并在快照和预留前校验工具调用结果配对。安全失败 code 贯通 Activity / Lifecycle / API，日志保留调用关联和受限 Provider 错误字段；Artifact 使用独立读取预算。详见[执行与恢复](execution.md)。

统一 Notification / Delivery 面向 Web 私有 inbox 与经过绑定核验的 QQ 目标。渠道接受不是用户已读，未知发送结果不自动重发；Work 完成只采用要求明确指定的证据。

HTML 的 `source_ref` 只传递准确来源正文，不会把父 Work brief 或聊天要求隐式带入子能力。父 Agent 需在 `instruction` 中显式转交用户的布局、交互、可访问名称和版本修改约束；引用、基础形状检查和保存事实都不证明这些行为已实现，仍需独立浏览器验收。

父侧 `result__save` 区分新建目录入口与 CAS 更新：新建使用 directory_id/name；更新使用 entry_id/expected_revision/expected_sha256。新建保存的 `workspace_saved` 判据必须省略 target_revision，版本更新才可携带准确 revision；不自动删除用户提交的版本条件。错误会指出应复用保存回执中的准确判据。HTML 硬化先移除唯一、末尾完整的 HTML 代码围栏（可有说明前缀），再要求完整文档；有歧义的多块或原始文档外说明不能通过。

父 Agent 的 HTML 工具 schema 要求非空 `instruction`，缺失时在创建 invocation 和付费子请求之前返回输入不足；这是模型适配边界，不改变固定调用者 v1 的可选 instruction 契约。已创建 invocation 的恢复沿原输入继续，不能因新模型工具要求而破坏重放。

生产 ResourcePool Research 合成的交付 Markdown 由已登记且引用已知 EvidenceItem 的 claims 渲染，保留准确来源链接；供应商自由正文只保存在原响应回执，不进入交付。claims 还需覆盖请求中显式命名的概念，缺失返回稳定的 missing_coverage，不从未登记正文补足。引用 ID 和名称覆盖只证明结构一致，事实及条件是否被正确理解仍须独立质量复核。HTML 转换同样应保留来源的条件、例外和适用范围，禁止添加来源之外的 API 行为。

父结果工具同时校验模型面对的参数 schema：`workspace_saved` 必须有 target_id/operation_id；`check_passed` 必须有 verifier_key/verifier_version；`output_exists` 不接收保存和 verifier 字段。可省略的空字符串或 null 在校验前规范化，确实缺少必填字段则在执行服务和副作用前返回字段形状错误，不推断目标或替模型生成证据。

面向模型的 HTML／父结果工具使用完整、平坦的属性表；不适用字段使用显式 null，避免模型工具 schema 的组合条件合并差异。此 wire schema 不替代内部契约：入口规范化可省略空值后，仍按互斥保存形式、判据必填字段及精确持久化事实校验；任何实际字段缺失或矛盾都必须拒绝。

受单次合成 4,096 输出 tokens 约束，生产合成允许最多 16 条完整、已引用 claims，覆盖多主题比较的事实清单；超过上限返回 insufficient，不截断或丢弃事实后宣称成功。该条数边界不放宽引用、覆盖和独立质量要求。

当前限制：`research.basic` 与 `html.basic` 不能证明事实蕴含、完整性或转换忠实性。即使来源 claims 已登记、HTML 已通过结构检查并保存，模型仍可能在 HTML 中新增错误限定或遗漏原定事实；独立事实复核失败必须阻止验收退出，不能以 typed evidence、Work completed 或交互通过替代。现有实现没有通用的 HTML 事实保持验证器。

## Web Retriever v1 设计总结

Web Retriever v1 已冻结，现有联网搜索与正文获取能力作为共享功能模块保留。模块边界为取得有来源信息的候选和正文；Research 的 Evidence 选择、报告合成、事实覆盖与完成判定属于调用方流程，不作为重新打开本模块开发的条件。

### 组合与调用

| 入口 | 职责 |
| --- | --- |
| `src/web_retrieval/` | 进程内 Search/Fetch 契约、Provider 选择、来源范围、安全抓取与预算适配。 |
| `src/research_adapters/` | legacy / DeepSeek Search、正文及 Markdown 转换 Adapter。 |
| `src/agent_activities/web_tools.py` | Main 根 Execution 的可信 web_search / web_fetch 工具。 |
| `src/bootstrap/durable_runtime.py` | 将同一个 WebRetrievalService 实例注入 Main 和 Research。 |

Main 沿原 ToolExecutionWorkflow 与 fenced Activity 执行，不依赖 Tavily/MCP，也不因轻量检索创建 Research、Work 或额外 Run。Research 通过原 discovery/content 接口调用共享服务，继续使用原有 Evidence、报告与持久化流程。网络 I/O 留在 Activity/Adapter，不进入 Workflow；不新增独立 HTTP 服务或预算权威。

### Provider 与格式

搜索支持 legacy（SearXNG/GitHub/RSS）和 DeepSeek；GitHub/RSS 遵循来源策略开关。Fetch 支持 local 与 DeepSeek。请求的 auto 读取配置默认值，显式选择失败不自动换供应商。新环境推荐 DeepSeek Search + Local Fetch；代码默认仍为 legacy/local，配置见[开发环境](../development/setup.md#web-retriever-配置)。

本地默认 Text，进程内可显式使用 `FetchRequest(format="markdown")` 选择 Markdown v3：按唯一 main、role=main、article 正文容器提取，以 BeautifulSoup + markdownify 保留标题、代码、链接和表格；相对链接按已验证最终 URL 解析，移除非 HTTP(S) 链接目标。正文容器缺失/歧义、空 Markdown 或转换异常时，复用已下载 HTML 降级为 Text，返回实际格式与有限的 fallback_reason。权限、下载及输出上限失败仍拒绝。SourceContent.text 是准确正文和 Unicode offset 基准，hash 定义不变，旧正文与 Evidence 不回填。

DeepSeek Fetch 保留准确 Markdown，final_url 未知；要求最终来源可验证的 Research 或带硬来源范围的 Fetch 在发送前拒绝该路径。静态抓取不提供浏览器或代理回退，动态页面与无法直连的来源属于现有能力限制。

### 来源与输出边界

`allowed_domains` / `excluded_domains` 是硬范围，匹配主机及子域，排除优先；`preferred_domains` 仅影响排序。freshness_days 为 0 时不限日期，正数要求可确认的来源日期；未知、超期或未来日期不满足要求。共享服务过滤越界、重复 URL 和明确导航索引，Fetch 对每次重定向重新检查范围与全部 DNS 地址，连接已验证公网 IP，保留 Host、TLS SNI 和证书校验。本地下载总超时有界、上限 2 MB，禁用环境代理与自动重试。

Main 工具仅允许根 Execution，身份由可信执行上下文注入，模型参数不能填写 account/run/execution/fence。Quick / Standard 只控制结果大小：搜索最多 3 / 8 条，正文最多 2,000 / 8,000 字符，下载最多 400 KB / 1 MB，JSON 最多 16 KB / 48 KB。输出包括来源 URL、可空最终 URL 及验证状态、全文 hash、接收时间、实际格式、截断和不可信来源标记。网页内容是资料，不是工具或权限指令。

### 预算与恢复

每次检索发送前通过原 RunBudgetService 占用一个 tool_calls，并按既有规则累计 Work 预算；不把候选数量或来源处理数当作请求数，不将检索伪装成 Account 模型 tokens。检索强制检查冻结上限，observe/off 也不能绕过；外层工具不重复扣占。新 Research 快照包含 tool_calls=40，research-web-v2 invocation 上限为 23；旧快照和 invocation 不自动加额。

稳定操作 ID 随原 Tool Operation 或 invocation/iteration/source 生成，不随 Activity attempt 更换。已持久化结果按原路径重放；已有占额但缺结果或发送未知时返回 usage_unknown / 非重试 retrieval_usage_unknown，不自动重发。失败或未知请求不释放已占名额，401/402/403 停止当前 Provider 实例后续请求。

## Research 查询与迭代

Research 将长目标转换为供应商长度范围内的查询（legacy 240、DeepSeek 128 Unicode 字符）；直接传给共享服务的超长 DeepSeek 查询拒绝发送。完整目标、查询规划、证据选择、缺口判断和报告正确性由 Research 自身负责。搜索成功、正文获取、Evidence 有效和最终报告正确分别判断。

最终检索查询保存在现有 research_iterations 和阶段结果中，完整目标仍用于缺口和合成。长查询保留复合标识符（如 no-cache），后续优先使用已有概念缺口并保留站点范围；同 Run、相同有效查询与策略不重复搜索。搜索阶段保留实际查询、日期过滤、候选数、上游异常与时效排除数量，Fetch 阶段保留获取状态计数。正常空结果、部分上游不可用和全部请求失败分别反馈；部分上游失败不丢弃健康上游的候选。候选可抓取不等于主题相关或证据充分。

每轮 SourceDiscovery 之前使用 invocation/iteration 稳定操作标识原子预留 research_iterations；提交轮次开始时结算一次，后续网络失败仍计入该已开始轮次，未提交开始的预留沿现有释放机制处理。GapAnalysis 不再扣占轮次。Activity 重试重放原扣占，最多三轮不变。父侧可从 Work brief 的 limits/used/reserved 及 Research 结果 summary 读取已用、上限、剩余、停止原因、尝试查询、主要缺口和搜索/抓取摘要；预算为零或查询策略重复时父准入直接返回明确错误，不创建子执行或搜索。实际轮次仍由原预算服务预留决定，不能通过新建 Run 绕过限制。
