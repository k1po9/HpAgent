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

### P2.4 PG调度用例

状态：实现及选定回归完成；独立Commit与最终审查结果在阶段末尾汇总。P2.3 Commit为`70777785`。

新增`application/work/evaluate_schedule.py`与`dispatch_due_work.py`；原WorkScheduleService.run_once的评估/occurrence事务和提交后dispatch委派移入EvaluateSchedule，dispatch_due完整批次事务移入DispatchDueWork。`orchestration/work_schedule.py`保留旧run_once/dispatch_due/occurrence/_occurrence签名及原进程循环。Worker组合根仍注入同样database/budget_mode/agent_policy给兼容服务，不需另建运行时对象树。

前：WorkScheduleService.run_once（原外层retry）→评估事务提交→dispatch_due（原内层retry）→批次事务/savepoint→admit函数；后：旧服务转发→EvaluateSchedule.execute（同一外层retry范围）→评估事务提交→DispatchDueWork.execute（同一内层retry）→同一批次事务/savepoint→AdmitWorkRun.execute_in_uow。没有在两个事务之外另套retry或合并事务，也没有改变原ready/at_time/retry_after、due、unknown effect、account检查位置。原SKIP LOCKED、tenant_position/last_admitted_at/work_id排序、最新revision/wakeup选择、capacity_turn、成功event、已知错误blocked映射及未知异常保留pending逻辑均原样移动。Occurrence的去重、daily newest coalesce、disabled/old-version skipped和next_due逻辑保留。

原源码只在新位置保留一份，旧模块缩为兼容服务/循环；没有删除注册/历史输入。两个新用例继续用原logger名称，避免迁移顺带改变诊断类别。`test/web_persistence/test_durable_review_regressions.py`仅将已有savepoint故障注入从旧模块admit函数改到新AdmitWorkRun公开事务内端口，仍注入真实PG除零，原“健康Work推进、坏Work无coordinator、移除故障后pending恢复”断言全部保留。

命令：`.venv/bin/python /tmp/hpagent-phase2/run-pg.py p24-after <p24-cases.txt节点> -m 'not temporal'`：20 passed/13.90s、失败0、跳过0。选择现有execution_strategy文件及durable_review_regressions中的retry-chain、倒退时钟epoch、单Work失败恢复、进程循环异常恢复、ready去重/最新Work和等待态不自动运行六项。全新隔离PG及原80项迁移/checksum/清理成功。新应用模块、纯准入规则及新run builder/strategy按实际import身份mypy12文件通过；项目lint/defaultmypy99文件通过；注册/sandbox及177个架构用例通过。

没有新增调度测试：两事务分界和恢复路径未改，现有occurrence/coalesce/old-callback/失败恢复断言已覆盖此次移动。最值得读evaluate_schedule.py的提交分界、dispatch_due_work.py的逐Work savepoint和原durable_review_regressions.py的PG故障恢复结果。剩余SQL writer、事件通知编排、UoW责任收敛属于Phase3，本阶段不开始。

### Phase 2 最终证据与阶段审阅

完成核验时间：2026-11-06 06:33:32 CST。P2.1–P2.4实现完成，停止于Phase2，不进入Phase3。

独立审查结论：无Critical/Important/可操作Minor。审查员对Phase1基线至本阶段全部源码（含未提交P2.4）做只读AST核查：外围revise/control/link/_cancel_coordinator/integration完全一致；读投影、claim/locked/link仅移动；Admit原15条SQL文本及执行顺序一致，Dispatch原5条SQL一致；occurrence/_occurrence完全一致；Evaluate保留原run_once外层retry及Dispatch内层retry的两个事务。审查未替代实际测试。新context bind是既有Work/wakeup行锁的同事务重入与权威重读，正常入口未改变锁顺序；增加的数据库读取开销未做性能测量，不宣称吞吐改善。

最终实际命令与结果：

| 命令 | 结果 | 环境/范围 |
| --- | --- | --- |
| `make lint typecheck PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python` | exit0；Ruff通过，mypy99文件无错误 | 仓库既有检查范围 |
| `MYPYPATH=src .venv/bin/python -m mypy --explicit-package-bases --follow-imports=silent src/application/work src/work_domain/admission.py src/run_domain/creation.py src/run_domain/strategy.py` | exit0，12文件无错误 | 新模块真实import身份检查，不外推到全部旧模块 |
| `make architecture-check PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python PYTEST_ARGS='--junitxml=/tmp/hpagent-phase2/architecture-final.xml'` | exit0；registry pass=true、无sandbox违例；177 passed/5.07s | 离线注册/import/sandbox，非真实Temporal |
| `make test-unit PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python PYTEST_ARGS='-q --junitxml=/tmp/hpagent-phase2/unit-final.xml'` | exit2；1442 passed、1 failed、8 skipped、712 deselected，49.11s | 不宣称全绿；postgres/temporal被选择规则排除 |
| `.venv/bin/python -m pytest -q test/test_work_execution_strategy.py test/test_persistent_policy.py` | exit0，22 passed/0.26s | 纯策略及冻结policy |
| `make docs-check scripts-test PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python` | exit0；36份Markdown、0错误；79脚本通过/13.438s | 离线文档/脚本，不代替PG |
| `git diff --check` | exit0 | 补丁检查 |

最终PG/API命令：将以下节点作为参数传给`.venv/bin/python /tmp/hpagent-phase2/run-pg.py phase2-final`，末尾加`-m 'not temporal'`。runner显式设置同一专用空库三角色DSN、HPAGENT_MIGRATIONS_DIR、PYTHONPATH，用仓库绝对.venv解释器运行`-m pytest --require-test-services -vv <这些node> -m 'not temporal' --junitxml=/tmp/hpagent-phase2/phase2-final/chat-results.xml`。

```text
test/work_domain/test_foundation.py
test/work_domain/test_time_policy.py
test/web_api/test_work_foundation.py
test/web_api/test_work_save_admission.py
test/work_domain/test_completion_contract.py
test/work_domain/test_save_admission.py
test/work_domain/test_progress.py
test/work_domain/test_execution_strategy.py
test/work_domain/test_persistent_policy_pg.py
test/web_persistence/test_durable_review_regressions.py::test_retry_chain_ends_at_success_and_new_occurrence
test/web_persistence/test_durable_review_regressions.py::test_retry_chain_follows_control_epoch_when_database_clock_moves_backwards
test/web_persistence/test_durable_review_regressions.py::test_admission_failure_is_local_and_pending_wakeup_recovers
test/web_persistence/test_durable_review_regressions.py::test_schedule_loop_recovers_iteration_failure_and_propagates_cancel
test/web_persistence/test_durable_review_regressions.py::test_ready_wakeup_is_deduplicated_and_obeys_latest_work
test/web_persistence/test_durable_review_regressions.py::test_success_does_not_autorun_waiting_work
```

实际结果：**98 passed、1 deselected，74.25s，exit0；失败0、跳过0、选中集合未执行0**。1项deselected为原progress文件的Temporal用例，未执行真实Temporal。本轮最终容器`hpagent-phase2-1531285`、独立网络同名前缀、回环随机端口32788、tmpfs库hpagent_onebot_test。原始80项迁移/checksum验证通过；容器和网络cleanup均exit0。所有PG轮均使用本轮新建资源；未触及既有服务或数据库。API为真实TestClient/lifespan+PG，执行/模型/渠道使用原测试夹具；不表述为真实Provider、QQ或部署通过。

已有失败/限制区分：

- 全量单元唯一失败仍为`test_snapshot_tracks_model_selection_but_not_main_recall_instruction`，与Phase1相同。上阶段已在原始基线用非敏感HINDSIGHT_API_LLM_MODEL覆盖复现；本阶段未重复基线单测定位、未修改memory/config及该测试，不将旧证据计为新通过。8项skip仍为5项缺少独立Hindsight/opt-in条件及3项缺nsjail；712项是单元选择规则deselected，不算通过或skip。
- P2.1首次迁移后的HTTP失败来自历史夹具仍patch旧校验器；注入点更新后原业务断言通过。新增stale-context用例实现前因公开入口缺失失败，实现后行为断言通过；均与既有业务失败分开记录。
- 额外探索`--explicit-package-bases`下全部旧work_domain曾发现save_policy.py第92行UUID|None→UUID类型问题（普通项目mypy通过）。它未被本次修改；在仓库外原始基线导出中单独运行同样真实import身份检查，仍得到相同1项类型错误。保留报告，不夹带修复，也不把这项额外探索写成通过。新12模块补充检查通过的范围已明确。

工作区保留：起始1430文件逐项SHA-256比对，只改本阶段列出的15个原文件，其余1415个原文件未变，丢失0、范围外改动0；13个新增文件均为本阶段应用/纯领域模块。原有code-analysis/、docs/learning/、蓝图及历史日志前缀均保留。所有原始输出、JUnit、hash清单和临时runner留`/tmp/hpagent-phase2/`，不提交Git，临时目录不保证跨机器保留。核心证据为各轮chat-tests.txt/run-summary.json、unit-final.txt、architecture-final.txt、docs-scripts.txt、workspace-preservation.json；额外类型问题的基线证据为save-policy-baseline-types.txt。

未验证/未实施：全量test-db/test-api、真实Temporal/replay/kill、浏览器/前端、真实Provider/QQ、压力/公平性和数据库断电耐久性、非空历史库迁移均未执行。不新增Schema/历史回填，不执行部署、重启既有服务或F1–F5修复。SQL及事务不以Workflow/UI完成推断权威事实。

审查不作判断的范围及执行方决议：真实Provider/QQ/部署、生产性能、Phase3仓储/UoW后续设计、F1–F5及既有memory环境敏感失败继续保持范围外；本轮未改这些行为，代价是不能用本阶段通过结果替代其独立验收。无待夹带实施的Minor项。

下一阶段前置条件：用户审阅本Phase2分层、锁内核验新增读取、已有失败及证据边界后再进入Phase3。Phase3按实际被迁移SQL选择真实PG原子性/权限/lease/savepoint回归；不先扩成完整矩阵。本轮保留WorkCommandService外围方法、旧策略/admit导出和Schedule facade；事件→通知→Outbox副作用和Repository writer未收拢，UoW实现未改。回滚按本阶段职责Commit逆序revert，不修改业务预算、pending wakeup、occurrence或历史迁移。

### Phase 2 Commit与暂停记录

| Task ID | Commit | 状态 |
| --- | --- | --- |
| P2.1 | `a662c2933ba59e04822e7b4dc42c419fc809da21` | 完成 |
| P2.2 | `8790214c215bc77f3ac2775c3fec51b749593b1a` | 完成 |
| P2.3 | `70777785c93654187a42383ca2df20c242f5ce27` | 完成 |
| P2.4 | `e273e72bf2385e656e87c24d03d26a37eadce4eb` | 完成 |

上述为独立实现Commit；本表自身的文档提交不改变实现。分支`codex/phase2-work-usecases`，未合并、未推送、未部署。用户已确认进入并继续本阶段；Phase2最终报告待审阅，Phase3未执行。原AS-IS蓝图不修改，当前事实维护在现有架构/代码地图及本日志的TO-BE说明。

## 2026-11-06 Phase 3：Repository与事务协作

用户“进行下一阶段”确认进入Phase3。本阶段只实施P3.1–P3.3，完成后暂停。起始Commit `a7669455`，新分支`codex/phase3-persistence-boundaries`保留原工作区。采用executing-plans、verification-before-completion，并延续用户批准的现有业务断言优先取舍；纯移动不新增目录/镜像/私有调用测试。UoW实现、Schema、F1–F5及部署不改，专用空测试库仍仅回放原完整80项迁移及checksum。

仓库外证据目录`/tmp/hpagent-phase3/`。每轮runner创建新PG16容器/独立网络/回环随机端口/tmpfs数据库、初始化原三角色，核验Schema历史并清理；不复用已有应用服务或输出凭据。原工作区的蓝图、code-analysis/、docs/learning/保留。

### P3.1 Run及Chat SQL

状态：完成；本任务独立Commit的精确Hash在阶段末尾汇总。

文件迁移：新增`persistence/{accounts,conversations,messages,runs}.py`接收原对应Repository及本次Chat投影/Surface读写SQL；`persistence/repositories.py`保留兼容导出，外围File/Budget/Idempotency/Outbox/Workflow类暂留原位。`run_domain/creation.py`增加冻结ChatRunInsert/WorkRunInsert与Chat输入builder。应用SendMessage/RetryChatRun在调用前构造Chat记录，AdmitWorkRun构造Work记录后分别走insert_chat/insert_work，Run仓储只编码JSON/绑定字段，不决定准入、策略或Work需求。旧insert签名仅兼容调用纯builder后转发。projection、Conversation创建和QQ的Message origin/Conversation创建/Run查找使用相应窄仓储接口；文件授权与执行事实SQL不扩成万能Run仓储。

前：Chat repo.insert内拼策略/snapshot，Work应用内INSERT runs；后：两条独立应用创建链→各自冻结记录→同一个RunRepository的两个明确写接口。Chat的executor_version/strategy_policy_version由原DB默认1改为显式绑定冻结值1；SQL列形状因此有意增加两列，有效行值和当前兼容四元组不变。Work INSERT列/顺序及编码不变。root仍由trigger创建，未新增root INSERT、commit、RPC或双写。所有仓储接受调用方UoW，原锁/写集/返回及权限过滤不变。旧位置删除的是已移动类/SQL，不删除公开模块或业务分支。

命令：`run-pg.py p31-before test/web_persistence/test_command_service.py test/web_persistence/test_conversation_commands.py test/web_persistence/test_web_artifacts.py test/work_domain/test_foundation.py test/web_persistence/test_permissions.py test/web_persistence/test_outbox_and_lifecycle.py::test_db_008_concurrent_retry_returns_one_child`：47 passed/18.25s。迁移后同选择加QQ canonical ingress文件：59 passed/23.01s，失败0、跳过0。断言覆盖幂等/冲突、并发单槽、Outbox回滚、HTML snapshot/retry/外层回滚、Work pointer/owner/revision、并发retry唯一child及API/Worker角色隔离。原80项迁移/checksum及资源清理均成功。`make lint typecheck`通过，mypy103文件无错误。不新增测试或mock转发断言。

最值得读run_domain/creation.py（不同owner形状）、persistence/runs.py（指定writer）、现有test_conversation_commands.py及foundation的行为断言。下一任务拆Work表writer与event→notification→Outbox同事务编排；UoW、事件和技术事实仓储尚未迁移。回滚以本职责Commit撤销，不改数据或迁移。

### P3.2 Work writer与事件编排

状态：完成；P3.1 Commit为`dea6239e`，本任务精确Hash在阶段末尾汇总。

新增`persistence/{works,work_requirements,wakeups,work_schedules,work_events}.py`分别负责Work行/CAS/epoch/continuation、append-only需求及原hash、wakeup状态、schedule/occurrence事实和纯事件append。原`work_domain/persistence.py`仅兼容导出/转发；dto移纯`work_domain/serialization.py`，schedule状态计算移`work_domain/scheduling.py`。`application/work/schedules.py`借用原UoW协调lock→纯计划→原upsert，daily时钟仍只在原需要计算next_daily的位置读取，不改once/awaiting_delivery语义。

`application/work/events.py`显式协调WorkEventRepository.append→delivery.enqueue_event→OutboxRepository.enqueue_work_event；原通知选择不变，accepted/advanced仍不新增通知。原Work命令、准入、调度、终态/完成、进度、integration、save-policy和Delivery的Work写入只把原SQL调用换成指定writer；原校验/分支/锁/事务/异常处理不移动。Work事件各调用点显式调用该应用函数；旧WorkRepository.event签名仅兼容。Outbox的Work入队SQL本任务加入原Outbox类，P3.3再整体迁技术队列仓储。

关联Run状态SQL也按原谓词迁到RunRepository：锁内running与仅queued→running保持两个不同接口，coordinator取消及带result_json终态保持原SQL，不合并状态条件或重写Lifecycle编排。Workspace/记忆治理标记不属于本次状态writer，原文件及SQL保留，未扩成Run万能仓储。开发中Ruff发现生成的两个running接口同名，已明确区分locked/queued接口后再验证；不将该临时检查失败记成已有业务问题。

24种原Work相关写SQL按原字段/参数/RETURNING分别收拢，保留GREATEST(now,..)、仅now及不更新timestamp三种不同写法，不顺手规范化。同步迁同表锁读/批量选择等必要接口；容量票据、资源、预算和能力事实继续由原能力接口负责。所有writer的第一个参数为原uow，没有创建连接、提交、RPC或额外锁；root trigger、同事务事件/通知/Outbox、原角色/FK不变。

测试：在原foundation文件仅新增一个尾部故障业务用例，真实PG中暂停Work，在最后publish_work_event Outbox INSERT抛错，断言Work快照、events/notifications/deliveries/Outbox/幂等数量全部回滚，同key恢复后仅1个paused事件和1条通知。注入公共UoW.execute的单一故障点，不新增私有调用/目录测试。先在原实现运行1 passed/1.46s，再用于迁移后行为核验。

命令：使用Phase2最终节点选择的`run-pg.py p32-after <final-cases> -m 'not temporal'`：99 passed、1 deselected/77.14s（较上一阶段多该1项），失败0、skip0。包括来源/权限、revision/pointer、保存/预算、completion、调度/失败隔离及新尾部回滚。后续Run状态纯SQL移动后，`p32-verified`选提醒准确回执后完成、stop+uncertain责任、旧revision回执、resume不重发、终态Outbox稳定ID和尾部回滚六项：6 passed/5.21s。所有轮次80项迁移/checksum、资源清理通过。`make lint typecheck`：Ruff及110个文件mypy通过。最终合并回归在P3.3后执行。

前链为用例→混合WorkRepository/direct SQL→隐藏事件副作用；后链为用例→指定行writer，事件在应用明确三步调用。新旧每步共享同一外层UoW/savepoint，累计预算、pending/occurrence和JSON/hash均不回填。最值得读application/work/events.py、persistence/works.py、work_domain/scheduling.py与尾部回滚断言。下一任务迁Outbox/Workflow SQL及纯分发contract，UoW实现仍不改；F2/F5不处理。

### P3.3 UoW与技术事实仓储

状态：实现及选定验证完成；P3.2 Commit为`1f8386d1`，本任务Hash在阶段末尾汇总。

文件变化：`persistence/outbox.py`接收完整原队列Repository（含P3.2的Work入队）；`persistence/workflow_executions.py`接收原has_current及Workflow SQL。聚合repositories保留兼容导出和原File/Budget/Idempotency实现，不新建另一套SQL。`web_domain/outbox.py`继续持有原claim/recover/ack/dead-letter短事务并委派；`web_domain/workflow_execution.py`继续持有原prepare/record/reconcile短事务及Run锁/校验，仅委派事实SQL及Run读。应用events/schedules显式接收借用的UnitOfWork；**persistence/uow.py最终字节与阶段起点相同**。

StartDecision、web_workflow_id、WorkflowExecutionStore/TemporalWebClient/CancellationFinalizer、ReconcileCandidate/TemporalFact及其Protocol移到现有run_lifecycle_contracts，旧dispatcher/reconciler保留同名兼容导出；Dispatcher/Reconciler执行类不改。Store不再为读取contract导入重型dispatcher/reconciler。Workflow输入、活动/Workflow名称、队列、固定ID、schema_version及SDK Start/AlreadyStarted行为不变。

前：短事务Service/Store→聚合/内联SQL，Store读contract需导入Dispatcher；后：同一原UoW→指定技术仓储，contract直接来自纯模块，提交后RPC仍在原Dispatcher。旧attempt、lease owned types、claim排序/SKIP LOCKED、recover/ack owner谓词和失败分类保持原样。没有新增连接/commit/独立子事务/RPC或lease token；远端terminal fact仍不能推进业务Run succeeded。F2/F5未修。

实际命令及结果：现有Web/Research/切换离线contract三文件`pytest -q ... -m 'not temporal'`：39 passed/0.77s。`run-pg.py p33-corrected <p33-cases.txt节点> -m 'not temporal'`：36 passed/13.63s、失败0、skip0；包括Outbox/Lifecycle文件、QQ canonical ingress、原UoW清理/序列化重试、过期owner/owned types/SKIP LOCKED/重试时序、坏Work隔离和Work dead-letter生命周期。UoW清理/重试两项使用原Fake连接，其余按现有业务夹具运行；不将整个集合都宣称为真实PG的提交可见性证明。

早期本轮测试命令误写1个不存在的node，收集退出4且fixture未执行；runner后续checksum验证因未建Schema报错，根因是选测名称错误。已改用实际存在的test_dispatch_dead_letter_uses_work_lifecycle，完整重跑上述36项，不计为业务断言失败/skip/通过。迁移过程静态检查也捕获新仓储缺少UnitOfWork类型import，补回后项目lint/typecheck通过。命令错误与已有失败分别保留原输出，不隐去为通过。

审查纠正：一次对整个persistence目录的格式化误改command_result/memory/memory_context/migrate/uow五个无关文件。逐个确认其AST无变化、起始hash等于HEAD、当前字节精确等于ruff-format(原文)后，只恢复这五个原文；不覆盖其他文件或原有变更。独立审查确认UoW恢复为原字节。另一处QQ set_origin调用的JSON编码/UUID构造顺序曾在SQL提取时对调；已用keyword求值顺序恢复原JSON→account→UUID，并复跑原QQ文件12 passed/10.63s，不新增镜像/调用次数测试。

`make lint typecheck`最终Ruff及112文件通过；真实import身份检查15个新仓储/builder/scheduling/事件模块通过；registry/sandbox及177架构用例通过。独立审查对原Repository、保留File/Budget/Idempotency和Dispatcher/Reconciler执行类做AST等值核验，并检查参数映射、SQL、事件顺序、clock、savepoint和事务所有权；无Critical/Important，唯一顺序Minor已纠正，无未实施Minor建议。没有重建连接池测试，因为UoW实现和提交边界未变。

最值得读persistence/outbox.py、workflow_executions.py及保留的PostgresWorkflowExecutionStore，顺着prepare_start提交→Dispatcher RPC追踪真实边界。旧Service和兼容导出尚在；下一阶段仅在用户审阅后做Phase4引用/目录收敛，本阶段不继续。

### Phase 3 最终证据与阶段审阅

核验时间：2026-11-06 07:02:10 CST。P3.1–P3.3实现完成，停止于Phase3，不进入Phase4。

任务变化及边界：Chat/Work分别经冻结记录和insert_chat/insert_work创建，root继续由trigger产生；Work/Requirement/Wakeup/Schedule/Event、Outbox和Workflow fact分别有指定SQL owner；事件三步编排显式借用原UoW。原Statement谓词、参数、SQL返回/NULL/hash和预算值、原锁与提交/savepoint边界保持。Chat显式版本1列、恢复QQ参数求值顺序及纯规则位置变化已在各任务说明，不以“仅文件移动”掩盖实际接口调整。

最终实际命令与结果（重复/重叠套件不相加为唯一用例数）：

| 命令 | 结果 | 证据范围 |
| --- | --- | --- |
| `make lint typecheck PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python` | exit0；Ruff通过，mypy112文件无错误 | 项目既有静态范围 |
| `MYPYPATH=src .venv/bin/python -m mypy --explicit-package-bases --follow-imports=silent src/persistence/accounts.py src/persistence/conversations.py src/persistence/messages.py src/persistence/runs.py src/persistence/works.py src/persistence/work_requirements.py src/persistence/work_events.py src/persistence/work_schedules.py src/persistence/wakeups.py src/persistence/outbox.py src/persistence/workflow_executions.py src/run_domain/creation.py src/work_domain/scheduling.py src/application/work/events.py src/application/work/schedules.py` | exit0，15文件无错误 | 实际import身份，不宣称旧模块全部严格类型通过 |
| `make architecture-check PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python PYTEST_ARGS='--junitxml=/tmp/hpagent-phase3/architecture-final.xml'` | exit0，registry pass=true、无sandbox/注册违例；177 passed/5.09s | 离线真实注册/import/sandbox，非真实Temporal服务 |
| `make test-unit PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python PYTEST_ARGS='-q --junitxml=/tmp/hpagent-phase3/unit-final.xml'` | exit2；1442 passed、1 failed、8 skipped、713 deselected，49.79s | 未宣称全绿；排除postgres/temporal集合 |
| `.venv/bin/python -m pytest -q test/test_web_temporal_contract.py test/test_research_temporal_contract.py test/test_w1c_cutover_contract.py -m 'not temporal'` | exit0，39 passed/0.77s | 现有Fake store/client及契约，非真实Temporal |
| `make docs-check scripts-test PYTHON=/home/hp/workspace/HpAgent/.venv/bin/python` | exit0；36份Markdown、0错误；79项脚本通过/13.880s | 离线文档/脚本，不代替业务PG |
| `git diff --check` | exit0 | 补丁检查 |

最终相关PG/API及事务回归命令：将下列节点作为参数传给`.venv/bin/python /tmp/hpagent-phase3/run-pg.py phase3-final`，末尾`-m 'not temporal'`。runner显式设置专用三角色DSN、完整原迁移目录及PYTHONPATH，以绝对.venv解释器执行`-m pytest --require-test-services -vv <这些node> -m 'not temporal' --junitxml=/tmp/hpagent-phase3/phase3-final/chat-results.xml`。

```text
test/work_domain/test_foundation.py
test/work_domain/test_time_policy.py
test/web_api/test_work_foundation.py
test/web_api/test_work_save_admission.py
test/work_domain/test_completion_contract.py
test/work_domain/test_save_admission.py
test/work_domain/test_progress.py
test/work_domain/test_execution_strategy.py
test/work_domain/test_persistent_policy_pg.py
test/web_persistence/test_durable_review_regressions.py::test_retry_chain_ends_at_success_and_new_occurrence
test/web_persistence/test_durable_review_regressions.py::test_retry_chain_follows_control_epoch_when_database_clock_moves_backwards
test/web_persistence/test_durable_review_regressions.py::test_admission_failure_is_local_and_pending_wakeup_recovers
test/web_persistence/test_durable_review_regressions.py::test_schedule_loop_recovers_iteration_failure_and_propagates_cancel
test/web_persistence/test_durable_review_regressions.py::test_ready_wakeup_is_deduplicated_and_obeys_latest_work
test/web_persistence/test_durable_review_regressions.py::test_success_does_not_autorun_waiting_work
test/web_persistence/test_command_service.py
test/web_persistence/test_conversation_commands.py
test/web_persistence/test_web_artifacts.py
test/web_persistence/test_permissions.py
test/web_persistence/test_outbox_and_lifecycle.py
test/web_persistence/test_qq_canonical_ingress.py
test/web_persistence/test_phase_a_hardening.py::test_uow_closes_on_success_business_error_and_commit_error
test/web_persistence/test_phase_a_hardening.py::test_serialization_retry_closes_failed_connection
test/web_persistence/test_phase_a_hardening.py::test_outbox_dead_letter_requires_current_lease
test/web_persistence/test_phase_a_hardening.py::test_expired_outbox_owner_cannot_dead_letter_after_reclaim
test/web_persistence/test_phase_a_hardening.py::test_outbox_claim_filters_owned_types_and_does_not_block
test/web_persistence/test_phase_a_hardening.py::test_skip_locked_claim_never_returns_same_event_twice
test/web_persistence/test_phase_a_hardening.py::test_retryable_outbox_failure_honors_owner_and_schedule
test/work_domain/test_execution_decoupling.py::test_dispatch_dead_letter_uses_work_lifecycle
test/web_persistence/test_work_integration.py::test_reminder_only_completes_after_exact_delivery_receipt
test/web_persistence/test_work_integration.py::test_stop_and_uncertain_delivery_preserve_responsibility
test/web_persistence/test_work_integration.py::test_revision_receipt_cannot_complete_new_requirements
test/web_persistence/test_work_integration.py::test_resume_revalidates_accepted_reminder_without_resending
```

实际结果：**158 passed、1 deselected，111.53s，exit0；失败0、skip0、所选集合未执行0**。其中原UoW两个清理/序列化用例使用Fake连接；其余按原PG/API/循环夹具核验各自行为，不把所有通过都写成真实PG/Temporal证明。被选择规则排除的1项为原progress文件Temporal场景。最终QQ求值顺序纠正后另跑原QQ集合12 passed/10.63s。全量单元和架构门禁未因这个局部求值顺序纠正机械重跑；相关QQ业务断言单独补证，文件格式恢复均有AST等值依据。

最终主轮专用容器`hpagent-phase3-1623777`、独立网络`hpagent-phase3-1623777-net`、回环端口`32796`；空库原80项迁移/checksum一致，pytest及容器/网络清理均exit0。各轮均用新建资源；无业务服务重启、部署、真实Provider/QQ连接。API是TestClient+PG，外部执行/渠道仍为原夹具；不把RPC契约、SIGKILL单个专用测试进程或数据库逻辑回滚外推为真实部署、Provider或PG断电验收。

独立审查覆盖整个Phase3源码及未提交技术仓储：原迁移Repository除有意适配Run.insert外AST一致；保留File/Budget/Idempotency及Dispatcher/Reconciler执行类AST一致；SQL文本/参数、不同状态writer、事件顺序、条件clock、savepoint/提交边界及兼容导出核验通过，18个新进程导入通过。无Critical/Important，唯一QQ求值顺序Minor已纠正。审查未独立重跑PG套件；最终报告与证据由执行方核对。

已有失败和检查过程问题分别记录：

- 全量单元唯一失败仍为memory质量配置的`test_snapshot_tracks_model_selection_but_not_main_recall_instruction`；Phase1已在原基线用非敏感HINDSIGHT_API_LLM_MODEL覆盖复现。当前阶段未重跑该基线定位，不修改记忆配置/实现/测试，不将历史证据冒充本轮通过。此前已知额外严格类型检查的save_policy UUID|None问题也未夹带修复；本轮补充15文件检查范围明确。
- 8项skip仍为5项缺独立Hindsight/opt-in及3项缺nsjail，713项是单元选择规则排除，不算通过或skip。
- P3.2运行态writer生成时的重复方法命名、P3.3缺类型import及错误node收集均为本次开发/命令问题，已纠正后实际复验；不归为既有业务失败。误扩大格式化5个无关文件也已按原字节恢复，并再次验证UoW未变。原始失败输出保留仓库外。

工作区保留：起始1443个文件中，只有本阶段列出的33个原文件变化，其余1410个原文件hash不变；新增15个文件均为本阶段仓储/纯规则/应用事件调度协作者，丢失0、范围外变化0。原AS-IS蓝图、code-analysis/、docs/learning/和日志历史前缀保留；uow/migrate/memory等文件与起点相同。当前事实更新在原overview/work/execution/代码地图，未新增重复阶段报告。

未验证/未实施：未跑全量test-db/test-api、真实Temporal服务/replay/kill链路、前端/浏览器、真实Provider/QQ、负载/锁性能、非空历史库升级或PG断电恢复；UoW未改，不新增Pool/savepoint测试矩阵，现有Mock清理及QQ/Scheduler/尾部故障回归分别提供对应证据。技术事实Store未新增真实远端验收。独立权限/迁移历史结果只证明本地原三角色夹具，不证明现有运行环境配置。没有改Schema/迁移checksum、运行部署或F1–F5修复。

仍保留的边界：兼容facade/导出及部分旧import留Phase4；外围查询/文件/预算/执行能力等不机械拆分。Conversation元数据改名仍在原QueryService，Workspace记忆撤权标记仍属原ResourcePolicy；本轮收拢创建/状态writer不宣称全仓库所有SQL均归一。研究保存与运行期能力/预算/资源事实继续各自负责，不并入万能RunRepository。

审查暂不判断及执行方决议：真实Temporal/Provider/QQ/部署、生产性能、非空历史升级、F1–F5、既有memory环境敏感失败及后续Phase4依赖收敛均保持范围外；本轮未改这些行为，代价是不能用当前通过证据替代独立验收。无未实施Minor建议。

下一阶段前置条件：用户审阅Phase3实际writer分工、原子集合、兼容层和已有失败/未验证范围后，才进入Phase4。Phase4按真实调用/注册/历史加载证据收敛import并更新当前文档，不为目录整齐新增抽象或删除未知消费者。回滚以职责Commit逆序revert，保留当前Schema、幂等、预算、pending wakeup和occurrence，不手工改业务事实或历史迁移。

### Phase 3 Commit与暂停记录

| Task ID | Commit | 状态 |
| --- | --- | --- |
| P3.1 | `dea6239e89d2a20309994403597f3503d23316a2` | 完成 |
| P3.2 | `1f8386d11b8fb81e17ed9a01d80fcfefe79f16dd` | 完成 |
| P3.3 | `dee13baf404a8269d7bddb8b7d9106da903292d3` | 完成 |

上述为独立实现Commit，本表本身的文档提交不改变实现。分支`codex/phase3-persistence-boundaries`，未合并、未推送、未部署。用户已通过前阶段并确认进入本阶段；Phase3报告待审阅，Phase4未执行。

## 2026-11-06 Phase 4：应用入口与依赖收敛

用户在审阅前阶段后明确“进行下一阶段”，本轮执行最后一个 Phase 4；不重新请求方案B批准。阶段起点 `6ae2f33a`，分支 `codex/phase4-application-entrypoints`。起始原有未跟踪蓝图、code-analysis/、docs/learning/ 保留。P4.1/P4.2 的实现结果在本节追加；Phase 0–3 历史不覆盖。当前事实维护于原架构和代码地图页面，不另建重复报告。

### 任务状态、取舍与文件迁移

| Task | 当前状态 | Commit |
| --- | --- | --- |
| P4.1 收敛引用 | 完成；最终相关回归与独立审查已结束 | 待本轮实现提交后登记 |
| P4.2 当前文档、删除核对及最终可见性 | 完成；当前文档及最终证据已核验 | 待文档提交后登记 |

Ruling：把已有 facade 定义移动到应用包，组合根直接引用它们；不新增另一层 Service 或重复实现。类名、构造参数、属性、返回值及原辅助命令保持。若外部只依赖旧 import，继续得到同一个类；类定义的模块归属更新，不承诺未使用的跨版本 pickle 兼容。

Ruling：保留全部旧兼容导出和私有兼容 shim。源码内主干已用公开入口，但历史测试、未知扩展、动态加载的完整消费者集合无法证明为空；不能仅凭当前 rg 没有调用就删除。WorkCommandDependencies.repo 维持旧 WorkRepository 方法集合，主干 requirement/wakeup 直接调用指定窄仓储。代价是这个有说明的兼容依赖仍存在，而不是追求零旧包 import。

Ruling：这是已批准的无行为结构整理，继续复用已有业务断言做 green→green 验证；只调整2个现有测试的 patch 模块定位，不新增镜像、目录、私有函数调用或静态 registry 测试。Workflow 命令图、注册与输入不改，使用现有 architecture-check，不机械重跑真实 Temporal 全场景。

| 文件 | 原职责 → 当前职责 / 删除依据 |
| --- | --- |
| src/conversation_domain/commands.py → src/application/chat/service.py | 原 Chat facade 实现整体移到应用包，继续组合 Send/Retry/Cancel 并保留其余原公开/兼容方法；旧文件只同名导出 CommandService/message_request_id |
| src/work_domain/commands.py → src/application/work/service.py | 原 Work facade 实现整体移到应用包，Accept/Advance 仍委派原用例，revise/control/link/integration 事务原样保留；旧文件只同名导出 Service/WorkConflict |
| src/web_artifacts/services.py → src/application/artifacts/service.py | 原 Artifact 命令/查询组合整体迁移，sender 与 legacy 默认不变；旧文件只同名导出 |
| src/orchestration/work_schedule.py、src/application/work/scheduler.py | 原 WorkScheduleService 类整体移到应用包，Evaluate/Dispatch/occurrence 行为不变；原进程循环及服务同名导出保留 |
| src/application/work/dependencies.py | 原公开 repo 属性保持兼容方法集合；dto 改用纯 serialization；没有双 SQL |
| src/application/work/queries.py、src/work_domain/integration.py、src/application/work/service.py | 原 requirement/insert_requirement/wakeup 别名调用改为 WorkRequirementRepository.get/insert、WakeupRepository.enqueue；相同参数、同 UoW；其他 DTO、Work 行读写 import 用实际实现 |
| src/workspace/resources.py | 三处私有 commands._cancel_run_in_uow 调用改为公开 commands.cancel.execute_in_uow + CancelRunCommand；原账号/Run/key、求值顺序及调用方事务不变 |
| test/test_generic_completion_contract.py、test/test_workspace_save_policy.py | 现有 revise 重放测试 patch 从旧 facade 模块定位到实际 application.work.service；业务断言及 accept patch 不变 |
| docs/architecture/overview.md、work.md | 更新实际 facade 路径与兼容例外，原契约保留 |
| docs/architecture/execution.md | 补完整入口图、核心关系图、Chat/Work 时序、UoW 所有权；原恢复与预算合同保留 |
| docs/reference/repository-layout.md | 更新当前入口，补最终目录/旧新映射、14个核心 Repo 职责、关联表 writer 和学习顺序 |
| 本日志 | 追加逐任务实际结果及最终第15.4节索引；旧记录保持 |

下列各文件调整实际 import 路径、必要 import 排序，以及旧 Work requirement/wakeup 别名到同一窄仓储的调用定位，原职责不变：组合根直接引用应用 facade；SQL、纯策略及 DTO 直接引用唯一实现；没有删除方法、修改 SQL 文本/参数或引入 writer：

- `src/application/chat/dependencies.py`
- `src/application/chat/projection.py`
- `src/application/chat/retry_run.py`
- `src/application/context_assembly.py`
- `src/application/conversation.py`
- `src/application/surfaces/conversation.py`
- `src/application/work/accept_work.py`
- `src/application/work/admit_work_run.py`
- `src/application/work/advance_work.py`
- `src/application/work/dispatch_due_work.py`
- `src/application/work/evaluate_schedule.py`
- `src/application/work_context.py`
- `src/bootstrap/worker_dependencies.py`
- `src/conversation_domain/admission.py`
- `src/conversation_domain/run_projection.py`
- `src/delivery/service.py`
- `src/file_domain/approvals.py`
- `src/orchestration/run_dispatcher.py`
- `src/orchestration/worker.py`
- `src/orchestration/worker_tasks.py`
- `src/persistence/memory.py`
- `src/research_domain/services.py`
- `src/run_domain/delegation.py`
- `src/run_domain/diagnostics.py`
- `src/run_domain/execution.py`
- `src/run_domain/lifecycle.py`
- `src/run_domain/queries.py`
- `src/web_api/error_handlers.py`
- `src/web_api/fake_executor.py`
- `src/web_api/lifespan.py`
- `src/web_api/queries.py`
- `src/web_api/routes/works.py`
- `src/web_api/terminal_publisher.py`
- `src/web_domain/file_services.py`
- `src/web_domain/lifecycle.py`
- `src/web_domain/outbox.py`
- `src/work_domain/completion.py`
- `src/work_domain/errors.py`
- `src/work_domain/progress.py`

删除的是旧文件中已迁移的重复定义位置，不删除业务步骤/兼容入口；新文件只有一份原实现。File/RunBudget/Idempotency 仍定义在 persistence/repositories.py，其原位置 import 合理；WorkSchedule 进程循环也保留 orchestration import。没有为目录整齐扩散其他领域迁移。

### 新旧调用链、事务与并发

前：Web/Worker 组合根 → conversation_domain.commands / work_domain.commands / web_artifacts.services / orchestration.work_schedule → 已迁移用例 → 指定 SQL writer → PG。

后：相同组合根 → application.chat.service / application.work.service / application.artifacts.service / application.work.scheduler → 原同一用例 → 指定 SQL writer → PG。QQ 与 Workspace 使用公开事务内端口。旧路径 import 得到同一个新类，原测试仍可验证兼容入口。

Chat 独立命令与 Work Accept/Advance 原事务所有权不变；QQ binding/receipt/savepoint、Artifact+Version+HTML Run 同事务不变；Work 调度评估提交后才进入独立 dispatch 批次，逐Work savepoint不变。Workspace 撤权依旧借用外层同一 UoW 取消 Run。事件、通知、Outbox 同事务，准备 Workflow fact 提交后才进行 RPC。锁顺序、预算/授权/revision/fence、时钟与 JSON/UUID 求值时点未调整。

仓库外 AST 审计在忽略 import、仅归一化本次明确的等价仓储/公开取消接缝后，46个原源码文件的定义与起点一致；调度 facade 类 AST 完全相同。迁移与 UoW 字节不变。该检查是差异审计，不是新静态测试门禁或并发证明。最初审计脚本生成的 AST 节点缺 ctx 导致比较拒绝，补齐脚本节点元数据后通过；未以此修改业务代码。memory.py 本轮仅 Outbox import 改用指定仓储，没有改记忆逻辑或配置。

### 实际测试命令、已有断言及结果

执行目录 `/home/hp/workspace/HpAgent`，现有 .venv。数据库 runner 位于 `/tmp/hpagent-phase4/run-pg.py`，本轮3次均创建新的专用空 PG 容器、独立网络、回环随机端口、tmpfs；显式同库三角色 DSN/原迁移目录/PYTHONPATH，不读取或输出私有环境凭据。只回放仓库原001–080链；测试后 verify_schema/checksum，随后删除临时容器与网络。镜像与原fixture同前阶段；无现有服务重启或部署。runner 的提示字符串仍写“selected 5 cases”，实际选择以命令参数、JUnit及下表数量为准。

首轮前后使用完全相同的已有32项：

```bash
.venv/bin/python /tmp/hpagent-phase4/run-pg.py p41-before test/web_persistence/test_conversation_commands.py test/web_api/test_work_foundation.py test/web_api/test_phase_b_api.py::test_api_003_004_005_send_idempotency_and_busy test/web_persistence/test_qq_canonical_ingress.py test/web_persistence/test_web_artifacts.py test/web_persistence/test_workspace_v41_p2.py::test_explicit_attachment_revocation_blocks_read_and_failed_retry test/web_persistence/test_workspace_v41_p2.py::test_independent_task_last_input_revoke_persists_cancellation
.venv/bin/python /tmp/hpagent-phase4/run-pg.py p41-after test/web_persistence/test_conversation_commands.py test/web_api/test_work_foundation.py test/web_api/test_phase_b_api.py::test_api_003_004_005_send_idempotency_and_busy test/web_persistence/test_qq_canonical_ingress.py test/web_persistence/test_web_artifacts.py test/web_persistence/test_workspace_v41_p2.py::test_explicit_attachment_revocation_blocks_read_and_failed_retry test/web_persistence/test_workspace_v41_p2.py::test_independent_task_last_input_revoke_persists_cancellation
make lint typecheck PYTHON=$PWD/.venv/bin/python
.venv/bin/python -m mypy --follow-imports=normal src/application/chat/service.py src/application/work/service.py src/application/artifacts/service.py src/application/work/scheduler.py
make architecture-check PYTHON=$PWD/.venv/bin/python
make test-unit PYTHON=$PWD/.venv/bin/python
make docs-check scripts-test PYTHON=$PWD/.venv/bin/python
node /tmp/hpagent-phase4/mermaid-check.cjs
.venv/bin/python /tmp/hpagent-phase4/preservation.py
git diff --check
```

| 检查 | 实际结果 | 证据范围 |
| --- | --- | --- |
| 首批迁移前 PG/API | 32 passed，15.48s，exit0，fail/skip0 | 现有Chat重放/并发单槽/尾部回滚、HTTP Work修订控制、QQ receipt/撤权、HTML retry/cancel、Workspace撤权取消 |
| 同批迁移后 PG/API | 32 passed，17.10s，exit0，fail/skip0 | 原同一断言通过新组合根和旧兼容 import；不外推为真实 QQ 或Provider |
| lint / 默认 mypy | exit0；112 source files 无问题 | 使用项目默认范围；另一次最终同命令通过 |
| 新应用 facade 显式 mypy | exit0；4 source files 无问题 | follow-imports=normal，补足默认范围外实际入口检查 |
| 既有 architecture-check | 注册检查无违禁定义/import；177 passed，5.46s | 原架构、import/sandbox及契约，不新增 registry；无真实 Temporal 服务 |
| 全量既有 test-unit | exit2（make），1442 passed、1 failed、8 skipped、713 deselected，52.87s | 唯一失败为下述既有 memory 配置；单元及原离线子进程契约 |
| docs-check / scripts-test | exit0；36 Markdown/0错误，79 tests/0失败，14.06s | 链接不核验远程/锚点；脚本离线/回环夹具 |
| Mermaid | 4 diagrams，0语法错误 | 现有临时解析器+DOM stub，仅语法，不是浏览器渲染验收 |
| 工作区/AST审计 | 46个原源码归一化后等值，调度类等值；84个原用户文件hash不变；迁移/UoW不变 | 不新增持久测试；不把静态等值当运行通过 |

现有关键断言：跨实例幂等只留1 Run/2 Message/1 start_run；竞争只取得一个Chat槽；Outbox故障整组回滚；QQ重投、busy receipt、撤销身份不能命中新Run；HTML保留executor/冻结snapshot且撤权阻止读和retry；独立Work最后输入撤权产生原取消结果。Work HTTP原断言继续覆盖接受不建Run、当前revision及控制/跨账号可见性。

最终相关集合复用上节Phase3列明的33个文件/完整node参数，并加以下2个本轮公开取消接缝用例；没有扩大到全量DB/API或其他能力可靠性场景：

```text
test/web_persistence/test_workspace_v41_p2.py::test_explicit_attachment_revocation_blocks_read_and_failed_retry
test/web_persistence/test_workspace_v41_p2.py::test_independent_task_last_input_revoke_persists_cancellation
```

实际由如下命令将上述既有选择逐个传入 runner，runner以同一绝对.venv解释器运行 `-m pytest --require-test-services -vv -m 'not temporal' <nodes> --junitxml=/tmp/hpagent-phase4/final/chat-results.xml`；完整参数保存在 `/tmp/hpagent-phase4/final-cases.txt` 和 `final/run-summary.json`：

```python
cases = Path('/tmp/hpagent-phase3/final-cases.txt').read_text().splitlines() + [
    'test/web_persistence/test_workspace_v41_p2.py::test_explicit_attachment_revocation_blocks_read_and_failed_retry',
    'test/web_persistence/test_workspace_v41_p2.py::test_independent_task_last_input_revoke_persists_cancellation',
]
subprocess.run(['.venv/bin/python', '/tmp/hpagent-phase4/run-pg.py', 'final', '-m', 'not temporal', *cases])
```

最终相关回归与审查结果见下文追加。

最终实际结果：**160 passed、1 deselected，106.05s，exit0；fail0、skip0、所选正文未执行0**。排除的是原 progress 文件的1项 Temporal 场景，不计通过。原 UoW 两个连接清理/序列化用例是 Fake 连接；API 是 TestClient+PG，循环和外部 RPC 使用各自原夹具，不把160项统称真实外部端到端。最终容器 `hpagent-phase4-1753971`、网络同名-net、回环端口32800；原80项迁移/checksum一致，pytest和容器/网络清理均exit0。前后32项的两轮各自也完成原链校验与清理，未遗留本轮服务。

### 已有失败、保留项与未验证范围

- 唯一单元失败仍是 `test/test_memory_quality_config.py::test_snapshot_tracks_model_selection_but_not_main_recall_instruction`。当前配置覆盖使 MINIMAX_STANDARD_MODEL 变化不改变 snapshot config_hash；Phase1已在原始基线用非敏感覆盖复现，本轮不改记忆逻辑/环境来掩盖失败。不能宣称全量单元全绿。
- 8项skip分别是5项需要独立 Hindsight/opt-in、3项缺 nsjail；713项为单元选择规则排除。最终PG集合另有1项Temporal deselected，均不算通过。此前额外严格类型探查发现的 save_policy UUID|None 问题未纳入独立修复，本阶段明确范围的类型检查通过。
- F1：Artifact legacy 预算默认差异仍保留；F2：同worker ID租约ABA仍待独立复现/claim token设计；F3：mark_processed=False 后日志语义仍原样；F4：dead_letter 仍只适用原Run上下文；F5：Terminal publisher整轮异常监督仍需独立核验。F2/F5是审计风险，不声明已经发生生产数据损失。均未夹带修复。
- File/RunBudget/Idempotency 仍在原聚合定义；Work dependencies.repo 保留兼容类；历史 facade/策略/准入/仓储导出仍在。外围查询、能力/资源/文件/预算/记忆/投递运行时并未统一进万能仓储；SQL例外已列明，不声称全仓库严格分层。
- 未执行全量 test-db/test-api、真实 Temporal 服务/replay/SIGKILL恢复、前端 ci-web/浏览器、真实 Provider/QQ、压力/性能/长时间运行、非空历史库升级或PG断电恢复。未更改这些边界，所选证据不替代独立验收。
- 没有改Schema、迁移SQL或checksum；测试只在专用空库回放原链。无生产部署、已有服务重启、合并、推送。原用户文件保持，运行日志/审计脚本/JUnit位于 `/tmp/hpagent-phase4/`，未纳入Git，临时路径不保证跨机器保留。

### 第15.4节最终可见性索引与学习说明

| 要求 | 当前事实位置 |
| --- | --- |
| 1. 最终目录、新旧文件映射 | [代码地图：当前业务主干目录与旧路径映射](../reference/repository-layout.md#当前业务主干目录与旧路径映射) |
| 2. 多端入口到PG完整调用图 | [执行：业务主干调用图](execution.md#业务主干调用图) |
| 3. Chat/Work/Run核心关系图 | [执行：Chat、Work、Run核心关系](execution.md#chatworkrun-核心关系)；图中记录概念与实际应用类已区分 |
| 4. Chat/Work完整主要分支时序 | [Chat时序](execution.md#chat-时序与事务)、[Work时序](execution.md#work-时序与事务) |
| 5. 核心Repository职责 | [代码地图：核心仓储职责与表写入责任](../reference/repository-layout.md#核心仓储职责与表写入责任)，14个Repo及职责边界 |
| 6. UoW/提交权 | [执行：UnitOfWork所有权](execution.md#unitofwork-所有权) |
| 7. 核心表writer映射 | 同代码地图仓储表和关联表表格；明确表、列内事实、外部服务及例外 |
| 8. 实际相关测试与结果 | 本节命令/结果及Phase1–3逐任务原记录；未新增测试，2个原测试只改patch模块位置 |
| 9. 尚未解决问题 | 本节既有memory失败、F1–F5、保留层与未验证范围，不自动转为本次修复 |
| 10. 从简单到复杂学习指南 | [代码地图：从简单到复杂阅读业务代码](../reference/repository-layout.md#从简单到复杂阅读业务代码) |

本阶段核心概念是“应用入口有唯一实现，旧路径只保留兼容导出；仓储只借用事务，业务调用方保留提交权”。最值得读3个文件：`src/application/chat/service.py`（组合与公开命令）、`src/application/work/admit_work_run.py`（当前锁内准入）、`src/persistence/uow.py`（提交与savepoint）。可独立追踪的真实链：Web route → application.chat.service.CommandService.send_message → SendMessage.execute → execute_in_uow → RunRepository.insert_chat / Message /预算 / Outbox /幂等结果 → 原UoW提交 → Dispatcher准备事实提交 → Temporal RPC。Work从Accept开始则先建立委托，另经Advance/Scheduler才进入AdmitWorkRun，不占Chat槽。

回滚：依赖/组合根切换和facade移动可按本阶段实现Commit revert，文档按对应提交恢复；不手工改业务数据、幂等键、wakeup、预算或迁移链。整个方案B范围至Phase4结束，后续没有自动实施阶段；最终报告等待用户审阅。若另行修复既有问题，应单独明确行为合同、隔离环境与相应测试范围。

### Phase 4 独立审查与最终保留核验

按 executing-plans 的整阶段审查要求，第一位独立审查因模型额度限制未能执行，没有产生审查结论；随后改用可用模型完成只读审查，不把失败启动计为审查通过。最终审查无 Critical/Important。独立新进程核验4组新旧facade类身份及反向导入顺序、兼容仓储别名身份均一致；额外33项既有架构/命令重放/保存策略测试通过。审查未重跑PG/API、全量单元或真实外部链路，不能拿其结论替代本轮测试输出。

审查指出1组Minor文档事实偏差：调用图把QQ send与Retry合并且漏Work控制，Chat时序未区分retry只新增助手Message，UoW表没有区分QQ work_* 的嵌套facade路径。已按实际源码逐处修正，运行代码不变，并重新通过4图语法和docs-check；无待处理Minor。没有为这些文字修正重跑业务套件。

最后保留核验：起始1458个文件中54个原文件为本阶段明确改动、1404个原文件hash不变、丢失0；新增4个唯一实现的应用facade/scheduler文件。原用户84个文件全部保持原字节，原蓝图AS-IS及日志历史前缀不变。迁移与UoW不变；没有扩大格式化范围或清理工作区。

用户审阅：当前Phase4最终报告待审阅，已停止后续实施。方案B批准范围的Phase0–4均已实施；完成的是结构重构范围，不表示全量单元全绿、F1–F5已修复、部署或真实链路已验收。

### Phase 4 实现提交登记

| Task | Commit | 交付状态 |
| --- | --- | --- |
| P4.1 | `540c467e4cbaa38802a17a2454ecf7f58fd0b433` | 完成；应用入口、真实import和公开取消接缝已提交 |
| P4.2 | 本节后的当前架构文档提交，完整hash于最终登记追加 | 完成；目录/映射、调用图/类图/时序、Repo/事务/表writer、学习指南及证据均已核验 |

最终脚本复核79项通过（12.16s），docs-check仍36份/0错误；文档纠正后的4个Mermaid语法通过，未做视觉渲染。运行实现未变，不重复扩大业务测试。
