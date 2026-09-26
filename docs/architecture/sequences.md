# 关键时序

## Web Agent Run

1. FastAPI 认证 Account，通过 `CommandService` 接收消息。
2. PostgreSQL 一次提交 Message、Run、Session、Idempotency 和 Outbox。
3. Dispatcher 启动 `AgentLifecycleWorkflow`，后者启动 `AgentRunWorkflow`。
4. ReAct 或 Plan-and-Execute 通过 Activity 调用 Context、Brain 和 Actions。
5. 结果与 Trace 提交到 PostgreSQL；Terminal Publication 唤醒 Web SSE Client。

模型调用内部顺序：Entitlement 与 Endpoint Tier 检查 → Prepared Model Request → Freeze Model Input Snapshot → 原子预留 Account/day 与 Run budget → Provider Dispatch → Settlement 或 Release。Snapshot 冻结与预算预留是不同事务边界。

## QQ Agent Run 与 Delivery

1. QQ Adapter 标准化 Provider Identity、Room/Thread、Mention 和 Message Identity。
2. 统一命令边界提交 Conversation 与 Run。
3. 标准 Lifecycle 和 Agent Workflow 执行 Run。
4. QQ Delivery 读取已提交结果、发送格式化分片，并独立记录投递状态。

## Durable Wait 与 Resume

1. Activity 记录 Approval/Wait 需求，Workflow 等待 Signal。
2. API 提交用户决定并向 Workflow 发送 Signal。
3. 执行重新获取 Account Lease，按需取得新 Fencing Token，并从已持久化的 Transcript 与 Operation 状态恢复。

## Research

1. Task Run 启动 `ResearchReportWorkflow`。
2. Run 创建时冻结 Task 输入候选与 Workspace 保存意图；Activity 按当前内容权限选取固定历史基线。
3. Activity 依次规划、搜索 SearXNG、获取内容、提取 Evidence 并综合 Report。
4. 先发布 Run 输出，再按冻结的目录/版本目标执行 Workspace 保存；required 保存成功后才提交 Run completed。
5. 每个持久化阶段写入 PostgreSQL；Client 可查询进度、Evidence、已发布文件、保存状态和最终 Report。

## 长期文件使用与版本更新

1. 所有者把 ready 文件对象保存为 Workspace entry；字节和来源不变。
2. Conversation 或 Task 授权目标 entry/目录。新 Run 在创建事务中冻结有界候选集合，搜索只在该集合内分页。
3. Agent 首次选择时校验当前权限并固定 file/revision，再按需物化；每次受控读取再次检查权限。
4. 新输出先发布为不可变 Run 文件。长期保存创建 entry；更新既有 entry 则以预期 revision/hash 进行 CAS 提交。
5. 移除入口只解除该长期引用。统一 GC 在消息、Run、修订、固定对象及 pending operation 均不保留时回收字节。

## Heavy Document

1. File Analysis 使用稳定 Operation ID 请求规范化。
2. `NormalizeDocumentWorkflow` 将 Activity 调度到 `hpagent-document`。
3. 独立 Worker 读取 Stored File，在 Run 目录转换/规范化、结算 Usage 并记录结果。
