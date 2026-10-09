# CI 修复验收记录 · 2026-10-09

基线为 `662fc863b4c3069cf6816cd7ab7f82018344f3a7`，工作区起始无改动。本轮根据附带 CI 诊断文档定位问题，以当前源码及本地复现为验证依据。

**结论：修复完成，原五个失败 Job 对应的最终本地门禁全部退出 0。没有推送或触发远端完整 CI，因此不宣称 GitHub 的十个 Job 已全绿。修复和验收记录一并提交，commit SHA 以 Git 历史为准。**

## 故障与修复

| 原失败 Job | 实际根因 | 修复 |
| --- | --- | --- |
| existing-unit-tests | 测试缺少委派查询、fencing 与容量依赖；身份、清理键、mock 签名、失败分类、策略、超时与注册清单滞后 | 使用显式受控测试边界，继续执行真实委派查询及运行时判断；校正断言。新增子代理允许/禁止、初始 fence 拒绝、模型资源撤权的回归。原 34 个失败 nodeid 全部恢复。 |
| phase-a-postgres-contract-tests | MemoryRetention 的两处 Run 成功状态断言过时 | Run 断言更新为 `succeeded`，assistant Message 继续断言 `completed`；只同步服务说明，生产逻辑没有改变。 |
| phase-b-api-contract-tests | 单独收集 API 目录时无法导入 `test/support`；Redis 缺失导致 SSE 跳过；五个数据库用例漏标 | CI 统一 `PYTHONPATH=.:src:test`；增加独立 Redis；补齐四个文件中五个用例的 `postgres` 标记。Temporal API 两个用例转交 Phase D。 |
| phase-c-isolation-gate | CI 引用已不存在的 Session 测试；当前 chat 已不以 Session 为执行前提 | 替换为真实 Conversation/Run/Execution 所有权测试，保留 Context 和 Workspace 门禁。增加 CI 单文件路径预检。 |
| phase-d-temporal-fault-gate | 准备终态仍使用 `completed`，意外走入未注册的 Activity；移入的 API 用例默认测试 namespace 不存在 | 成功参数改为 `succeeded`，额外断言历史中只调度预期 Activity、没有启动 Agent 子工作流；CI 明确 `TEMPORAL_TEST_NAMESPACE=default`。 |

生产权限检查、Run 状态机和 Workflow 代码均未修改。没有新增 skip/xfail，没有删掉原断言或吞掉异常。`scripts/check/temporal_registry.py` 的必需清单新增 `WorkDelegationWorkflow`，测试仍校验完整注册列表。

## 最终验证

| 门禁 | 实际选中 | 通过 | 原有环境条件跳过 | 失败 |
| --- | ---: | ---: | ---: | ---: |
| 非 PostgreSQL 单测 | 575 | 554 | 21 | 0 |
| Phase A PostgreSQL | 267 | 246 | 21 | 0 |
| Phase B API | 64 | 64 | 0 | 0 |
| Phase C PostgreSQL | 7 | 7 | 0 | 0 |
| Phase C Context / Workspace | 15 | 15 | 0 | 0 |
| Phase D + 冻结 History replay | 28 | 28 | 0 | 0 |

这些集合存在重叠，不累加为独立用例总数。单测与 Phase A 的 21 个跳过均为既有 Temporal 环境条件；本轮没有通过增加跳过消除失败。

- 原 34 个失败：先实测 `34 failed, 54 passed`，再将完整 nodeid 与最终单测 JUnit 中通过记录逐一比对，`34/34` 恢复。详细映射见 [results.json](results.json)，原 traceback 见 [unit-before.txt](unit-before.txt)。
- Phase B 最终收集 `64/97`，其余 33 个为 31 个非 PostgreSQL 用例和 2 个 Temporal 用例。31 个由普通单测运行；2 个由 Phase D 实际运行。五个漏标 API 用例均单独补验通过，并纳入最终 64 个用例整组验收。
- Phase C 数据库七例：跨账户 send/cancel/retry 被拒且不留命令副作用；无 Session 的两个对话具有不同且正确归属的根 Execution；上下文只含本对话 watermark 内消息；retry 复用原 watermark；跨账户上下文读取被拒。另有 12 个 ContextAssembly 和 3 个 Workspace 隔离单测。
- Phase D 共 20 个真实生命周期/数据库/取消用例 + 8 个冻结历史回放；终态成功、失败与取消均不启动 Agent，历史中也不调度 strategy 或失败兜底 Activity。
- 生产 lifecycle 调用的 8 个 Activity 均已核对注册；四条 task queue 的完整 Workflow 注册检查通过，包含 `WorkDelegationWorkflow`。
- CI 路径检查正反例通过；YAML 含十个 Job 且解析正确；仓库指定 Ruff 和 mypy 检查通过（36 个源文件），修改范围附加 Ruff 与 `git diff --check` 通过。

## 复验命令与环境

使用本地 `.venv/bin/python`（Python 3.12），设置 `PYTHONPATH=.:src:test` 及仓库 migrations 目录。三个 DSN 分别使用 migration/API/worker 角色，指向同一个专用测试数据库。

```bash
python -m pytest -q -m 'not postgres' test
python -m pytest -q -m postgres test/web_persistence
python -m pytest -q -m 'postgres and not temporal' test/web_api
python -m pytest -q -m postgres test/web_persistence/test_phase_c_conversations.py test/web_persistence/test_phase_c_context.py
python -m pytest -q test/test_context_assembly.py test/test_workspace_isolation.py
python -m pytest -q -s test/test_web_temporal_integration.py test/test_agent_segment_replay.py test/web_persistence/test_web_temporal_e2e.py test/web_persistence/test_web_temporal_lifecycle.py test/web_api/test_workspace_research_cancel_chain.py
python scripts/check/ci-test-paths.py
make lint typecheck
```

隔离环境使用本轮新建的 PostgreSQL 16、Redis 7、Temporal 1.26.2 容器。PG 端口 55432，API/Phase A/Temporal 集合使用独立数据库；Redis 端口 56379、DB 15，Temporal 端口 57233。最终 Temporal 验收使用现有 `default` 测试 namespace。专用可丢弃 PG 临时关闭 fsync/synchronous_commit 以降低测试开销；本轮验证数据库契约与 Worker 生命周期，不验证 PostgreSQL 进程崩溃后的磁盘持久性。

首次组合 Temporal 命令已经完成 26 个用例，随后在两个 API 用例默认不存在的 `hpagent-v41-final` namespace 启动阶段等待；该命令被中止，不作为门禁通过证据。修正测试 namespace 后两个 API 用例单独通过，最终完整 28 个用例命令再次执行并退出 0，见 [phase-d.txt](phase-d.txt)。

最终日志和 JUnit 保存在本目录；失败基线日志仅清理行尾空白，traceback 与断言内容保留。测试容器及网络在验收后清理，现有业务服务没有被测试重置。前端与其余原已通过 Job 未作改动或本轮重新验收；真实模型 Provider 未调用。

## 验收边界

已完成本地修复和五个原失败门禁的实测。修复已形成本地提交，尚无新的远端 CI Run URL；完整十 Job 的远端 CI 结果仍需实际推送后取得。
