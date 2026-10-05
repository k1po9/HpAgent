# 扩展 HpAgent

## 添加工具

在对应的 `src/sandbox/tools/` Adapter 下实现工具，声明 Schema、Side-effect 和 Idempotency Metadata，在 Tool Registry 中注册，并添加聚焦的契约测试。任何写操作都必须使用当前 Execution Identity，并遵守 Workspace、File、Approval、Budget 与 Fencing 边界。

## 添加 MCP 能力

在 `config/mcp/servers.yaml` 声明 Server，凭据使用环境变量，并通过 `python scripts/check/mcp-health.py` 验证发现与初始化。MCP Adapter 必须遵循与本地工具相同的 Action Result 和副作用语义。

## 添加应用能力

领域规则放入 Domain Package，协调逻辑放入 Application Service，Provider/Storage 细节放入 Adapter。需要事务性接收的持久化工作通过 PostgreSQL + Outbox 进入系统。Web 与 QQ 继续作为统一命令边界之上的表现层。

## 添加 Temporal Workflow 或 Activity

- Workflow 必须保持确定性，I/O 放入 Activity。
- 使用稳定的 Workflow、Run 与 Operation ID。
- 明确定义 Retry 与 Timeout，并区分永久错误。
- 外部写入前持久化幂等与副作用事实。
- 注册到能力所属 Queue，并测试 Registry。
- 按适用范围覆盖 Cancellation、Replay Compatibility、Restart Recovery、Lease Reacquisition 与 Stale Fencing。

## 新增 Work 能力或执行策略

扩展 Requirement 的明确 capability / spec 校验、StrategyRegistry 的版本化 executor 和 admission 冻结合同，在统一 AgentLifecycleWorkflow 内执行。固定流程不建立新的委托实体；后台 Run 不生成聊天 Message、不复用来源聊天预算。完成策略使用持久证据，产生文字不能代替验收。

Main 工具复用 WorkCommandService 并绑定可信 Account / source Message；Work / Subagent 不继承 Main 管理权限。分支只读工具与资源 manifest 由父级核准，不允许递归委派或自行扩权。费用和容量仍通过 Account / Work / Run 账本与 PostgreSQL 票据协调。

增加 Workflow 后同步 Registry 和真实 sandbox prepare 测试，避免 Activity 网络依赖出现在间接 import 链。新增错误 code 要贯通 Activity、Lifecycle stable failure mapping、API failure 和 retry contract，不能仅在一个捕获层改错误文字。Model Provider 适配经 Prepared Request v2 和快照边界，不能改写历史请求体。

文档同步顺序：当前[Work 架构](../architecture/durable-work-v1.md) → API / 配置 / Temporal → 操作 / 测试 → [实施索引](../implementation/README.md)。历史阶段报告保留原验证条件，不将未执行的真实验收补写为已通过。
