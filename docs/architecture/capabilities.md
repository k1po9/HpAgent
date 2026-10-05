# 能力边界

## Account 与模型访问治理

Account 是模型访问的主体。`account_entitlements` 保存账号自己的 `model_access_tier`、`daily_token_limit`、`prompt_visibility`、有效期和版本；账号停用、entitlement 缺失或过期时，模型访问不可用。Web 自助注册可不填邀请码，获得代码定义的默认 entitlement；也可使用管理员预先创建的邀请凭证，将其 `entitlement_profile` 复制到新账号。邀请码只参与注册时的 provision，后续模型访问读取账号 entitlement，而不是反复读取邀请码。注册和额度细节见[账号治理运维说明](../operations/account-governance.md)。

模型调用经受治理的 Provider 路径执行：先检查账号 entitlement 与端点 tier，准备 Provider 请求并冻结 Model Input Snapshot，再由 `ModelBudgetCoordinator` 在同一个 PostgreSQL 事务中原子预留 Account UTC 日额度、累计 Work budget（若有）与 Run budget；随后才向 Provider 发送请求，并结算或释放预留。Snapshot 冻结不属于预算预留事务。`daily_token_limit` 为 `NULL` 时不设 Account 每日上限，Run budget 仍独立生效。Model Input 查询按账号的 `prompt_visibility` 投影为最小元数据、摘要或已保存的 Provider 请求体；凭据与 Secret 不应写入模型输入。

## Agent

Agent 能力由 Context Assembly、Brain/Model 决策循环和持久化策略 Workflow 组成。ReAct 与 Plan-and-Execute 共享同一套 Lifecycle、Actions、Memory、Workspace 和结果提交路径。

## Work 与执行策略

Work 接受持续委托，固定不可变 requirement revision、checkpoint 与 continuation。提醒、研究、通用工作和成果构建通过服务端 StrategyRegistry 选择注册 executor；不支持的能力直接拒绝。Main 使用 Work 管理工具，Work Agent 不获得这些工具；工作上下文不加载关联聊天全文。Generic Work root 可一次委派最多三个只读分支，共用 Run / Work 预算。完成由持久证据与 WorkCompletionPolicy 判断。详见 [Durable Work V1](durable-work-v1.md)。

## Actions、Tools、MCP 与 Sandbox

Actions 通过统一调用契约暴露本地工具和已配置的 MCP Server。Tool Retrieval 选择数量受限的相关工具。Side-effect Metadata、稳定 Operation ID、Budget 和 Reconciliation 共同保护可重试执行。Shell 类操作可在 Sandbox 和账号级 Workspace Isolation 中运行。

## Memory

Context Assembly 在模型执行前从 Hindsight 召回长期记忆。完成的 Run 会异步保留，并附带 Account、Conversation、Run 和 Source 元数据。定时 Reflection 与 Metrics Workflow 复用相同 Memory 边界。

## File 与 Workspace

File 能力负责上传、校验、不可变对象、Run 绑定和生成输出。账号级长期文件 Workspace 用稳定目录与 entry ID 组织对象；Conversation/Work 授权控制 Agent 发现与读取，Run 冻结候选并在选择时固定版本。发布输出与保存长期入口是两次独立操作。PostgreSQL 是目录、授权、修订、操作与保留引用的权威；TenantFileStore 保存字节，RunFileWorkspace 仅按需物化临时副本。Git Repository 是代码任务的独立能力，不参与普通文件保存与 GC。详见 [Workspace v4.1](workspace-v4.1.md)。

## Research

Research 是固定 Workflow：规划查询、通过 SearXNG 发现来源、获取和提取内容、生成 Evidence、综合 Claim 并发布 Report。其持续委托、资源和调度由 Work 拥有；固定研究图在通用 Run Lifecycle 内执行。它不替代 ReAct 或 Plan-and-Execute。

## Artifact

Artifact 是与来源 Message 或 Research 关联的版本化应用输出。异步 HTML 构建创建 artifact_build Work，由其有限 Run 承担模型成本。版本保存 producing Run / Execution / operation，生成、采用、Workspace 保存与用户验收分别记账。

## Heavy Document

Heavy Document Normalization 是 File 能力中高开销的执行分支。独立 Temporal Activity Worker 负责转换与规范化文档、记录幂等 Operation、结算 Budget 并发布规范化结果；它不是第二套文件系统。

## Provider 请求与通知

Provider serializer v2 显式规范化 OpenAI / Anthropic transcript，并在快照和预留前校验工具调用结果配对。安全失败 code 贯通 Activity / Lifecycle / API，日志保留调用关联和受限 Provider 错误字段；Artifact 使用独立读取预算。详见[可靠性](reliability.md)。

统一 Notification / Delivery 面向 Web 私有 inbox 与经过绑定核验的 QQ 目标。渠道接受不是用户已读，未知发送结果不自动重发；Work 完成只采用要求明确指定的证据。
