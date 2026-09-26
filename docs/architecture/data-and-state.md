# 数据与状态归属

## PostgreSQL

应用 PostgreSQL 是 Account、Identity Binding、Conversation、Message、Session、Run、Outbox、Workflow Execution Fact、Transcript、Operation、Lease、Fencing Token、Delivery、Trace、File、Research 和 Artifact 状态的权威来源。必须保持一致的状态转换在同一个事务中提交。

主要归属：

- **身份与对话**：Account、Identity Binding、Account Entitlement、Registration Invite、Conversation、Message、Session。
- **执行**：Run、Workflow Execution、Outbox、Agent Transcript、Operation、Wait、Execution Segment、Lease 和 Fencing Token。
- **投递与追踪**：QQ Delivery、Trace Run 和 Trace Event。
- **长期文件与 Workspace**：Stored File、目录/条目、修订、Conversation/Task 授权、Run 候选/访问、发布/保存 Operation、Message/Run Binding，以及文件 Budget 和 Usage Ledger。
- **Research 与 Artifact**：Task、Plan、Source、Evidence、Report、Artifact 和 Artifact Version。
- **Heavy Document**：Normalized Document Operation 的结果与状态。

`registration_invites.entitlement_profile` 是注册凭证的配置；注册事务将其复制到 `account_entitlements`，并以 `provisioned_by_invite_id` 保留来源。普通自助注册直接写入默认 entitlement。账号后续权限以 `account_entitlements` 为准，修改邀请 profile 不会追改已注册账号。`account_daily_model_budgets` 和 `account_model_usage_ledger` 按 UTC 日期保存账号模型 token 的预留与结算；`run_budgets` 是另一个 Run 级边界。两种预算在一次预留事务中协调。Model Input Snapshot 在预算预留前单独冻结，查询时才按账号 entitlement 投影可见字段。

## Redis

Redis 负责短期 Session/Event Context、通知和工具/运行时缓存等临时协调状态。Redis 丢失可能影响进行中的便利状态，但不会取代 PostgreSQL 已提交的业务事实。

## Hindsight

Hindsight 负责长期语义 Memory Bank 和检索索引。HpAgent 在应用 PostgreSQL 中保留稳定的 Account 与 Run 关联元数据，但不会复制语义索引。

## 长期文件、Git 与字节存储

账号级长期文件 Workspace 的目录、入口、来源、版本、授权、Run 固定结果和保存操作都以 PostgreSQL 为权威。TenantFileStore 保存不可变字节；同一 `file_id` 的多个入口不复制字节。Run 的 inputs/scratch/outputs 是临时执行材料，结束后回收。普通文件能力不依赖 Git。

账号级 Git 工作区只服务代码任务。Redis 与 Hindsight 分别提供临时协调和长期语义记忆，不决定文件所有权或物理回收。详见 [Workspace v4.1](workspace-v4.1.md)。
