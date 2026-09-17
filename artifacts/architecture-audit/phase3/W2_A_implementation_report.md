# Phase 3 · W2-A Surface-neutral Conversation Domain

日期：2026-09-15。状态：**W2-A VERIFIED**。当前交付仅为 **W2-A**；完整 W2 / G06 尚未完成，不能进入 W3。
冻结合同：Phase 2.2 R2.1；本次没有修改 phase2_2 或把 TARGET 改写为历史实现事实。

## 基线与提交

- Session baseline：`cbd67d1`。
- W1 实现完成点：`e0dfc7a`；Gate 报告提交：`1c6a86e`，报告记录 G02/G03/G04（W1）通过。
- 本报告随 W2-A 实现独立提交。实际提交可用 `git log -1 --format=%H -- artifacts/architecture-audit/phase3/W2_A_implementation_report.md` 定位。
- 原有 `phase2_1/03_architecture_truth_table.md` 未提交修改保持原样，不纳入本次提交。

## 修改与架构变化

1. 从 `web_domain` 提取 Chat 的 `CommandService` 和 `ConversationSessionService` 到 `conversation_domain`。所有现有生产、测试和脚本调用方直接使用新边界；旧路径不留 wrapper 或 re-export。这不是整个 package rename，File、Research、Artifact、Workspace、Budget、Trace 等 owner 保持独立。
2. 发送和 retry 在 ownership 校验及 Conversation 行锁内调用 `AdmissionPolicy`。首版 `SingleActiveRunAdmission` 用同一 PG 事务查询 active Run，并由现有 PG 唯一索引兜底。Session 轮换仍以 PG 活跃状态决定。没有新增 mailbox、内存队列、Redis authority 或 Agent runtime。
3. 命令接受 UUID 或最长 128 字符的稳定来源键；非 UUID 键通过固定 namespace UUIDv5 映射到 Message 去重标识。PG 保留 account 作用域。来源键应由 adapter 纳入 provider/bot/room/thread/message，不因同一 account 合并 Conversation。QQ 具体绑定、规范化与生产调用尚待 W2 后续实现。
4. Conversation、Message、Session 及 Chat 对共享 Run 的创建，继续在同事务内写 Run、预算快照、输入附件引用及 Outbox；外部 Dispatcher 仍启动 W1 canonical lifecycle。本次不改变 Run 的共享 Execution/Lifecycle owner，不改 Research fixed Workflow 或其非 Chat Run shape。
5. 命令结果不再生成 SSE/文件 HTTP URL；`web_api.command_projection` 根据已提交事实生成 URL，保留既有 HTTP API 响应形状与重放行为。通用 `CommandResult` 迁入 `persistence.command_result`，File/Artifact 的通用事务结果无需依赖 Conversation 命令实现；PG 已有 response_status 编码保持不变。
6. retained Message 去重在幂等响应记录删除后，除内容/Conversation 外也核对 strategy 和附件身份。附件按身份集合的排序比较，避免 PG 存储排序与请求排序不同导致相同请求被误拒绝。
7. 错误类型、失败策略、预算投影、Outbox consumer 与 W1 的 Workflow ID/queue 仍保留既有物理路径或地址；物理名称本身不决定领域归属，本次也未新增状态权威。本次没有为名称一致性继续迁移独立能力。

未来 persistent queue / interrupt / append-to-current-run 须同步实现 policy、PG 状态转换和约束；不能只替换 Python 类就宣称能力已实现。PG authority 不变，领域边界无需重设。

## 验证

| 执行 | 结果 |
| --- | --- |
| `.venv/bin/python scripts/verify_w2a_contracts.py` | **120 passed / 0 skipped**，1147.80s；namespace `hpagent-w1b-test-893e1b3ab2`；原始输出见 [W2_A_validation_results.txt](W2_A_validation_results.txt) |
| `.venv/bin/python scripts/verify_w2a_contracts.py test/web_persistence/test_conversation_commands.py` | **6 passed / 0 skipped**，92.14s；namespace `hpagent-w1b-test-2d6e004fe7`；原始输出见 [W2_A_retained_replay_validation.txt](W2_A_retained_replay_validation.txt) |
| Ruff（所有修改的 Python 文件）、`git diff --check` | PASS |

主回归 collection 后发现附件存储顺序与请求顺序不同，已修正 retained replay 比较并新增一个测试；随后 6 项定向复验覆盖最终代码。两次合计 **121 个不同用例通过**，其中 5 项重复验证。最终脚本默认 collection 为 121 项。

主回归覆盖 Web HTTP response/replay、真实 Redis SSE、Session/admission/ownership、Outbox/lifecycle、真实 PG + Temporal 的 canonical Web E2E，以及 File approval、Budget、Memory retain、Artifact 和 Research/File composition。PG、Redis、Temporal 真实运行；模型等外部能力使用受控替身，不是实际 QQ bot delivery 或第三方模型验收。唯一 warning 为既有 Starlette/httpx 弃用提示。

验证脚本复用隔离 PG / Temporal fixture runner，并为 SSE 提供独立 Redis 容器；从空 schema 执行 migrations，不清空应用数据库。两次结束均已清理临时容器。首次执行期间环境恢复导致旧进程及 `/tmp` 日志丢失，未将其计为完成证据；上表为重新执行并保存到本目录的结果。

新增测试明确只模拟两个 surface 的 command caller；不会把它们命名为实际 QQ ingress E2E。
覆盖稳定非 UUID 键与 retained replay、并发 busy、同账户不同 Conversation/Session、跨账户拒绝、Session 轮换/retry、PG policy transaction 和 Outbox 失败整笔回滚。HTTP projection 验证添加链接且不改动持久化结果。

## Gate

| Gate / 范围 | 结论 |
| --- | --- |
| G01 文档与范围（本次） | PASS：冻结合同不改，HEAD 文档只描述已实现 W2-A，原有 Phase 2.1 修改保持 |
| G03 共享命令合同（W2-A 子范围） | PASS：生产调用方、共享事务结果、非 UUID 键、PG policy 与 HTTP 投影通过 |
| G04 Web canonical 回归（所选用例） | PASS：现有真实 PG/Temporal Web E2E 通过；不宣称重跑全部 W1 Gate |
| G06 领域子场景 | PASS：共享 command caller 的幂等、忙时竞争、ownership、Session 与事务回滚 |
| G06 完整双入口 / W2 退出条件 | NOT COMPLETE：QQ production ingress/runtime/delivery 尚未切换，缺完整 QQ E2E |
| G05 / W3 | 未开始；必须等待完整 W2 Gate 及完成提交 |

**完整 W2 Gate / G06：NOT COMPLETE。此提交不能授权进入 W3。**

## 剩余 W2 与下一 Work Package

W2 仍需：

1. QQ protocol/identity/room/group/thread mapping 与消息规范化实际调用统一 PG command；未绑定与 DB 失败使用不同 UX。
2. QQ production composition 切入同一 Run + Outbox → Dispatcher → canonical durable runtime，退出私有 Agent loop / mailbox 的生产调用。
3. 从已提交结果产生可靠 QQ delivery，失败只重试投递；保留引用、@、附件、群聊/私聊隔离及必要长期记忆来源。
4. 真实 QQ → PG → Outbox → durable → delivery E2E，覆盖重投、忙时、轮换、取消/retry、授权和投递恢复，完成 G06。

W2 Gate 通过并提交之后，下一 Work Package 才是 W3：按 retirement plan 救出必需合同与能力，删除失去目标调用的 legacy implementation。本次没有执行 W3，也没有实现 W5/W6 或新的 Research/Document/File/Workspace/Artifact 生命周期。

## Architecture deviation

无。上述命名、结果 envelope 与 policy 切口属于 implementation detail，未推翻冻结决策。
当前文档已同步 W2-A 的实际状态；旧组件图的 ownership/主线 visual drift 明示记录，Excalidraw 未修改。
