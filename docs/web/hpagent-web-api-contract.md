# HpAgent Web API 与 SSE 契约

## 1. 文档信息

| 项目 | 内容 |
|---|---|
| 文档版本 | 0.3 |
| 状态 | 已评审 |
| 文档类型 | HTTP API 与 SSE 详细契约 |
| 日期 | 2026-08-04 |
| 需求基线 | [HpAgent Web MVP 需求规格说明书 0.4](hpagent-web-mvp-requirements.md) |
| 领域基线 | [HpAgent Web 领域模型与状态模型设计 0.3](hpagent-web-domain-and-state-model.md) |
| 架构基线 | [HpAgent Web 系统架构设计 0.4](hpagent-web-system-architecture.md) |
| 数据基线 | [HpAgent Web 数据库与持久化详细设计 0.3](hpagent-web-database-design.md) |

本文定义浏览器、HpAgent Web API、SSE Gateway 和 assistant-ui Adapter 之间的稳定边界。实现可以更换 ASGI 框架、认证凭证校验器、ORM 或 assistant-ui 版本，但不得改变本文关于后端真相源、账号隔离、幂等、终态提交和断线回源的语义。

## 2. 范围与原则

### 2.1 MVP 范围

- 登录入口、服务端登录会话、退出和当前 Account。
- Conversation 创建、列表、详情和 P1 重命名。
- Message 历史分页和发送。
- Run 查询、取消和失败/取消后重试。
- SSE 快照、流式 delta、安全 Run 进度、Run 状态和终态事件。
- assistant-ui Adapter 的 DTO、命令和状态映射。

不提供 Conversation 删除、归档、搜索、置顶、Agent 重新生成、回复版本关系、文件上传、管理端或团队 API。

### 2.2 契约原则

1. **后端是真相源**：Account、Conversation、Message、Run 和最终回复以 API/数据库为准。
2. **不接受账号主键**：浏览器请求不得提交 `account_id` 选择数据范围；API 从服务端会话解析。
3. **资源归属隐藏**：资源不存在和属于其他 Account 均返回相同的 `resource_not_found`。
4. **命令与事件分离**：创建 Conversation、发送、取消、重试使用 HTTP；SSE 只投影状态，不接收命令。
5. **终态提交后可见**：`run.completed`、`run.failed`、`run.cancelled` 只投影已提交数据库状态。
6. **在线投影可丢、终态不可丢**：`message.delta`、`run.progress` 不持久化；断线后查询 Message/Run。
7. **第三方类型隔离**：HTTP/SSE DTO 不直接暴露 assistant-ui 内部类型。

## 3. 通用 HTTP 约定

### 3.1 基础路径和传输

- JSON API 基础路径：`/api/v1`。
- 登录浏览器入口：`/auth/*`，由凭证校验适配器实现。
- SSE：`GET /api/v1/runs/{run_id}/events`。
- 生产环境只接受 HTTPS；Gateway 将 HTTP 重定向到 HTTPS。
- JSON 请求使用 `Content-Type: application/json; charset=utf-8`。
- JSON 响应使用 `Content-Type: application/json; charset=utf-8`。
- API 不允许跨源凭证访问；MVP Web、Gateway 和 API 保持同源。

### 3.2 JSON 和标识格式

| 项目 | 规则 |
|---|---|
| 字段命名 | snake_case |
| ID | 小写规范 UUID 字符串；业务 ID 由后端生成 |
| 客户端请求 ID | UUID，由浏览器生成并在同一意图重放时复用 |
| 时间 | RFC 3339 UTC，例如 `2026-08-04T08:30:00.123Z` |
| 状态 | 小写 snake_case 稳定枚举 |
| 缺失字段 | 与 `null` 含义不同；固定 DTO 字段原则上不省略 |
| 未知响应字段 | 客户端必须忽略，以支持兼容性扩展 |
| 未知枚举值 | 客户端进入安全 fallback，不得当作成功终态 |

请求中的 UUID 非规范或格式错误返回 `validation_error`，不得直接暴露路由框架的原始解析错误。

### 3.3 通用请求头

| 请求头 | 适用范围 | 规则 |
|---|---|---|
| `Accept: application/json` | JSON API | 推荐；不支持的响应类型返回 406 |
| `X-CSRF-Token` | 所有修改状态的请求 | 从 `GET /api/v1/me` 获取；必须匹配当前登录会话 |
| `Idempotency-Key` | 创建 Conversation、发送、取消、重试 | 必填，规范 UUID；同一用户意图的网络重试保持不变 |
| `If-Match` | Conversation 重命名 | 必填，使用详情响应的 ETag |
| `X-Request-ID` | 可选 | 客户端可提交合法值；服务端最终决定并回显安全 request ID |

### 3.4 通用响应头

| 响应头 | 说明 |
|---|---|
| `X-Request-ID` | 用于日志与问题定位，不包含账号或资源信息 |
| `Cache-Control: no-store` | 登录态、Account、Conversation、Message 和 Run API |
| `ETag` | Conversation 详情和修改响应 |
| `Idempotency-Replayed: true` | 相同 Account、operation、Idempotency-Key 命中已完成幂等结果时返回 |
| `Resource-Reused: true` | 不同命令因领域唯一约束复用既有资源时返回，例如直接重试子 Run 已存在 |
| `Retry-After` | 429/503 可提供秒数 |

### 3.5 请求大小和文本规则

- 单条用户消息规范化后最多 32,000 个 Unicode code point，且 UTF-8 编码最多 128 KiB。
- API/Gateway 对发送消息请求体设置 160 KiB 上限，其他 JSON 请求默认 64 KiB。
- 服务端把 CRLF/CR 规范化为 LF，保留其他空白；规范化后仅包含 Unicode whitespace 的消息返回 `empty_message`。
- 幂等请求摘要基于规范化后的业务 DTO，不包含 request ID、追踪头和 CSRF token。
- Conversation title 去除首尾空白后为 1～200 个 Unicode code point。

### 3.6 CSRF 与来源校验

- 所有修改状态的浏览器请求必须同时校验 session-bound CSRF token 和 `Origin`。
- `Origin` 存在时必须与部署配置的规范同源 origin 精确匹配；不接受通配、子域后缀或字符串前缀匹配。
- 浏览器兼容场景中 `Origin` 缺失时，必须验证 `Referer` 的 scheme、host、port 完全同源；两者都缺失时 MVP 拒绝请求。
- CSRF token 使用恒定时间比较，不写入 URL、日志或持久浏览器存储。
- 会话轮换会使旧 token 失效。旧标签页收到 403 `csrf_invalid` 后只允许 GET `/api/v1/me` 刷新一次 token；重试命令必须沿用原 Idempotency-Key，避免重复副作用。

## 4. 认证与当前账号

### 4.1 认证类别

认证采用同源、服务端有状态浏览器会话。成功登录后 Gateway/API 设置：

```text
Set-Cookie: __Host-hpagent_session=<opaque-token>; Path=/; Secure; HttpOnly; SameSite=Lax
```

- Cookie 不设置 `Domain`，原始 token 不进入 JavaScript 或数据库。
- 服务端用 token 摘要查找 `web_auth_sessions`，并验证 Account、IdentityBinding、撤销和过期状态。
- 浏览器不得把 JWT、access token 或 refresh token 保存到 localStorage/sessionStorage。
- 登录成功、身份安全属性变化时轮换 session token；退出时服务端撤销会话。

### 4.2 登录入口

```http
GET /auth/login?return_to=%2Fconversations
```

该入口启动已配置的凭证校验适配器，可以返回同源登录页或 302 到外部身份提供商。`return_to` 只允许同源站内绝对路径，不接受 scheme、host 或协议相对 URL。

成功行为：

1. 校验凭证或外部 callback 的 state/nonce。
2. 解析 active、verified Web IdentityBinding 和 active Account。
3. 创建 `web_auth_sessions` 记录并设置 HttpOnly Cookie。
4. 303 跳转到已校验的 `return_to` 或默认 `/`。

失败行为由登录页安全展示，不把上游 token、主体 ID、账号是否存在等细节放入 URL。具体采用密码、OIDC 或 Passkey 属于认证适配器设计，不改变成功后的会话契约。

### 4.3 当前账号

```http
GET /api/v1/me
```

成功：`200 OK`

```json
{
  "account": {
    "account_id": "0198aa10-9f20-7a11-8e63-28640cebf930",
    "status": "active",
    "created_at": "2026-08-04T08:30:00.123Z"
  },
  "session": {
    "expires_at": "2026-08-11T08:30:00.123Z",
    "idle_expires_at": "2026-08-05T08:30:00.123Z"
  },
  "csrf_token": "<session-bound-csrf-token>",
  "capabilities": {
    "qq_long_term_memory_shared": true,
    "qq_self_service_binding": false
  }
}
```

响应不返回 QQ 号、原始 external subject、Cookie token、Hindsight bank ID 或内部权限信息。未登录返回 401 `unauthenticated`。

`account_id` 作为诊断、事件关联和未来账号数据导出使用，可以返回给当前已认证用户；MVP 前端不得把它放入任何资源请求来选择授权范围。即使客户端提交相同或伪造的 account_id，服务端也必须忽略或拒绝，并始终使用登录会话解析的 Account。

### 4.4 退出

```http
POST /api/v1/auth/logout
X-CSRF-Token: <token>
```

有效会话且 CSRF 校验通过时返回 `204 No Content`，撤销服务端会话并清除 Cookie。若 Cookie 对应的会话已经过期或不存在，端点可直接返回 204 并清除无效 Cookie，因为没有仍受保护的登录状态可被跨站修改；若 Cookie 对应有效会话但 CSRF 缺失或错误，必须返回 403 `csrf_invalid`。

### 4.5 登录态失效

- 所有受保护 JSON API 返回 401 `unauthenticated`。
- SSE 在响应头发送前失效返回 401；连接建立后会话失效则发送 `auth.expired` 控制事件并关闭。
- Adapter 收到 401/`auth.expired` 后停止新命令和事件订阅，保留尚未发送的输入框文本于当前内存，并导航到登录入口。

## 5. 通用 DTO

### 5.1 `ConversationDto`

```json
{
  "conversation_id": "0198aa12-2ec7-7d58-9c53-aa4e09738c37",
  "title": "新的对话",
  "status": "active",
  "last_message_seq": 8,
  "metadata_version": 4,
  "created_at": "2026-08-04T08:31:00.000Z",
  "updated_at": "2026-08-04T08:35:20.000Z"
}
```

MVP API 只返回 active Conversation；archived 是后续能力预留状态。

### 5.2 `MessageDto`

```json
{
  "message_id": "0198aa13-707c-79cb-a58c-cb9e2367300f",
  "conversation_id": "0198aa12-2ec7-7d58-9c53-aa4e09738c37",
  "role": "assistant",
  "status": "completed",
  "content": "这是最终回复。",
  "sequence": 8,
  "client_request_id": null,
  "produced_by_run_id": "0198aa13-2117-7549-9592-dd3dcad23b44",
  "created_at": "2026-08-04T08:34:00.000Z",
  "completed_at": "2026-08-04T08:35:20.000Z"
}
```

字段规则：

- user Message：`status=accepted`、`client_request_id` 非空、`produced_by_run_id=null`、`completed_at=null`。
- pending Agent Message：`content=null`、`completed_at=null`。
- completed Agent Message：`content` 和 `completed_at` 非空。
- failed/aborted Agent Message：`content` 可以为空，`completed_at` 非空。

### 5.3 `RunDto`

```json
{
  "run_id": "0198aa13-2117-7549-9592-dd3dcad23b44",
  "conversation_id": "0198aa12-2ec7-7d58-9c53-aa4e09738c37",
  "session_id": "0198aa12-b3d5-7f98-b349-c11da925d7e3",
  "trigger_message_id": "0198aa13-05aa-76df-9505-d95b8564d0be",
  "retry_of_run_id": null,
  "status": "completed",
  "failure": null,
  "version": 3,
  "created_at": "2026-08-04T08:34:00.000Z",
  "started_at": "2026-08-04T08:34:01.000Z",
  "finished_at": "2026-08-04T08:35:20.000Z",
  "updated_at": "2026-08-04T08:35:20.000Z"
}
```

失败时：

```json
{
  "failure": {
    "code": "model_unavailable",
    "message": "Agent 暂时无法完成本次请求，请稍后重试。",
    "retryable": true
  }
}
```

`failure` 只在 failed Run 非空。不得返回堆栈、供应商原始错误、SQL、Prompt、模型密钥或工具凭据。

### 5.4 `RunSnapshotDto`

```json
{
  "run": { "run_id": "...", "status": "running" },
  "assistant_message": {
    "message_id": "...",
    "role": "assistant",
    "status": "pending",
    "content": null,
    "produced_by_run_id": "..."
  }
}
```

实际响应中的嵌套对象必须是完整 `RunDto` 和 `MessageDto`。每个 Run 必须存在且只存在一个 `assistant_message`；API 不返回 null 占位掩盖数据库不变量破坏。

### 5.5 分页信封

```json
{
  "items": [],
  "next_cursor": null,
  "has_more": false
}
```

`next_cursor` 是 URL-safe opaque string。客户端不得解析、拼接或跨 Account、端点、筛选条件复用。

## 6. Conversation API

### 6.1 创建 Conversation

```http
POST /api/v1/conversations
Idempotency-Key: 0198aa11-29ce-75b4-8584-b5fafcd18ce7
X-CSRF-Token: <token>
Content-Type: application/json
```

请求：

```json
{
  "title": null
}
```

`title=null` 或省略时使用确定性回退标题“新的对话”；P1 可在首条消息完成后自动生成标题。

成功：`201 Created`

```json
{
  "conversation": { "conversation_id": "...", "title": "新的对话" }
}
```

同时返回 `Location: /api/v1/conversations/{conversation_id}` 和 Conversation ETag。相同 Account、`create_conversation` operation、Idempotency-Key 和规范化 title 返回同一个 Conversation。前端在一次创建意图开始时生成 key，并在请求完成前禁用或合并重复点击；响应丢失后的网络重试沿用该 key，因此不会产生重复空对话。新的主动创建意图或改变 title 后必须生成新 key。

### 6.2 Conversation 列表

```http
GET /api/v1/conversations?limit=30&cursor=<opaque>
```

- `limit` 默认 30，范围 1～100。
- 按 `(updated_at DESC, conversation_id DESC)` 排序。
- cursor 固化上一页最后一项的排序键、端点版本和筛选上下文，并使用服务端签名防篡改。

成功：`200 OK`

```json
{
  "items": [
    {
      "conversation_id": "...",
      "title": "项目讨论",
      "status": "active",
      "last_message_seq": 8,
      "metadata_version": 4,
      "created_at": "2026-08-04T08:31:00.000Z",
      "updated_at": "2026-08-04T08:35:20.000Z"
    }
  ],
  "next_cursor": "eyJ2IjoxLC4uLn0",
  "has_more": true
}
```

Conversation 的 `updated_at` 在分页期间可能变化，因此跨页结果是 keyset pagination 的弱快照：服务端保证每页内部顺序，客户端按 `conversation_id` 去重；需要最新顺序时从第一页刷新。

### 6.3 Conversation 详情

```http
GET /api/v1/conversations/{conversation_id}
```

成功：`200 OK`

```json
{
  "conversation": { "conversation_id": "...", "metadata_version": 4 },
  "active_run": {
    "run": { "run_id": "...", "status": "running" },
    "assistant_message": { "message_id": "...", "status": "pending" }
  }
}
```

无活跃 Run 时 `active_run=null`。响应返回 `ETag: "conversation-{conversation_id}-m{metadata_version}"`。详情不内嵌完整历史，消息通过分页端点加载。发送消息只改变 `last_message_seq/updated_at`，不会改变 metadata ETag。

### 6.4 重命名 Conversation（P1）

```http
PATCH /api/v1/conversations/{conversation_id}
If-Match: "conversation-{conversation_id}-m4"
X-CSRF-Token: <token>
Content-Type: application/json
```

请求：

```json
{
  "title": "新的标题"
}
```

成功：`200 OK`，返回完整 `ConversationDto` 和新 ETag。

- 缺少 `If-Match` 返回 428 `precondition_required`。
- metadata_version 已变化返回 412 `version_conflict`，details 可包含当前安全版本号，不自动覆盖。
- MVP 不支持通过 PATCH 修改 status、归属或 `last_message_seq`。

P1 自动标题必须使用条件更新：仅当标题仍为系统默认来源时才写入，并递增 `metadata_version`；用户手动重命名后自动标题不得覆盖。自动标题失败不影响 Message/Run 完成。是否通过独立 Outbox 执行以及用于区分 default/auto/user 的 `title_source` 字段，在启用 P1 前通过数据库 migration 和标题任务详细设计确定。

## 7. Message API

### 7.1 获取消息历史

```http
GET /api/v1/conversations/{conversation_id}/messages?limit=50&cursor=<opaque>
```

- `limit` 默认 50，范围 1～100。
- 首次请求返回最新一页；SQL 按 sequence DESC 读取，响应 `items` 按 sequence ASC 排列，便于直接插入聊天列表。
- `next_cursor` 指向更早消息，内部基于不可变的 `before_sequence`。
- 同一页及连续 cursor 页不会打乱 sequence；客户端仍按 `message_id` 去重。

成功：`200 OK`

```json
{
  "items": [
    { "message_id": "...", "role": "user", "status": "accepted", "sequence": 7 },
    { "message_id": "...", "role": "assistant", "status": "completed", "sequence": 8 }
  ],
  "next_cursor": null,
  "has_more": false,
  "conversation_last_message_seq": 8
}
```

实际 items 为完整 `MessageDto`。历史端点返回 pending/failed/aborted Agent Message，因为它们是用户可见执行槽位；仅 Context Builder 会过滤这些状态。

### 7.2 发送消息

```http
POST /api/v1/conversations/{conversation_id}/messages
Idempotency-Key: 0198aa13-0047-70b7-8b70-b6b725f855fd
X-CSRF-Token: <token>
Content-Type: application/json
```

请求：

```json
{
  "content": "请帮我总结这段内容。"
}
```

服务端把 `Idempotency-Key` 的 UUID 同时写入用户 Message 的 `client_request_id`。成功接受后：`202 Accepted`

```json
{
  "user_message": { "message_id": "...", "role": "user", "status": "accepted", "sequence": 7 },
  "assistant_message": { "message_id": "...", "role": "assistant", "status": "pending", "sequence": 8 },
  "run": { "run_id": "...", "status": "queued", "trigger_message_id": "..." },
  "events_url": "/api/v1/runs/0198aa13-2117-7549-9592-dd3dcad23b44/events"
}
```

三个 DTO 均为完整对象，并由同一数据库事务创建。相同 Account、operation、Idempotency-Key 和相同规范化请求必须返回第一次冻结的 HTTP 状态与 DTO，并带 `Idempotency-Replayed: true`。

特殊错误：

| 条件 | HTTP / code |
|---|---|
| 内容为空白 | 422 `empty_message` |
| 超过字符或字节上限 | 413 `message_too_large` |
| Conversation 已有 active Run | 409 `conversation_busy` |
| 相同幂等键、不同 Conversation/content | 409 `idempotency_conflict` |
| Conversation 已归档或无权访问 | 404 `resource_not_found` |

失败响应不会创建 Message、Run、sequence 或 Outbox。客户端只有在没有收到可解析响应且仍代表同一发送意图时才能用原 Idempotency-Key 重发；编辑 content 后必须生成新 key。

## 8. Run API

### 8.1 查询 Run

```http
GET /api/v1/runs/{run_id}
```

成功：`200 OK`，返回完整 `RunSnapshotDto`：

```json
{
  "run": { "run_id": "...", "status": "failed", "failure": { "code": "model_unavailable", "message": "...", "retryable": true } },
  "assistant_message": { "message_id": "...", "status": "failed", "content": null }
}
```

该端点是刷新、SSE 断线、事件缺口和终态校正的权威回源入口。它不查询 Temporal 后直接覆盖产品状态；Reconciler 负责将执行事实收敛到数据库。

### 8.2 取消 Run

```http
POST /api/v1/runs/{run_id}/cancel
Idempotency-Key: 0198aa20-5809-7a72-8ce9-f93e498da310
X-CSRF-Token: <token>
Content-Type: application/json
```

请求体为 `{}`，不接受客户端目标状态或 account_id。

响应语义：

| 当前情况 | HTTP | 响应 |
|---|---:|---|
| queued 且无 current Workflow Execution | 200 | 同事务收敛为 cancelled/aborted 的完整 RunSnapshot |
| queued 且存在 current Workflow Execution | 202 | RunSnapshot，状态 cancelling |
| running | 202 | RunSnapshot，状态 cancelling |
| cancelling | 202 | 当前 RunSnapshot，不重复取消副作用 |
| cancelled | 200 | 当前终态 RunSnapshot，按幂等成功 |
| completed/failed | 409 | `run_not_cancellable`，details 携带当前 `run_status` |

同一幂等键重放返回第一次冻结的状态和响应。即使客户端错误地产生新 key，`cancel-run:{run_id}` Outbox 业务键仍防止重复外部取消。

“queued 且无 current Workflow Execution”只是 API 直接取消的数据库判定，不构成对 Dispatcher 的进程间互斥。`start_run` 消费者在每次调用 Temporal 前仍必须重新查询 Run；只有权威状态仍为 queued 才允许启动。观察到 cancelling/cancelled/failed/completed 时不得启动，应幂等完成或交给取消/Reconciler 收口。API 与 Dispatcher 之间残余的外部调用窄竞态按数据库设计 10.5 节处理。

### 8.3 重试 Run

```http
POST /api/v1/runs/{run_id}/retry
Idempotency-Key: 0198aa21-5c31-74d3-8ba2-85182cead4b1
X-CSRF-Token: <token>
Content-Type: application/json
```

请求体为 `{}`。仅 failed/cancelled Run 可作为来源；重试复用原 `trigger_message_id` 和 context 水位，创建新 Run 与新 pending Agent Message，不复制 user Message。

创建成功：`202 Accepted`

```json
{
  "source_run_id": "0198aa13-2117-7549-9592-dd3dcad23b44",
  "assistant_message": { "message_id": "...", "status": "pending", "sequence": 9 },
  "run": { "run_id": "...", "retry_of_run_id": "0198aa13-2117-7549-9592-dd3dcad23b44", "status": "queued" },
  "events_url": "/api/v1/runs/.../events"
}
```

- 相同幂等键重放返回原冻结响应。
- 不同 key 竞争同一来源时，数据库只允许一个直接子 Run；后到请求返回该子 Run，HTTP 200，并带 `Resource-Reused: true`。
- 来源非 failed/cancelled 返回 409 `run_not_retryable`；Conversation 存在与该直接子 Run 无关的 active Run 时返回 409 `conversation_busy`。

## 9. 分页游标契约

### 9.1 游标属性

cursor 是服务端签名的 base64url opaque token，逻辑内容至少包括：

```json
{
  "v": 1,
  "endpoint": "conversation_messages",
  "account_scope": "server-derived-scope",
  "resource_id": "conversation-id",
  "sort_key": { "before_sequence": 7 },
  "limit": 50,
  "issued_at": "2026-08-04T08:35:20Z"
}
```

实际 token 不保证可被客户端解码。签名密钥由服务端管理；cursor 不包含消息正文、外部身份或可复用登录凭证。

### 9.2 错误与生命周期

- 格式、签名、端点、Account 或资源不匹配：400 `invalid_cursor`。
- 超出配置有效期：400 `cursor_expired`，客户端从第一页重新加载。
- cursor 中固定 limit；客户端后续提交不同 limit 返回 `invalid_cursor`。
- Cursor 不是授权令牌；每次请求仍执行会话与资源归属校验。
- Message cursor 基于 immutable sequence，支持稳定加载更早历史。
- Conversation cursor 基于可变 updated_at，是弱快照；客户端按 ID 去重并允许刷新第一页。

## 10. 错误契约

### 10.1 错误信封

```json
{
  "error": {
    "code": "conversation_busy",
    "message": "当前对话仍有请求正在执行，请稍后再试。",
    "request_id": "req_01K1...",
    "retryable": true,
    "details": {
      "active_run_id": "0198aa13-2117-7549-9592-dd3dcad23b44"
    }
  }
}
```

- `code` 供程序判断，已发布后保持稳定。
- `message` 面向用户且可本地化，客户端不得用它判断逻辑。
- `details` 只包含完成交互所需的安全字段；默认 `{}`。
- 5xx 不返回内部异常；服务端通过 request_id 关联日志。

### 10.2 稳定错误码

| HTTP | code | retryable | 含义 |
|---:|---|---:|---|
| 400 | `malformed_request` | 否 | JSON 或基础请求格式错误 |
| 400 | `invalid_cursor` | 否 | cursor 无效或上下文不匹配 |
| 400 | `cursor_expired` | 是 | cursor 已过期，应从第一页加载 |
| 401 | `unauthenticated` | 是 | 未登录、会话失效或 Account 不可用 |
| 403 | `csrf_invalid` | 是 | CSRF token 缺失、失效或来源校验失败 |
| 404 | `resource_not_found` | 否 | 资源不存在或不属于当前 Account |
| 406 | `not_acceptable` | 否 | 不支持的响应类型 |
| 409 | `conversation_busy` | 是 | Conversation 已有 active Run |
| 409 | `idempotency_conflict` | 否 | 同一幂等范围的 key 对应不同语义请求 |
| 409 | `run_not_cancellable` | 否 | Run 已完成或失败，不能取消 |
| 409 | `run_not_retryable` | 否 | Run 状态不允许重试 |
| 412 | `version_conflict` | 是 | Conversation ETag 已过期 |
| 413 | `message_too_large` | 否 | 消息超过字符、字节或请求体限制 |
| 415 | `unsupported_media_type` | 否 | Content-Type 不支持 |
| 422 | `validation_error` | 否 | 字段校验失败，details 提供字段级安全信息 |
| 422 | `empty_message` | 否 | 规范化消息为空白 |
| 428 | `precondition_required` | 是 | 修改资源缺少 If-Match |
| 429 | `rate_limited` | 是 | 达到账户、IP 或端点限流 |
| 503 | `service_unavailable` | 是 | 关键依赖暂不可用，且无法安全持久化命令 |

数据库唯一/外键/trigger 错误必须由 API 映射为上述领域错误。不得把约束名、表名或 SQLSTATE 直接返回客户端。

## 11. Idempotency-Key 契约

### 11.1 适用端点

| operation | 端点 | 结果资源 |
|---|---|---|
| `create_conversation` | `POST /api/v1/conversations` | Conversation |
| `send_message` | `POST .../messages` | user Message + Agent Message + Run |
| `cancel_run` | `POST .../cancel` | 目标 RunSnapshot |
| `retry_run` | `POST .../retry` | 新 Run + Agent Message |

### 11.2 规则

1. Header 必须出现一次，值为规范 UUID，缺失或非法返回 422 `validation_error`。
2. key 的作用域是认证 Account + operation；不能跨 Account 命中。
3. 相同 key 和相同规范化业务请求返回第一次冻结的 HTTP 状态、响应 DTO 和资源 ID。
4. 相同 key 与不同 path resource/content 返回 409 `idempotency_conflict`。
5. 客户端编辑请求、主动再次取消/重试等新意图必须生成新 key；网络重放沿用旧 key。
6. API 不使用内存锁提供正确性，依赖 `idempotency_commands` 和领域唯一约束。
7. 401/403 等未进入领域事务的响应不占用 key；一旦领域命令完成，成功或稳定业务冲突都可以冻结。

## 12. SSE 契约

### 12.1 建连

```http
GET /api/v1/runs/{run_id}/events
Accept: text/event-stream
```

SSE 使用同源 HttpOnly Cookie 鉴权，不需要 CSRF token。响应头：

```text
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache, no-transform
X-Accel-Buffering: no
```

发送任何 SSE 数据前可以返回 401、404、429 或 503 JSON 错误。响应建立后不能再改变 HTTP 状态，后续问题使用控制事件并关闭或降级。

前端必须使用基于 `fetch()` + `ReadableStream` 的可控 SSE Client，或具备等价能力的 SSE 库，并设置 `credentials: 'same-origin'`。客户端必须能够读取建连失败的 JSON 错误、显式关闭连接、禁止默认自动重连，并在 degraded 后永久停止本次 delta 拼接。不得假设浏览器原生 `EventSource` 的自动重连、Last-Event-ID 和错误处理行为满足本契约。

### 12.2 SSE 帧与事件信封

```text
id: 0198aa25-4d4e-71a4-a78e-8df12a51f590
event: message.delta
data: {"schema_version":1,"event_id":"0198aa25-4d4e-71a4-a78e-8df12a51f590","event_type":"message.delta","conversation_id":"...","run_id":"...","message_id":"...","stream_id":"0198aa24-c054-76fa-a5c8-886aad4d6ab6","event_seq":12,"occurred_at":"2026-08-04T08:35:01.120Z","payload":{"delta":"你好"}}

```

领域事件信封：

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | integer | 初始为 1 |
| `event_id` | UUID string | 去重标识；重复投递保持相同 ID |
| `event_type` | string | 必须与 SSE `event:` 一致 |
| `conversation_id` | UUID string | Run 归属 |
| `run_id` | UUID string | 订阅 Run |
| `message_id` | UUID string/null | Message 事件必填，Run-only 事件可空 |
| `stream_id` | UUID string/null | 单次在线 Event Sink 生命周期；Gateway、数据库状态投影和终态事件为 null |
| `event_seq` | integer/null | 同一 stream_id 内从 1 开始单调递增；非 Event Sink 投影为 null |
| `occurred_at` | timestamp | 事件产生时间，不是客户端接收时间 |
| `payload` | object | 按事件类型定义 |

SSE `id` 等于 `event_id`，客户端按 event_id 去重。当前 Run Event Sink 启动时生成新的 stream_id，并在内存中从 event_seq=1 递增；MVP 不在 PostgreSQL 或 Redis 保存计数器，也不承诺跨 Worker/Event Sink 重启连续。客户端订阅后观察到的首个非空 event_seq 建立当前基线，不要求它等于 1；同一 stream_id 的后续新事件必须连续 +1。重复序号去重，倒序或跳号触发短暂缓冲；stream_id 改变或缺口无法补齐时立即进入 degraded。未知 event_type 记录指标后忽略，不能据此把 Run 判为成功。

终态事件不参加在线 delta 序列，`stream_id/event_seq` 均为 null。`publish_terminal_event` Outbox 保存稳定 terminal event_id；Terminal Event Publisher 重试时复用该 ID，并携带完整数据库 RunSnapshot。这样 delta 保持轻量，Worker 重启后的正确性由 degraded + 数据库回源保证，而不是伪造跨生命周期连续序号。

### 12.3 Gateway 控制事件

#### `run.snapshot`

建连时的第一个 data 事件，`event_seq=null`：

```json
{
  "event_type": "run.snapshot",
  "stream_id": null,
  "event_seq": null,
  "payload": {
    "snapshot": {
      "run": { "run_id": "...", "status": "running" },
      "assistant_message": { "message_id": "...", "status": "pending", "content": null }
    }
  }
}
```

snapshot 来自数据库。它不包含已发布但未持久化的历史 delta，也不建立可重放 cursor。

#### `stream.degraded`

```json
{
  "event_type": "stream.degraded",
  "stream_id": null,
  "event_seq": null,
  "payload": {
    "reason": "sequence_gap",
    "recovery": "query_run",
    "retry_after_ms": 2000
  }
}
```

reason 稳定值：`handshake_buffer_overflow`、`sequence_gap`、`redis_unavailable`、`upstream_disconnected`。收到后 Adapter 停止拼接 delta，关闭流并进入第 13 章回源流程。

#### `auth.expired`

表示建连后会话失效，payload 为空对象；客户端关闭流并进入登录恢复。

心跳使用 SSE comment `: keepalive`，不是业务事件，不进入 Adapter 消息列表。

### 12.4 领域事件

#### `run.started`

```json
{
  "event_type": "run.started",
  "message_id": null,
  "stream_id": "0198aa24-c054-76fa-a5c8-886aad4d6ab6",
  "event_seq": 1,
  "payload": {
    "status": "running",
    "started_at": "2026-08-04T08:34:01.000Z"
  }
}
```

#### `message.delta`

```json
{
  "event_type": "message.delta",
  "message_id": "0198aa13-707c-79cb-a58c-cb9e2367300f",
  "stream_id": "0198aa24-c054-76fa-a5c8-886aad4d6ab6",
  "event_seq": 12,
  "payload": {
    "delta": "增量文本"
  }
}
```

- delta 是应按 event_seq 追加的 UTF-8 文本片段，不是完整 Message。
- 只允许目标为当前 Run 的 pending Agent Message。
- delta 不持久化，不能包含权威 Message status。
- 重复 event_id 或同一 stream_id/sequence 不重复追加；乱序可短暂缓冲，发现 stream 变化或无法补齐的缺口立即 degraded。
- Adapter 必须把 delta 当作易失显示缓冲，不写入后端或浏览器持久真相源。

#### `run.progress`

`run.progress` 是当前在线 Event Sink 发布的非权威、可丢失进度提示：

```json
{
  "event_type": "run.progress",
  "message_id": null,
  "stream_id": "0198aa24-c054-76fa-a5c8-886aad4d6ab6",
  "event_seq": 13,
  "payload": {
    "phase": "executing_tool",
    "summary": "正在分析项目文件"
  }
}
```

`phase` 是稳定枚举：

| phase | 含义 |
|---|---|
| `assembling_context` | 正在加载和组装当前 Conversation 上下文 |
| `recalling_memory` | 正在召回长期记忆 |
| `calling_model` | 正在等待模型生成或决策 |
| `selecting_tools` | 正在选择可用工具 |
| `executing_tool` | 正在执行一个或多个工具 |
| `finalizing` | 已获得候选结果，正在完成安全处理或持久化 |

规则：

- `run.progress` 与 delta 使用相同的 `stream_id/event_seq` 在线序列；不持久化、不重放，也不改变 Run 数据库状态。
- `summary` 必须由服务端模板/白名单生成，最多 120 个 Unicode code point；不能直接使用模型自由文本或工具输出。
- payload 只允许 `phase` 和 `summary`，不得包含工具参数、工具原始结果、文件内容、提示词、隐藏推理、凭证、绝对路径或账号标识。
- 生产方应合并重复进度并限制频率，默认同一 Run 每秒最多一个 progress 事件；超限可丢弃。合并/丢弃必须发生在分配 `event_id/event_seq` 之前，不能人为制造在线序号缺口。
- 未知 phase 时 Adapter 使用通用“正在处理”提示并记录指标，不展示原始未知值。
- progress 只显示在独立运行状态区域，不追加到 Agent Message、Markdown 内容或 Conversation 历史。
- 终态、`stream.degraded`、切换 Conversation 或重新查询 Run 后清除临时 progress。
- progress 丢失或乱序按在线事件规则处理，但其缺失不影响 Run 正确性；客户端不得根据 progress 判断完成、失败或取消。

#### `run.status`

用于 queued/running/cancelling 等非终态变化：

```json
{
  "event_type": "run.status",
  "stream_id": null,
  "event_seq": null,
  "payload": {
    "status": "cancelling",
    "run_version": 3
  }
}
```

#### `run.completed`

```json
{
  "event_type": "run.completed",
  "message_id": "...",
  "stream_id": null,
  "event_seq": null,
  "payload": {
    "snapshot": {
      "run": { "run_id": "...", "status": "completed" },
      "assistant_message": { "message_id": "...", "status": "completed", "content": "完整最终回复" }
    }
  }
}
```

Adapter 以该 snapshot 完整替换本地 delta 缓冲，然后再执行一次 GET Run 校正。`run.completed` 不等同于仅仅停止收到 delta。

#### `run.failed`

```json
{
  "event_type": "run.failed",
  "message_id": "...",
  "stream_id": null,
  "event_seq": null,
  "payload": {
    "snapshot": {
      "run": {
        "run_id": "...",
        "status": "failed",
        "failure": {
          "code": "model_unavailable",
          "message": "Agent 暂时无法完成本次请求，请稍后重试。",
          "retryable": true
        }
      },
      "assistant_message": { "message_id": "...", "status": "failed", "content": null }
    }
  }
}
```

#### `run.cancelled`

`stream_id/event_seq` 均为 null，payload 携带 cancelled Run 和 aborted Agent Message 的完整 `RunSnapshotDto`。客户端只有收到该事件或 GET Run 返回 cancelled 后，才能展示“已取消”；收到 cancelling 只能展示“正在取消”。

`run.completed` 已携带完整 Message 和 Run，是完成路径唯一必需终态事件；不再额外发布重复的 `message.completed`。通用消息组件通过 Adapter 从 RunSnapshot 取得 completed Message，执行层通过同一事件关闭 Run 状态。即使客户端只收到 `run.completed`，也拥有完整恢复数据。

终态事件均由 Terminal Event Publisher 从已提交 Outbox 发布，允许 at-least-once 重复。相同终态重复事件不得造成重复 Message 或重复 UI 提示。

### 12.5 建连缓冲和关闭

1. Gateway 先鉴权并校验 Run 归属。
2. 先订阅 Redis，再启用每连接 256 事件或 1 MiB 的有界握手缓冲。
3. 查询数据库并发送 `run.snapshot`，再按同一 stream_id 的 event_seq 排空在线事件；null-seq 数据库状态投影按接收顺序处理，缓冲中出现多个非空 stream_id 时直接 degraded。
4. `run.snapshot` 已包含终态时丢弃缓冲 delta，该 snapshot 本身就是权威恢复结果；无需伪造新的领域 event ID/sequence，发送后即可关闭。
5. 缓冲溢出、序号缺口或 Redis 断开时发送 `stream.degraded`。
6. 收到终态通知后 Gateway 再查数据库，确认已提交终态后发送并关闭。

MVP 不提供历史 delta replay。若客户端库提交 `Last-Event-ID`，Gateway 不据此补发事件，仍建立新订阅并发送新 snapshot；客户端不能把该 header 当作恢复保证。

## 13. 断线与回源规则

### 13.1 允许丢失范围

- Redis 订阅真正建立前的 delta 允许丢失。
- 浏览器/SSE 断开期间的 delta 允许丢失。
- `stream.degraded` 后的 delta 不再用于拼接，允许丢失。
- 握手缓冲正常工作期间已接收的 delta 不应因数据库 snapshot 查询而丢失。
- 最终 Message、Run 和终态 Outbox 不允许丢失。

### 13.2 客户端恢复算法

遇到 SSE error、`stream.degraded`、event_seq 缺口或页面刷新时：

1. 停止把后续 delta 追加到权威显示内容，保留当前临时文本仅作视觉提示。
2. `GET /api/v1/runs/{run_id}`。
3. 若 Run 为 completed/failed/cancelled，用完整 RunSnapshot 覆盖本地 Run 和 Agent Message。
4. 若 Run 为 queued/running/cancelling，显示“连接中断，任务仍在执行”，按服务器建议退避轮询 Run。
5. MVP 不把重新建连后的新 delta 与可能缺失的旧前缀拼成完整回复；继续等待最终持久化 Message。
6. Run 终态后停止轮询；用户显式取消仍通过取消 API，而不是关闭 SSE。

同一页面的网络库可以自动重新建立 SSE 以接收状态通知，但 Adapter 一旦检测到 delta 缺口，就必须保持 degraded，直到最终 Message 回源。页面刷新后自动续接原增量流属于 P2，不是 MVP 验收条件。

### 13.3 轮询退避

- 首次回源立即执行。
- active Run 建议按 1s、2s、3s、5s 递增，之后最多每 5s 一次；响应 `Retry-After` 时优先遵循。
- 页面不可见时可降低频率，但不能据此把 Run 判为取消。
- 连续 503 时显示服务暂不可用并保留手动刷新入口。

## 14. assistant-ui Adapter 映射

### 14.1 边界类型

业务前端先定义自有类型，再在单一 adapter 目录映射 assistant-ui 当前版本：

```text
HpConversation
HpMessage
HpRunSnapshot
HpStreamEvent
HpCommandError
        ↓
HpAgent assistant-ui Adapter
        ↓
assistant-ui runtime/message/thread types
```

业务 API client、路由和状态 store 不得到处导入 assistant-ui 内部类型。升级 assistant-ui 时只修改 Adapter 和组件边界。

### 14.2 Message 映射

| 后端状态 | assistant-ui 展示语义 | 规则 |
|---|---|---|
| user/accepted | 已由服务端确认的 user message | 使用后端 message_id 作为稳定 key |
| assistant/pending + queued | 等待执行 | 显示占位与停止入口 |
| assistant/pending + running | 正在生成 | delta 写入仅内存的 volatile buffer |
| assistant/pending + cancelling | 正在取消 | 禁止再次发送取消副作用 |
| assistant/completed | 完整 assistant message | 以 content 覆盖 volatile buffer，正常 Markdown 渲染 |
| assistant/failed | 执行失败槽位 | 展示 Run failure 与重试入口，不伪造正文 |
| assistant/aborted | 已取消槽位 | 丢弃未确认 delta，展示取消状态 |

Markdown、代码块和链接安全渲染由 UI 层完成，但 content 始终来自 `MessageDto` 或临时 delta，不允许执行原始 HTML/脚本。

### 14.3 发送映射

1. 用户点击发送时生成 UUID Idempotency-Key，保留原始输入草稿。
2. Adapter 可以显示带 `local:{key}` 的“待确认”临时项，但不把它写入 canonical Message store。
3. POST 成功后，用后端 user/assistant message_id 和 Run 替换临时项，清空输入并订阅 events URL。
4. 无响应的网络失败保留草稿并用同一 key 提供“重新发送”。
5. 明确 4xx 业务失败移除待确认项并恢复草稿；conversation_busy 可引导用户等待 active Run。

### 14.4 停止与重试映射

- assistant-ui 停止动作调用 cancel API；不得只停止本地渲染或关闭 SSE Client。
- 202 cancelling 映射为“正在取消”；200 cancelled 才映射为“已取消”。
- failed/cancelled 的重试动作调用 retry API，复用原 user Message，加入新 pending Agent Message。
- MVP 禁用 assistant-ui 的 regenerate/branch 能力；它与 retry 不是同一业务语义。

### 14.5 Conversation 初始化与切换

1. 进入 Conversation 时先 GET detail 和 messages，不从 assistant-ui local cache 恢复权威历史。
2. 以 sequence 排列 Message；按 message_id 去重。
3. detail 含 active_run 时 GET Run 校正；页面刷新后的 MVP 默认进入 degraded/轮询。即使为接收终态通知重新订阅 SSE，也不得把新 delta 与刷新前已丢失的前缀拼接。
4. 切换 Conversation 时清理旧 Conversation 的 volatile delta、SSE 连接和临时运行状态，不清理后端数据。
5. 多标签页各自维护投影；正确性依赖后端幂等和单 active Run 约束，不依赖 BroadcastChannel 协调。

### 14.6 事件映射

| SSE event | Adapter 动作 |
|---|---|
| `run.snapshot` | 初始化/校正 Run 和 Agent Message 投影 |
| `run.started` | queued → running 展示；不自行改数据库状态 |
| `message.delta` | 去重、顺序检查后追加 volatile buffer |
| `run.progress` | 在独立状态区域替换临时安全摘要；不写入 Message content |
| `run.status` | 更新 queued/running/cancelling 展示 |
| `run.completed` | 用 RunSnapshot 覆盖并 GET Run 最终校正 |
| `run.failed` | 清除不可信 delta，展示安全 failure 与 retry |
| `run.cancelled` | 清除不可信 delta，展示 aborted/cancelled |
| `stream.degraded` | 停止拼接，关闭流，进入回源轮询 |
| `auth.expired` | 停止受保护操作并进入重新登录流程 |

## 15. HTTP 与 SSE 验收矩阵

| 编号 | 场景 | 预期结果 |
|---|---|---|
| API-001 | 未登录访问 Conversation/Run/SSE | JSON/SSE 建连前返回 401，不泄露资源存在性 |
| API-002 | Account A 请求 Account B 的资源 | 返回 404 `resource_not_found` |
| API-003 | 同一发送 key 并发重放 | 返回同一 user Message、Agent Message 和 Run |
| API-004 | 同一发送 key 改变 content/Conversation | 返回 409 `idempotency_conflict` |
| API-005 | 两标签页同时向同一 Conversation 发送不同消息 | 最多一个 202；另一个 409 `conversation_busy` |
| API-006 | 消息历史跨多页加载 | 每页升序且整体 sequence 不乱序、不重复 |
| API-007 | queued 且未建立 Workflow 时取消 | 200 cancelled/aborted，不启动 Temporal Workflow |
| API-008 | running Run 取消 | 202 cancelling，最终由 SSE/GET 收敛真实终态 |
| API-009 | 两次不同 key 重试同一失败 Run | 只产生一个直接子 Run |
| API-010 | SSE 建连时正在产生 delta | snapshot 后排空握手缓冲，不因查询窗口静默丢失已订阅事件 |
| API-011 | SSE 序号缺口或缓冲溢出 | `stream.degraded`，Adapter 停止拼接并 GET Run |
| API-012 | 完成事件重复投递 | Message/Run/UI 不重复，最终 content 与 GET Run 一致 |
| API-013 | SSE 断开但 Run 继续 | 不自动取消；轮询后显示数据库终态 |
| API-014 | 页面刷新时 Run active | 加载历史和 RunSnapshot；不承诺恢复旧 delta |
| API-015 | 登录会话在 SSE 中失效 | 收到 `auth.expired` 或连接关闭后 401，保留未发送草稿 |
| API-016 | Conversation ETag 过期后重命名 | 412 `version_conflict`，不覆盖新标题 |
| API-017 | assistant-ui 本地缓存清空 | 重新登录后可完全从 API 重建历史和状态 |
| API-018 | 创建 Conversation 响应丢失并使用同一 key 重放 | 返回同一个 Conversation，不产生重复空对话 |
| API-019 | 正常发送消息后使用旧 metadata ETag 改标题 | 若标题元数据未被其他操作修改则成功，不因 sequence 更新返回 412 |
| API-020 | Worker 在 delta 期间重启并产生新 stream_id | Adapter 立即 degraded，不把新 delta 拼到旧前缀，最终 GET Run 恢复 |
| API-021 | start Outbox 已领取但 Run 被直接取消 | Dispatcher 最终状态检查发现非 queued，不启动 Workflow |
| API-022 | 修改请求 CSRF 正确但 Origin 非同源 | 返回 403 `csrf_invalid`，不执行领域事务 |
| API-023 | SSE 建连返回结构化 401/404 | fetch-based Client 读取 JSON，关闭且不触发原生自动重连 |
| API-024 | Worker 发布 `run.progress` | 只接受稳定 phase 和安全 summary；Adapter 不追加到 Message/历史，终态时清除 |

## 16. 详细设计待确认项

以下实现参数可在开发前锁定，但不得改变契约语义：

1. 具体认证凭证校验适配器和 `/auth/*` 页面/回调路径。
2. Cursor 签名算法、密钥轮换和有效期。
3. CSRF token 的签发细节与前端刷新策略。
4. 限流的 Account/IP/端点配额和 `Retry-After` 计算。
5. SSE 心跳间隔、代理读超时和最大连接数。
6. assistant-ui 锁定版本及对应 runtime adapter 的具体 API。
7. 用户可见错误文案的本地化资源；稳定 code 不随文案变化。
8. `run.progress` 的生产端合并窗口和 UI 展示文案；不得扩展 payload 的安全边界。

## 17. 评审清单

- [ ] 接受 `/api/v1`、snake_case JSON、UUID 和 RFC 3339 UTC 约定。
- [ ] 接受认证凭证适配器可替换，而服务端有状态会话契约固定。
- [ ] 接受 `GET /api/v1/me` 返回 session-bound CSRF token。
- [ ] 接受 Conversation、Message、Run DTO 及资源归属隐藏规则。
- [ ] 接受创建 Conversation、发送、取消、重试强制 UUID `Idempotency-Key`。
- [ ] 接受 Message 32,000 code point / 128 KiB 上限。
- [ ] 接受 Message cursor 基于 sequence，Conversation cursor 为弱快照并由客户端去重。
- [ ] 接受 Conversation ETag 使用 metadata_version，不随 Message sequence 更新。
- [ ] 接受 queued 未启动 Run 的直接取消和 active Execution 的 cancelling 语义。
- [ ] 接受 SSE 使用 stream_id + 生命周期内 event_seq，不保证跨 Worker 重启连续。
- [ ] 接受 `run.progress` 只承载白名单 phase 和安全摘要，不成为 Message 或 Run 真相源。
- [ ] 接受完成路径只发布携带完整 RunSnapshot 的 `run.completed`，不重复发布 `message.completed`。
- [ ] 接受终态 event_id 稳定，但终态不参与 delta event_seq 连号。
- [ ] 接受 delta 不持久化、不通过 Last-Event-ID 重放。
- [ ] 接受断线/缺口后停止拼接并通过 GET Run 回源。
- [ ] 接受使用 fetch/ReadableStream 或等价可控 SSE Client，不直接依赖原生 EventSource 默认行为。
- [ ] 接受 CSRF token、Origin/Referer 和恒定时间比较的组合防护。
- [ ] 接受 assistant-ui 仅作为交互投影，通过集中 Adapter 映射。

本文通过评审后，前端 API client、assistant-ui Adapter、ASGI Controller、SSE Gateway 和契约测试必须共同引用本基线；不得在不同层各自定义同名但语义不同的 DTO 或错误码。
