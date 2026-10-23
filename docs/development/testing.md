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

静态检查范围：Ruff覆盖Conversation、Web、Work、Run、Application、Bootstrap、Agent Workflow/Activity、Orchestration、Research及全部test；mypy覆盖Conversation、Web、Persistence、Web API、Work、Run与Agent Workflow。其他通用能力/渠道适配包尚未全部纳入，不以这两个命令证明全仓库静态检查通过。

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

Main/UI 组合契约由 `test/work_domain/test_main_ui.py`、能力结果持久化查询用例和 `test/web_api/test_work_foundation.py` 验证消息来源、接受重放、revision 与账户隔离；群聊裁剪沿用 durable review 用例。`web/e2e/composable-main.spec.ts` 使用隔离 Fake API 验证离页/刷新找回、准确来源消息和普通聊天独立；它只证明后台协调 Run 的持久身份不丢失，实际持续执行与新 fence 恢复由 `test/work_domain/test_continuation_temporal.py` 验证。自然语言工具描述和脚本调用测试不证明真实模型决策，真实 Provider / QQ 外发仍需单独授权。

执行查询回归从任务高级详情进入：窄屏关闭所属 Inspector 后、桌面所属 Inspector 仍挂载时，迟到响应均不得覆盖新前景。Model Input 回归从 Trace 节点的 snapshot_id 查询；无 Trace 不发输入查询，账号可见范围拒绝须清除已有正文，并拒绝在途正文回灌。用例见 [执行查询与前景](../../web/e2e/ui-7-self-review.spec.ts) 与 [执行详情和输入权限](../../web/e2e/ui-3-execution.spec.ts)。

有界推进用例在 `test/work_domain/test_progress.py`：跨 Run 与同 Run 保护、样本重放、内容/摘要身份、固定 daily 基线、失败不推进、预算实际增额、旧生产者及暂停/停止。浏览器 `web/e2e/entry/entry.spec.ts` 的 progress protection 用例使用 API 路由夹具，在桌面/窄屏核对原因和干预入口，不证明真实 Provider 决策或 QQ 投递。基础设施集合使用独立资源；同一 PostgreSQL cluster 上新建多个测试库时，完整迁移需串行执行，旧链包含 cluster 级角色配置。

Web Retriever v1 的模块边界见[设计总结](../architecture/capabilities.md#web-retriever-v1-设计总结)。离线回归入口为 `test/test_web_retrieval.py`、`test/test_deepseek_retrieval.py`、`test/test_local_fetch_markdown.py` 与 `test/test_main_web_tools.py`，覆盖 Provider 选择、来源范围、网络安全、格式/大小、身份、预算和恢复。`test/fixtures/research/local_fetch_html.json` 是可执行格式夹具，继续保留。

持久化预算回归为 `test/web_persistence/test_web_retrieval_budget.py`；Main Command → Temporal → 工具 → 回复的隔离回归为 `test/web_persistence/test_main_web_tools.py`，需要显式专用 PG/Temporal。模型及供应商夹具只验证调用契约；Research 的证据选择、合成和报告质量按其自身流程测试，与 Retriever 模块冻结分别处理。
