# 数据与状态归属

## PostgreSQL

应用 PostgreSQL 是 Account、Identity Binding、Conversation、Message、Run、Outbox、Workflow Execution Fact、Transcript、Operation、Lease、Fencing Token、Work、Execution、Notification、Delivery、Trace、File、Research 和 Artifact 状态的权威来源。必须保持一致的状态转换在同一个事务中提交。

主要归属：

- **身份与对话**：Account、Identity Binding、Account Entitlement、Registration Invite、Conversation、Message。
- **持续委托**：Work、不可变 Requirement、Checkpoint / Continuation、Control Epoch、Conversation 关联、Input Ref、Schedule / Occurrence / Wakeup、累计预算与用量。
- **执行**：Run、Workflow Execution、Outbox、Execution Transcript、Operation / Producing Attempt / Result Receipt、Wait、Execution Segment、Lease 和 Fencing Token。
- **投递与追踪**：Notification、Delivery Target、Delivery / 决议、Work Event、Trace Run 和 Trace Event。
- **长期文件与 Workspace**：Stored File、目录/条目、修订、Conversation/Work 授权、Run 候选/访问、发布/保存 Operation、Message/Run Binding，以及文件 Budget 和 Usage Ledger。
- **Research 与 Artifact**：Work 需求对应的 Plan、Source、Evidence、Report、Artifact、Artifact Version 与 revision-specific adoption。
- **Heavy Document**：Normalized Document Operation 的结果与状态。

`registration_invites.entitlement_profile` 是注册凭证的配置；注册事务将其复制到 `account_entitlements`，并以 `provisioned_by_invite_id` 保留来源。普通自助注册直接写入默认 entitlement。账号后续权限以 `account_entitlements` 为准，修改邀请 profile 不会追改已注册账号。`account_daily_model_budgets` 和 `account_model_usage_ledger` 按 UTC 日期保存账号模型 token 的预留与结算；`run_budgets` 是另一个 Run 级边界。Work Run 还受 `work_budgets / work_usage_ledger` 累计边界控制，三层在一次预留事务中协调；revision、Run 重试或 Subagent 不重置 Work 账本。Model Input Snapshot 在预算预留前单独冻结，查询时才按账号 entitlement 投影可见字段。

## Redis

Redis 负责短期 Event Context、通知和工具/运行时缓存等临时协调状态。Redis 丢失可能影响进行中的便利状态，但不会取代 PostgreSQL 已提交的业务事实。

## Hindsight

Hindsight 负责长期语义 Memory Bank 和检索索引。HpAgent 在应用 PostgreSQL 中保留稳定的 Account 与 Run 关联元数据，但不会复制语义索引。

## 长期文件、Git 与字节存储

账号级长期文件 Workspace 的目录、入口、来源、版本、授权、Run 固定结果和保存操作都以 PostgreSQL 为权威。TenantFileStore 保存不可变字节；同一 `file_id` 的多个入口不复制字节。Run 的 inputs/scratch/outputs 是临时执行材料，结束后回收。普通文件能力不依赖 Git。

账号级 Git 工作区只服务代码任务。Redis 与 Hindsight 分别提供临时协调和长期语义记忆，不决定文件所有权或物理回收。详见 [Workspace v4.1](workspace-v4.1.md)。

## 完成与状态投影

`runs.status=succeeded` 只说明执行成功；Work completed 需要当前 revision 的验收证据。Work 状态、通知渠道 accepted 与显式用户 acceptance 分别持久化。Message / Artifact Version 的 completed 不替代 Run 终态。聊天和 Work 共用 Run 查询，但 Work variant 没有 assistant Message。

`capacity_queue / capacity_turns` 是跨进程容量与公平轮转的权威。`work_events` 有独立序列和恢复查询；SSE 与 Redis 仅通知或投影，不决定业务完成。统一 `notifications / deliveries` 取代旧 `qq_deliveries`，Work 取代 Research Task 委托实体。详见 [Durable Work V1](durable-work-v1.md)。

旧业务 `sessions` 表及 Session branch recovery 已由 055 移除；`runs.session_id` 只保留空值字段约束，不再是执行身份。浏览器认证 session / cookie 是另一项认证机制，仍然存在。
