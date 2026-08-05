# HpAgent Web Phase A Hardening 报告

## 1. 结论

本轮针对 Phase A 的数据库不变量、Transactional Outbox、Unit of Work、幂等响应和 migration runner 进行了第二轮审查与加固。

- Phase A 数据库与持久化合约测试通过。
- 既有 Agent、orchestration 和 Workspace 兼容性问题已收口，非 PostgreSQL 测试无失败、无跳过。
- 当前本地验证满足 Phase A Gate；仍需以远端 CI 结果作为合并门禁。
- 数据库加固提交为 `2bfda6d fix(web): harden phase A persistence invariants`；Agent/Workspace 兼容性收口随本报告提交。
- 当前提交尚未由 Codex push。

## 2. 主要修改

### 2.1 Retry 反向不变量

新增 migration `008_retry_reverse_and_terminal_transitions.sql`，在 Run 发生 INSERT、UPDATE 或 DELETE 时，不仅验证当前 Run，还按 `run_id` 顺序验证所有以该 Run 为 `retry_of_run_id` 的直接 Retry Child。

这会阻止旁路 SQL 通过修改 Source Run 的状态、`trigger_message_id` 或 `context_message_seq` 破坏已经存在的 Retry Child。

### 2.2 终态不可回退

新增数据库状态转换 trigger：

- Run 只允许设计基线规定的非终态转换。
- `completed`、`failed`、`cancelled` 不可回退或转换。
- Assistant Message 只允许 `pending` 进入 `completed`、`failed` 或 `aborted`。
- Assistant Message 终态不可回退或互相转换。

Run 与 Assistant Message 即使被旁路 SQL 成对修改，也不能从终态恢复为运行态。

### 2.3 Trigger OLD/NEW 安全性

Run/Message 跨表约束函数按 `TG_OP` 分别处理 INSERT、UPDATE、DELETE，不再无条件读取不存在的 OLD 或 NEW。

测试覆盖了 Run、Message、Session、Identity Binding 和 Web Auth Session 的合法 INSERT、UPDATE、DELETE 路径。

### 2.4 Outbox 租约协议

Outbox 增加或收紧以下能力：

- claim 必须声明消费者拥有的 event types。
- Dispatcher 只领取 `start_run`、`cancel_run`。
- Memory Worker 只领取 `retain_memory`。
- Terminal Publisher 只领取 `publish_terminal_event`。
- dead-letter 必须匹配 `status='processing'` 和当前 `locked_by`。
- pending、processed 或其他 Worker 持有的事件不能被 dead-letter。
- 租约过期并被新 Worker 领取后，旧 Worker 不能再修改事件。
- 新增 `mark_retryable_failure()`，原子完成 `processing -> pending`、清理租约、记录安全错误并设置下次可领取时间。
- processed/dead-letter 事件不能重新进入 pending。

`start_run` dead-letter 仍会在同一事务中把 queued Run 和 pending Assistant Message 收敛为 failed，并创建终态 Outbox；终态事件 dead-letter 不会反向修改已经完成的 Run。

### 2.5 Unit of Work 清理

`UnitOfWork` 现在保证：

- 正常 commit 后关闭连接。
- 业务异常时 rollback 并关闭连接。
- commit 或 deferred trigger 抛错时尝试 rollback，并始终关闭连接。
- rollback/close 清理错误不会覆盖原始业务异常或 commit 异常。
- deadlock/serialization 整体事务重试前，失败连接已经关闭。
- 初始化 `search_path` 失败时也会关闭刚建立的连接。

### 2.6 幂等状态与冻结结果

幂等记录不再统一写入 HTTP 200：

- `create_conversation`：201
- `send_message`：202
- `retry_run` 新建：202
- `retry_run` 复用直接子 Run：200
- cancel 根据实际结果冻结 200 或 202

新增只读 `CommandResult`，同时携带冻结的 `response_status` 和响应 body，使未来 API Adapter 可以原样重放首次状态和 DTO。

send 冻结结果包含：

- User Message ID、角色、状态和 sequence；
- Assistant Message ID、角色、状态和 sequence；
- Run ID、状态和 trigger Message ID；
- Session ID。

retry 冻结结果包含 Source Run、Retry Run、Assistant Message、sequence、状态和 `retry_of_run_id`。

### 2.7 Migration runner

Migration runner 新增：

- PostgreSQL transaction advisory lock，串行化并发 migration 实例；
- migration 文件 SHA-256 checksum；
- 已应用 migration 被修改时拒绝继续执行；
- 旧 migration 历史的一次性 checksum 回填；
- checksum 列最终设为 NOT NULL。

当前 runner 仍不支持 `CREATE INDEX CONCURRENTLY`、dirty-state 恢复、自动 rollback 或 expand/contract 编排。这些限制已记录在 Phase A 开发文档中。

### 2.8 CI

CI 被拆分为：

1. `lint-and-typecheck`
2. `existing-unit-tests`
3. `phase-a-postgres-contract-tests`

现有项目测试不会被跳过或吞掉；PostgreSQL 合约测试继续使用 PostgreSQL 16，并上传 JUnit 报告。

### 2.9 既有测试兼容性收口

针对完整测试发现的 45 个既存失败，修复了以下兼容性断点：

- 恢复 Agent registry 的 `find_best()` 实例方法，消除 registry 被错误识别为抽象类的问题。
- 兼容 LLM 响应对象与字符串响应，并恢复 Supervisor agent 标签和 Stub planner 调用约定。
- Resource Pool adapter 对普通调用返回文本，对需要工具元数据的调用保留完整响应对象。
- Workspace 同时创建当前目录结构和既有 `persistent/`、`session.yaml`、input/scratch/output 兼容路径。

本机从源码安装 `/usr/bin/nsjail` 后，原先因二进制缺失而跳过的 3 个真实沙箱测试也已执行通过。

## 3. 新增 Migration

- `persistence/migrations/008_retry_reverse_and_terminal_transitions.sql`

旧的 `001`～`007` migration 没有被改写。

## 4. 测试结果

实际执行结果：

| 验证项 | 结果 |
|---|---:|
| 空库 migration | 通过 |
| 连续执行 migration 两次 | 通过 |
| 并发 migration/advisory lock | 通过 |
| migration checksum 篡改检测 | 通过 |
| Phase A 真实 PostgreSQL 合约 | 56 passed |
| Ruff | 通过 |
| mypy | 通过 |
| UoW 定向测试 | 2 passed |
| DB-001～DB-025 编号覆盖 | 完整 |
| 既有非 PostgreSQL 测试 | 170 passed，0 skipped |
| nsjail 真实执行测试 | 3 passed |

Phase A PostgreSQL 完整套件最终结果：

```text
56 passed in 71.89s
```

当前工作区非 PostgreSQL 测试结果：

```text
170 passed, 56 deselected in 28.51s
```

其中原先 3 个 nsjail skip 在安装二进制并于允许嵌套 namespace 的真实终端中运行后结果为：

```text
3 passed in 0.42s
```

当前工作区在隔离空库 `hpagent_phase_a_verify` 上重跑 PostgreSQL 合约的结果为：

```text
56 passed in 73.28s
```

## 5. 未完成项与风险

### 5.1 本地默认数据库状态漂移

本地默认 `hpagent` 数据库存在业务表但 migration 历史不完整，直接执行 `make test-db` 会从 001 重放并触发 `DuplicateTable`。为保留既有 volume，本轮没有删除或重建该数据库，而是在隔离空库完成了最终验证。继续本地开发前应备份后重建默认开发库，或为测试固定使用一次性空库。

nsjail 在 Codex 默认受限沙箱内无法再次创建嵌套 namespace，会返回 255；在用户授权的真实终端会话中运行正常。CI runner 也必须允许所需的 user、mount、PID 和 network namespace 能力。

### 5.2 数据库连接池

本轮没有引入连接池。原因是 Phase A 尚无稳定的 Web API 应用组合根，直接创建全局同步池会给后续 ASGI 生命周期和事件循环带来错误边界。

Phase B 必须选择并显式注入：

- 同步 `ConnectionPool`，并在线程池中调用同步 CommandService；或
- `AsyncConnectionPool` 与 Async UoW。

不得在 ASGI 事件循环中直接执行同步数据库 I/O。

### 5.3 Migration runner 能力边界

当前 runner 已具备绿色建库、重复执行、并发互斥和历史 checksum，但仍不是完整生产 migration 框架。进入复杂在线变更前需要评估正式 migration 工具和 expand/contract 流程。

## 6. Gate 建议

| 门禁范围 | 建议 |
|---|---|
| Phase A 数据库与持久化合约 | 通过 |
| 既有非 PostgreSQL 测试 | 通过 |
| 整体 Phase A Gate | 本地通过，等待远端 CI |

本地 Phase A 门禁已经满足。推送后应等待三个 CI Job 全部通过，再开始 Phase B。
