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
