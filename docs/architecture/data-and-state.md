# 数据与状态归属

## PostgreSQL

应用 PostgreSQL 是 Account、Identity Binding、Conversation、Message、Session、Run、Outbox、Workflow Execution Fact、Transcript、Operation、Lease、Fencing Token、Delivery、Trace、File、Research 和 Artifact 状态的权威来源。必须保持一致的状态转换在同一个事务中提交。

主要归属：

- **身份与对话**：Account、Identity Binding、Conversation、Message、Session。
- **执行**：Run、Workflow Execution、Outbox、Agent Transcript、Operation、Wait、Execution Segment、Lease 和 Fencing Token。
- **投递与追踪**：QQ Delivery、Trace Run 和 Trace Event。
- **文件与 Workspace 元数据**：Stored File、Message/Run Binding、Persistent Revision、Approval、Budget 和 Usage Ledger。
- **Research 与 Artifact**：Task、Plan、Source、Evidence、Report、Artifact 和 Artifact Version。
- **Heavy Document**：Normalized Document Operation 的结果与状态。

## Redis

Redis 负责短期 Session/Event Context、通知和工具/运行时缓存等临时协调状态。Redis 丢失可能影响进行中的便利状态，但不会取代 PostgreSQL 已提交的业务事实。

## Hindsight

Hindsight 负责长期语义 Memory Bank 和检索索引。HpAgent 在应用 PostgreSQL 中保留稳定的 Account 与 Run 关联元数据，但不会复制语义索引。

## Git 与文件存储

账号级 Git Workspace 负责可编辑项目的历史和工作文件。File Store Volume 保存上传及生成的 Blob；PostgreSQL 保存其元数据、Lineage、权限和 Run 关联。临时 Run/Document 目录只是执行材料，不是业务权威。

因此 PostgreSQL、Redis、Hindsight 与 Git 分别拥有不同类别的状态，它们是协作存储，而不是重复权威。
