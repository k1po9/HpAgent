# Durable Work V1 — Phase 2 实施记录

唯一实施依据：`docs/design/durable-work-v1-implementation-design.md`，阶段二 Conversation / Work Execution Decoupling。开始实施前已完整阅读设计，并核对 Phase 1 的实际 schema、命令服务、Research 与聊天执行链。

## 已完成

- 每个 Run 自动建立独立且身份不可变的 root Execution；Conversation admission 仅限制同一对话的 chat Run。Work Run 不创建 synthetic Message，也不依赖 Session。
- loader、Agent DTO、transcript、segment/wait、workflow execution、operation 均绑定真实 Execution。Agent 与 Research 使用 Execution attempt lease/fencing token；替换 Account execution lease，移除通用 Session repository/recovery。
- 通用执行准备独立 scratch/Sandbox，不初始化 Git、不持有 Account 长执行锁。scratch 按 Account / Run / Execution / attempt / 物理调用隔离，旧 Activity 清理不破坏新尝试。显式 Git 工具的资源锁仍保留。
- Research 阶段复用通用 operation 账本与 attempt fence，持续 heartbeat；结果保存和 Artifact/Workspace 发布在提交时重新检查当前 revision、epoch、Run 与权限。
- 注册 producing attempt 后才可接收结果回执；回执的 provenance、revision、digest 与 disposition 不可修改。迟到结果仅进入原 operation/receipt，不能追加当前 transcript、写 Workspace 或完成新 revision。被替代的仍运行尝试只保留回执元数据，不能抢占新尝试的 operation 结果。
- 统一 prepare、失败/取消终态、outbox dead letter 与 reconciler 的 Run 生命周期；Work 指针按匹配 Run 释放。暂停/停止记录控制命令事件；存在未知外部写入时保留 blocker，等待确认回执后再收敛，禁止直接重新 admission。
- effect key 在 Account / Work 范围唯一，参数摘要冲突拒绝；跨 Run 复用追加 producer reference，保留原 Run / Execution provenance。intent/uncertain 状态交给已有 reconciliation 路径，不作为可重新发送的成功结果。
- REST 与 SSE 共用 chat/work discriminated Run snapshot；Work variant 无 assistant_message。取消某个 Work Run 保留委托，与 Work.pause/stop 分开；QQ `/cancel` 范围维持聊天。

## 最终 schema 关系与权限

新增 migration `055_execution_decoupling.sql`；不修改已提交 migration checksum，不执行历史回填。只允许空 development Run store 应用。

| 关系 | 约束 |
|---|---|
| Run → root Execution | 自动创建，Run 唯一，Account / Run / Execution 复合 owner |
| Execution → attempt lease / segment / wait / transcript | 复合 owner；transcript 按 Execution 唯一；lease owner segment 复合 FK |
| workflow_executions → Execution | 复合 owner，stable workflow start identity 保持 |
| execution_operations → Execution | 复合 owner；身份与 completed result 不可改；Work / revision 固定 |
| operation → producing attempt → result receipt | attempt 注册、token、result reference、revision 校验；回执不可改；current receipt 必须持有效 fence |
| current Execution → effect reference → producing operation | 两侧复合 FK，同 Work，参数摘要一致，producer provenance 不改 |
| transcript event → operation | 数据库拒绝借用其他 Execution 的 operation |
| control event → command / Execution | 双侧 Account owner，append-only |

API 仅查询 Execution/operation/receipt；控制命令可追加 control event。Worker 可操作执行账本、租约和必要状态，attempt/receipt/reference 仅 SELECT/INSERT；禁止删除 operation。root 创建与状态投影函数禁止 PUBLIC 调用。

## 简要验证

使用本次新建的隔离 PostgreSQL 16 development 数据库，最终空库 schema 和 migrate / API / worker 三种角色 gate 均通过。未清空或修改已有业务数据库。

- 核心 Work/Execution、segment、Agent contract/hardening、source identity、outbox recovery、文件隔离定向集合：101 passed。
- 最终 migration 上的 Execution 边界与 Research R0–R2：19 passed；包括跨 Execution transcript 引用拒绝、旧 fence 禁止 Workspace 写入、迟到回执、effect 参数冲突、旧 scratch 清理不破坏新尝试。
- 真实 Temporal + PG 定向验收：3 passed。阻塞真实 Research Activity 时，原 Conversation 后续聊天与新 Conversation 均实际调用受控 Brain 并完成，同对话并发仍 busy；还覆盖启动 RPC 接受但丢回执、history replay、Activity Worker SIGKILL 与不重复外部写入。使用独立 Temporal namespace，未调用付费模型服务。
- HTTP Work cancel 的 snapshot、幂等重放、跨账户拒绝、取消后保留 Work 委托已验证；SSE 在 Redis 不可用时正确降级，Redis 在线事件测试未运行。
- 前端类型检查及 Run feed / workbench 定向测试：25 passed。
- mypy：36 source files passed；变更 Python 文件 Ruff 检查及 `git diff --check` 通过。

没有运行全量集成套件；测试使用受控模型/外部写入实现，不能据此宣称所有真实供应商行为均已验证。

## 阶段边界

本阶段完成 root Execution 解耦及并发/控制/回执边界。Main Work tools、StrategyRegistry、Generic WorkContextProvider、Work schedule / deterministic reminder executor 属于 Phase 3；完整 Artifact adoption、Delivery、公平预算与 Work UI 属于 Phase 4；Subagent 未实现。已有本地 reminder 工具等待 Phase 3 替换，未宣称可靠提醒闭环已完成。

未部署。已有非空共享数据库需按设计另行处理数据/重建决定；本次实施没有授权或执行现有数据库 reset。
