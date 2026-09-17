# 能力边界

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
