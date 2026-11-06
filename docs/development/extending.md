# 扩展 HpAgent

## 添加工具

在对应的 `src/sandbox/tools/` Adapter 下实现工具，声明 Schema、Side-effect 和 Idempotency Metadata，在 Tool Registry 中注册，并添加聚焦的契约测试。任何写操作都必须使用当前 Execution Identity，并遵守 Workspace、File、Approval、Budget 与 Fencing 边界。

## 添加 MCP 能力

在 `config/mcp/servers.yaml` 声明 Server，凭据使用环境变量，并通过 `python scripts/check/mcp-health.py` 验证发现与初始化。MCP Adapter 必须遵循与本地工具相同的 Action Result 和副作用语义。

## 添加应用能力

领域规则放入 Domain Package，协调逻辑放入 Application Service，Provider/Storage 细节放入 Adapter。需要事务性接收的持久化工作通过 PostgreSQL + Outbox 进入系统。Web 与 QQ 继续作为统一命令边界之上的表现层。

Chat 从 `application/chat/service.py` 组合 SendMessage、RetryChatRun 与共享 CancelRun；Work 从 `application/work/service.py` 组合 AcceptWork、AdvanceWork，调度从 `application/work/scheduler.py` 进入。QQ 身份/receipt 在 `application/surfaces/conversation.py`，显式 Artifact 写事务在 `application/artifacts/commands.py`。组合根引用这些实际实现，纯兼容命令和策略模块已移除；仓储与编排调用边界见代码地图。

服务门面只保留实际使用的业务命令、查询及事务协作入口。Chat 保留 QQ 使用的 database、conversations、messages、runs，以及测试使用的 outbox 和 agent_policy；其余依赖直接由用例使用。Work 的内部幂等、响应组装、锁和关联操作直接调用 dependencies；integration_command 复用同一 dependencies，不经过私有透传。Artifact 的创建/版本与查询、WorkScheduleService 的 run_once/dispatch_due/occurrence 均有调用方，继续保留。

独立命令的 execute 拥有 UoW 和有限事务重试；execute_in_uow 借用调用方事务，不提交、不另开连接。Repository 接收 UoW 执行指定表的 SQL，应用协调状态、事件、通知和 Outbox 同事务提交；提交后才调用外部服务。QQ Work 控制沿 WorkCommandService(uow) 保留原嵌套 savepoint，Work 调度的评估与 dispatch 仍是两个事务。具体边界以[事务所有权表](../architecture/execution.md#unitofwork-所有权)为准。

测试先选[已有业务行为断言](testing.md#业务命令与持久化选测)，只有实际接缝缺少可观察证据时才补测试。不要用目录存在、重复实现或私有函数调用断言代替幂等、权限、并发与原子性结果；迁移链及历史回放 fixture 是正式执行输入，不能作为临时文件清理。

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

文档同步顺序：当前[Work 架构](../architecture/work.md) → API / 配置 / Temporal → 操作 / 测试。只原位维护当前契约和限制，不新增阶段报告；未执行的真实验收不能写为通过。

## 业务主干命名

| 名称 | 含义 |
| --- | --- |
| ChatCommandService | Chat 命令门面，与 WorkCommandService 区分。 |
| database | Chat 服务和用例依赖的数据库入口，可为 DSN、连接池或借用的 UoW；环境配置中的 DATABASE_URL 名称不变。 |
| ProcessWorkSchedules | 同步并评估 Work 调度，提交后再调用 DispatchDueWork；两个事务各自提交。 |
| to_json_value | 将 Work 投影中的 UUID、datetime、bytes 和容器递归转换为 JSON 可用值。 |
| create_artifact_chat_sender | 构造显式 Artifact 动作使用的 Chat 发送用例；保留 observe、file-p0-v1、1024 保留 token 等原默认值。 |

仓储统一使用 WorkRepository，不再为同一实现引入 WorkRows 别名。上述名称直接用于实现与调用方，不保留旧名转发。
