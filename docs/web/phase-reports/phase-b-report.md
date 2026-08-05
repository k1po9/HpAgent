# HpAgent Web Phase B 实施报告

## 1. 结论

Phase B 已按评审设计基线完成独立 Web API、个人用户认证会话、Conversation/Message/Run HTTP API、确定性伪执行器以及 API 0.3 合约与安全门禁。

- Web API 使用独立组合根，不加载 Agent、模型、Sandbox、Temporal Worker 或 ChannelRouter。
- 登录、Conversation、发送、查询、取消、重试和刷新恢复可在不接真实 Agent 的情况下完成。
- Phase B 真实 PostgreSQL 集成测试 20 项全部通过。
- Ruff、mypy 和 Docker Compose 配置校验通过。
- 本阶段通过独立验收提交推送；远端 CI 是最终合并门禁。

## 2. B-01：独立 Web API 进程

新增 `src/web_api/`、`src/Dockerfile.web-api` 和 Compose `hpagent-api` 服务：

- `/api/v1` 路由、健康检查和独立 `python -m web_api` 入口；
- 同步 psycopg 连接池在线程池中的 FastAPI 同步 handler 内使用，避免阻塞 ASGI 事件循环；
- 统一 JSON 错误体、request ID、严格 JSON Content-Type/Accept、请求体限制和安全响应头；
- 生产配置强校验，API 容器不注入 Worker、模型或工具凭据；
- 导入隔离测试验证 API 启动不会加载 Agent 运行时模块。

## 3. B-02：登录会话与 CSRF

认证边界采用设计确定的“受控预创建 Account/IdentityBinding”策略：

- 可配置 Argon2id 凭证 Adapter；
- 只解析 active、verified、`provider='web'` 的既有 IdentityBinding，未知身份统一拒绝；
- 服务端 `web_auth_sessions` 保存 token 摘要，浏览器只持有 opaque Secure/HttpOnly/SameSite=Lax Cookie；
- CSRF token 由会话原始 token 和服务端密钥派生，服务端保存摘要并使用 constant-time 比较；
- 状态修改同时校验 CSRF 与 Origin/Referer；
- 登录轮换会撤销旧会话，退出撤销当前会话；客户端 `account_id` 不参与授权。

## 4. B-03：Conversation API

已实现创建、列表、详情和重命名：

- 创建要求 `Idempotency-Key`，重放返回冻结结果；
- 列表使用带 HMAC、key ID、TTL、Account、endpoint、resource 和 limit 绑定的 keyset cursor；
- 所有查询从认证会话取得 Account，跨账号资源返回 opaque 404；
- 重命名使用只基于 `metadata_version` 的 ETag/If-Match，消息 sequence 变化不会制造元数据冲突；
- 缺失 If-Match 返回前置条件错误，版本冲突返回稳定错误码。

## 5. B-04：Message 与 Run API

已实现消息分页、发送、Run 查询、取消和重试：

- 发送通过正式 CommandService 原子创建 user Message、queued Run、pending assistant Message 和 Outbox；
- 返回完整稳定 DTO，首次状态码和响应体随幂等记录冻结，重放保持资源身份；
- 消息采用稳定顺序的 keyset 分页，查询可仅从 PostgreSQL 恢复；
- 正文同时限制 32,000 code point 和 128 KiB；未知字段、畸形 JSON 与过大请求均返回统一错误；
- 取消与重试遵守领域状态机，cancel/retry body 不接受 `account_id` 或其他额外字段；
- 并发发送、跨账号访问、响应丢失后重放和终态行为均由真实数据库测试覆盖。

## 6. B-05：确定性伪执行器

FakeRunExecutor 只在非生产测试配置下可启用：

- 消费正式 `start_run`、`cancel_run` Outbox；
- 通过正式 CommandService 生命周期服务推进 queued、running、completed、failed、cancelled；
- 支持成功、稳定失败和 hold/取消窗口模式，以及可配置延迟和成功正文；
- 不执行旁路 SQL，不实现第二套状态机，不加载模型或工具；
- 生产环境设置伪执行器开关会直接拒绝启动。

## 7. B-06：API 0.3 合约与安全门禁

`test/web_api/` 使用真实 PostgreSQL 覆盖 Phase B 适用的 API-001～API-009、API-016～API-019、API-021～API-023，包括：

- 登录、Cookie、会话轮换、`GET /me`、退出和登录枚举防护；
- CSRF、Origin、资源隔离、opaque 404；
- Idempotency-Key 重放/冲突和并发发送；
- cursor 篡改、分页顺序、ETag 和前置条件；
- 请求大小、Content-Type、Accept、畸形 JSON、未知字段与安全头；
- 刷新恢复、运行中取消、失败后重试和 SSE 占位路径的结构化 404。

CI 新增独立 Phase B PostgreSQL job；`make test-api` 与 `make ci` 已纳入同一门禁入口。

## 8. 验证结果

| 验证项 | 结果 |
|---|---:|
| Phase B 真实 PostgreSQL 集成测试 | 20 passed |
| Phase A 真实 PostgreSQL 回归 | 56 passed |
| 既有非 PostgreSQL 回归（含真实 nsjail） | 173 passed |
| Ruff | 通过 |
| mypy | 通过 |
| Docker Compose 配置 | 通过 |
| API 运行时导入隔离 | 通过 |
| 生产环境伪执行器拒绝 | 通过 |

Phase B 完整测试结果：

```text
20 passed, 1 warning in 23.76s
```

Phase A 数据库回归和全量非 PostgreSQL 回归结果：

```text
56 passed in 81.76s
173 passed, 73 deselected, 1 warning in 24.63s
```

唯一 warning 来自 FastAPI/Starlette TestClient 对当前 `httpx` 兼容层的弃用提示，不影响运行时或合约结果；后续依赖升级时切换到建议的新版测试客户端。

## 9. 已知环境风险

### 9.1 Docker 镜像拉取

`docker compose config --quiet` 已通过。构建 `hpagent-api` 镜像时，Docker daemon 已使用 `proxy_all_on` 配置的代理，但基础镜像层经 CloudFront 下载多次出现 EOF，因此本轮未取得完整镜像构建成功证据。这是外部下载链路问题，不是 Dockerfile 或 Compose 解析错误；远端 CI 仍应实际构建镜像。

### 9.2 默认开发数据库漂移

既有默认 `hpagent` 数据库仍存在“业务表已存在但 migration 历史缺失”的漂移。本轮保留该数据库，仅使用隔离空库执行 migration 和测试。继续开发时不得向默认库伪造 migration 历史，应备份后重建或固定使用一次性测试库。

### 9.3 真实 Agent 边界

Phase B 按设计只交付伪执行器。真实 Agent、Conversation Session/Context、Workspace 隔离和 Temporal 编排属于后续 Phase C/D，当前 API 不应提前启用真实执行路径。

## 10. Gate 建议

| 门禁范围 | 建议 |
|---|---|
| B-01～B-06 本地实现与合约测试 | 通过 |
| Docker Compose 静态配置 | 通过 |
| Docker 镜像实际构建 | 等待网络稳定或远端 CI 验证 |
| 整体 Phase B Gate | 本地通过，等待远端 CI |
