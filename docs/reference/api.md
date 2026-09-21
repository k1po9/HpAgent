# HTTP API 参考

Compose 中 FastAPI 监听 `127.0.0.1:8080`。以下健康端点无需认证：

- `GET /health/live`
- `GET /health/ready`

`/api/v1` 下的主要认证资源：

- Account Context：`GET /me`；
- QQ Binding Challenge：创建与查询；
- Conversation：创建、列表、读取、重命名、查询消息和发送消息；
- Run：读取、Trace、Event Stream、Cancel 和 Retry；
- File：创建 Upload、上传内容、查询、下载、删除、Lineage、Persistent Destination 和 Action Approval；
- Research Task：创建、运行、定时、查询 Run/Evidence/Report；
- Artifact：从 Message 创建、列表、查询，以及创建/读取 Version。

认证端点为 `/auth/register`、`/auth/login` 和 `/api/v1/auth/logout`。Browser Client 必须遵守配置的 Public Origin、Session Cookie 和 CSRF Protocol。除 Upload Content 使用 `application/octet-stream` 外，Mutation Endpoint 使用 JSON；Run Event 使用 `text/event-stream`。

`POST /auth/register` 的 JSON 包含用户名、密码，可选 `invite_code`。省略邀请码时使用默认自助注册 entitlement；提供邀请码时使用其 profile 创建账号 entitlement。

模型输入查询需要认证，并限制在当前账号：

- `GET /api/v1/runs/{run_id}/model-inputs`：列出 Run 的 Model Input Snapshot。`none` 只返回最小元数据；`summary` 和 `full_safe` 返回摘要。
- `GET /api/v1/model-inputs/{snapshot_id}`：读取单个 Snapshot。`none` 返回 403 `model_input_unavailable`；`summary` 返回摘要；`full_safe` 还包含已保存的 `provider_request_body`。

摘要包括模型、Provider、端点、时间、消息数、工具数和 dispatch 状态等，不包含 Prompt 正文。`full_safe` 返回存储的模型请求体，不执行额外的 Secret 脱敏；不应将凭据或 Secret 放入模型输入。

`src/web_api/app.py` 的 Route 声明是端点列表的权威来源；契约与行为测试位于 `test/web_api/` 和 Web Test。
