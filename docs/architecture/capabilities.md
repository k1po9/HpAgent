# 能力边界

## Account 与模型访问治理

Account 是模型访问的主体。`account_entitlements` 保存账号自己的 `model_access_tier`、`daily_token_limit`、`prompt_visibility`、有效期和版本；账号停用、entitlement 缺失或过期时，模型访问不可用。Web 自助注册可不填邀请码，获得代码定义的默认 entitlement；也可使用管理员预先创建的邀请凭证，将其 `entitlement_profile` 复制到新账号。邀请码只参与注册时的 provision，后续模型访问读取账号 entitlement，而不是反复读取邀请码。注册和额度细节见[账号治理运维说明](../operations/account-governance.md)。

模型调用经受治理的 Provider 路径执行：先检查账号 entitlement 与端点 tier，准备 Provider 请求并冻结 Model Input Snapshot，再由 `ModelBudgetCoordinator` 在同一个 PostgreSQL 事务中原子预留 Account UTC 日额度与 Run budget；随后才向 Provider 发送请求，并结算或释放预留。Snapshot 冻结不属于预算预留事务。`daily_token_limit` 为 `NULL` 时不设 Account 每日上限，Run budget 仍独立生效。Model Input 查询按账号的 `prompt_visibility` 投影为最小元数据、摘要或已保存的 Provider 请求体；凭据与 Secret 不应写入模型输入。

## Agent

Agent 能力由 Context Assembly、Brain/Model 决策循环和持久化策略 Workflow 组成。ReAct 与 Plan-and-Execute 共享同一套 Lifecycle、Actions、Memory、Workspace 和结果提交路径。

## Actions、Tools、MCP 与 Sandbox

Actions 通过统一调用契约暴露本地工具和已配置的 MCP Server。Tool Retrieval 选择数量受限的相关工具。Side-effect Metadata、稳定 Operation ID、Budget 和 Reconciliation 共同保护可重试执行。Shell 类操作可在 Sandbox 和账号级 Workspace Isolation 中运行。

## Memory

Context Assembly 在模型执行前从 Hindsight 召回长期记忆。完成的 Run 会异步保留，并附带 Account、Conversation、Run 和 Source 元数据。定时 Reflection 与 Metrics Workflow 复用相同 Memory 边界。

## File 与 Workspace

File 能力负责上传、校验、内容访问、Run Binding、Persistent Destination、Lineage、Approval 和生成输出。Workspace 提供账号级 Git Repository 与 Run 级执行目录。元数据保存在 PostgreSQL，Blob 与 Worktree 保存在专用存储。

## Research

Research 是固定 Workflow：规划查询、通过 SearXNG 发现来源、获取和提取内容、生成 Evidence、综合 Claim 并发布 Report。它不是 Agent Strategy，也不替代 ReAct 或 Plan-and-Execute。

## Artifact

Artifact 是与来源 Message 或 Research 关联的版本化应用输出。Artifact Build 通过 Durable Dispatch 执行，并由 Activity 更新 PostgreSQL 状态。

## Heavy Document

Heavy Document Normalization 是 File 能力中高开销的执行分支。独立 Temporal Activity Worker 负责转换与规范化文档、记录幂等 Operation、结算 Budget 并发布规范化结果；它不是第二套文件系统。
