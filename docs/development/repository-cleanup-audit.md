# 上线前仓库清理与收束审计

日期：2026-10-17（Asia/Shanghai）。审计基线：`316e1184410fe4d3e6f902d0c80af10826b16a0f`。扫描开始时 Git 工作区干净。

本轮仅扫描并新增此报告；未删除、迁移或修改原文件，未启动部署、执行数据库操作或运行会调用真实模型的脚本。统计不包含本报告。

## 1. 结论与统计口径

主要问题是历史证据与工具持续积累，以及当前文档入口落后于最近 UI 接受基线。不能把所有非源码文件都当成垃圾：迁移、配置、测试 fixture、运行时 Skill 和当前验收合同需要保留。

- Git 跟踪：2,710 文件，86.98 MiB（工作区文件逻辑大小，不是 Git 压缩体积）。
- 被忽略的本地文件：45,224 文件，4.77 GiB；未忽略的未跟踪文件：0。
- `.git/` 磁盘占用约 1,021 MiB；`git count-objects -vH` 显示 loose objects 925.75 MiB、pack 87.66 MiB。
- `artifacts/` 占已跟踪文件数 61.1%，占已跟踪文件逻辑大小 83.1%。
- 跟踪的 Markdown 共 209 份；其中 `docs/` 为 82 份 Markdown，另含 5 张参考图。
- 跟踪文件中 ≥1 KiB 的完全相同内容有 78 组，重复副本合计约 4.73 MiB；相同内容可能对应不同证据角色，不能仅按哈希删除。

### Git 跟踪文件

| 目录 | 文件数 | 逻辑大小 |
| --- | ---: | ---: |
| `(root)` | 9 | 0.04 MiB |
| `.github` | 1 | 0.01 MiB |
| `artifacts` | 1,655 | 72.27 MiB |
| `config` | 10 | 0.04 MiB |
| `docs` | 87 | 5.97 MiB |
| `persistence` | 60 | 0.24 MiB |
| `prototypes` | 24 | 2.16 MiB |
| `scripts` | 33 | 0.31 MiB |
| `src` | 273 | 1.71 MiB |
| `test` | 170 | 1.53 MiB |
| `tools` | 2 | 0.00 MiB |
| `web` | 386 | 2.70 MiB |

### 已忽略的本地文件

| 目录 | 文件数 | 逻辑大小 |
| --- | ---: | ---: |
| `(root)` | 1 | 0.00 GiB |
| `.data` | 29 | 0.00 GiB |
| `artifacts` | 23,558 | 1.60 GiB |
| `dist` | 3 | 2.89 GiB |
| `src` | 290 | 0.00 GiB |
| `tools` | 5 | 0.00 GiB |
| `ui_skill_delete_after_done` | 113 | 0.02 GiB |
| `web` | 21,225 | 0.26 GiB |

这两张表不含 `.git/`；目录 `du` 数字与文件逻辑大小因块分配和目录开销不同。`dist/` 中约 2.89 GiB 是离线镜像 tar，并非普通前端编译缓存。

## 2. 清理处置清单

| 对象 | 证据与性质 | 建议处置 |
| --- | --- | --- |
| `artifacts/benchmarks/` | 442 文件；Temporal 原始运行目录含 SQLite、同步标记及日志 | 提取当前汇总、参数、种子、环境与复现命令；原始运行输出移至仓库外归档，禁止后续纳入 Git |
| `artifacts/architecture-audit/` | 192 文件，14.07 MiB，含大 CSV、旧阶段报告与辅助审计脚本 | 将仍有效的约束写入当前架构文档；保留一份决策索引，原始证据移出日常源码树 |
| `artifacts/product-acceptance/` | 1,003 跟踪文件，56.19 MiB；另有大量本地忽略产物 | 精选当前接受决定、合同、源码清单、验证结论和必要反例；旧截图、trace、视频、独立站点 bundle 外置归档 |
| `artifacts/ci-remediation/` | 17 个历史修复记录 | 将仍有效的要求进入测试/CI；历史记录外置 |
| `docs/design/` | 45 文件，其中 5 张参考图 | 抽取 R1–R6 当前产品/动效合同；历史 Prompt、逐轮计划和候选设计退出当前阅读路径 |
| `docs/implementation/` | 18 文件，带日期的交接与验收 | 合并成当前交付状态和已知限制；历史不再承担产品说明职责 |
| `web/scripts/` | 106 文件，其中 105 个 `.mjs`、1 个 E2E 启动 shell | 保留 `e2e-backend.sh`；逐项将可复用断言进入正式 E2E，其余评审脚本归档后移除 |
| `web/r416-e2e/` 与 `web/playwright.r416.config.ts` | 11 个阶段测试文件及独立配置；默认 Playwright `testDir` 为 `web/e2e` | 审核有效断言，迁入正式 E2E 或接入专用门禁后退役旧入口 |
| `prototypes/auth-transition/` | 24 文件，含独立依赖、测试、截图和视频 | 确认产品已覆盖有效行为后归档；同步修正 auth 交接中的复现路径 |
| `ui_skill_delete_after_done/` | 本地忽略，113 文件，约 19.18 MiB；历史 Prompt 仍引用 | 先保全必要设计来源与许可记录，再移出工作目录；不按目录名字直接删除 |
| `dist/` | 本地忽略，离线镜像 tar + manifest + SHA256 | 移至独立发布制品目录/存储；核验校验和与恢复路径后再清理本地副本 |
| `web/dist/`、测试输出、`__pycache__/` | 可再生成的本地文件 | 核对没有运行进程使用后清理；`node_modules` 是依赖缓存，清理收益与重装成本另算 |
| `.env`、`.data/`、`tools/vectors/` | 凭据和本地运行状态，均被忽略 | 排除在普通仓库清理之外，按备份与数据保留策略处理 |
| `tools/skills/pdf-processing/SKILL.md` | 当前 worker 配置仍加载 `tools/skills/` | 保留，属于运行时资源，不是开发遗留 Skill |
| `test/fixtures/`、`persistence/migrations/` | 测试基准/历史 schema 及 checksum 契约 | 保留；不能因历史或非源码扩展名移除 |

跟踪的 `.sqlite` 共 70 个、`.ready` 210 个、`.boundary` 80 个、`.release` 50 个、`.log` 75 个。其中前四类集中在 Benchmark 原始运行证据。`.gitignore` 对已跟踪文件不生效，因此已有 `*.log` 规则仍会保留历史日志。

## 3. README 与文档收束

已确认的漂移：根 README、`docs/README.md`、`docs/implementation/README.md` 主要指向 UI-1～UI-8；最新提交 `316e118` 与 `docs/design/ui-iteration-2026-10/R6-ACCEPTED-CONTRACT.md` 已记录 R1–R6 正式接受。UI 接受与生产上线验收是两项独立结论，文档更新时均需保留。

建议职责：

| 入口 | 应承担的内容 |
| --- | --- |
| `README.md` | 产品用途、当前能力、唯一快速启动路径、生产部署入口、开发入口、目录图与文档导航；去掉重构叙事 |
| `docs/README.md` | 按使用者/开发者/运维导航；标明当前基线与历史材料入口 |
| `docs/architecture/` | 从当前代码核实的运行时、状态、可靠性、Workspace 和 Web UI 契约；吸收仍有效的接受合同 |
| `docs/development/` | 本地环境、测试分层、脚本用法和扩展方式 |
| `docs/operations/` | 新部署、升级、回滚、恢复、日志、真实链路验收；明确数据库迁移限制 |
| `docs/reference/` | 配置、API、目录及服务注册事实；避免复制其他章节 |
| 历史记录 | 仓库外归档 + 简短索引（基线提交、校验和、存放位置、复现边界）；若必须跟踪，集中一处并标历史 |

应优先更新：`README.md` → `docs/README.md` → `architecture/web-ui.md` → `operations/web-workbench.md` → `development/testing.md` → `reference/repository-layout.md` → 部署/备份/运行手册。正文以代码核实后的现状编写，不把旧报告整体复制成当前规范。

Markdown 本地内联链接启发式扫描：44 处目标不存在、31 处目标存在但被 Git 忽略（fresh clone 不会携带）。前者集中在 2026-10-04 历史验收材料；后者集中在 UI 迭代历史计划。`README.md` 和 `docs/README.md` 未命中这两类问题。此扫描不验证远程 URL、锚点、引用式链接、图片或动态路径，不等于完整文档验证。附录列出全部命中。

## 4. 脚本收束

后端/运维 `scripts/` 已有按用途分组，可在此基础上收束，不必重新发明目录。

| 分组 | 跟踪文件数 | 处理 |
| --- | ---: | --- |
| `scripts/acceptance` | 1 | 核对职责和现有调用后保留；Benchmark 区分代码/输入/输出 |
| `scripts/benchmarks` | 12 | 核对职责和现有调用后保留；Benchmark 区分代码/输入/输出 |
| `scripts/check` | 8 | 核对职责和现有调用后保留；Benchmark 区分代码/输入/输出 |
| `scripts/dev` | 2 | 核对职责和现有调用后保留；Benchmark 区分代码/输入/输出 |
| `scripts/operations` | 8 | 核对职责和现有调用后保留；Benchmark 区分代码/输入/输出 |
| `scripts/queued-run-diagnostic.sql` | 1 | 迁入 `scripts/check/` 或 `scripts/operations/`，同步引用 |
| `scripts/schema-runtime-smoke.sh` | 1 | 迁入 `scripts/check/` 或 `scripts/operations/`，同步引用 |

前端 106 个脚本中，98 个包含 `C:/Program Files/...` 浏览器路径；多个脚本硬编码本地端口、测试账户和 `attempt-*` 输出位置。此类脚本多数是评审过程工具，不能直接作为可移植的发布验证。`web/playwright.config.ts` 明确调用 `scripts/e2e-backend.sh`，需要保留该路径或同步配置。

建议为 `scripts/` 增加一份入口索引，每项注明用途、依赖、参数、是否写数据库/发起模型请求、是否可用于生产、输出路径与清理方式。统一 `--help`、环境变量、失败退出码及仓库外输出位置；由 Makefile/package scripts 暴露少量稳定命令。

具体缺口：`scripts/check/release-smoke.sh` 自称生产 Compose 验证，但 `SMOKE_PROFILE` 默认 `web`，网关 URL 却默认 80 端口；它会 build/up、登录并发送真实请求。应修复默认拓扑、核对当前 API/认证契约并在隔离环境复验，不能在本轮扫描中直接运行。

## 5. 上线前还要处理的事项

### P0：凭据与数据边界

- 在已跟踪 `artifacts/product-acceptance/ui-refactor/initial/logs/backend-results.xml:50` 发现具有 Provider Key 格式的值，位于环境字典的 `ALIBABA_BAILIAN_API_KEY` 字段。按疑似凭据泄露处理：核实来源，若为真实有效密钥则撤销/轮换，清理当前日志并评估已有仓库访问范围和 Git 历史。报告不复述该值，未使用其进行任何请求。
- 当前 `.env` 被 Git 忽略，`git log --all -- .env` 未发现同路径提交；这不能排除改名凭据、其他分支之外历史或日志泄露。对跟踪文本的窄范围私钥/常见 token 正则未发现其他候选，不能替代完整 secret scan。
- 根 `.dockerignore` 未排除 `.env`、本地设计包和 `prototypes/`。根上下文镜像目前使用显式 COPY，因此不能据此断言 `.env` 已在镜像层中，但构建上下文仍应主动排除凭据与无关材料。
- Document Worker 的 context 为 `./src`，没有 `src/.dockerignore`，且 Dockerfile 执行 `COPY . .`；应防止 `src/.data` 等本地文件随构建进入镜像。
- `backup.sh` 默认输出到根 `backups/`，当前没有相应忽略规则；应将备份默认移出仓库并防止误提交业务数据。

### P1：发布的可复现性与恢复

- 根 `requirements.txt` 与 `src/requirements.txt` 当前完全相同；属于两个易漂移入口，应建立单一来源，结合各 Docker context 调整。Python 依赖多为范围约束，未发现 Python lock 文件；前端已有 `package-lock.json`，应保留并用 `npm ci`。
- Python Worker/Document 镜像使用 3.11，API/Migrate 和 CI 使用 3.12，README 描述 3.11+；确认支持矩阵或统一版本。
- `web-prod` 共用的服务仍挂载宿主 `src/`（主 Worker、API、Migration 等），当前发布内容会受宿主源码影响；评估生产 overlay，采用可追溯镜像及版本标签，明确配置和数据挂载。
- NapCat 默认 `latest`；其他镜像也有可漂移 tag。若启用 QQ，应固定验证过的镜像版本/摘要。主 Worker 下载 nsjail 二进制未见校验摘要，应纳入构建来源核验。
- `backup.sh` 明确不含 Temporal 与 File Store 全量状态；补齐匹配快照、恢复演练、RPO/RTO 和回滚版本。
- 054/055/057 迁移包含旧业务存储空条件；先确认生产是否为空库或已有数据，不能将自动迁移理解为任意旧库都能原位升级。
- 真实模型业务 Run、生产 Worker、QQ（若启用）、Research 默认 lease 恢复、移动端/读屏等缺口已有文档记录，需要按实际上线范围重验并形成当前发布记录。
- 已有 loopback 绑定和生产参数说明；上线时复核 TLS、Origin、Secure Cookie、生产 Secret、假执行器关闭及实际公网入口，不以配置文件存在代替环境验证。

### P2：长期维护

- 未发现仓库根 LICENSE、SECURITY.md、CONTRIBUTING.md；根据是否公开分发决定许可、第三方字体/图片/Skill 来源及漏洞报告渠道，不擅自指定许可证。
- CI 现有门禁覆盖多类测试，但脚本和文档结构移动后要更新显式路径、Makefile、Compose、Docker COPY、Playwright 和测试中的引用。
- 建立生成物白名单/忽略策略、文档链接校验和凭据扫描，防止下一轮验收再次把临时目录纳入 Git。
- 本地 `.git` 约 1 GiB 不等于远端 clone 大小；可达历史中仍有旧 deb、视频、ZIP、独立 HTML。删除当前树文件不会清除历史；普通 GC 与改写历史是不同操作，本轮均未执行。

## 6. 建议执行顺序与验收

1. 固定当前基线，处理疑似凭据；将需要保留的本地验收材料、离线镜像包做带校验和的仓库外归档。
2. 建立当前合同与证据白名单；更新 README/文档入口，消除“历史状态当现状”和仅本地可用引用。
3. 将有效前端断言收进正式 E2E；合并脚本入口、修复 release smoke，再移除逐轮重复工具和原始证据。
4. 收束依赖入口、构建上下文与生产发布方式；补齐恢复、升级和真实链路验收。
5. 根据改动执行文档链接/路径校验、shell 语法及 Python 静态编译；再跑相关测试、前端门禁、Compose 配置验证及隔离部署 smoke。

完成标准：fresh clone 能按 README 启动和运行门禁；当前文档不依赖本地忽略文件；受保留合同可追溯；生成物不进 Git；脚本没有本机绝对路径依赖；备份不会落入可提交路径；发布镜像与源码基线一致；生产验证有独立结果。

本轮未运行应用测试或发布验证：没有修改功能实现；本报告只证明静态盘点发现，不能证明当前服务可上线。

## 附录 A：Markdown 链接问题

| 源文件与行 | 目标 | 状态 |
| --- | --- | --- |
| `artifacts/product-acceptance/2026-10-04/evidence.md:3` | `../../../.data/product-acceptance` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:21` | `../../../.data/product-acceptance/runtime-baseline.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:21` | `../../../.data/product-acceptance/runtime-final.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:21` | `../../../.data/product-acceptance/deployment-code-hashes.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:21` | `../../../.data/product-acceptance/document-runtime-drift.diff` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:22` | `../../../.data/product-acceptance/network.jsonl` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:23` | `../../../.data/product-acceptance/04-configured-origin-ui.png` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:23` | `../../../.data/product-acceptance/05-mobile-ui.png` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:23` | `../../../.data/product-acceptance/browser-origin-context.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:24` | `../../../.data/product-acceptance/first-run.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:24` | `../../../.data/product-acceptance/first-model-inputs.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:24` | `../../../.data/product-acceptance/first-trace.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:24` | `../../../.data/product-acceptance/backend-first-run.jsonl` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:24` | `../../../.data/product-acceptance/backend-db-final.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:24` | `../../../.data/product-acceptance/08-trace-failed.png` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:25` | `../../../.data/product-acceptance/switch-result.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:26` | `../../../.data/product-acceptance/fixed-upload-create.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:26` | `../../../.data/product-acceptance/workspace-upload.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:26` | `../../../.data/product-acceptance/file-content.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:27` | `../../../.data/product-acceptance/cancel-send.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:27` | `../../../.data/product-acceptance/cancel-response.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:27` | `../../../.data/product-acceptance/cancel-final.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:28` | `../../../.data/product-acceptance/work-immediate-created.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:28` | `../../../.data/product-acceptance/work-immediate-final.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:28` | `../../../.data/product-acceptance/work-immediate-runs.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:28` | `../../../.data/product-acceptance/work-notifications.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:28` | `../../../.data/product-acceptance/http-errors.jsonl` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:29` | `../../../.data/product-acceptance/account-isolation.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:29` | `../../../.data/product-acceptance/csrf-boundary.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:31` | `../../../.data/product-acceptance/chat-file-saved.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:31` | `../../../.data/product-acceptance/removed-file-retention.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:31` | `../../../.data/product-acceptance/attachment-revoked.json` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:32` | `../../../.data/product-acceptance/related-tests.log` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:33` | `../../../.data/product-acceptance/20-final-reloaded.png` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/evidence.md:59` | `../../../.data/ci-browser-closure/results.md` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/manual-e2e-investigation.md:10` | `../../../web/src/components/WorkspacePanel.tsx#L216` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/manual-e2e-investigation.md:11` | `../../../web/src/components/WorkspacePanel.tsx#L608` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/manual-e2e-investigation.md:39` | `../../../web/src/components/WorkspacePanel.tsx#L290` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/manual-e2e-investigation.md:109` | `../../../web/src/components/WorkspacePanel.tsx#L274` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/repair-results.md:43` | `../../../.data/product-repair` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/repair-review.md:12` | `../../../web/src/components/WorkspacePanel.tsx#L109` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/repair-review.md:15` | `../../../web/src/components/ResearchOutputs.tsx#L22` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/repair-review.md:16` | `../../../web/src/components/WorkPanel.tsx#L90` | 目标缺失 |
| `artifacts/product-acceptance/2026-10-04/repair-review.md:18` | `../../../web/src/components/WorkspacePanel.tsx` | 目标缺失 |
| `artifacts/product-acceptance/ui-iteration/R1.3/attempt-06/DECISION.md:17` | `FEEDBACK-MATRIX.md` | 仅本地忽略文件 |
| `artifacts/product-acceptance/ui-iteration/R1.3/attempt-06/DECISION.md:17` | `VALIDATION.md` | 仅本地忽略文件 |
| `artifacts/product-acceptance/ui-iteration/R2/attempt-06/HANDOFF.md:5` | `../attempt-04/HANDOFF.md` | 仅本地忽略文件 |
| `artifacts/product-acceptance/ui-iteration/R5/R5.0-DECISION.md:7` | `attempt-01-basic-fold/INTERACTION-CONTRACT.md` | 仅本地忽略文件 |
| `artifacts/product-acceptance/ui-iteration/R5/R5.0-DECISION.md:11` | `attempt-01-basic-fold/VALIDATION_RESULTS.md` | 仅本地忽略文件 |
| `artifacts/product-acceptance/ui-iteration/R5/R5.0-DECISION.md:11` | `attempt-01-basic-fold/REMAINING_WORK.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/R2-PLAN.md:23` | `../../../artifacts/product-acceptance/ui-iteration/R1.3/attempt-04/STATIC-DESIGN-CONTRACT.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/R2-PLAN.md:23` | `../../../artifacts/product-acceptance/ui-iteration/R1.3/attempt-05/STATIC-DESIGN-CONTRACT.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/R3-FUNCTION-AUDIT.md:8` | `../../../artifacts/product-acceptance/ui-iteration/R3/attempt-03/logs/source-manifest.json` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/R3-FUNCTION-AUDIT.md:29` | `../../../artifacts/product-acceptance/ui-iteration/R3/attempt-03/VALIDATION.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/R3-FUNCTION-AUDIT.md:33` | `../../../artifacts/product-acceptance/ui-iteration/R3/attempt-03/FEEDBACK-MATRIX.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/R4-PLAN.md:108` | `../../../artifacts/product-acceptance/ui-iteration/R4/attempt-11-artifact/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/R4-PLAN.md:112` | `../../../artifacts/product-acceptance/ui-iteration/R4/attempt-14-file-relay/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/R4-PLAN.md:122` | `../../../artifacts/product-acceptance/ui-iteration/R4/attempt-15-task-relay/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/R5-PLAN.md:22` | `../../../artifacts/product-acceptance/ui-iteration/R5/attempt-03-conversation-transit/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/R5-PLAN.md:22` | `../../../artifacts/product-acceptance/ui-iteration/R5/attempt-03-conversation-transit/VALIDATION_RESULTS.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/R5-PLAN.md:43` | `../../../artifacts/product-acceptance/ui-iteration/R5/attempt-02-mercury-r51-r53/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/R5-PLAN.md:43` | `../../../artifacts/product-acceptance/ui-iteration/R5/attempt-02-mercury-r51-r53/VALIDATION_RESULTS.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/R5-PLAN.md:57` | `../../../artifacts/product-acceptance/ui-iteration/R5/attempt-01-basic-fold/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/README.md:9` | `../../../artifacts/product-acceptance/ui-iteration/R5/attempt-03-conversation-transit/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/README.md:172` | `../../../artifacts/product-acceptance/ui-iteration/R2/attempt-03/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/README.md:180` | `../../../artifacts/product-acceptance/ui-iteration/R2/attempt-04/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/README.md:188` | `../../../artifacts/product-acceptance/ui-iteration/R3/attempt-01/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/README.md:192` | `../../../artifacts/product-acceptance/ui-iteration/R3/attempt-02/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/README.md:210` | `../../../artifacts/product-acceptance/ui-iteration/R4/attempt-01/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/README.md:214` | `../../../artifacts/product-acceptance/ui-iteration/R4/attempt-02/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/README.md:222` | `../../../artifacts/product-acceptance/ui-iteration/R4/attempt-11-artifact/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/README.md:226` | `../../../artifacts/product-acceptance/ui-iteration/R4/attempt-12-ai-inspector-handoff/README.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/README.md:230` | `../../../artifacts/product-acceptance/ui-iteration/R4/attempt-14-file-relay/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/README.md:240` | `../../../artifacts/product-acceptance/ui-iteration/R4/attempt-15-task-relay/HANDOFF.md` | 仅本地忽略文件 |
| `docs/design/ui-iteration-2026-10/README.md:257` | `../../../artifacts/product-acceptance/ui-iteration/R5/attempt-01-basic-fold/HANDOFF.md` | 仅本地忽略文件 |

## 附录 B：复核入口

统计命令：`git status --short`、`git ls-files -z`、`git ls-files --others --ignored --exclude-standard -z`、`git count-objects -vH`、按文件 stat 汇总逻辑大小、对 ≥1 KiB 跟踪文件计算 SHA256、Markdown 相对内联链接目标存在性与 Git 跟踪状态检查。

Git 可达历史大文件：`git rev-list --objects --all` 配合 `git cat-file --batch-check`；仅检查大小和路径，没有重写历史。敏感值扫描仅输出路径与脱敏上下文。
