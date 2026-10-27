# 测试与验证

## 分层和环境

| 层 | 入口 | 范围与副作用 |
| --- | --- | --- |
| 文档/脚本隔离测试 | `make docs-check scripts-test` | 不访问业务库或模型；脚本测试使用独立文件和本机隔离HTTP服务。 |
| Python静态/单元 | `make lint typecheck test-unit` | 先安装requirements-dev；单元集排除postgres与temporal标记，`test-existing`是兼容入口。 |
| PG领域契约 | `make test-db` | 收集全部postgres且非temporal测试，排除web_api；包括Work、Conversation、持久化与权限。fixture可能TRUNCATE，必须专用可丢弃库。 |
| API契约 | `make test-api` | web_api中postgres且非temporal测试；需要专用PG，部分用例还需Redis。 |
| Temporal/replay/SIGKILL | `make test-temporal`；回放fixture在单元集中 | 按temporal标记收集全部目录，包括Work、Research、Document与Worker终止恢复；使用独立PG/Redis和专用Temporal，不对共享业务环境执行kill harness。 |
| 前端静态/单元 | `make ci-web` | ESLint/Prettier、TypeScript、Vitest和构建；不是浏览器或真实Provider证明。 |
| 构建产物合同浏览器 | `make e2e-contract` | `playwright.entry.config.ts`，API/Auth路由夹具；不访问PG/Redis/模型。 |
| 源码夹具浏览器 | `cd web && npm run test:e2e:fixtures` | 本地组件 harness / 路由夹具；无后端，默认5312，可用 `HPAGENT_FIXTURE_PORT` 覆盖。 |
| 正式API浏览器 | `make e2e` / `make e2e-canonical` | 真实API/PG/Redis/File Store，Fake执行器；会迁移、注册账户并写业务夹具。 |
| 发布/真实链路 | [脚本索引](../../scripts/README.md)中的release/model/benchmark入口 | 可能构建/启动服务、调用真实模型或对外请求，不作为安全日常测试运行。 |

## 后端环境

宿主安装 `requirements-dev.txt`，从仓库根执行。三个DSN应指向同一**专用测试库**的migration/API/worker角色；Redis、文件目录、Temporal namespace与端口也要隔离。

```bash
export MIGRATION_DATABASE_URL='postgresql://MIGRATION_ROLE:PASSWORD@localhost:5434/hpagent_test'
export APP_DATABASE_URL='postgresql://API_ROLE:PASSWORD@localhost:5434/hpagent_test'
export WORKER_DATABASE_URL='postgresql://WORKER_ROLE:PASSWORD@localhost:5434/hpagent_test'
export HPAGENT_MIGRATIONS_DIR="$PWD/persistence/migrations"
export REDIS_URL='redis://localhost:6379/15'
# 测试数据库及角色由操作员预先建立；不从业务库复制未经脱敏的数据。
make migrate
make test-db test-api
```

四个入口与CI共用同一选择规则；支持`PYTEST_ARGS='-x --junitxml=/tmp/results.xml'`追加pytest参数。基础设施入口使用`--require-test-services`，在任何fixture执行前检查被选中测试所需的三个DSN、Temporal地址/namespace与Redis配置，缺失时失败。使用数据库fixture的测试必须标记postgres，真实Temporal测试必须标记temporal。收集用例可以直接执行`python3 -m pytest --collect-only -m temporal test`，不会运行fixture或要求服务在线。

Temporal验收还需显式设置`TEMPORAL_HOST`指向独立测试服务，设置`TEMPORAL_NAMESPACE`与`TEMPORAL_TEST_NAMESPACE`为该服务上已创建的同一测试namespace（独立自动初始化服务可使用default），再运行`make test-temporal`。该入口包含会启动并终止测试Worker的用例，子进程须能访问同一专用PG/Redis。缺少服务配置不算测试跳过或通过。

使用完整迁移链和原checksum；旧业务库门禁见[部署](../operations/deployment.md)。`make db-up`只启动Compose所配置的数据库，不能据此认为它就是专用测试库。`make test`包括基础设施集合，运行前必须检查fixture要求。

静态检查范围：Ruff覆盖Conversation、Web、Work、Run、Application、Bootstrap、Agent Workflow/Activity、Orchestration、Research及全部test；mypy覆盖Conversation、Web、Persistence、Web API、Work、Run与Agent Workflow。OneBot11、共享发送回执及 Delivery 已纳入 Ruff；其他通用能力/渠道适配包尚未全部纳入，不以这两个命令证明全仓库静态检查通过。

记忆核心回归可定向执行 `test/web_persistence/test_memory_retention.py`、
`test/web_persistence/test_memory_governance.py`、`test/web_persistence/test_memory_readiness.py`、
`test/web_api/test_memory_governance.py`、`test/work_domain/test_memory_context.py`；仍使用上述三角色
专用 DSN/Redis。`test/test_memory_readiness.py` 覆盖默认关闭/显式启用、分页、未知来源和在途阻断。
目标服务契约另显式设置 `HINDSIGHT_CONTRACT_ISOLATED=1`、`HINDSIGHT_CONTRACT_URL`，
运行 `test/test_hindsight_contract.py` 及上述持久化集合中的真实目标用例；这会写测试 Bank、维护事实、
删除文档/Operation，并 SIGKILL 测试子进程。可复用 `test/fixtures/hindsight-contract.compose.yaml`
以独立 project 启动 1.1.0、原生 mock LLM/确定性 embedding/rrf，禁止外联；不能连接业务 Bank。
空库与专用合成夹具不证明真实旧库迁移/恢复或真实 Provider 语义质量。本轮选择空目标启用，
旧数据迁移与副本验收不在交付范围；不要将未执行项写成通过。

质量配置回归见 `test/test_memory_quality_config.py`、`test/scripts/test_memory_config.py` 和
`test/test_hindsight_contract.py`：覆盖严格配置校验、显式服务环境覆盖、SDK Bank PATCH/回读/null reset、
丢失配置 ack 后对账、正式组合的非默认 top_n、权限过滤后选取、所选来源依赖与实际提示词消费。
专用目标 PG 用例还覆盖配置不匹配时不取得来源发送资格、同 profile 的未发送动作经 CLI 修复后
复用原 UUID/payload/hash 完成，及 unknown/历史无快照/不同 profile 拒绝修复切换。
Retention PG 集合还验证配置变化后重投递复用原 action snapshot/hash/UUID，历史动作不补填当前快照。
契约 mock 不证明真实中文类型分类、稳定偏好归纳、重排阈值或实际服务模型可用性。真实 Provider
评测应按同一中文样本分别比较提取归属、一次性决定与长期偏好、冲突时间及召回结果，不能用固定
mock 的非空返回替代。部署模型与历史数据切换仍遵循[质量切换流程](../operations/deployment.md#记忆质量配置切换)。

PG16 的 native/PGroonga 对照使用独立的
`test/fixtures/hindsight-pgroonga.compose.yaml`，不改业务 Compose。先构建固定基础镜像和
pgvector 包版本的数据库镜像，再显式传入本地构建的 digest：

```bash
docker build -f test/fixtures/hindsight-pgroonga/Dockerfile \
  -t hpagent/hindsight-pgroonga:pg16-p1 test/fixtures/hindsight-pgroonga
export PGROONGA_FIXTURE_IMAGE="$(docker image inspect hpagent/hindsight-pgroonga:pg16-p1 --format '{{index .RepoDigests 0}}')"
PGROONGA_FIXTURE_BACKEND=native PGROONGA_FIXTURE_DB=pgroonga_native PGROONGA_FIXTURE_PORT=18889 \
  docker compose --env-file /dev/null -p hpagent-pgroonga-native \
  -f test/fixtures/hindsight-pgroonga.compose.yaml up -d --wait
PGROONGA_FIXTURE_BACKEND=pgroonga PGROONGA_FIXTURE_DB=pgroonga_candidate PGROONGA_FIXTURE_PORT=18890 \
  docker compose --env-file /dev/null -p hpagent-pgroonga-candidate \
  -f test/fixtures/hindsight-pgroonga.compose.yaml up -d --wait
```

启动前检查这些端口、project、卷和网络未被占用。两组数据库镜像相同，网络为 internal，
模型使用 mock/确定性 embedding/rrf；不能挂业务卷或导入业务环境文件。构建取包需要联网，
运行时不连接真实 Provider。`test/fixtures/hindsight-pgroonga/checks.Dockerfile` 可基于本地
`hpagent/hpagent:offline` 构建独立 Python 验证镜像，不向业务容器安装工具。
在相应独立网络内，以 `HINDSIGHT_CONTRACT_ISOLATED=1` 和显式
`HINDSIGHT_CONTRACT_URL=http://hindsight:8888` 运行 `test/test_hindsight_contract.py` 及
`test/test_hindsight_pgroonga.py`；后者保留合成 Bank，供数据库/API 重启前后核对。
治理与恢复另用 `test/fixtures/hindsight-pgroonga/recovery.compose.yaml`，显式指定独立 project
和 `--env-file /dev/null`；其 Redis、Temporal/PG、两组恢复 PG 及应用恢复 PG 均无宿主端口、
仅 internal 网络，不能连接业务卷。`restore-api` profile 额外启动固定 mock/embedding/rrf 的
恢复 API。API/Worker/Migration 用三个原角色和完整迁移链；SIGKILL 子进程需要显式继承
`PYTHONPATH=src:test`（容器中用对应绝对路径），不能只依赖 pytest 的进程内导入路径。
`test/web_persistence/test_memory_pgroonga_route.py` 仅在 `P4_ROUTE_ISOLATED=1`、明确的
`P4_NATIVE_URL/P4_CANDIDATE_URL` 及空恢复实例中演练初始化、配置回读、Adapter 选择与无写入回退。

切换准备入口另用 `test/fixtures/hindsight-pgroonga/preparation.compose.yaml`，显式指定
`--env-file /dev/null -p hpagent-pgroonga-p5`。它挂载实际配置编译入口，模型请求指向
同一 internal 网络的本地 embedding/rerank fixture，所有聊天请求被拒绝。
`test/web_persistence/test_memory_bank_initialization.py` 要求 `P5_PREPARATION_ISOLATED=1`
以及专用三角色 DSN 和 `P5_MEMORY_DATABASE_URL`；验证活跃账户初始化、重复执行、
禁用账户排除、已有配置冲突和非空目标拒绝。不得在业务库运行。

`recovery_app.py` 在专用应用库和实际目标服务中构造/维护合成来源，`recovery_db.py` 添加固定
Observation、mental_model 和图引用，并仅输出整行摘要/数量/schema/评分；不会输出正文/向量。
备份字节只放仓库外权限受限目录，通过显式 pg_dump/pg_restore 恢复到独立目标；同时对账
应用治理引用与记忆侧ID、向量、时间、tags、失效档案和已完成操作。切换后有写入时，夹具采用
停止发送者后两系统完整一致快照恢复，不直接回到旧备份；这不证明任意业务旧库迁移或增量日志
重放。`recovery_app.py context` 在恢复后的真实应用PG/SDK/ContextAssembly/执行lease中验证
被删除与撤权来源排除、仍可读来源入选，测试不调用回答模型。

隔离 Hindsight 内的 `/p1-fixture/verify.py` 提供 `schema`、`plans`、`snapshot` 检查，
`guards` 仅在 native 组运行。它调用镜像自带的协调器与 SQL builder，在专用 schema
验证空表转换、有数据拒绝及 mental_models 特例；查询计划在默认优化器设置下采集，
包括只出现在 context/text_signals/name/content 的候选。小 Bank 可能选择其他索引或顺序扫描，
此时 PGroonga 分数可为零；这些工程检查不证明中文语义质量或真实数据迁移通过。

资源撤权记忆回归在 `test/web_persistence/test_memory_governance.py -k resource_revoke`：
覆盖登记前、登记后发送前、Operation 查询期间的撤权，旧事件重放、重新授权后新 Run 的正向对照，
以及已接受删除/丢失删除响应后的撤权和载荷清理。`test_memory_revocation_migration.py` 在同一专用
PG 另建临时数据库，以完整 001–077 链构造旧夹具，再验证 078 与重复执行；不使用业务副本，
不证明真实旧数据迁移或恢复。新增 Run 标记只能置 true，测试同时检查终态输入仍不可改写。

内容链离线回归见 `test/test_research_excerpts.py`、`test/test_research_domain.py` 和 `test/test_web_artifacts.py`，公开来源片段 fixture 为 `test/fixtures/research/content_passages.json`，保留原文出处、版本 hash 与偏移。覆盖冻结的句子截断、共同限制、公平性及取消条件反例，另用合成的入馆政策覆盖不同主题；验证精确偏移、去重、字符/UTF-8 上限、claims→Markdown→HTML 的传递及引用标记丢失反馈。确定性模型夹具只证明传递与结构检查，不证明真实模型保持事实，旧失败批次不改判。

模型协议测试使用MockTransport，不证明真实Provider。Research 发布/保存 SIGKILL harness 使用实际默认 30 秒 lease；配套 PG 用例让阶段跨越初始租约，验证续租与拒绝抢占。其余短 lease 用例只证明对应受控窗口，不能外推生产恢复速度；机制见[执行与恢复](../architecture/execution.md)。测试范围和排除项必须随结果记录。

## 前端与浏览器

```bash
make web-install
make web-lint web-typecheck web-test
make web-build
```

`web-typecheck`同时检查src、E2E、测试harness和全部Playwright配置；类型通过不代表浏览器用例已执行。

若已有服务使用`web/dist`，验证构建应写独立目录，避免影响现有预览：

```bash
cd web
npm run build -- --outDir /tmp/hpagent-verification-build
```

合同测试默认启动本机5311预览，需先构建；可用 `HPAGENT_E2E_BUILD_DIR` 指定独立构建目录，`HPAGENT_ENTRY_PORT` 指定端口。也可通过`HPAGENT_ENTRY_BASE_URL`复用操作员自己创建的独立静态服务（例如上述输出目录）；它不配置或启动API。`HPAGENT_BROWSER_EXECUTABLE`可覆盖浏览器路径，默认使用Playwright Chromium。

正式API套件：

```bash
# 延续上方显式隔离DSN与Redis；不可使用日常业务库。
export E2E_ISOLATED=1
export FILE_STORE_ROOT=/tmp/hpagent-dedicated-e2e-files
export WEB_API_PORT=8180
export WEB_DEV_PORT=5273
export WEB_PUBLIC_ORIGIN=http://localhost:5273
make e2e-canonical
# 或 make e2e：整个正式套件
```

`e2e-backend.sh`要求隔离标记与显式资源配置，启动自己的API并注册alice/bob；默认端口8180/5273可覆盖，Vite使用strictPort，Playwright不复用已有服务。套件串行执行，避免共享alice登录相互失效。`--list`只验证配置/导入和用例发现，不执行浏览器或后端，不能作为浏览器通过证明。

`e2e/config/shared.ts` 统一端口校验、启动与隔离约束。默认 API 套件排除纯夹具、entry 与 visual；`playwright.canonical.config.ts` 从正式源用例选择核心回归，并构建独立生产产物。需要容器内 API 时，显式设置 `E2E_ISOLATED=1` 和 `HPAGENT_E2E_API_URL` 指向已创建的专用 API；仍启动自己的前端，API 的允许来源须匹配前端端口。

`npm run test:e2e:visual` 要求 `HPAGENT_VISUAL_ISOLATED=1` 和显式 `HPAGENT_API_TARGET`；自行准备专用 API、账户和资源，默认前端5290，可用 `HPAGENT_VISUAL_PORT` 覆盖。执行详情专项配置复用同一夹具启动器。所有浏览器证据写入 `test-results`，不使用带日期的结果目录。

截图、trace和JSON输出在忽略的`web/test-results/`，新实验输出在`artifacts/benchmark-runs/`。证据需形成独立受限批次，保留失败和环境错误；不要将原始运行记录或验收摘要加入Git。

## 覆盖与结果解释

组件/store单测覆盖请求代际、授权、幂等、SSE、草稿/附件和Inspector身份；正式E2E覆盖真实页面与隔离API。合同浏览器还验证生产构建没有历史候选控制、查询参数不启用评审模式，以及减动/视口/认证恢复等行为。历史工具中未证明等价的断言继续保留，不能因当前套件通过就宣称全部替代。

当前UI约束见[Web架构](../architecture/web-ui.md)。真实供应商、生产Worker、QQ外发、真机IME/软键盘/读屏、其他浏览器和恢复演练分别验收；单元及隔离测试不能证明这些路径通过。

正式 Fake Executor 只完成 chat/artifact，不执行所有提醒/研究 Work。Artifact 复用局部 HTML 能力与有限 Chat Run，受同 Conversation 单 active 槽约束；排队 Work 不占此槽。完整确认使用新建隔离 PG/Redis/API/File Store，不延长超时或放宽业务容量掩盖限制。独立 auth-transition 实验室的 CPU/Flip/资源释放断言不能仅凭正式 UI 套件通过宣称已等价覆盖。

通用文字闭环由 `test/work_domain/test_text_closure.py` 验证：首 Run 的 checkpoint/ready 经调度与 Outbox 自动接续，第二 Run 通过生产文字能力发布、登记 typed evidence、完成 Work、投递 inbox，引用与 GC 保留一致。覆盖显式验收、模型漏调用验收工具、最终 JSON 漏列证据及多次发布后强制最终轮次输出伪工具文本；专用子进程分别在父观察提交前、观察及证据已提交但响应未返回时 SIGKILL，恢复复用同一 operation/字节且仅一次观察和证据。`test/work_domain/test_output_evidence.py` 覆盖观察与证据原子回滚/重放、终态重放、旧 revision/fence、其他必需条件、显式等待、文件可用性复查，以及普通文件或未观察成果不能自动补证。`test/work_domain/test_text_publish.py` 另覆盖文件已发布、typed result 提交前的响应丢失重放，以及暂停后的旧子执行拒绝。此处模型是确定性夹具，不证明真实模型决策。Work 领域 fixture 会保留其他用例的 queued/running 事实；组合验证应使用独立测试库并避免与会 TRUNCATE 的 API/持久化 fixture 共库并发；调度闭环用例的 due loop 同样不得与其他领域用例共库并发，保持生产容量默认值。

Workspace 保存前置条件由 `test/test_workspace_save_policy.py`、`test/work_domain/test_save_admission.py` 和 `test/web_api/test_work_save_admission.py` 覆盖：Main 普通交付默认不请求保存、显式待补目录、真实目录/Work 授权、无效或跨账户目录、撤权后准入、旧命令重放/显式修订、累计预算保留与旧 fence 拒绝。隔离 PG 用例通过生产文字 publisher 和 typed evidence 分别完成普通发布与授权保存，不调用真实模型/检索；这些用例不能证明真实模型会正确识别用户的保存意图。真实浏览器复验应分别提交仅下载 Markdown 与明确保存到 Workspace 的请求，后者检查未选目录/未授权时没有后台 Run，正常修订及显式授权后才执行。

Main/UI 组合契约由 `test/work_domain/test_main_ui.py`、能力结果持久化查询用例和 `test/web_api/test_work_foundation.py` 验证消息来源、接受重放、revision 与账户隔离；群聊裁剪沿用 durable review 用例。`web/e2e/composable-main.spec.ts` 使用隔离 Fake API 验证离页/刷新找回、准确来源消息和普通聊天独立；它只证明后台协调 Run 的持久身份不丢失，实际持续执行与新 fence 恢复由 `test/work_domain/test_continuation_temporal.py` 验证。自然语言工具描述和脚本调用测试不证明真实模型决策，真实 Provider / QQ 外发仍需单独授权。

产品保存/修改回归还包括 `web/e2e/artifact.spec.ts` 的自然语言展示修改、新旧源码副本保存、旧版本不变、刷新从原消息找回，以及保存发布 HTML 后从空间打开准确交互版本；`web/e2e/ui-5-tasks.spec.ts` 检查目录选择不隐式授权、通过权限编辑器显式授予 Work 创建子项权限。均使用专用 Fake API，生成内容不证明真实模型事实质量。`test/work_domain/test_composable_chain.py` 另核对 Generic 父保存投影、从已完成 Work 的 Artifact 命令继续生成与保存新版、Research 不重跑及旧保存仍对应旧字节；模型和来源仍是确定性夹具。`test/web_persistence/test_workspace_v41_p5.py` 保留 trace 账户隔离与普通文件无 Artifact 关联的用例。

执行查询回归从任务高级详情进入：窄屏关闭所属 Inspector 后、桌面所属 Inspector 仍挂载时，迟到响应均不得覆盖新前景。Model Input 回归从 Trace 节点的 snapshot_id 查询；无 Trace 不发输入查询，账号可见范围拒绝须清除已有正文，并拒绝在途正文回灌。用例见 [执行查询与前景](../../web/e2e/ui-7-self-review.spec.ts) 与 [执行详情和输入权限](../../web/e2e/ui-3-execution.spec.ts)。

有界推进用例在 `test/work_domain/test_progress.py`：跨 Run 与同 Run 保护、样本重放、内容/摘要身份、固定 daily 基线、失败不推进、预算实际增额、旧生产者及暂停/停止。浏览器 `web/e2e/entry/entry.spec.ts` 的 progress protection 用例使用 API 路由夹具，在桌面/窄屏核对原因和干预入口，不证明真实 Provider 决策或 QQ 投递。基础设施集合使用独立资源；同一 PostgreSQL cluster 上新建多个测试库时，完整迁移需串行执行，旧链包含 cluster 级角色配置。

组合全链的确定性决策用例见 `test/work_domain/test_composable_chain.py`：以同一生产目录和 ToolExecutionWorkflow 执行 Research → 不足 HTML → 复用 Markdown 修正 → 固定旧 HTML 修改 → 父侧保存及 typed evidence 验收，并模拟成功结果提交后响应丢失。Research/模型/来源适配器均为本地夹具，不向示例 URL 发请求；这不证明真实模型自主决策或真实用户修订命令。用户修订、暂停停止与权限/GC 继续由对应 Work/API/浏览器用例验证。`test/work_domain/test_capability_harness.py` 的新增候选能力用例只登记新定义、绑定与测试实现，复用原 ToolExecutionWorkflow；`test/test_capability_composition.py` 检查入口目录一致与缺服务时隐藏能力。这些用例不需要新增 schema，仍使用完整迁移链。

等待恢复还覆盖 `test/test_durable_wait_eviction.py` 的缓存协程销毁和 `test/test_tool_execution_approval_temporal.py` 的真实 Worker 重启。缓存淘汰不等同取消，前者不得创建 finish wait 命令，后者仍需由权威 probe 与持久控制 Activity 收敛。

## 真实浏览器全链路

后端自然语言委托、真实 Search/Fetch、Markdown 发布、完成证据、Work completed、inbox 接受与本人认证读取已在独立环境验证；这不表示部署环境或网页旅程已通过。后续在另一固定 checkout/提交的目录进行浏览器验收，使用自己的 PG、Redis、File Store、Temporal 服务/namespace 和端口，配置见[开发环境](setup.md)与[配置参考](../reference/configuration.md#web-retriever-与-research)。不要借用当前开发目录的服务或清表 fixture。

1. 登录网页，从聊天输入框提交普通中文后台请求，页面显示真实任务身份；核对同一原消息只建立一个 Work。
2. 确认接受后关闭标签页或浏览器，断开聊天/SSE，停止前端轮询；后台应自行推进。不要点击 advance 或由脚本补建任务。
3. 稍后重新打开网页，必要时重新登录，从原聊天或任务列表找回同一个 Work，看到权威完成状态。
4. 在本人 inbox 查看完成通知，点击「查看任务」进入对应任务；刷新后无重复完成通知。
5. 在「成果与执行」展开输出文件并下载 Markdown；检查非空中文正文、真实来源链接、准确文件身份，以及登录失效/换账号后的拒绝。当前无普通 Markdown 的页内正文预览。
6. 单独按需要检查目标环境 Worker/API 重建、文件卷权限、网络直连与重启后可读性；不要把隔离短 lease 或夹具恢复结果视为实际部署恢复证明。

保留原消息、Work/revision、后台 Run、工具调用/观察、准确成果/完成证据、通知与接受记录的关联。浏览器截图和 trace 写忽略目录或仓库外。专业报告评分、HTML、长期保存和 QQ 外发不属于此简单文字旅程的门槛。单样例成功不证明任意任务的长期自主能力。

本页不授予新增付费调用额度；执行前按最新任务授权读取原累计账本，失败与未知请求均计入上限。不要从历史文档中的 token 数字推导新的授权，也不复制或重置账本。

## 专项验收工具

`python3 scripts/acceptance/s7_1.py --help` 是已有 Research/HTML 专项工具，支持冻结、隔离资源准备、真实执行、核对与清理；其固定样本/Provider/故障矩阵不等同于上述通用文字浏览器验收。freeze/preflight 不发模型请求；prepare 可能读取明确的部署模型字段，`prepare --fixture-services` 只建立新 PG/Redis/Temporal；run 会付费且需显式执行与故障选项；cleanup 只删除本批所有权标签资源，保留原累计账本和失败/未知证据。具体参数与副作用见[脚本入口](../../scripts/README.md)。

专项工具中的历史授权升级或 unknown 决议入口必须匹配当前人类授权，不能作为通用重试或提额开关。离线工具安全回归见 `test/scripts/test_s7_acceptance.py` 和 `test/test_s7_hooks.py`。普通浏览器验收不要求执行旧 S7 矩阵、专业质量门槛或 MVP source-precheck。

Web Retriever v1 的模块边界见[设计总结](../architecture/capabilities.md#web-retriever-v1-设计总结)。离线回归入口为 `test/test_web_retrieval.py`、`test/test_deepseek_retrieval.py`、`test/test_local_fetch_markdown.py` 与 `test/test_main_web_tools.py`，覆盖 Provider 选择、来源范围、网络安全、格式/大小、身份、预算和恢复。`test/fixtures/research/local_fetch_html.json` 是可执行格式夹具，继续保留。

持久化预算回归为 `test/web_persistence/test_web_retrieval_budget.py`；Main Command → Temporal → 工具 → 回复的隔离回归为 `test/web_persistence/test_main_web_tools.py`，需要显式专用 PG/Temporal。模型及供应商夹具只验证调用契约；Research 的证据选择、合成和报告质量按其自身流程测试，与 Retriever 模块冻结分别处理。

Main Tool-first 与交付格式的隔离验证入口：`test/test_delivery_contract_unit.py` 覆盖格式字节、约束 schema、Provider 冻结和旧 text 定义兼容；`test/work_domain/test_main_tool_first.py` 用确定性 Brain 与真实 PG 检查多轮修正、伪调用、终止/命令回执分离、响应丢失幂等与强制收尾；`test/work_domain/test_delivery_format.py` 覆盖 Markdown/DOCX/双格式、无显式验收工具、同 ResultRef 配对、重复发布、发布后崩溃、检查失败、旧 revision/fence、保存和其他必需条件。`test_text_closure.py` 的专用 SIGKILL Worker 同时覆盖旧契约与格式契约；Web 任务编辑器测试检查格式修订保留独立条件。Main Temporal 确定性模型使用真实 respond_directly 动作，历史 History 仍通过 Replayer 校验。以上均不新增真实 Provider 请求；运行时使用独立数据库、namespace 和 File Store。

回复声明回归见 `test/work_domain/test_main_response.py`：普通记忆描述、否定、条件和引用不误判，显式业务声明须与具体回执动作/状态匹配；组合持久化与幂等行为仍由 `test_main_tool_first.py` 验证。Workspace 的 `test/web_persistence/test_workspace_v41_p2.py::test_activity_resources_restore_selected_input_and_recheck_revocation` 验证选择文件后销毁并重建 Activity，读取工具重新可用且固定输入可读，撤权后恢复拒绝。仅在一个临时 scope 内选择并读取，不能代替跨 Activity 回归。

真实记忆验收使用专用账号和唯一合成事实，每次召回另开会话并明确 opt-out，分别核验 Run、Retain/治理终态、实际 Model Input 来源依赖及远端文档。纠错重放保留原幂等键和 payload；失效、恢复与指定来源删除逐项执行。文件撤权先证明授权后的真实正文读取，再撤销准确 grant，检查实际访问拒绝、旧 Run 保留禁用、来源隔离和新会话不再注入；所有者下载不代表 Agent 仍获授权。QQ、跨账号、在途取消与迟到响应按各自授权范围单独记录，不能从上述用例推定通过。

## Hindsight 版本与隔离契约

契约测试使用固定 Hindsight 1.1.0 镜像（Compose 中记录 index digest）及
`hindsight-client==1.1.0`。`test/test_hindsight.py` 与
`test/test_hindsight_retain_document.py` 通过本地 HTTP 夹具运行实际安装的 SDK，
验证非空 strict scope、固定时间/metadata、来源证据和错误分类。

真实服务契约使用独立 `test/fixtures/hindsight-contract.compose.yaml`：
`docker compose -f test/fixtures/hindsight-contract.compose.yaml up -d`。
该拓扑只有专用 PG、Hindsight 和确定性 embedding 夹具，网络禁止外联；LLM
使用目标服务原生 mock，reranker 使用原生 rrf。健康后显式设置
`HINDSIGHT_CONTRACT_ISOLATED=1 HINDSIGHT_CONTRACT_URL=http://127.0.0.1:18888`
运行 `python3 -m pytest -q test/test_hindsight_contract.py`。Docker daemon 不在本机时，
测试进程须加入该专用网络，URL 改用 `http://hindsight:8888`。测试新建随机账户 Bank，
验证异步 Retain/Operation、facts/Observations/附带来源的私聊/双群/跨账户隔离、
事实纠正/失效/恢复、Document scope PATCH/Delete 与终态 Operation 删除。
Operation 不存在的原生契约是 HTTP 200 + `status=not_found`，Document/Fact 不存在为 404。

结束运行 `docker compose -f test/fixtures/hindsight-contract.compose.yaml down -v`。
这些结果只证明真实目标服务加模型夹具的接口和隔离行为，不证明真实 Provider 的提取、
归纳或语义质量；禁止把测试 URL 指向业务服务。Recall 默认关闭；空目标业务切换与真实 Provider 质量另行验收，
不以隔离测试证明旧数据迁移或生产可用。


可靠来源链的隔离持久化入口为 `test/web_persistence/test_memory_retention.py`，使用上述
三个专用 DSN 与完整迁移链（001–078），不连接业务 Hindsight。覆盖重复终态事件、请求冲突、
固定 UUID/载荷、丢 ack、双 Worker、同名 Worker 旧投递 fence、generation 变更、超过原投递
上限的正常轮询、永久 4xx、服务故障恢复、原生 retry、来源/Observation 读取限制及最小权限。
其中 HTTP 夹具配合实际官方 SDK，子进程在接受后响应前被 SIGKILL，恢复仍使用相同请求。
这是应用进程故障与接口夹具证明；真实 Hindsight 去重/隔离沿用上述 1.1.0 契约测试，
不作为真实 Provider 语义质量或数据库断电恢复证明。QQ 可信来源组合另见
`test/web_persistence/test_qq_canonical_ingress.py`；后台消费/恢复挂载见
`test/test_web_background_tasks.py`。证据放忽略目录，结束清理本轮专用测试资源。

治理闭环入口为 `test/web_persistence/test_memory_governance.py`、
`test/web_api/test_memory_governance.py` 和 `test/work_domain/test_memory_context.py`。
使用完整迁移链（001–078）、专用 API/Worker/Migration DSN；设置上述
`HINDSIGHT_CONTRACT_ISOLATED` 和 `HINDSIGHT_CONTRACT_URL` 时，还会执行真实目标服务的
应用组合场景及实际子进程 SIGKILL：纠错、失效/恢复、范围 PATCH、来源与已登记晋升副本
删除、原事实/归档/旧 Observation 验证、Operation 载荷清理和队列重放。
丢失发送资格证据的场景必须维持 uncertain 和去重记录，不能用 Document 404 改判完成。
新建 source 的 Retain、正常轮询、restore 不增加失效序号；私聊/双群按实际 scope 判交集，
未知范围或所需历史丢失时才扩大保护。

`test/web_persistence/test_memory_governance_temporal.py` 需要显式专用 Temporal host 与
一致的 `TEMPORAL_NAMESPACE/TEMPORAL_TEST_NAMESPACE`。实际 Workflow/Activity 先提交
Snapshot/dispatch，再在请求在途期间撤回，验证旧响应不能推进工具或 fallback 成功提交。
PG 集合另验证历史助手消息复用、旧 base 重绑定、Snapshot/fallback、final 与子执行继承。
它们不接入 Work Recall/Retain，也不证明真实 Provider 语义质量、业务数据迁移或部署切换。


OneBot11 专用拓扑见 `test/fixtures/onebot11.compose.yaml`，显式指定新的 project 和
`ONEBOT_EVIDENCE_DIR`（仓库外），使用 `--env-file /dev/null`。它包含独立 PG 三角色、
Redis、Temporal default namespace（专用服务）及 File Store 卷；无宿主端口、网络 internal。
夹具应用 PG 关闭 fsync/synchronous_commit 以减少临时迁移回放成本；测试证明逻辑事务与应用进程恢复，
不证明 PG 断电/存储故障持久性。
`checks` 镜像需预先准备 pytest/ruff/mypy 与 requirements-dev，不借用运行中业务容器。
在 checks 内运行 `make lint typecheck test-unit test-db test-api test-temporal`；
`test_onebot11_transport.py` 用实际回环 WS 对端证明鉴权/ack/连接代次与失败分类，
不连接 QQ、不调用真实模型。新增渠道需额外定向 Ruff；真实扫码、媒体、断线恢复和观察另验。

`test/web_persistence/test_onebot11_receipts.py` 组合实际回环 WS 与 PG；
`test/fixtures/onebot11_delivery_sender.py` 是专用 SIGKILL 输入，验证 provider ack 后、
PG finish 前被杀的发送者只恢复为 uncertain，不自动重发。它不连接真实 QQ。

真实 QQ 验收另用本人明确选择的私聊/测试群，并保持专用 PG、Temporal namespace 和 File Store。
确定性 Brain/Sandbox 夹具可组合真实入站、命令、Outbox、Temporal 和逐片投递，不能据此宣称
真实模型或 Sandbox 执行通过。图片读取单独检查实际来源；测试下载应限制公开 QQ CDN、
禁用重定向并限制读取字节，只记录状态、媒体类型和文件头验证结果，不保存签名 URL 或图片正文。
Worker 重启、反向 WS 网络中断与 QQ 客户端自身离线/重启分别验收；在途发送故障产生的
uncertain 必须保留显式决议，不能为清理测试状态而重发或改写成 accepted。
