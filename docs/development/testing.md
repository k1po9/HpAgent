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
