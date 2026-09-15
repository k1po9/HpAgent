# Phase 3 W2 — Conversation / QQ Convergence implementation report

## Commit 与合同

- W2 baseline：`cbd67d1c945df674c2b4aca344c5c1ded01c22be`，W1 已通过 Gate 并提交。
- W2-A：`64f54abda3d5cf64d380d2e55da927e468e1ab62`，共享 Conversation commands。
- W2-B：`9be9e1c5b6f8eeb7f41f32feadccf4d0db66547f`，QQ canonical ingress。
- W2-C / final production implementation：`37427eb16666c39b6ffdfc041ffb276c500fd7a3`，独立 QQ delivery。
- W2-D / final validated commit：`3504f96f634b4fc80520858d332daff83f39fa11`，跨入口与恢复证据。
- 本报告随后独立文档提交；可用 `git log -1 --format=%H -- artifacts/architecture-audit/phase3/W2_implementation_report.md` 解析其提交。没有将报告内自引用哈希伪写成已知值。

合同：Phase 2.2 R2.1。冻结目录仍是历史事实与实施合同，未改写为当前实现文档。本报告记录 HEAD 的已实现范围。W1/W2 串行遵守；本次没有进入 W3。

## Domain changes

Conversation/Message/Session、消息幂等、Run creation、PG admission 与 Outbox 由 `conversation_domain` 统一管理。Web HTTP URL 投影留在 API。single active + busy reject 是初版 policy，PG 行锁与约束仍是全部 surface 的 authority；没有 QQ mailbox 或第二 execution queue。

同账户不同 Conversation 不自动互通。跨入口使用同一 Conversation 必须显式选择并验 owner。Session 终态后复用，显式轮换创建后继；活跃 Run 拒绝轮换。重试只支持允许重试的 failed Run，cancelled Run 不可重试；两入口不另设规则。

迁移 035 增加 account-scoped bindings、immutable ingress receipts、Message origin。迁移 036 增加独立 qq_deliveries。所有 migrations 在空测试 schema 执行通过。

## QQ path changes

```text
Web API ────────────┐
                   ├→ Conversation commands → PG Message/Session/Run + Outbox
QQ protocol/identity┘                          ↓
                                  Dispatcher → AgentLifecycleWorkflow
                                             → AgentRunWorkflow / durable Activities
                                             → PG committed assistant result
                                                 ├→ Web SSE projection
                                                 └→ PG delivery state → QQ adapter
```

QQ 使用 provider message ID、bot/scope/room/thread/sender 构成稳定键；重复事件不重新执行，busy outcome 持久化。身份未绑定与数据库不可用的 UX 分开。private/group/guild/dm 按来源绑定，群非触发消息不创建 Run。Redis 仅提供可选群上下文，触发时来源快照写 PG；执行不依赖实时缓存权威。

生产 QQ 不构造 Host/Facade/loop/SessionStore，不注册旧 QQ OrchestrationWorkflow/process_turn；Web 与 QQ 都进入同一 canonical durable registry、活动区间 lease 和资源实现。旧代码与共享合同尚留源码，删除是 W3 工作。

Chat completion 同事务创建唯一 delivery payload。发送失败仅重试原结果当前分段，不修改 Run、不增加模型调用。确认后推进分段；超时/进程失联标记 uncertain，显式恢复接受渠道重复风险，旧 token 不可覆盖状态。NapCat 按 bot 选连接并等待 echo 确认；Official 失败不提前登记成功去重。QQ quote/@/分段/附件引用与 Web SSE presentation 分开。

长期记忆以 PG committed 用户消息和回答为 retain 来源；account bank 相同，group recall 使用来源隔离 key。附件是需原账户访问的 Web 文件引用，未新增 QQ 原生文件上传或公共下载授权。completed result 有可靠 delivery；失败/取消终态通知没有新增 delivery payload，取消控制提示保留 W2-B 行为。

## Tests / evidence

完整复现：`.venv/bin/python scripts/verify_w2_contracts.py`。

本轮最终 **199 个不同用例通过，无 skip**，分批证据如下。未把重复执行的用例累加：

| 执行 | 结果 | 证据 |
| --- | --- | --- |
| 主回归 | 183 passed；新增组合测试最初 1 failed | [W2_validation.txt](W2_validation.txt) |
| 新组合测试纠正后复验 | 1 passed | [W2_cross_surface_validation.txt](W2_cross_surface_validation.txt) |
| 真实 PG Outbox 故障与恢复 | 15 passed | [W2_outbox_validation.txt](W2_outbox_validation.txt) |

新增测试先错误读取 Web result 的 top-level conversation_id，随后错误假设 cancelled 可重试。两项均是测试预期错误，按既有 DTO 和 failed-only retry 合同纠正，最终通过；没有修改生产语义。已通过的 183 项未无理由重复执行。唯一 warning 是既有 Starlette/httpx 弃用提示。

| 验证要求 | 实际证据 |
| --- | --- |
| Web → PG → Outbox → durable → result；QQ → 同路径 → delivery | `test_web_temporal_e2e.py`：真实 PG/Temporal，Web ReAct/Plan completed/failed/cancelled，QQ completed/cancelled；delivery 首次失败重试且模型调用数不增，History replay |
| 同 account、不串 Conversation、QQ private/group | `test_w2_cross_surface.py`：三条并存 Run 的实际 context 文本互不包含其他 Conversation 内容；同 Conversation 的 Web/QQ 共享 busy |
| idempotency、duplicate QQ event、身份/DB 区分 | `test_qq_canonical_ingress.py`、`test_qq_canonical_protocol.py`、`test_unbound_identity.py`：并发重投、冲突、binding、撤销、事务回滚、缓存故障 |
| Session rotation、busy、cancel、retry | 新跨入口组合测试及 `test_conversation_commands.py`、`test_phase_c_sessions.py`：QQ cancel、轮换、后继 QQ failed Run 从 Web retry，保留 QQ result delivery 来源 |
| Hindsight recall/retain | QQ PG group snapshot/recall/retain 测试，`test_memory_retention.py`、`test_phase_c_context.py`：PG 来源、account/group 参数、失败不回滚 Run |
| delivery failure/recovery | `test_qq_delivery.py`、`test_qq_delivery_adapter.py`：相同 payload、分段、uncertain、显式重试、bot 路由、协议确认 |
| Outbox recovery | `test_outbox_and_lifecycle.py` 15 项，包括 killed claimer、replacement dispatcher、同一事件 ID、租约恢复、不抢其他消费者 lease、事务原子性 |
| Agent crash recovery | `test_durable_agent_worker_kill.py`：真实 Workflow Worker SIGKILL 的 ReAct tool / Plan step 恢复及 replay；`test_durable_agent_activity_worker_kill.py`：真实 Activity Worker SIGKILL，PG intent 与副作用不重复 |
| 同一 canonical runtime | 实际两入口 lifecycle/AgentRunWorkflow 测试 + production composition/registry 断言；未使用旧 QQ Host 作为替身 |
| Web HTTP/SSE | `test_phase_b_api.py`、`test_sse_gateway.py`，真实 PG/Redis + API 测试客户端 |

环境：隔离 PG、Redis、Temporal namespace；外部模型/工具、QQ 网络、Hindsight 服务使用受控替身。Hindsight 验证的是生产 adapter 调用和来源合同，不声称真实远端语义质量。Crash 用例在共享 runtime 层真实杀进程，与两入口 E2E 分层组合；没有声称真实机器人外网在同一个测试里经历 SIGKILL。没有重跑完整 W1 故障矩阵或全镜像构建。

## Gates

| Gate | 结论与范围 |
| --- | --- |
| G01 当前文档与范围 | PASS（Phase 3 scoped）：冻结合同及用户修改保留、报告区分历史/实现、diff 与 Ruff F 通过。历史 checker 的失败见下文，不伪报其 PASS |
| G02 组合与资源相关回归 | PASS（W2 selected）：worker/resource/workspace 与 consumer lease 分离测试；完整 composition 重整仍属 W4 |
| G03 双入口命令/registry 合同 | PASS（W2）：共享 PG authority、DTO、幂等/身份、唯一 runtime 入口 |
| G04 恢复相关回归 | PASS（selected）：真实 Workflow/Activity SIGKILL、Outbox recovery、canonical replay；W1 完整 Gate 沿用 W1 报告 |
| G06 双入口领域 | **PASS**：上表所列双入口、隔离、Session/admission、取消/失败重试、来源和 delivery 不重执行均已验证 |
| W2 exit | **PASS**；本报告提交后满足开始下一 Session W3 的前置条件 |
| G05 / W3 retirement | NOT RUN，不能把候选清单当作删除完成 |
| G11 | 仅空 schema 与测试服务通过；完整构建/全新镜像验收未重跑，W3 退出仍需执行 |

`phase2_2/audit_tools/validate_review.py` 在当前 HEAD 返回 FAIL（[原始输出](W2_audit_validation.txt)），因为它校验审计时源码哈希、不得有源码变化/新文件。Phase 3 合法实现必然不满足这些历史审计范围断言；结构/引用类检查仍通过。工具写入的冻结 validation_report.json 已恢复原字节，未纳入提交。本报告不把该历史 checker 改成忽略失败来制造通过；G01 当前范围由 git diff、冻结目录不变和人工语义核对证明。

## Deviations

无架构决策偏离。测试纠正、delivery uncertainty、内部恢复入口、保留 Web 物理名称和附件引用均属实施细节。未实现 Research UniversalWorkflow、Model Review/Workspace UI、W5/W6，也未合并 File/Heavy Document/Workspace/Artifact 生命周期。

## Remaining W3 retirement candidates

依据冻结 `08_retirement_plan.md` / `retirement_candidates.csv`；逐对象核验后才删除：

1. WebRunWorkflow / execute_agent_activity：先迁出 queue、FailureInput 和仍需 lifecycle 合同。
2. QQExecutionHost、WebExecutionHost、AgentExecutionFacade、DefaultBrainActionLoop：目标入口已移走；`StableExecutionFailure`、`EventSink`、ExecutionRequest/Result 等仍被 durable/Trace/adapters 使用，须先救出。
3. 旧 OrchestrationWorkflow / process_turn 与注入代码：保留 ReflectWorkflow、MetricsReportWorkflow、prompt/context、必要记忆维护能力。
4. QQ SessionStore/WAL、旧 turn memory/archive：核验 Action/Memory/工具间接依赖后删除第二短期权威实现。
5. `src/agent` 实验实现及导出：`agent.protocol` 仍是 Actions/Activities 正式依赖，先迁出协议。
6. JSON AccountService/models 与 merge-account 运维脚本：确认正式入口不依赖后退役，不运行脚本删除个人数据。
7. legacy flag、旧注册/分派、配置/文档和仅服务旧实现的测试：保留已建立的身份、取消、幂等、投递和恢复回归。
8. 必要 migrations/SQL/授权不得按名字删除；G05/G11 对新的目标 schema 与安装链重新验收。

当前仍有旧名称及历史注释，例如 worker init_dependencies 的旧组件描述；W3/W4 应结合真实依赖同步，不能据此宣称第二 production runtime 仍在运行。没有新建永久 legacy/old/deprecated 目录。
