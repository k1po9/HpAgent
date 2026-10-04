# 环境与证据索引

所有原始证据在 [本地验收证据目录](/home/hp/workspace/HpAgent_web/.data/product-acceptance)。该目录是本地数据目录，不自动提交。专用账号密码保存在目录下 `.private.json`（权限 0600），仅限本机使用，不得随报告/HAR/截图包发布。HTTP 网络记录不保存 Cookie、密码、CSRF header 或请求体；账号上下文文件已去掉 CSRF token。QQ 截图含专用账号的短期挑战，已过期，不代表已绑定。

## 实际环境

- `127.0.0.1:5173` 返回 Vite 页面 200；`127.0.0.1:8080/health/ready` 返回 ready 200。Vite 实际代理 `HPAGENT_API_TARGET=http://hpagent-api:8080`，没有指向历史 Fake 测试服务。
- API settings：development，Fake Executor=false，cookie_secure=false，file_upload=true，file_transform=false，file_shell=false，run_budget=enforce；public_origin 为 `http://172.24.198.41:5173`。
- API 和主 Worker 使用当前 bind mount 源码，关键模块 hash 匹配；Document Activity runtime 在镜像内，hash 不匹配，差异单列 F09。
- API/主 Worker/前端/Hindsight/Document Worker 从 baseline 到 final 启动时间和 restart 次数完全相同。Hindsight 的 18 次、Document Worker 的 2 次 restart 是本轮开始前已存在的计数，不是本轮操作导致。
- Hindsight healthy，但 `HINDSIGHT_API_SKIP_LLM_VERIFICATION=true`。实际账户 recall 3 秒 timeout，不能用 healthy 推断记忆功能可用。
- Temporal/数据库/Redis/搜索/转换容器在初始 `docker compose ps` 中在运行；主 Worker 已通过 schema verification，注册 web lifecycle/agent queue。健康检查不验证最终模型/文档/Research 成果。
- Worker 启动日志显示 MCP 0/6 连接成功，这是外部工具验收限制，未把它泛化为所有本地能力失败。
- 没有修改代理、`.env`、Compose、Docker daemon 或数据库 schema；没有 reset/迁移/删业务数据；测试操作仅使用新 A/B 账号、两个小文件、新 entry/临时目录和两项测试 Work。
- 本轮模型成果场景只发送一次；另一次附件消息 queued 即取消，model_calls=0。测试 Work 均为 account_inbox，不选择 QQ 目标。

## 证据编号

| ID | 文件与断言 |
|---|---|
| E00 | [runtime-baseline.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/runtime-baseline.json)、[runtime-final.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/runtime-final.json)、[deployment-code-hashes.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/deployment-code-hashes.json)、[document-runtime-drift.diff](/home/hp/workspace/HpAgent_web/.data/product-acceptance/document-runtime-drift.diff)；真实模式、Origin、能力开关、服务稳定及文档部署差异 |
| E01 | `01-localhost-login`、`02-localhost-registered`、`03-localhost-origin-failure` 的 PNG/txt；[network.jsonl](/home/hp/workspace/HpAgent_web/.data/product-acceptance/network.jsonl) 有 201 注册/403 创建及配置 IP 下的 303 登录/201 创建 |
| E02 | [04-configured-origin-ui.png](/home/hp/workspace/HpAgent_web/.data/product-acceptance/04-configured-origin-ui.png)、[05-mobile-ui.png](/home/hp/workspace/HpAgent_web/.data/product-acceptance/05-mobile-ui.png)、[browser-origin-context.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/browser-origin-context.json)；入口/空状态/窄屏/secure context |
| E03 | [first-run.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/first-run.json)、[first-model-inputs.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/first-model-inputs.json)、[first-trace.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/first-trace.json)、[backend-first-run.jsonl](/home/hp/workspace/HpAgent_web/.data/product-acceptance/backend-first-run.jsonl)、[backend-db-final.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/backend-db-final.json)、[08-trace-failed.png](/home/hp/workspace/HpAgent_web/.data/product-acceptance/08-trace-failed.png)；真实模型 dispatch/超时/Run failure、SSE终态、记忆降级 |
| E04 | [switch-result.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/switch-result.json)、`09-refresh-switch.png/.txt`、`20-final-reloaded.png/.txt`；快速 A/B/A 后 A 正确，刷新恢复最新 B 与数据 |
| E05 | [fixed-upload-create.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/fixed-upload-create.json)、[workspace-upload.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/workspace-upload.json)、[file-content.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/file-content.json)、`downloaded-acceptance.txt`、`grants-created.json`、`grants-revoked.json`、`workspace-versions.json`、`workspace-after-rename.json`、`10b-workspace-state.png`、`11-granted-file.png`、`12-workspace-versions-revoke.png`；真实 File bytes、授权/撤销、v1、改名、名称搜索 |
| E06 | [cancel-send.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/cancel-send.json)、[cancel-response.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/cancel-response.json)、[cancel-final.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/cancel-final.json)、`cancel-resources.json`、`13-cancel-attachment.png/.txt`；附件 ready/bound、queued cancel终态；资源404的UI错误 |
| E07 | `work-future-created.json`、`work-paused.json`、`work-stopped.json`、[work-immediate-created.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/work-immediate-created.json)、[work-immediate-final.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/work-immediate-final.json)、[work-immediate-runs.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/work-immediate-runs.json)、`work-immediate-events.json`、[work-notifications.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/work-notifications.json)、`14-work-control.png`、`18-completed-work.png`；真实控制、Run、投递回执及测试成果；[http-errors.jsonl](/home/hp/workspace/HpAgent_web/.data/product-acceptance/http-errors.jsonl) 保存 Work SSE406 |
| E08 | [account-isolation.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/account-isolation.json)、[csrf-boundary.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/csrf-boundary.json)、`account-context.json`、`account-b-context.json`、`own-model-input-detail.json`、`logout-b.json`、`account-a-still-signed-in.json`、`17-account-b.png/.txt`；9类跨账号 GET404、跨授权POST404、缺CSRF403、B退出401、A仍在线、本人模型详情403 |
| E09 | `16-qq-challenge.png/.txt`、`19-final-ui.txt`、network challenge201/查询200；Web挑战/说明/过期状态，未发送QQ指令 |
| E10 | [chat-file-saved.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/chat-file-saved.json)、[removed-file-retention.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/removed-file-retention.json)、`space-after-remove.json`、[attachment-revoked.json](/home/hp/workspace/HpAgent_web/.data/product-acceptance/attachment-revoked.json)、`19-final-ui.png/.txt`；保存entry、移除/目录操作、引用保留及附件可用false |
| E11 | [related-tests.log](/home/hp/workspace/HpAgent_web/.data/product-acceptance/related-tests.log)、`related-checks.txt`、`preservation-check.json`；25单测、类型/单文件lint/格式、原修复保留核对 |
| E12 | [20-final-reloaded.png](/home/hp/workspace/HpAgent_web/.data/product-acceptance/20-final-reloaded.png)、`workspace-final.json`、`workspace-space-final.json`、backend-db-final；最终持久化快照/刷新与测试成果保留 |

网络证据含 UI 发出的真实请求和少量浏览器中执行的验收辅助 fetch；下载由实际 UI 链接触发。Playwright context.request 的补充只读快照不触发 page response listener，但每次完整状态/响应另存 JSON。Work fixtures 通过 same-origin browser fetch 创建；矩阵没有把它们计为 UI 创建覆盖。

## 本轮关键真实身份

| 对象 | ID |
|---|---|
| 专用 A | `01a1025a-bd4e-70db-b79b-d1bd4e11ff5a` |
| 专用 B | `01a1029c-6b8a-7f1a-bb06-23071343c809` |
| A 失败真实模型 Run | `01a1025c-ec3a-72c1-ac87-9eccc16b558a` |
| A queued 取消 Run | `01a10292-0a8b-7e5a-b0d9-d452952b89d2` |
| 收件箱 Work | `01a10299-a319-742d-8275-738e993d85a1` |
| 真实 reminder Run | `01a10299-a733-78d4-8779-f570a9c005f4` |
| 停止的未来 Work | `01a10293-f3ec-74d9-b8b8-3ff2f8968c91` |
| Workspace 43-byte 对象 | `01a1028c-6360-7657-9251-20b873f5d520` |
| 该文件稳定 entry | `01a1028c-6480-7e0b-ad19-21096ab8d087` |

提醒最终成果：notification fulfillment payload 中有 `WORK-INBOX-203`；`provider_receipt.level=account_inbox_committed`。完成凭据 evidence 为 delivery_receipt，evaluator 为 delivery_receipt_policy_v1。不能由“请求接受”“排队”“Run成功”单个事实推定这三个条件。

最终 A 有 3 个 Run（失败 chat、取消 chat、成功 reminder）、2 个 ready 小文件、2 个 Work（stopped/completed）。只有 43-byte 文件有长期活动 entry，另 24-byte 文件有消息/Run引用，去掉 entry 后仍保留；最终物理统计 2 个对象、67 bytes。B 没有 A 的内容。

## 回归与保留边界

只运行与本轮最小修复相关的前端检查：`vitest` 的 idempotency/workbench/App 三个文件，单 worker，25 passed；TypeScript noEmit；WorkspacePanel 单文件 ESLint、Prettier；git diff --check。没有新增镜像/依赖、重跑 Fake 浏览器全套或全量 Python 单测。

历史 [ci-browser-closure/results.md](/home/hp/workspace/HpAgent_web/.data/ci-browser-closure/results.md) 的 17/17 Fake E2E 与 36 项非 PostgreSQL 失败原样保留。架构文档主体、旧 Task/legacy scheduler、原未提交最小修复都没有重置/覆盖/丢弃。
