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

`src/web_api/app.py` 的 Route 声明是端点列表的权威来源；契约与行为测试位于 `test/web_api/` 和 Web Test。
