# 运行时

## 命令接收与 admission

Web / QQ 将身份和输入转换为应用命令。聊天 `CommandService` 在一个 PostgreSQL 事务中写入 Message、Run、幂等事实和 Outbox，Conversation 的 active 限制只作用于 chat Run。

`WorkCommandService` 接受、修订和控制 Work，保存不可变 requirement 与持久 wakeup。Work admission 检查 revision / epoch、协调权、预算、资源和容量，冻结需求、checkpoint、触发来源、Artifact 引用及策略；不创建聊天 Message 或 Session。关联 Conversation 不授予文件权限。

## 统一有限生命周期

所有开始都使用 `start_run`，Dispatcher 以稳定 `hpagent-web-run-{run_id}` 在 `hpagent-web-lifecycle` 启动 `AgentLifecycleWorkflow`。Lifecycle 读取冻结策略：

- `deterministic / reminder`：Activity 持久通知入队，不请求模型或 Sandbox。
- `fixed_workflow / research_report`：在同一 Lifecycle identity 内运行固定研究图。
- `fixed_workflow / artifact_html`：Activity 构建成果，成本由 Artifact-build Work / Run 承担。
- `generic_agent / chat_agent` 或 `work_agent`：在 `hpagent-web-agent` 启动 `AgentRunWorkflow`，执行 ReAct 或 Plan-and-Execute。

生命周期最终通过通用 Run 服务提交 `succeeded / failed / cancelled`，聊天完成与 Work 状态分别投影。Generic Work 的证据由 `WorkCompletionPolicy` 校验，文本输出本身不等于委托完成。

## Execution 与共享基础设施

每个 Run 有真实 root Execution，transcript、operation、segment / wait、attempt lease 与 fencing 都按 Execution 绑定。Generic Work 可一次委派最多三个只读子 Execution；串行 plan steps 沿用当前 Execution，不是独立并行分支。

Composition Root 拥有 Resource Pool、Sandbox、Brain、Action Runtime、Redis、可选 MCP 和 Hindsight 协作者。所有数据库、模型、工具、文件和外部渠道 I/O 在 Activity / Adapter 内完成。通用准备只建立隔离 scratch，明确的代码工具才使用 Git。

Chat Context 使用当前对话历史、记忆与授权资料；Work Context 使用冻结需求、checkpoint、输入和资源候选，不加载关联聊天全文。Subagent 只接收父级核准 brief 和资源/工具子集。模型请求统一规范化和冻结，详见[能力边界](capabilities.md)。

## 调度与独立进程

主 Worker 的 PostgreSQL due loop 维护 Work schedule desired / applied version、occurrence 和 wakeup，容量等待后重新校验执行条件。成功后的 ready continuation 用事务性 wakeup 自动续跑；等待、暂停或停止不自动续跑。

Reflection / Metrics 仍使用 Temporal Schedule。Document Workflow 在生命周期 Worker 编排，高开销规范化 Activity 在独立 `hpagent-document-worker` / `hpagent-document` 队列执行。统一通知有独立 delivery lease 和重试，投递不重新执行 Agent。

队列见[Temporal 参考](../reference/temporal.md)，恢复限制见[可靠性](reliability.md)与[实施索引](../implementation/README.md)。
