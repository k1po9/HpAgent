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
