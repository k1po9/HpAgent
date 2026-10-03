# Durable Work V1 五阶段复查修复记录

日期：2026-10-03。依据：[五阶段实施复查](../design/durable-work-v1-implementation-review-2026-10-02.md)。

本轮修复 R1–R5，并更新报告指出的八项旧测试。继续使用 Main / Work / Run 与独立 Execution 的现有边界；不放宽完成证据、资源授权、累计预算或重试唯一约束。

## 修复合同

| 问题 | 修复与行为 | 回归证据 |
|---|---|---|
| R1 群聊披露 | 从持久化 Message origin 传递 scope 和 interaction_profile 至 execution sandbox。Main 的所有 Work 工具返回使用同一个公开投影：仅 Work ID、状态、版本、revision、continuation kind。标题、objective、checkpoint、资源、Artifact、Run 快照和 continuation 细节不进入群聊工具观察值。Web/私聊保留账户授权详情；guild 同样裁剪 | 真实 QQ ingress → ExecutionResourceService → SandboxManager → Main 工具；覆盖发现、查询、接受、修订、暂停/恢复、关联、推进，以及私聊/Web/guild |
| R2 重试与调度 | 查询当前 revision 的最近一次 Run，仅 advance/retry 唤醒且最近一次 failed/cancelled 才设置 ancestry。成功后的新步骤、下一个 daily occurrence 不指向历史失败；连续失败形成逐次重试链。单 Work 的数据库异常回滚自身 savepoint，保留 pending wakeup；调度循环记录异常并在下轮恢复，取消仍传播 | 连续两次失败 → 重试成功 → 再推进/下一日 occurrence；真实 PG admission 故障不阻断第二账户；故障解除后 pending wakeup 入场；循环恢复与取消 |
| R3 ready 静默停滞 | 成功结束事务为 ready 创建持久 advance wakeup，使用 continuation:run_id 去重。完成、暂停、停止、旧 revision 和等待输入/blocked 均不自动续跑；预算、资源、容量继续由 admission 校验 | 重复 terminal 回执仅一条 pending；控制与新 revision fence；真实 Generic Temporal 生命周期两次 Run 恢复 checkpoint，并发聊天实际完成；历史重放 |
| R4 文件预算 | generic/fixed Run 增加扫描 1 GiB、返回 2 MiB、写入/输出各 256 MiB 的有限额度。新 Work 写入/输出默认各 512 MiB，能够容纳真实工具的 128 MiB 最大预留，再按实际字节结算。没有关闭预算或无限增额 | 授权 Workspace 文本读取/分析、真实小 DOCX/XLSX 生成、有限子分支读取、超额拒绝与 Work/Run 账本一致性 |
| R5 孤立预留 | Run reserve 在内部 savepoint 中统一修改 Work 与 Run。Run 拒绝时先回滚本次 Work hold，再按原合同持久化 standalone Run exhausted；model coordinator 拒绝仍回滚 Account/day、Work、Run 整个事务。保留 Work → Run 锁顺序与未知调用责任 | 第 41 次调用重复拒绝时两个 ledger 均无该 operation，Run exhausted；未知调用跨 terminal 保留并可迟到结算；三分支并行预留不超 Run 限额；模型拒绝三层均无 hold，合法调用正常结算 |

这些默认额度适用于新 Work / Run。已有 Work 的 limits 不被静默修改；确需增额时使用既有带版本、幂等与审计的预算调整命令。

## 旧测试的等价覆盖

- 两个 completion 测试使用真实执行租约、注册 operation 与持久化 receipt，继续验证 Run 成功不自动完成 Work，以及 ongoing 不完成。
- QQ 幂等缓存测试模拟合法的过期时间，删除可回收的非审计引用命令，保留 execution_control_events 引用；旧 cancel/busy receipt 重放仍不能影响后来 Run。
- QQ adapter 测试改用统一 route/content_scope，同时保留分片、CQ 转义、引用/mention 与受保护附件链接断言。
- Research 使用统一 hpagent-web-run-* identity，dispatcher 测试通过 dispatch_start 验证持久身份与 start/cancel 竞态。
- registry 合同加入 WorkDelegationWorkflow，保留有限执行图注册检查。
- PG fixture 将相同表集合合并为一次 TRUNCATE ... CASCADE，减少新增外键图上的重复级联清理；仅作用于显式配置的测试数据库。
- 扩展崩溃恢复验收时，将 SIGKILL harness 改为先持久化 canonical Workflow identity，再通过 AgentLifecycleWorkflow 执行 Research 图，保留 publish/save 提交后崩溃及唯一成果断言。撤权测试读取实际 Execution attempt 的 scratch 路径，继续验证 adapter 返回前不清理、返回后清理并取消 Run。

## 验证环境与范围

使用本轮创建的独立 PostgreSQL 16 容器与不同测试库，完整执行 001–058，并使用真实 hpagent_api / hpagent_worker 角色。Temporal 使用独立队列；崩溃/撤权验收使用独立测试 namespace。模型和外部研究来源采用受控依赖，没有向真实 QQ 发信。目标 schema 上的真实 API lifespan 与 /health/ready 已通过。

| 验证 | 结果 |
|---|---|
| Work domain（含新增真实 Generic 持续推进）、Work persistence/API、segment replay，以及 Run/Account budget、QQ ingress/adapter、schema、Research persistence、Durable/Research contract | 139 passed；原八项失败全部通过；一条现有 Starlette/httpx 弃用警告 |
| `test/web_persistence/test_durable_review_regressions.py` | 20 passed |
| `test/test_run_budget.py`、Account daily contract、file read/analysis/output | 32 passed |
| `test/test_schema_runtime.py` | 2 passed |
| Research publish/save 提交后 SIGKILL、Temporal 文件读取撤权、resource/account integrity、QQ delivery | 7 passed；SIGKILL 使用 5 秒自然到期租约，见下述生产默认配置限制 |
| 独立目标 schema API lifespan 与 `/health/ready` | 通过，58 migrations，ready |
| 前端 `npm test`、`npm run typecheck` | 14 files / 87 tests passed；类型检查通过 |
| 本轮修改的 Python 文件 Ruff 与 `git diff --check` | 通过 |

上述后端共 200 项不同测试通过，没有运行全仓全部测试。重复运行的 schema 与单项复核没有重复计入。SIGKILL 的短租约条件见下面说明。

复跑 PostgreSQL 测试需要显式配置 `MIGRATION_DATABASE_URL`、`APP_DATABASE_URL`、`WORKER_DATABASE_URL`，分别连接同一隔离库的 migration/API/worker 角色；`HPAGENT_MIGRATIONS_DIR` 指向仓库的 `persistence/migrations`。Temporal 测试需要 `TEMPORAL_HOST`，崩溃/撤权测试还使用 `TEMPORAL_TEST_NAMESPACE`。

## 部署与退出验收

既有业务数据库没有重置或迁移；本轮代码没有自动切换既有 Compose 部署。原报告中 53/58 migrations 的部署状态不能由隔离库通过替代。054 明确要求空开发库，因此环境切换应使用新干净数据库与明确切换步骤，不直接对有历史 Run/Task 的旧库补执行。

真实 Generic 持续推进与并发聊天已补验收；提醒 receipt、revision fence、三个分支失败/隔离与多租户预算/容量由当前集成测试覆盖。真实模型调查的分支收益、真实 QQ 发信、浏览器跨入口完整流程和持续多租户压力验收仍需产品环境执行。不能据本轮受控验证宣称原设计六场景全部完成。

额外 SIGKILL 测试发现：Research 每个 stage 创建的 `AgentDataStore` 默认租约为 3600 秒，而 publish/save 的 Activity schedule-to-close 为 120 秒。实际杀死 Worker 后，替代 Worker 等待旧租约期间无法在该窗口内完成恢复。本轮没有修改生产租约策略。SIGKILL harness 使用 5 秒租约，通过真实 PG 的自然到期与 fencing 验证接管和成果去重，没有强制清空租约或未知预算预留。该条件必须随结果一起解释；生产默认租约下的快速崩溃恢复仍是后续阻断项。
