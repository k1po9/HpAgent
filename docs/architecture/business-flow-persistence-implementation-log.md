# 业务流程与持久化架构实施日志

按[已批准蓝图](business-flow-persistence-refactoring-blueprint.md)第15节逐任务追加。原始 AS-IS 蓝图保持不变；只有实施后才记录相应 TO-BE。原始运行输出保存在仓库外，不纳入 Git。

## 2026-11-05 Phase 0：Chat 基线选择与前置条件核验

实施时间：2026-11-05 22:35（Asia/Shanghai）。
基线分支：`main`；基线 Commit：`9b3d159c59253900f0141bac11036cf14c332152`。
本次没有创建 Commit；本节是工作区中的实施记录，不代表已提交。没有进行生产代码实施。

### 任务状态与结论

| Task ID | 状态 | 本次结果 |
| --- | --- | --- |
| P0.1 | 阻塞（选测与收集完成，业务执行未完成） | 已选择并成功收集5个现有Chat测试；服务前置门禁退出4，5个测试正文均未运行，尚未建立动态业务基线 |
| P0.2 | 本轮覆盖检查完成；无需补测；迁移时仍须继续检查 | 当前没有改变事务接缝、入口或编码；现有断言足以作为P1.1的首批回归，不新增测试。后续迁移才触发的缺口检查仍待对应任务开始 |
| Phase 1–4 | 未执行 | 按用户要求暂停，不进入P1.1 |

Phase 0 本轮可执行的检查已结束，**业务基线验收未通过，不能声明 Phase 0 全部完成或 Chat 行为通过验证**。

### 授权边界与执行取舍

- 用户已批准方案B，不重新请求方案批准。本轮只执行Phase 0，不执行Schema迁移、生产部署、F1–F5独立修复。
- `test/web_persistence/conftest.py` 的session级 `database_url` fixture在返回API DSN前无条件调用 `migrate(migration_url)`；`db` fixture随后执行 `TRUNCATE … CASCADE`。
- 当前进程的 `MIGRATION_DATABASE_URL`、`APP_DATABASE_URL`、`WORKER_DATABASE_URL` 均未设置。只检查变量是否存在，没有读取或输出凭据，没有加载私有环境文件。
- 因此只运行不会执行fixture的收集，以及现有 `--require-test-services` 前置门禁。门禁在fixture前拒绝；没有连接数据库、调用迁移或清表。
- 不临时绕过迁移、不用Mock替代PG来宣称事务/并发通过、不借用现有业务服务，也不新增fixture机制。本轮不创建新测试环境或安装依赖。
- 这是基线核验和文档记录，没有实施代码或合并操作；保留原工作区，不为此另建worktree或提交原有未跟踪文件。按用户要求和第15节使用本日志，不另建阶段报告文件。

### 文件变更与保留情况

| 文件 | 操作 | 原职责 → 本次职责 |
| --- | --- | --- |
| `docs/architecture/business-flow-persistence-implementation-log.md` | 新增 | 原不存在 → 第15.2节要求的逐任务实施记录，包含本轮报告和证据索引 |

没有修改、移动或删除生产代码、测试、迁移、配置；没有删除代码或引入兼容facade。
蓝图保持原字节内容及原始AS-IS，不勾选尚未完成的P0.1。
原工作区已有未跟踪 `code-analysis/`、`docs/learning/` 和蓝图；均保留。交付前对起始1415个文件逐一比对SHA-256：改动0、丢失0，唯一新增文件为本日志；tracked diff为空。

### 业务流程对照与事务边界

本轮前后调用链完全相同，没有接口变化或代码移动。以下来自源码阅读，**不是本轮PG执行证明**：

1. Web `routes/conversations.py::send_message` → `CommandService.send_message` → 外层 `UnitOfWork` → `_send_message_in_uow`。
2. 事务内先处理幂等及Conversation锁、持久消息重放、单active Run准入和输入授权，再分配消息序号，写用户Message、Run、助手Message及相关文件/资源快照、预算。
3. 同一UoW写入 `start_run` Outbox、构建响应projection及保存幂等结果；正常退出时提交，异常退出时回滚。新Run的root Execution仍由既有数据库trigger创建。
4. 提交后分发仍走既有Outbox路径；本轮未运行分发器或Temporal。
5. Retry仍从 `CommandService.retry_run` 进入，锁Conversation和源Run；复用直接child返回200，新建child返回202，相关助手Message、预算、Outbox及幂等结果使用原同一UoW。

数据库写集、锁顺序、事务所有权和提交位置均没有改动；没有新增commit、SQL writer、RPC或双写。尚未引入SendMessage用例或其他TO-BE层。

### P0.1 选测及关键断言

只选择首个Chat任务直接相关的5个现有业务测试，不运行整个文件或全量后端套件。

| 现有测试（完整node ID见下方命令） | 已读到的业务断言 | 本轮运行情况 |
| --- | --- | --- |
| `test_db_003_send_is_atomic_and_busy` | 首次send生成1个queued Run；再次send抛ConversationBusy；仍只有2条Message、1条Outbox | 收集成功；正文未执行 |
| `test_surface_keys_replay_from_pg_across_command_instances` | 跨Service重放相同body；1 Run/2 Message/1 start_run；无Session；同key变内容冲突；删除响应缓存后变strategy/附件冲突，原输入仍复用Run | 收集成功；正文未执行 |
| `test_independent_surface_callers_race_at_one_pg_admission_slot` | 两个Service同时发送，恰有1个busy；只写1 Run/2 Message/1 Outbox | 收集成功；正文未执行 |
| `test_outbox_failure_rolls_back_admission_message_session_and_run` | Outbox写入抛错后Message/Run/Outbox/预算均为0，消息序号回到0；恢复后原key可成功 | 收集成功；正文未执行 |
| `test_db_008_concurrent_retry_returns_one_child` | 两个不同key并发retry返回相同child，HTTP语义为200/202；真实库仅1个直接child，两条幂等结果为200/202 | 收集成功；正文未执行 |

测试通过CommandService公开业务入口观察结果；未新增镜像测试、目录测试、私有函数调用测试或调用次数断言。
Outbox失败用例复用既有故障注入，业务存储原本仍应为真实隔离PG，不把它归为纯Mock通过。
retry是P0.1明列范围；不扩到Cancel、HTML、QQ receipt、Work或模型预算内核。

### 实际命令、环境与结果

执行目录均为仓库根 `/home/hp/workspace/HpAgent`，使用现有 `.venv`。
导入检查确认pytest、psycopg、temporalio可发现；成功收集证明所选模块可导入，不证明服务可连接或Schema可用。

收集命令（stdout/stderr另以 `tee` 留存）：

```bash
.venv/bin/python -m pytest --require-test-services -q --collect-only test/web_persistence/test_command_service.py::test_db_003_send_is_atomic_and_busy test/web_persistence/test_conversation_commands.py::test_surface_keys_replay_from_pg_across_command_instances test/web_persistence/test_conversation_commands.py::test_independent_surface_callers_race_at_one_pg_admission_slot test/web_persistence/test_conversation_commands.py::test_outbox_failure_rolls_back_admission_message_session_and_run test/web_persistence/test_outbox_and_lifecycle.py::test_db_008_concurrent_retry_returns_one_child
```

实际结果：exit 0，`5 tests collected in 0.03s`。没有运行fixture或测试正文；这不是5项业务通过。

业务执行入口（同样以 `tee` 留存，并设置 `set -o pipefail` 保留pytest退出码）：

```bash
.venv/bin/python -m pytest --require-test-services -q test/web_persistence/test_command_service.py::test_db_003_send_is_atomic_and_busy test/web_persistence/test_conversation_commands.py::test_surface_keys_replay_from_pg_across_command_instances test/web_persistence/test_conversation_commands.py::test_independent_surface_callers_race_at_one_pg_admission_slot test/web_persistence/test_conversation_commands.py::test_outbox_failure_rolls_back_admission_message_session_and_run test/web_persistence/test_outbox_and_lifecycle.py::test_db_008_concurrent_retry_returns_one_child
```

实际结果：exit 4，`no tests ran in 0.03s`。原始错误：

```text
Selected tests require explicit isolated service configuration: APP_DATABASE_URL, MIGRATION_DATABASE_URL, WORKER_DATABASE_URL
```

业务测试数量：**通过0、失败0、跳过0、正文未执行5**；另有1次命令级配置错误。
失败0只表示没有测试正文产生失败，不表示没有既有业务缺陷。缺服务不记为skip或通过。

本次唯一仓库变更是协议要求的文档，按AGENTS.md执行文档/脚本检查：

```bash
make scripts-test PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python
make docs-check PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python
```

实际结果：scripts-test exit 0，79项通过、0失败、0跳过（12.270s）；docs-check exit 0，检查36份Markdown、0错误。文档检查不校验远程URL或锚点，也不执行文档命令。脚本集合为离线脚本测试（可能使用独立临时文件和回环HTTP）；不能替代Chat PG业务回归。

仓库外证据目录：`/tmp/hpagent-phase0-99v7ip6o/`。
其中 `chat-collect.txt`、`chat-preflight.txt`、`scripts-test.txt`、`docs-check.txt` 保存原始命令输出；
`initial-hashes.json` 与 `workspace-preservation.json` 用于比对原有文件，不包含环境变量值。该临时目录不保证跨机器/清理后保留。

### 已有失败、风险与未验证范围

- 已观察到的是运行环境配置阻塞，非Chat断言失败；本轮无法确认既有业务测试是否有失败，不能区分尚未执行的历史缺陷。
- Fixture自动迁移与当前禁止执行Schema迁移的边界冲突，是恢复PG执行前必须解决的条件；不能只补DSN后直接运行。
- 未运行真实隔离PG、API、Temporal、浏览器或真实Provider/QQ。未核验部署Schema/checksum、角色实际权限、历史数据兼容或RPC提交可见性。
- 未运行全量lint/typecheck/test-unit/test-db/test-api/test-temporal、架构门禁、前端检查。没有相应代码或导入改动，不将这些扩大为Phase 0必跑项。
- Work、Scheduler、其他Repository、Pool清理、ABA、公平性、故障矩阵及F1–F5未执行、未修复。不存在本轮引入的生产行为偏差；原有偏差仍待各自范围验证。
- 旧CommandService、私有事务内入口、QQ/Artifact原调用及Repository责任均尚未迁移；未新增临时兼容facade。无需生产回滚Commit。

### 下一阶段前置条件与用户审阅

1. 先审阅本报告。本轮结束后暂停；未获得阶段审阅确认前不进入Phase 1。
2. 补齐显式专用、可丢弃、同库三角色DSN并核验隔离性。当前5项无需Redis、Temporal或File Store服务。
3. 先明确符合迁移禁令的执行方式；现有fixture会调用迁移，当前不能执行。若今后单独允许在专用空测试库回放原有完整迁移链，也仅属于后续授权，绝不推导为生产或Schema变更授权。本轮不请求或代行此授权。
4. 恢复所选5项真实PG测试，记录实际通过/失败，隔离既有失败后，才能宣称Chat动态基线成立并开始P1.1。
5. P1.1开始时再次检查实际接缝；只有已有业务断言无法观察迁移风险才最小补测。P1.2/P1.3及Work/仓储验证留到对应任务前选择，不提前铺开。

用户审阅意见：待审阅；没有把用户对方案B的批准解释为对后续Phase的确认。

### 学习说明

本轮核心概念是“源码覆盖证据、用例收集、动态业务通过”三种证据不能互换；单槽及原子性应由真实隔离PG上的业务结果证明。
最值得阅读的3个文件：

- [Conversation命令](../../src/conversation_domain/commands.py)：沿send/retry观察UoW、幂等、准入和写集。
- [Chat业务回归](../../test/web_persistence/test_conversation_commands.py)：读跨Service重放、并发单槽及Outbox失败回滚的结果断言。
- [数据库fixture](../../test/web_persistence/conftest.py)：确认自动迁移和清表发生在何处，理解为何收集成功不能替代执行。

可独立追踪的真实调用链：Web发送路由 → CommandService.send_message → UnitOfWork → _send_message_in_uow → Message/Run/预算/Outbox及幂等结果 → UoW提交。上述链路本轮未修改。

## 2026-11-06 Phase 0 补执行：真实 PostgreSQL 基线成立

实施时间：2026-11-06 00:48（Asia/Shanghai）。基线仍为 `main` / `9b3d159c59253900f0141bac11036cf14c332152`。本轮没有创建Commit。

### 用户审阅与授权变化

用户已通过上轮报告，并明确“涉及到数据库修改数据的之类的都行，当前不是部署环境”。据此允许为Phase 0建立专用空测试库、回放现有迁移及执行fixture的数据写入/清表；不再沿用上轮的数据库操作阻塞。上轮记录作为当时事实保留，不覆盖历史。

本轮只补齐Phase 0既定5项，不开始Phase 1、不执行F1–F5独立修复、不发布或重启已有服务。

### 任务状态、文件变化与业务对照

| Task ID | 本轮状态 | 证据 |
| --- | --- | --- |
| P0.1 | 完成 | 原定5个现有业务用例在真实隔离PG全部通过 |
| P0.2 | Phase 0覆盖检查完成，无需补测 | 未迁移入口、事务接缝或数据编码，没有新增测试；未来每个迁移任务开始时仍按原条目检查具体缺口 |
| Phase 0 | 完成，等待本轮完整结果审阅 | 动态Chat基线可用，上轮环境阻塞已解除 |
| Phase 1–4 | 未执行 | 无生产业务代码实施 |

唯一仓库变更：向本实施日志追加本节。文件原职责和本轮职责均为第15.2节实施记录；没有新增或移动产品/测试文件、删除代码、引入接口或兼容facade。蓝图和上轮日志记录保持原内容。

重构前后调用链一致：Web发送入口 → CommandService.send_message → 外层UnitOfWork → _send_message_in_uow → Message/Run/资源快照/预算/Outbox/幂等响应 → UoW提交。Retry仍由CommandService.retry_run锁Conversation及源Run、复用或创建唯一直接child。数据库写集、锁顺序和事务边界没有改变；本轮通过结果验证上述选定不变量，不表示已实施新的UseCase层。

### 专用测试环境与迁移证据

- 复用本机已有固定镜像 `postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685`，容器内实际PostgreSQL为16.15；没有拉取镜像或安装依赖。
- 成功轮容器 `hpagent-phase0-558574`、独立网络 `hpagent-phase0-558574-net`；数据库 `hpagent_onebot_test`，宿主回环随机端口 `127.0.0.1:32770`。数据目录为本轮tmpfs，未挂载已有数据卷。
- 复用 `test/fixtures/onebot11-init.sql` 初始化API/Worker角色；三个显式DSN均检查为上述同一库/端口，并实际查询current_database/current_user验证身份。API及Worker角色的superuser/createdb/createrole均为false。
- 在pytest前确认 `hpagent` Schema不存在；由未修改的session fixture调用原 `migrate()`，完整执行001–080原始迁移。测试后查询应用记录为80项，并调用现有 `verify_schema()` 验证与仓库全链checksum一致。
- 没有修改迁移SQL、checksum或迁移机制。fixture仅在本轮专用库写入/清表。
- 没有启动Redis、Temporal、API、File Store、Provider或QQ服务。没有读取私有.env、打印连接凭据或使用已有应用数据库。
- 成功轮及前两次建库尝试的容器/网络均已清理，cleanup命令退出码均为0；数据库随临时数据目录销毁。

### 实际测试命令及结果

从仓库根执行，宿主Python 3.12.16、pytest 8.4.2，复用现有.venv。仓库外启动脚本创建资源、设置显式三角色DSN、设置 `HPAGENT_MIGRATIONS_DIR=/home/hp/workspace/HpAgent/persistence/migrations` 和仓库/src/test导入路径，运行：

```bash
.venv/bin/python /tmp/hpagent-phase0-resume-20261106/run.py
```

其实际pytest命令如下（没有额外选择其他业务测试）：

```bash
/home/hp/workspace/HpAgent/.venv/bin/python -m pytest --require-test-services -vv test/web_persistence/test_command_service.py::test_db_003_send_is_atomic_and_busy test/web_persistence/test_conversation_commands.py::test_surface_keys_replay_from_pg_across_command_instances test/web_persistence/test_conversation_commands.py::test_independent_surface_callers_race_at_one_pg_admission_slot test/web_persistence/test_conversation_commands.py::test_outbox_failure_rolls_back_admission_message_session_and_run test/web_persistence/test_outbox_and_lifecycle.py::test_db_008_concurrent_retry_returns_one_child --junitxml=/tmp/hpagent-phase0-resume-20261106/chat-results.xml
```

实际结果：pytest exit 0，**5 passed in 2.49s**。通过5、失败0、错误0、跳过0、原定集合未执行0。

| 测试 | 本轮实际通过的关键断言 |
| --- | --- |
| test_db_003_send_is_atomic_and_busy | 首次生成1个queued Run；再次发送busy；最终2条Message、1条Outbox |
| test_surface_keys_replay_from_pg_across_command_instances | 跨实例相同响应和单一Run；变更输入冲突；删除响应缓存后仍按持久事实重放/校验 |
| test_independent_surface_callers_race_at_one_pg_admission_slot | 两个真实并发调用恰有1个busy；数据库仅1 Run、2 Message、1 Outbox |
| test_outbox_failure_rolls_back_admission_message_session_and_run | 注入Outbox异常后Message/Run/Outbox/预算均未残留、序号回滚；恢复后同key成功 |
| test_db_008_concurrent_retry_returns_one_child | 两个并发retry复用同一child，响应状态200/202；数据库只有1个直接child，幂等结果状态一致 |

这是公开业务入口与真实隔离PG的组合证据。Outbox异常仍由既有monkeypatch注入；存储和回滚没有使用Mock替代。不新增镜像、目录或私有函数调用测试。

### 本轮遇到的问题及处置

专用容器初次启动时，PostgreSQL官方entrypoint先启动仅Unix socket可用的初始化临时服务，然后停止并启动正式TCP服务。最初的仓库外启动脚本用默认Unix socket的pg_isready，误判临时服务为就绪；随后psql遇到socket不存在，建库失败。

- 第一次失败记录为建库命令exit 2；第二次仅增加诊断重现，stderr及容器启动日志证明了上述先启动/停止时序。两次均未开始pytest，不是业务测试失败。
- 将仓库外脚本就绪探测改为 `pg_isready -h 127.0.0.1`，等待正式TCP服务；只做此一处环境修正后成功。没有修改项目fixture或生产实现。
- 本轮选定集合没有观察到已有业务失败；此结论仅限这5项，不能推导为全仓库无缺陷。上轮缺DSN的exit 4记录仍属当时前置条件失败。

### 文档检查、原文件保留与证据位置

文档/脚本检查按AGENTS.md执行，具体结果见本节末尾验证补记：

```bash
make docs-check PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python
make scripts-test PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python
```

原始输出及临时脚本保存在仓库外 `/tmp/hpagent-phase0-resume-20261106/`：
`chat-tests.txt`、`chat-results.xml`、`run-summary.json`、`attempt1-summary.json`、`attempt2-summary.json`、`attempt2.txt`、`startup-error.txt`、`attempt3.txt`、`docs-check.txt`、`scripts-test.txt`。
该目录是本机临时证据，不提交Git，也不保证跨机器保留。

### 未验证范围、风险与下一阶段前置条件

- 未执行Work、Scheduler、其他仓储、QQ/HTML/Cancel相关PG/API场景、Temporal/replay、全量Python静态/单元、前端/浏览器或真实链路；没有扩大Phase 0业务测试范围。
- 不以这5项证明附件授权全部组合、HTML预算、模型预算内核、Outbox租约ABA、数据库断电耐久性、全量权限、吞吐/公平性、历史非空库升级或真实部署通过。F1–F5保持独立范围。
- 没有生产代码变更或新兼容facade；原CommandService、QQ/Artifact私有入口及Repository责任尚未迁移。回滚只涉及撤销本节文档追加，不存在业务回滚Commit。
- P1.1所需的首批Chat动态基线已建立。下一步先审阅本轮Phase 0完整结果，再按协议进入P1.1；开始时核查实际接缝缺口，必要时才最小补充业务断言。后续环境按任务重新建立，不保留本轮已清理的DSN供复用。
- 用户已通过上轮报告并解除本轮数据库操作限制；本轮完成结果尚待审阅。未将该授权扩展为自动开始后续阶段。

### 学习说明

本轮补齐了从“收集可用”到“隔离PG上的行为成立”的证据链。值得阅读：`test/web_persistence/test_conversation_commands.py` 的重放/并发/回滚断言，`test/web_persistence/test_outbox_and_lifecycle.py` 的唯一retry child断言，以及 `test/web_persistence/conftest.py` 的迁移和清表生命周期。真实业务调用链及事务所有权仍与上一节一致，没有位置移动或接口变化。

### 最终验证补记

- `make docs-check`：exit 0，36份Markdown、0错误。
- `make scripts-test`：exit 0，79项通过、0失败、0跳过，14.593s。此为离线脚本证据，与5项PG业务回归分别计数。
- 起始1416个文件比对：仅本实施日志变化，另外1415个文件SHA-256不变，丢失0、新增0；旧日志全文仍为本文件原样前缀。tracked diff为空，蓝图及既有未跟踪目录未变。
- Docker查询确认本轮Phase 0容器和网络均已不存在；原始比对结果保存为仓库外 `workspace-preservation.json`。
- 结果报告已完成，停止于Phase 0；下一阶段未实施，无新增Commit。



## 2026-11-06 Phase 1：Chat 应用服务实施

用户以“下阶段”确认进入Phase 1。本轮只执行P1.1–P1.3，完成后暂停。沿用专用测试库操作授权；F1–F5及生产部署仍不在范围。使用executing-plans、test-driven-development和verification-before-completion工作流；批准蓝图及“先复用行为断言”的用户要求优先：纯移动使用已有回归的绿→绿证据，不人为制造失败或新建镜像测试。

实施工作区仍为原路径，切换至独立分支 `codex/phase1-chat-usecases`，保留所有原有未跟踪文件；不创建需要复制原有蓝图/日志的另一工作区。起始HEAD为 `9b3d159c59253900f0141bac11036cf14c332152`。仓库外证据目录 `/tmp/hpagent-phase1/`，不提交原始运行输出。

### P1.1 提取SendMessage

状态：完成；本任务独立提交，精确Hash在本阶段末尾汇总。

文件职责迁移：

- `application/chat/commands.py`：新增冻结SendMessageCommand及稳定message_request_id规则；旧命令模块保留该符号兼容导出。内部fixed_html原dict形状保留，不改变HTTP契约。
- `application/chat/send_message.py`：接收原_send_message_in_uow完整幂等、锁/准入、授权、消息/Run/预算/Outbox写集及返回值编排；execute拥有UoW/有限事务重试，execute_in_uow只借用事务。
- `application/chat/projection.py`：原响应及快照组装原样迁入ChatProjection，保留Chat/Work取消快照差异、NULL、时间格式、预算及root Execution字段。
- `application/chat/dependencies.py`：具体仓储、原effective配置、幂等记账及预算/Outbox协作者的共享组装；不定义新Protocol，不收拢SQL，不改变已有Repository写入责任。
- `application/chat/__init__.py`：包入口。
- `conversation_domain/commands.py`：Send转发至新用例，保留旧签名、仓储访问属性、可更新agent_policy与暂存私有事务内兼容入口；Retry/Cancel等待P1.2。

前：调用者→CommandService.send_message→_send_message_in_uow；后：调用者→旧facade→SendMessage.execute→execute_in_uow→原仓储/策略。位置变化，无业务步骤变化；UoW提交/异常回滚、锁顺序、写集及提交后分发不变，没有新增RPC或独立连接。原编排及projection从旧模块移除，无双份实现；QQ/Artifact暂经兼容转发。

实际验证：迁移前和迁移后分别运行 `/tmp/hpagent-phase1/run-pg.py p11-before|p11-after test/web_persistence/test_command_service.py test/web_persistence/test_conversation_commands.py`（解释器为仓库.venv）。每轮为新空PG16、三角色、完整80项原迁移，checksum验证后清理资源。两轮分别10 passed/4.51s与10 passed/6.68s，失败0、跳过0，断言覆盖创建重放/冲突、输入绑定、跨实例持久幂等、并发单槽、账号边界、事务内准入及Outbox异常后的写集回滚。`python -m mypy src/application/chat src/conversation_domain`：12个文件无错误；本次改动文件Ruff通过。未新增测试。

最值得读：send_message.py（用例主干）、projection.py（响应稳定性）、原test_conversation_commands.py（业务结果）。剩余旧模块为Retry/Cancel、QQ及Artifact；下一任务保留source锁、retry直接child、取消Chat/Work及外层UoW。最终项目级验证及精确命令见阶段末尾。

### P1.2 提取Retry/Cancel

状态：完成；P1.1 Commit为 `8041ec86`，本任务Hash在阶段末尾汇总。

文件变化：`application/chat/commands.py`新增冻结RetryChatRunCommand；`application/chat/retry_run.py`接收原retry完整编排；`application/run/{__init__,commands,cancel_run}.py`新增共享CancelRun输入及独立/事务内用例；`conversation_domain/commands.py`移除原retry/cancel实现并转发。旧代码是移动而非重写；没有删除旧模块、SQL或业务分支。没有新Protocol或合并Chat/Work创建准入。

前链：旧Service.retry_run/cancel_run直接编排；后链：旧签名→RetryChatRun.execute / CancelRun.execute；Workspace→旧_cancel_run_in_uow兼容签名→CancelRun.execute_in_uow。独立入口继续拥有同一UoW/有限重试，借用端口不提交。源Run锁、Conversation先锁顺序、唯一直接child、7天幂等、普通retry当前policy、HTML源snapshot、当前输入授权检查、取消审批及Outbox写集原样保留。Work Run取消仍是attempt控制，不排除Work，也不自动停止Work。

验证命令：`.venv/bin/python /tmp/hpagent-phase1/run-pg.py p12-before|p12-after <p12-cases.txt中逐项node>`；完整实际pytest命令及各项结果在对应run-summary.json/chat-tests.txt，选择列表位于仓库外p12-cases.txt。14个node为并发retry、queued取消、start/cancel并发收敛、取消审批、附件撤权后retry、当前policy冻结、Work attempt-only取消、HTTP policy重放及6项send/retry/cancel HTTP行为。前后分别14 passed/7.20s及14 passed/6.54s，失败0、跳过0。使用新建三角色空PG、80项原迁移，API通过实际TestClient/lifespan组合，不使用Temporal或真实Provider；两轮资源均已清理。`mypy src/application/chat src/application/run src/conversation_domain`：16个文件无错误，改动文件Ruff通过。

未新增测试；尚缺HTML retry源snapshot的直接行为断言，留到P1.3的HTML入口检查一起补齐。最值得读retry_run.py、cancel_run.py及test_persistent_policy_pg.py的Chat policy用例。下一任务切换QQ与HTML的公开事务内端口，保持savepoint、receipt和Artifact默认预算，不修F1。

### P1.3 接回QQ与Artifact

状态：实施及相关回归完成；等待本阶段最终审查记录。本任务独立Commit在阶段末尾汇总。

文件迁移及职责：

| 文件 | 原职责 → 本轮职责 |
| --- | --- |
| `application/surfaces/{__init__,conversation}.py` | 新增；接收原surface identity/binding/receipt完整外层事务，直接调用SendMessage/CancelRun公开execute_in_uow |
| `conversation_domain/surface_commands.py` | 原完整实现 → 保留SurfaceConversationCommands、IdentityNotBound、stable_key兼容导出 |
| `application/artifacts/{__init__,commands}.py` | 新增；ArtifactCommands持有原create_artifact/create_version写编排，依赖注入SendMessage；legacy_artifact_sender显式保持原effective预算默认值 |
| `web_artifacts/queries.py` | 新增；原Service中的只读查询、事务内查询及DTO投影原样迁入ArtifactQueries，仍在web_artifacts包，SQL责任未收拢 |
| `web_artifacts/services.py` | 原写/读混合实现 → 查询兼容facade及两个原签名写命令转发 |
| `web_artifacts/__init__.py` | eager ArtifactService导出 → 同名lazy兼容导出，解决新应用命令读取queries时的包初始化循环 |
| `web_api/lifespan.py` | 原现场默认ArtifactService组装 → 显式注入legacy_artifact_sender；不使用Web Chat预算覆盖Artifact预算，不修F1 |
| `test/web_persistence/test_web_artifacts.py` | 原业务测试 → 在原准入测试补legacy预算与busy外层全回滚断言，另加一个HTML retry冻结snapshot行为用例 |
| `docs/architecture/{overview,execution}.md`、`docs/reference/repository-layout.md` | 原入口说明 → 反映现有Chat/Run/Surface/Artifact用例及兼容入口；原AS-IS蓝图不改 |

本任务用ArtifactCommands两个明确公开方法承载原写用例，不额外套两层单方法类。只读代码在原web_artifacts包单列以复用原DTO，避免写用例反调旧Service私有入口；未新建Repository或Protocol。SendMessageCommand继续携带原fixed_html dict，避免先改变历史JSON协议；冻结dataclass不宣称深冻结dict。

业务前后对照：

- QQ前：Ingress→旧surface→外UoW+savepoint→CommandService私有send/cancel；后：Ingress→兼容导出→application.surfaces完整同一外UoW+savepoint→SendMessage/CancelRun.execute_in_uow。身份、路由幂等、origin、拒绝receipt及Work控制分支原样移动。
- Artifact前：Service外UoW→Artifact/Version→_enqueue现场CommandService(None)→私有send；后：Service转发→ArtifactCommands外UoW→原Artifact/Version写集→注入SendMessage.execute_in_uow。parent不可变文件、当前撤销检查、UUID5的operation/account/key命名空间及fixed_html形状不变。
- 这些步骤只有位置/调用端口变化。没有新增commit/连接、事务重试层或RPC；借用UoW的内层不独立提交。新Service仍保留旧公开读写方法。原代码只在新位置保留一份，无SQL双写、root创建仍由原trigger负责。
- 原Workspace的三个_cancel_run_in_uow调用暂留兼容转发；不扩做Phase4导入收敛。Chat私有send兼容签名仍保留，但QQ/Artifact已无私有调用。

测试取舍及实际结果：

- 移动前QQ完整现有11项及Artifact原4项（含参数化）：15 passed/8.75s，失败0、跳过0。
- 先补最小直接行为断言，在入口移动前运行原Artifact准入测试及新增HTML retry测试：2 passed/2.98s；这是已存在行为的基线，未人为引入RED或新增镜像/目录/私有调用测试。
- 移动后同两文件：16 passed/8.73s，失败0、跳过0；新增1项，原准入测试增强不增加用例数量。
- QQ关键实际断言：重投只生1个Run、重启重建PG事实、不同room/bot/thread归属、busy receipt可重放、取消重放不针对后来Run、撤权禁止重放、Outbox失败连binding/origin/Message/Run一起回滚、并发首次送达同一binding/单槽。
- Artifact关键实际断言：重放/冲突、不创建Work/跨账号404、queued/running取消保留provenance并拒绝迟到publish；补充busy拒绝时Artifact/Version/Message/Run/预算/Outbox/幂等命令数量和消息序号均不变；legacy预算为observe/file-p0-v1、model_calls20、reserve1024；HTML retry在当前policy改变后仍保留source的fixed_workflow/artifact_html/NULL agent_strategy及完全相同input_snapshot。

新增导入问题及修复：第一次make test-unit发现原包eager导出与新application→queries依赖形成循环，导致两个已有独立进程导入测试失败。按systematic-debugging追踪到web_artifacts.__init__→services→application.artifacts.commands→queries→包初始化；改为lazy同名导出后，原失败测试test_canonical_imports_do_not_load_retired_runtime_owners和test_web_api_import_does_not_load_agent_runtime均通过（2 passed/3.86s）。没有新增import镜像测试或删除兼容导出。

学习路径：先读application/surfaces/conversation.py的外层savepoint，再读application/artifacts/commands.py的事务与sender注入，最后读增强的test_web_artifacts.py看实际业务结果。剩余Work用例与Repository/UoW收拢属于Phase2/3；本轮不开始。

### Phase 1 最终验证与审查

本阶段完成P1.1/P1.2/P1.3，停止于Phase 1，尚未进入Phase 2。独立审查员对完整基线差异和未提交P1.3文件做只读审查：无剩余Critical/Important/可操作Minor；另在7个新进程核验新Artifact命令、旧package/Service导出、SendMessage、CancelRun、Surface和lifespan均可导入。审查未替代集成测试。审查注意到queued取消曾省略一次冗余Run复读；为保持原SQL读取步骤，本轮最终仍恢复原get_for_account再finalize，之后重新运行40项相关回归及架构检查。

实际命令与最终结果（均从仓库根，未把重复套件数字相加为唯一用例数）：

| 命令 | 实际结果 | 证据边界 |
| --- | --- | --- |
| `make lint typecheck PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python` | exit0，Ruff通过；mypy95个文件无错误 | 项目既有静态范围 |
| `.venv/bin/python -m ruff check src/web_artifacts src/application/chat src/application/run src/application/artifacts src/application/surfaces` | exit0 | 补检查Makefile未全覆盖的Artifact及新文件 |
| `.venv/bin/python -m mypy src/application/chat src/application/run src/application/artifacts src/application/surfaces src/web_artifacts src/conversation_domain` | exit0，28个文件无错误 | 显式覆盖全部新增应用模块及Artifact |
| `make test-unit PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python PYTEST_ARGS='-q --junitxml=/tmp/hpagent-phase1/unit-final.xml'` | exit2；1442 passed、1 failed、8 skipped、711 deselected，50.83s | 未宣称全绿；基础设施标记被排除，跳过不算通过 |
| `make architecture-check PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python PYTEST_ARGS='--junitxml=/tmp/hpagent-phase1/architecture-final.xml'` | exit0，registry pass=true，无注册/sandbox违例；177 passed/6.55s | 离线注册/import/sandbox，不是真实Temporal服务执行 |
| `make docs-check scripts-test PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python` | exit0；36份Markdown、0错误；79项脚本通过/12.785s | 离线文档及脚本，不代替PG/API |
| `git diff --check` | exit0 | 补丁空白检查 |

最终40项PG/API准确执行方式：先设置仓库外runner管理的专用三角色DSN、HPAGENT_MIGRATIONS_DIR和PYTHONPATH；runner本身创建全新PG16容器、回环随机端口、独立网络、tmpfs数据并检查库/角色，绝不复用已有应用服务；将以下选择项传给`.venv/bin/python /tmp/hpagent-phase1/run-pg.py phase1-final-preserved-read`。其内部使用绝对.venv解释器执行`-m pytest --require-test-services -vv <以下node> --junitxml=/tmp/hpagent-phase1/phase1-final-preserved-read/chat-results.xml`：

```text
test/web_persistence/test_command_service.py
test/web_persistence/test_conversation_commands.py
test/web_persistence/test_outbox_and_lifecycle.py::test_db_008_concurrent_retry_returns_one_child
test/web_persistence/test_outbox_and_lifecycle.py::test_db_019_cancel_queued_before_start_is_terminal
test/web_persistence/test_outbox_and_lifecycle.py::test_db_020_start_and_cancel_converge_without_duplicate_execution
test/web_persistence/test_file_action_approvals.py::test_run_cancel_atomically_cancels_pending_approval_and_enqueues_wake
test/web_persistence/test_workspace_v41_p2.py::test_explicit_attachment_revocation_blocks_read_and_failed_retry
test/work_domain/test_persistent_policy_pg.py::test_chat_admission_replay_and_explicit_retry_freeze_new_policy
test/work_domain/test_foundation.py::test_failed_run_retains_responsibility_and_cancel_is_attempt_only
test/web_api/test_admission_policy_api.py
test/web_api/test_phase_b_api.py::test_api_003_004_005_send_idempotency_and_busy
test/web_api/test_phase_b_api.py::test_api_007_cancelled_run_cannot_be_retried
test/web_api/test_phase_b_api.py::test_api_rejects_retry_while_run_is_running
test/web_api/test_phase_b_api.py::test_api_rejects_retry_after_uncertain_side_effect
test/web_api/test_phase_b_api.py::test_api_008_running_cancel_reaches_cancelled
test/web_api/test_phase_b_api.py::test_b05_failure_then_retry
test/web_persistence/test_qq_canonical_ingress.py
test/web_persistence/test_web_artifacts.py
```

最后实际结果：**40 passed in19.38s，exit0；失败0、跳过0、选择集合未执行0**。两种独立新库最终轮（恢复冗余读前后）均通过；均应用原始80项迁移并通过verify_schema checksum核验，容器/网络cleanup均exit0。所有数据库fixture使用真实PG；API是TestClient加真实PG及既有Fake执行器，HTML生成/QQ入站使用已有离线夹具，不能表述为真实Provider/QQ或Temporal通过。

已有失败核验：`test/test_memory_quality_config.py::test_snapshot_tracks_model_selection_but_not_main_recall_instruction`在全量单元中仍失败，修改MINIMAX_STANDARD_MODEL未改变hash。没有修改memory/config或该测试。在仓库外由git archive导出的原始基线9b3d159c中，独立默认环境运行该测试1 passed；设置非敏感合成覆盖`HINDSIGHT_API_LLM_MODEL=phase1-fixed-fixture`后同一断言1 failed（exit1）。旧实现model_identities本来优先该覆盖变量，当前私有环境文件也设置该变量（仅核存在，未输出值）；因此这是可在原代码复现的环境敏感失败。本轮不修改配置优先级、加载逻辑或测试以消除它，也不把全量单元报告为通过。

8项跳过分别为5项需要独立Hindsight服务/显式opt-in的契约或就绪场景，以及3项缺少nsjail二进制的用例。711项是postgres/temporal标记等被单元命令排除的场景，不记成通过或skip。

工作区保护：起始1416个文件逐项SHA-256核验，仅本阶段列出的10个原文件发生预期变化，其余1406个原文件未变，丢失0、无范围外修改；Phase0日志全文仍为原样前缀。原有未跟踪code-analysis/、docs/learning/及蓝图未修改或加入本阶段提交。新增文件仅为本阶段应用模块及Artifact查询文件。原始运行输出、历史基线导出和hash清单留仓库外，不提交。

未验证/未实施：未跑全量test-db/test-api、真实Temporal/replay/kill、前端/浏览器、真实QQ/Provider、性能、数据库断电耐久性或非空历史库升级；没有部署、重启已有服务或执行F1–F5修复。Work接受/调度/准入及Repository/UoW收拢均尚未实施。现有兼容facade和Workspace私有取消转发保留，不因目录整齐删除；legacy Artifact预算差异仍存在。

审查暂不评价的范围及执行方决议：F1–F5、部署配置、真实Provider/QQ、Schema历史升级、记忆模型配置行为及更广Work/Scheduler/Repository设计均保持本阶段范围外，因为本轮未修改对应行为；代价是不能将本阶段证据外推为这些能力已验收。没有遗留可操作Minor建议需要夹带实施。

下一阶段前置条件：用户审阅Phase1实际分层、上述已有单元失败及测试边界后再进入Phase2；迁移Work时重新选择其相关PG/策略用例，按实际接缝判断最小补测，不预先扩测。原有公开facade仍可按同一Schema运行；必要回滚以本阶段单职责Commit逐个revert，不回填业务数据或修改历史迁移。

### Phase 1 Commit与暂停记录

| Task ID | Commit | 状态 |
| --- | --- | --- |
| P1.1 | `8041ec8667e8ab3e0e5a9e83254c4f27a29b3990` | 完成 |
| P1.2 | `a529cb37c4e99d678825c4291aa89ef03b2e599e` | 完成 |
| P1.3 | `d829c68a08c74ceb5ccc4bc2958ee4ae64a8197a` | 完成 |

P1.3还恢复P1.2迁移时省略的原冗余Run读取，最终40项回归针对恢复后的版本。上述为本阶段实现Commit；本表本身的后续文档提交不改变业务实现。分支为 `codex/phase1-chat-usecases`，未合并、未推送。用户对Phase0的审阅已通过，Phase1报告待审阅，Phase2未执行。

## 2026-11-06 Phase 2：Work应用服务与准入

用户“继续下阶段”确认进入Phase2。本阶段限P2.1–P2.4，完成后暂停。起始Commit `43819771`；分支`codex/phase2-work-usecases`从Phase1结果继续，保留原工作区。使用executing-plans及verification-before-completion；延续用户批准的已有行为断言优先取舍，纯移动以现有回归绿→绿验证，不增镜像测试或预建故障矩阵。专用数据库迁移/数据清理授权仍有效，只回放原始001–080链，不新建Schema变更或执行部署/F1–F5修复。

证据及临时脚本放仓库外`/tmp/hpagent-phase2/`。每轮PG测试使用新容器、独立网络、回环随机端口、tmpfs空库及migration/API/worker三角色，确认空Schema后由原fixture迁移，核验80项checksum并清理本轮容器/网络。不连接已有应用库，不输出凭据。

### P2.1 Accept与读模型

状态：完成；本任务独立Commit在阶段末尾列出精确Hash。

文件变化：新增`application/work/{__init__,commands,accept_work,queries,dependencies}.py`，分别提供包入口、冻结Accept/Advance输入、AcceptWork主干、原snapshot/get/list/records、具体仓储/配置及幂等/链接协作者。`work_domain/commands.py`保留原公开签名和外围revise/control/link/integration，通过兼容转发委派Accept及读模型；`work_domain/errors.py`接收原WorkConflict，旧模块同名导出保留，避免应用用例依赖旧命令facade。仓储SQL未独立收拢，仍留后续Phase3。

前：WorkCommandService.accept→原UoW内claim/校验/SQL；后：旧facade→AcceptWork.execute→同一原UoW内完整主干。公开execute_in_uow借用外部事务，不另开连接、提交或重试。原title/Requirement准备仍在独立事务打开前；claim先于新写校验、来源归属、账号接受上限advisory锁、Work预算、提醒目标、requirement、link、accepted事件/通知/Outbox、inbox、保存门禁、schedule和条件wakeup顺序均保留。Accept不创建Run或聊天Message。查询投影字段、排序、分页、来源、schedule/progress/baseline/integration保持原形状。删除的是旧位置重复主干/查询/异常定义，新位置保留一份，没有删除业务分支或兼容模块。

测试变化仅为已有业务测试：`test/work_domain/test_foundation.py`的原两种capability接受测试增加Run数0、Message数0断言；`test/web_api/test_work_foundation.py`、`test/work_domain/{test_completion_contract,test_save_admission}.py`的历史夹具校验器patch路径迁到新Accept实现；两个原离线legacy replay测试按accept/revise选择实际UoW/claim注入位置，原返回及未校验断言全部保留。没有新增私有调用测试。

命令与结果：`.venv/bin/python /tmp/hpagent-phase2/run-pg.py p21-before test/work_domain/test_foundation.py test/work_domain/test_time_policy.py test/web_api/test_work_foundation.py`：35 passed/15.63s。首次迁移后同组34 passed/1 failed，原因是历史HTTP夹具仍patch旧校验器，实际新入口正确拒绝了旧criteria；不是已接受数据行为变化。更新注入点后，`p21-fixed`加上现有completion_contract/save_admission两文件：56 passed/32.07s，失败0、跳过0。既有离线generic_completion_contract/workspace_save_policy：14 passed/0.41s。Ruff及原路径mypy19文件通过；另用`MYPYPATH=src ... mypy --explicit-package-bases --follow-imports=silent src/application/work`核验真实导入身份，5个新模块通过。扩大到全部旧work_domain时暴露既有save_policy的UUID|None类型问题，未夹带修改；最终静态范围另记。

最值得读accept_work.py、queries.py和增强的test_foundation.py。下一步迁纯策略及快照，再迁Admit/Advance；旧revise/control暂留facade，WorkRepository.event的通知/Outbox副作用未拆。没有业务数据回滚需求；必要时revert本任务Commit，不清除pending wakeup或预算。

### P2.2 纯策略及快照构造

状态：完成；P2.1 Commit为`a662c293`，本任务精确Hash在阶段末尾汇总。

文件变化：`run_domain/strategy.py`接收原StrategySnapshot/StrategyRegistry/WorkExecutionPlanner的纯实现，`orchestration/execution_strategy.py`保留三符号兼容导出；`run_domain/creation.py`提取原Work基础input_snapshot组装，`run_domain/admission.py`改为调用上述纯入口。daily baseline、artifact_refs及当前授权等数据库读取仍在原事务中，不放入纯builder。

前：admit→orchestration planner→内联snapshot；后：admit→run_domain纯planner/builder。策略选择、版本1、未知能力拒绝、generic-only agent_policy、deliverables缺省/NULL、wakeup字符串化、checkpoint及requirement字段和JSON序列化保持一致；Run SQL、epoch、trigger和预算均未改变。不修改历史snapshot，也不新增回填。

命令：`.venv/bin/python -m pytest -q test/test_work_execution_strategy.py test/test_persistent_policy.py`：22 passed/0.26s。真实隔离PG的`run-pg.py p22-after test/work_domain/test_execution_strategy.py test/work_domain/test_persistent_policy_pg.py -m 'not temporal'`：14 passed/15.16s，失败0、跳过0，覆盖确定性提醒、daily恢复/合并、timing revision、冻结brief、策略/current policy及旧snapshot读取。新库80项迁移/checksum及清理均成功。改动文件Ruff通过；全阶段注册/sandbox门禁在最终结果统一记录。不新增测试，因为现有策略/冻结策略断言已观察本次移动。

最值得读run_domain/strategy.py、creation.py和test_persistent_policy_pg.py。尚未实施Admit/Advance及PG调度；下一任务保持容量/预算/保存门禁的原读取顺序与显式/Scheduler错误映射。

### P2.3 Admit与Advance

状态：完成；P2.2 Commit为`8790214c`，本任务Hash在阶段末尾汇总。

文件迁移：`application/work/admit_work_run.py`接收原原子admit完整写集，提供公开事务内端口及冻结WorkAdmissionContext/Result；`work_domain/admission.py`承载纯facts→decision及累计预算判断；`run_domain/admission.py`保留原签名/返回Run或None的兼容函数；`application/work/advance_work.py`接收原显式Advance主干，旧WorkCommandService.advance转发。`run_domain/creation.py`另接收原纯预算limits构造，数值/默认项不变。没有创建新Repository、修改UoW或迁移SQL。

前：显式Advance/Scheduler→原admit_work_run；本任务后：显式Advance→AdmitWorkRun.execute_in_uow，旧Scheduler仍经兼容函数到同一端口。Core只有事务内接口，不提交、另开连接或重试调用者。原Run INSERT、Work pointer/epoch、wakeup consumed/superseded、Run预算、资源snapshot及research save intent、Outbox均原顺序同事务。Pure判断按原progress→save→capacity→budget的I/O顺序逐步接收facts，不提前读取后面的门禁，不将容量票据/三层模型预算与协调容量混合。Result.reason是内部门禁类别，外部202及错误原因仍由原调用方读取当前Work并映射。

实际改变的接缝：Context.bind_in_uow会在原调用方已锁定的Work上重入FOR UPDATE，重读当前requirement，并锁内核验owner、revision、epoch、row_version/coordinator及pending wakeup。不能以传入dict声明持锁；不新增事务范围、全局容量锁或改变原容量计数顺序。新增读取不等同数据库writer收拢；现有两个入口仍分别检查account、continuation、due/unknown effect。progress保护路径仍允许旧兼容函数传None wakeup并返回None；真正写Run前必须有pending wakeup。

最小补测：在现有foundation文件新增一个公开事务内业务用例，捕获旧Work/requirement后修订，尝试用旧context消费新revision的pending wakeup；断言Work不变、Run数0、wakeup仍pending且无Run，随后当前Advance能生成revision2的Run。先运行时因新公开入口尚不存在1 failed；实现后该用例随现有回归通过。未新增私有调用/目录/镜像测试，未创建逐SQL故障矩阵。

实际命令：`run-pg.py p23-before test/work_domain/test_progress.py test/work_domain/test_save_admission.py test/web_persistence/test_durable_review_regressions.py::test_admission_failure_is_local_and_pending_wakeup_recovers -m 'not temporal'`：32 passed、1 deselected/44.81s。迁移后加foundation文件运行同选择：58 passed、1 deselected/46.79s，失败/跳过0；包括并发准入、pointer/owner、revision fence、保存权限重查、budget不能靠resume/advance重置、坏Work的真实PG错误savepoint隔离及pending恢复。新模块按真实import身份mypy8文件通过，Ruff通过。纯预算limits后移仍需最终统一回归验证，未把这次格式/位置调整表述为额外独立通过。

学习入口：admit_work_run.py看事务写集和锁内绑定；work_domain/admission.py看无I/O判断；advance_work.py看显式门禁与202/冲突映射。外围revise/control仍在旧facade，SQL仍未收仓；下一步切Scheduler到同一公开事务内端口，保持批次savepoint。
