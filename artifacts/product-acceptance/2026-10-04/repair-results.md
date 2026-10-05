# 按修复方案实施后的结果

修复与复验日期：2026-10-04。此次保留此前全部未提交修改；没有提交、推送、修改架构文档主体、重置数据库或发送 QQ 消息。当前 localhost 已连接真实后端，API ready，Fake Executor=false。

**已修复明确代码/UI 缺陷并完成受控服务更新；完整真实产品 E2E 仍受模型网络阻断。**

## 修复状态

| 编号 | 改动与结果 | 验证边界 |
|---|---|---|
| F01 | `.env` 的 WEB_PUBLIC_ORIGIN 改为 `http://127.0.0.1:5173`，仅重新创建 API 服务，localhost 登录/创建通过。 | 错误 IP Origin 和缺少 CSRF token 仍 403；没有扩大 CSRF 放行范围。 |
| F02 | EmptySelection 展示现有 workbench error，并可关闭。 | 新专用 C 账号无对话，从错误 Origin 创建得到真实 403，页面显示“CSRF 校验失败。”；回 localhost 登录后创建成功。组件回归还验证关闭提示和成功后清除。 |
| F03 | 保留此前 newIdempotencyKey 的三处兼容修复。 | 再次从页面下载 43-byte 文件，SHA-256 与原输入一致。没有据此将 v2 成果链算作通过。 |
| F04 | RunStatus 增加安全 failure.message/code；ModelClient 增加不含请求/凭据/响应体的连接、读取等错误类别和耗时日志。主 Worker 受控重启后加载代码。 | 提示与日志经单元测试；没有再次提交必然超时的模型聊天来验证新失败 UI。模型执行仍阻断，不能说模型服务已修复。 |
| F05 | 640px 以下改为侧栏与聊天上下布局；表单换行、面板/输入宽度受限，状态条可换行。 | 实际 Chromium 的 390/768/1440px 均无横向溢出；390px 输入宽度 236px；真实 Trace 面板可打开、关闭，关闭后输入可操作。Artifact 成果缺样本，未签收其真实预览。 |
| F06 | ProtocolMiddleware 按真实路由区分 Run SSE、Work SSE 与 Work JSON events。 | 真实 Work SSE 200且 Content-Type 正确，收到事件1；断线后 after=1 收到事件2；JSON events 200。不是仅看列表轮询。 |
| F07 | candidateRunId 与版本 currentRunId 分离；仅 queued/running 查询候选，状态变化时刷新；会话/状态/请求身份保护丢弃过期响应；授权错误独立显示。 | 新真实 Run 取消为 cancelled，0 次模型请求；页面及刷新没有资源授权加载错误。延迟响应、A→B→A、分页切换、queued→running 与完成 Run 的版本入口经组件回归。未知活跃 Run 404 仍显示错误。 |
| F08 | Research 显示加载、成功为空和失败状态；现有执行/搜索字段补充普通用户能理解的提示。 | 新账号/无 Research 样本的实际页面显示空状态。Research 最终报告、文件与保存仍未验证。 |
| F09 | 文档镜像源码漂移已消除。现有依赖满足当前 requirements，离线构建新镜像并仅重新创建 Document Worker。 | 六个关键模块/清单 hash 一致；镜像导入成功；Worker 注册低并发文档队列。真实文档成果仍未验证，transform=false 保持原配置。 |
| F10 | stopped/completed 不再显示继续执行的 continuation/未来到期文案；paused 明确为暂停前计划。 | 新未来 Work 经真实页面暂停、恢复、停止后，显示“工作已停止，不再按原计划继续。”；后台快照 stopped。 |

Workspace 候选的 404 仍可能意味着不存在、账号不匹配或快照未就绪。此次没有更改后台所有权规则，也没有统一吞掉 404。版本输出仍使用完整 currentRunId；只有候选接口受可选择状态限制。

## 部署与网络

受控更新了三个服务：API 重新创建以加载 Origin/协议修复；Document Worker 用新镜像重新创建；主 Worker 在无在途 Run 时重启以加载安全诊断日志。前端与 Hindsight 的启动时间保持原值。Docker daemon、Compose 配置和各容器代理配置未变，命名卷保留。

标准文档构建失败于 Dockerfile frontend 的 Docker Hub 授权连接：`auth.docker.io` 解析到 198.18.0.31 后 TCP 超时。因此使用本地旧文档镜像的已验证依赖层，COPY 当前 src，执行无网络构建。旧镜像留有 `hpagent/hpagent-document:before-product-repair-20261004` 标签；新 `offline` 镜像 ID 为 `sha256:49b85e3a2dba73a901c938f1157508f2cbb9ef080e3c7f79be3e4d94b1aa6cf4`。离线 Dockerfile 与构建日志保存在本地证据目录，未改变仓库标准 Dockerfile。

Worker 内只读网络探测得到：

| 域名 | DNS 返回 | 失败阶段 |
|---|---|---|
| api.minimaxi.com | 198.18.0.29 | TCP 连接 5 秒超时，尚未进入 TLS |
| api.siliconflow.cn | 198.18.0.30 | TCP 连接 5 秒超时，尚未进入 TLS |

这证明当前 Worker 出站路径有阻断；**疑似 Fake-IP 与路由/TUN 衔接问题是推断**，尚未证明具体代理组件或供应商故障。没有修改 DNS、hosts、全局代理或盲目增加模型 timeout/retry。有效 chat timeout 仍 30s、max_tokens 4096；Fast 与 Embedding 分别为 15s/10s。

后续需在允许的网络配置中恢复真实 Worker 出站，并先用无收费的 TCP/TLS 探测确认；然后只提交一次小聊天输入，再继续 Work、Research、Artifact、文档和 retain→跨对话 recall 成果验收。恢复网络不自动证明记忆正常，transform 开关也不能替代真实文档验证。

## 回归与浏览器证据

原始证据在 [本地修复证据目录](../../../.data/product-repair)。密码文件 `credentials-c.private.json` 与环境备份 `env.before` 为 0600，仅供本机使用，不得发布。此前账号文件实际名为 `credentials.private.json` 和 `credentials-b.private.json`，位于 product-acceptance 目录。

| ID | 证据与结论 |
|---|---|
| R01 | `frontend-tests.log`：5 个相关测试文件，**39 passed**；覆盖幂等键、Workbench、App、RunStatus、WorkspacePanel。最后补强 succeeded Run 的版本 ID 断言后，`workspace-final-tests.log` **6 passed**。`typecheck.log`、`eslint.log` 无错误，`prettier.log` 通过，git diff --check 通过。 |
| R02 | `python-related-tests.log`：协议、模型配置及安全日志 **17 passed**。`document-tests.log`：文档 Worker/迁移契约 **7 passed**，均未使用业务库做集成夹具。 |
| R03 | `network.jsonl`、`01-localhost-restored.png`、`created-conversation.json`、`csrf-boundaries.json`、`empty-creation-retest.json`、`09-empty-creation-error.png/.txt`、`10-empty-creation-success.png`：真实 Origin/空页面错误与创建复验。 |
| R04 | `work-created.json`、`work-stream-initial.json`、`work-stream-resume.json`、`work-json-events.json`、`work-stopped.json`、`03-work-sse-stopped.png/.txt`：真实流式事件、游标续读及控制/终态文案。该 Work 是验收辅助 API 创建，未计为聊天自然语言创建通过。 |
| R05 | `cancel-run.json`、`04-cancel-resources.png/.txt`、`05-refresh-cancel.png/.txt`：Run `01a10339-28f6-7610-b3bf-9aa43d81158f` cancelled，模型尝试数0，终态资源区无错误，刷新消息恢复。 |
| R06 | `connectivity.jsonl`、`runtime-before.json`、`runtime-after.json`、`worker-restart.log`、`document-build.log`、`document-offline-build.log`、`document-imports.log`、`document-runtime-hashes.json`：环境阻断与受控部署/hash 对齐。 |
| R07 | `layouts.json`、`layout-390.png`、`layout-768.png`、`layout-1440.png`、`06-mobile-trace.png/.txt`、`trace-close-layout.json`：实际布局、Trace 打开关闭与输入可操作。 |
| R08 | `account-isolation.json`、`logout-isolation.json`、`file-consistency.json`：B 访问 A 的对话/Work/Run/File 均404；B退出401、A仍200；下载43bytes，hash为 f3b93cfbaa3c398cfe20f47c2a7537b1bff70216da02b119d6ea77297437b009。 |
| R09 | `preservation.json`：此前 tracked diff（排除原 Workspace UUID 修复和本轮修改文件）hash仍 f68fe2cc4e5820dd02e4a173504e6ffad584402751da564b4e83774d340b7cf7；原未跟踪测试保留；.env仅Origin一行变化。 |

本轮新增一项专用 A 的测试对话、一个未来提醒 Work（已停止）、一条立即取消的消息/Run，以及一个用于空页面验收的专用 C 账号及其测试对话。复用原小文件只读下载，未修改既有文件/入口或真实用户数据，没有发起 QQ 绑定或投递。

浏览器辅助脚本曾用错误 locator 等待、从错误 Origin 回来后需要重新登录，及尝试通过调试代码载入旧失败 Run 但未得到对应 UI；这些操作没有作为产品通过证据。F04 新失败提示只按组件回归签收，真实失败后提示仍待下一次模型场景复验。没有将脚本超时列为新的产品缺陷。

额外扩大检查 `test_model_governance.py` 时为 **19 passed / 1 failed**（包含同次执行的协议与模型配置测试）。失败是测试 `_Budget` 缺少 database 属性，在模型 dispatch 前发生；使用 HEAD 原始 ModelClient 的内存加载复测同一用例，仍是相同失败，见 `governance-baseline.log`。本轮未为此修改容量/治理架构或测试夹具。历史36项非 PostgreSQL失败未全量复验，不能宣称全量 CI 已通过。

## 尚未关闭的验收条件

- 真实模型聊天完成与最终助手成果；Generic Work/Research/Artifact/文档真实生成、发布与保存。
- Hindsight retain、跨对话 recall 与真实记忆隔离；健康检查不能替代这些证据。
- 运行中模型/工具取消、动态撤权后的执行停止、真实 v2/CAS。
- 文档转换仍需当前开关与依赖条件下的真实样本验收；此次仅关闭镜像漂移。
- 缺少 Web 入口及缺获准测试 QQ 的项目保持原分类，不新增功能。

因此，**当前可以继续进行真实后端的浏览器验收，但尚不具备完整真实产品 E2E 通过条件**。人工复验沿用 [操作教程](manual.md)，当前标准地址为 `http://127.0.0.1:5173`。
