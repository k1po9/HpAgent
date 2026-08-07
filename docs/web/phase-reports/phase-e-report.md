# HpAgent Web Phase E 实施报告

## 1. 结论

Phase E 已按 `docs/web/phase-e` 完成 SSE 实时事件管道与 assistant-ui 前端工作台：后端新增
SSE Gateway、Terminal Event Publisher 和 Web Run 事件桥，前端新增 `web/` React + TypeScript
工作台，并落地真实浏览器 E2E 门禁。

- 命令仍走 HTTP，SSE 只投影状态；PostgreSQL 是 Message、Run、Session 和终态的唯一真相源，
  Redis 只承载瞬时 delta/进度与终态通知。
- 登录、长对话、刷新恢复、多标签页 409、停止、重试、断线降级、Markdown/code 渲染和基础
  可访问性均以真实 API/PostgreSQL/Redis 端到端验证通过。
- 后端全量 345 项通过；前端单测 45 项通过；Playwright E2E 11 项全部通过。
- Ruff、mypy、ESLint、Prettier、`tsc`、vite build 全部通过。
- 生产环境不启用 Fake Executor。

## 2. E-01：前端工程与 Adapter 边界

- `web/` 目录，React + TypeScript + Vite，Zustand 状态管理，@radix-ui/themes 视觉层。
- assistant-ui 只允许出现在 `web/src/adapters/assistant-ui/` 单一适配层（合约 §14.1）；
  `types.ts` 是唯一导入 assistant-ui 类型表面的模块，业务 DTO（Hp*）与 API client 保持
  assistant-ui 无关。
- 无 Account/Conversation 真相存入组件内部状态：刷新后从 API 完全重建，后端 ID 是稳定 key。

## 3. E-02：API Client 与认证恢复

- same-origin HttpOnly Cookie；CSRF token 来自 `GET /api/v1/me`；修改请求统一携带
  `X-CSRF-Token` 与 `Idempotency-Key`（一次意图一个 key，网络重试复用）。
- 按稳定错误码恢复：`csrf_invalid` 用 `GET /me` 刷新一次 token 后原样重放（同幂等 key）；
  `conversation_busy` 引导等待 active Run；`idempotency_conflict` 不覆盖。
- 登录通过 303 + Set-Cookie 建立会话后以 `/me` 确认真实会话并播种 CSRF，不信任登录响应本身。
- 客户端不提交 `account_id`；失败请求不得自动换新幂等 key。

## 4. E-03、E-04：工作台与发送/停止/重试

- Conversation 列表/创建/切换、历史 keyset 分页、空状态；进入时先 GET detail + messages，
  不从 assistant-ui local cache 恢复权威历史。
- composer 发送：生成 Idempotency-Key → POST messages → 用后端 user/assistant message_id 和
  Run 替换临时项 → 订阅 events URL。
- 停止调用 cancel API；failed/cancelled 的 retry 调用 retry API 复用原 user Message；禁用
  assistant-ui 自带 regenerate/branch 语义。
- 同一 Conversation 有 active Run 时前端禁用冲突发送，但以后端 409 为最终裁决。

## 5. E-05：SSE Gateway、Terminal Event Publisher 与事件桥

- `GET /api/v1/runs/{run_id}/events`（`src/web_api/sse.py`）：鉴权校验归属 → 订阅 Redis
  `hpagent:web:run:{run_id}` 原始通道 → 有界握手缓冲 → 数据库 `run.snapshot` → 按 event_seq
  排空在线事件 → 终态后确认并关闭；心跳用 SSE comment；缓冲/序号/Redis 异常稳定降级。
- Terminal Event Publisher 只领取已提交的 `publish_terminal_event` Outbox，复用稳定
  `terminal_event_id`，终态事件 `stream_id/event_seq` 均为 null；发布失败不改数据库终态。
- `RedisWebRunEventSink` 扩展为契约形状 `run.started` / `message.delta` / `run.status` /
  `run.progress`，phase 白名单补齐 assembling_context/recalling_memory/calling_model/
  selecting_tools/executing_tool/finalizing，同一 stream_id 内 event_seq 从 1 单调递增。

## 6. E-06：SSE Client 与 Adapter

- 基于 `fetch()` + `ReadableStream` 的可控客户端（`credentials: 'same-origin'`），禁止原生
  EventSource 默认重连/Last-Event-ID 行为。
- 按 event_id 去重；同 stream_id 跳号或 stream_id 改变立即 degraded 并永久停止本次 delta
  拼接；progress 只显示在独立运行状态区域，不进 Message 内容；终态 snapshot 完整覆盖本地
  delta。
- 恢复算法：SSE error/degraded/刷新 → 停止拼接 → `GET /runs/{id}` → 终态则覆盖，active
  则按 1s/2s/3s/5s 退避轮询。
- external-store runtime 适配：authoritative `HpMessage[]` 流入，composer/onCancel 映射回
  store 的 send/stop；terminated Run 解除 composer 门禁以支撑长对话。

## 7. E-07：浏览器 E2E 与可用性验收

`make e2e` 基于 Playwright 与真实 API/PostgreSQL/Redis，双 webServer（API :8080 + vite
:5173），`workers: 1`、`fullyParallel: false`，避免同账号并发登录互相吊销会话。测试不依赖
测试顺序或共享 local state——截断全部应用表（含 accounts/identity_bindings）后全新库上
11/11 通过，等价 CI 空库首跑，账号由 `e2e-backend.sh` 启动播种。

| 覆盖项 | 用例 |
|---|---|
| 登录/登出 | 错误凭证安全提示、有效登录、登出回到登录门禁且刷新保持 |
| 长对话 | 三轮连续对话后 `reload()`，历史与三条回复均可见 |
| 多标签页 | 不同账号浏览器上下文完全隔离；第二个标签页发送命中 409 conversation_busy |
| 停止/重试 | 运行中点击停止到达已停止，Retry 复用到完成 |
| 断线 | 拦截 SSE 请求后发送，显示降级提示，经轮询恢复并完成 |
| Markdown/code | 回复中的 Python 代码块渲染为 `<pre><code>` |
| 可访问性 | 登录/工作台 heading 层级、label 化控件、语义按钮 |

### 7.1 E2E 暴露并修复的真实缺陷

E2E 首次把前端放在真实后端面前，连续暴露五个单测无法覆盖的缺陷：

1. **登录静默失败**：`api.login` 用 `redirect: "manual"`，浏览器把 303 包装成 opaque-redirect
   （status 0），登录永远返回 false。改为 `redirect: "follow"` 并通过 `/me` 确认会话。
2. **用户消息携带 status 导致线程崩溃**：`toThreadMessageLike` 无条件写入 `status`，
   assistant-ui 抛 "status is only supported for assistant messages"，HpThread 反复卸载。
   改为仅 assistant 消息携带 status，并新增单测锁回归。
3. **Idempotency-Key 从未上报文**：`ApiClient.request` 声明了 `idempotencyKey` 却不发送
   `Idempotency-Key` 头，每个修改请求被后端 422。修复为统一 `buildRequestHeaders`，CSRF
   重放路径同样复用同一 key。
4. **conversation_busy 提示被瞬间清除**：`refreshActiveRun` 与 run monitor 无脑清
   `activeRunError`，第二标签页的“当前对话仍有请求正在执行。”一闪即逝。改为 busy 提示在外部
   Run 到达终态前保持可见。
5. **E2E 后端不播号身份绑定**：`e2e-backend.sh` 只跑 migration，alice/bob 的
   `identity_bindings` 依赖历史手工库，bob 登录 401。改为启动时幂等播种两个账号的绑定。

## 8. 验证结果

| 验证项 | 结果 |
|---|---:|
| 后端全量测试（含真实 PostgreSQL + Redis） | 345 passed, 7 skipped* |
| 后端 API 合约（test/web_api，含 SSE Gateway） | 34 passed |
| 前端单测（vitest） | 45 passed |
| Playwright E2E | 11 passed |
| Ruff | 通过 |
| mypy | 通过 |
| ESLint / Prettier | 通过 |
| `tsc --noEmit` / vite build | 通过 |

\* 7 项 skipped 为需要 Temporal 服务的集成测试，属 Phase D 已有门禁范围，本地未起 Temporal。

后端全量结果：

```text
345 passed, 7 skipped, 1 warning in 147.29s (0:02:27)
```

E2E 结果：

```text
11 passed (1.6m)
```

唯一 warning 来自 FastAPI/Starlette TestClient 对当前 `httpx` 兼容层的弃用提示，不影响运行
时与合约结果。

## 9. 已知边界

- 前端生产构建当前单 chunk 约 607 kB（gzip 183 kB），来自 assistant-ui/radix/react-markdown；
  已出现 chunk 体积警告，后续可用动态 import 拆分，不影响本期交付。
- E2E 共享同一 PostgreSQL 库，对话在用例间累积（断言已用 `.first()` 规避严格模式）；如需要
  可加用例级清库，但当前不依赖用例顺序。
- 真实 Agent 执行、QQ 长期记忆验收不属于 Phase E 范围，仍由后续阶段接管。

## 10. Gate 建议

| 门禁范围 | 建议 |
|---|---|
| E-01～E-07 实现与测试 | 通过 |
| 长对话主流程 / 刷新 / 多标签页 / 进度事件契约 | 通过 |
| `make e2e` 全绿 | 通过 |
| 生产环境不启用 Fake Executor | 通过 |
| 整体 Phase E Gate | 本地通过，等待远端 CI |
