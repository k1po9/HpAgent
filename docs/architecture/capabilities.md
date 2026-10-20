# 能力边界

## Account 与模型访问治理

Account 是模型访问的主体。`account_entitlements` 保存账号自己的 `model_access_tier`、`daily_token_limit`、`prompt_visibility`、有效期和版本；账号停用、entitlement 缺失或过期时，模型访问不可用。Web 自助注册可不填邀请码，获得代码定义的默认 entitlement；也可使用管理员预先创建的邀请凭证，将其 `entitlement_profile` 复制到新账号。邀请码只参与注册时的 provision，后续模型访问读取账号 entitlement，而不是反复读取邀请码。注册和额度细节见[账号治理运维说明](../operations/account-governance.md)。

模型调用经受治理的 Provider 路径执行：先检查账号 entitlement 与端点 tier，准备 Provider 请求并冻结 Model Input Snapshot，再由 `ModelBudgetCoordinator` 在同一个 PostgreSQL 事务中原子预留 Account UTC 日额度、累计 Work budget（若有）与 Run budget；随后才向 Provider 发送请求，并结算或释放预留。Snapshot 冻结不属于预算预留事务。`daily_token_limit` 为 `NULL` 时不设 Account 每日上限，Run budget 仍独立生效。Model Input 查询按账号的 `prompt_visibility` 投影为最小元数据、摘要或已保存的 Provider 请求体；凭据与 Secret 不应写入模型输入。

## Agent

Agent 能力由 Context Assembly、Brain/Model 决策循环和持久化策略 Workflow 组成。ReAct 与 Plan-and-Execute 共享同一套 Lifecycle、Actions、Memory、Workspace 和结果提交路径。

## Work 与执行策略

Work 接受持续委托，固定不可变 requirement revision、checkpoint 与 continuation。提醒、研究、通用工作和成果构建通过服务端 StrategyRegistry 选择注册 executor；不支持的能力直接拒绝。Main 使用 Work 管理工具，Work Agent 不获得这些工具；工作上下文不加载关联聊天全文。Generic Work root 可一次委派最多三个只读分支，共用 Run / Work 预算。完成由持久证据与 WorkCompletionPolicy 判断。详见 [Work](work.md)。

## 可组合能力基础契约

[capability_contracts.py](../../src/run_domain/capability_contracts.py) 定义显式版本化的 CapabilityDefinition、CapabilityResult、ResultRef、Check、CapabilityObservation 与 AcceptancePredicate。静态 CapabilityRegistry 与顶层 StrategyRegistry 分离，注册时冻结定义副本；发现只按 surface / 依赖筛选，不是授权。定义使用封闭、自包含 JSON Schema，调用登记和结果提交分别校验输入、输出；不允许远程 schema 引用。

[capabilities.py](../../src/run_domain/capabilities.py) 的 CapabilityFacts 是服务端事务接口，要求调用方提供 UoW 与有效 Execution fence。`create` 以已存在的 root tool Operation 为 invocation ID，固定 definition（含实现版本）、规范化 inputs、resource_scope、limits 和 requirement revision/control epoch，原子创建同 account / Run 的 capability Execution。同身份同描述去重，不同描述冲突；不同 Operation 可以多次调用同一能力。资源上限包含 node_ids、file_ids、tool_names、result_ids，空集合须显式传入。定义与调用限制不替代真实调用的预算预留或容量票据。

`publish_result` 从当前 fence 填充并核对结果 provenance（invocation_id / execution_id / operation_id），即使没有输出也保留准确生产者；原子完成子生产 Operation、回执、结果、引用和检查记录，并设置子 Execution 的 result_ref；不完成 caller Operation、Execution、Run 或 Work。`read_result` 仅供同 Run 的 caller / producer 查询；`read_ref` 返回 ResultRef 值对象，解析准确 file_id 或 Artifact Version，并检查当前执行身份、显式输入或采用关系及当前资源权限。结果元数据接口不直接返回文件字节。`adopt` 由 root 将获准旧结果固定到当前 Execution、revision 和 control epoch，读取仍重验；采用不改写生产者，也不代表 Workspace 保存。

检查记录固定 verifier key/version、subject_ref、verdict（passed / failed / not_checked）和 evidence_ref；证据必须引用同一结果的 check_evidence 输出。检查由可信实现提供，基础服务只验证登记与关联，不执行专业检查。AcceptancePredicate 固定 output_exists / check_passed / workspace_saved / user_accepted / delivery_accepted 的字段；`check_passed` 固定 verifier key/version；`workspace_saved` 的 target_id 为 Workspace node_id，operation_id 固定保存/版本操作，target_revision 可固定 destination revision；user/delivery 的 target_id 指向各自可信回执。现有 Work evidence_types 验收保持原行为，尚未接入这些谓词求值。

`CapabilityObservation` 固定 schema_version / invocation_id / producing_operation_id / producer_result_ref，放在父 Operation result_payload 和 tool_result 事件 payload 的 `capability_observation` 字段。子生产引用为 `capability-result:{invocation_id}`，父 Operation 使用自己的唯一引用（例如 `capability-observation:{invocation_id}`）；不能复用生产引用。`record_observation` 要求父事件及 caller Operation 已在同一 UoW 中写入，且两处指针与精确 producer 一致。Harness 的持久调用边界见下节；当前没有注册 Research / HTML 等生产复杂能力。Research / HTML 仍使用下面描述的现有路径，不能把基础契约视为功能已开放。

## 通用持久调用与 Harness

[CapabilityInvocationWorkflow](../../src/agent_workflows/capability.py) 接入共享 ToolExecutionWorkflow。服务端 [CapabilityBinding](../../src/agent_activities/capabilities.py) 将准确契约版本绑定到固定 Workflow 和输入资源范围解析器；`capability__` 前缀保留给复杂能力。模型只填写有类型的业务参数，不提供 Execution、fence、资源上限或 Workflow 名称。生产组合目前注册空能力集合；确定性能力仅位于测试支持代码，不开放给用户。`delegate_work` 仍走独立的只读分支边界。

发现按 surface 与服务依赖筛选，仅 root 获得复杂能力工具。执行时再次检查当前账号、Run、Work revision/control epoch、父 Execution、输入 schema、冻结定义和显式资源范围。输入读取适配器使用 `invocation_context` 检查当前子 fence、定义实现版本和资源权限；专业实现每次使用资源和发出模型请求仍须重验，不可把登记视为永久授权。

父登记 segment 结束后，以子 Execution UUID 固定 Child Workflow 身份；等待不持父 lease、Workspace 锁或物理容量票据。专业 Workflow 自行将短 Activity 划分为独立 segment，重试次数与总时限来自冻结定义；持久审批/输入等待复用 DurableWait 与权威 probe，Signal 不代表批准。能力只允许 read_only / candidate_output，不因此获得长期修改、外发或审批权限；已有 Atomic Tool 的文件审批路径保持独立。

准入串行核对父侧单个活动复杂调用，以及账号/全局子 Execution 数量（`CAPABILITY_ACCOUNT_EXECUTIONS` 默认 4，`CAPABILITY_GLOBAL_EXECUTIONS` 默认 32）；容量不足产生持久失败观察。预算准入只检查 Run / Work 剩余余量，不预收整次能力费用。实际模型请求仍走 Snapshot 后 Account/day + Work + Run 原子预留，工具仍走既有账本和容量服务。迁移 061 从同一 `run_usage_ledger` 按 producing Execution 限制 model_calls / tool_calls；重试原 Operation 去重，不增加第二份费用账本。

可信阶段先登记生产 Operation、预留实际用量，再调用 `commit_result` 原子提交 typed result、回执、引用/检查和可选的非模型实测用量结算；模型结算仍由 ModelBudgetCoordinator 同时处理 Account/day、Work 和 Run。旧 attempt 只保留原回执，不能借新 fence 发布。Workflow 返回不等于业务成功：关闭子 Execution 必须检查已提交结果；父恢复发现已有结果时只补关闭与观察。`uncertain` 与取消结果进入控制路径，不能当普通成功观察自动重发。

父观察取得新的 segment，按当前授权读取结果，在同一事务内登记父新 attempt、完成 caller Operation、追加配对 tool_result 和 CapabilityObservation。transcript CAS 冲突整笔回滚；响应丢失重读同一 Operation。取消等待 Child Workflow 确认后关闭子 Execution；父暂停、修订、停止或终结后，结果只能留在原生产者，不能推进新 revision。专业实现必须在取消清理完成后确认结束，不能丢弃未知外部副作用。

## Actions、Tools、MCP 与 Sandbox

Actions 通过统一调用契约暴露本地工具和已配置的 MCP Server。Tool Retrieval 选择数量受限的相关工具。Side-effect Metadata、稳定 Operation ID、Budget 和 Reconciliation 共同保护可重试执行。Shell 类操作可在 Sandbox 和账号级 Workspace Isolation 中运行。

## Memory

Context Assembly 在模型执行前从 Hindsight 召回长期记忆。完成的 Run 会异步保留，并附带 Account、Conversation、Run 和 Source 元数据。定时 Reflection 与 Metrics Workflow 复用相同 Memory 边界。

## File 与 Workspace

File 能力负责上传、校验、不可变对象、Run 绑定和生成输出。账号级长期文件 Workspace 用稳定目录与 entry ID 组织对象；Conversation/Work 授权控制 Agent 发现与读取，Run 冻结候选并在选择时固定版本。发布输出与保存长期入口是两次独立操作。PostgreSQL 是目录、授权、修订、操作与保留引用的权威；TenantFileStore 保存字节，RunFileWorkspace 仅按需物化执行副本。Git Repository 是代码任务的独立能力，不参与普通文件保存与 GC。详见 [Workspace](workspace.md)。

## Research

Research 是固定 Workflow：规划查询、通过 SearXNG 发现来源、获取和提取内容、生成 Evidence、综合 Claim 并发布 Report。其持续委托、资源和调度由 Work 拥有；固定研究图在通用 Run Lifecycle 内执行。它不替代 ReAct 或 Plan-and-Execute。

## Artifact

Artifact 是与来源 Message 或 Research 关联的版本化应用输出。异步 HTML 构建创建 artifact_build Work，由其有限 Run 承担模型成本。版本保存 producing Run / Execution / operation，生成、采用、Workspace 保存与用户验收分别记账。

## Heavy Document

Heavy Document Normalization 是 File 能力中高开销的执行分支。独立 Temporal Activity Worker 负责转换与规范化文档、记录幂等 Operation、结算 Budget 并发布规范化结果；它不是第二套文件系统。

## Provider 请求与通知

Provider serializer v2 显式规范化 OpenAI / Anthropic transcript，并在快照和预留前校验工具调用结果配对。安全失败 code 贯通 Activity / Lifecycle / API，日志保留调用关联和受限 Provider 错误字段；Artifact 使用独立读取预算。详见[执行与恢复](execution.md)。

统一 Notification / Delivery 面向 Web 私有 inbox 与经过绑定核验的 QQ 目标。渠道接受不是用户已读，未知发送结果不自动重发；Work 完成只采用要求明确指定的证据。
