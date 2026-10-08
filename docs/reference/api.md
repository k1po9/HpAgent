# HTTP API 参考

Compose 中 FastAPI 监听 `127.0.0.1:8080`。`GET /health/live`、`GET /health/ready` 无需认证。Route 列表以 [`src/web_api/app.py`](../../src/web_api/app.py) 为准，测试位于 `test/web_api/`；下表只列主要操作。

认证入口为 `POST /auth/register`、`POST /auth/login`、`POST /api/v1/auth/logout`。注册包含 username / password，可选 invite_code；省略邀请使用默认自助 entitlement。Browser mutation 要求 Session Cookie、允许的 Public Origin、CSRF 与相应幂等键。Upload Content 使用 `application/octet-stream`，普通请求使用 JSON，event stream 使用 `text/event-stream`。

## Work 命令与查询

| Method / Path | 契约 |
| --- | --- |
| `GET /api/v1/works` | Account 内列表，`before` 分页。 |
| `POST /api/v1/works` | title、结构化 requirement、可选 conversation_id / source_message_id；201，返回 Location。 |
| `GET /api/v1/works/{work_id}` | Work、current requirement、执行/预算/成果等投影，返回 ETag。 |
| `POST /api/v1/works/{work_id}/revisions` | requirement、change_reason；创建不可变新 revision。 |
| `POST /api/v1/works/{work_id}/pause`、`resume`、`stop`、`advance` | 控制或推进，JSON `{}`；advance 可返回 not_due / waiting_capacity。 |
| `PUT /api/v1/works/{work_id}/conversations/{conversation_id}` | 关联交互来源，不导入历史或授予资料权限。 |
| `GET /api/v1/works/{work_id}/runs`、`events` | 执行与 Work event 查询；events 支持数值 `after`。 |
| `GET /api/v1/works/{work_id}/events/stream` | `work.event` SSE，独立 event_seq，支持 Last-Event-ID / after 与恢复查询。 |
| `GET/POST /api/v1/works/{work_id}/resources` | 当前 grant / 新 grant。 |
| `DELETE /api/v1/works/{work_id}/resources/{grant_id}` | 撤权并阻断受影响执行。 |
| `GET/POST /api/v1/works/{work_id}/inputs` | 显式文件输入；POST 为 file_id、purpose、可选 source_message_id。 |
| `DELETE /api/v1/works/{work_id}/inputs/{ref_id}` | 撤销输入引用。 |
| `GET/POST /api/v1/works/{work_id}/artifacts` | 精确成果列表 / 绑定 artifact_version_id 与 input 或 evidence role。 |
| `POST /api/v1/works/{work_id}/accept-result` | requirement_revision 与精确 artifact_version_id 的用户验收。 |
| `POST /api/v1/works/{work_id}/budget` | budget_version、limits；显式增额，revision / retry 不清零用量。 |
| `GET/PUT /api/v1/works/{work_id}/notification-targets` | 查询 / 从已验证来源选取目标及内容范围。 |
| `DELETE /api/v1/works/{work_id}/notification-targets/{target_id}` | 禁用目标。 |
| `POST /api/v1/works/{work_id}/deliveries/{delivery_id}/resolve` | accepted、not_sent 或 retry_accepting_duplicate_risk，显式解决未知投递。 |
| `GET /api/v1/notifications` | 已进入账户私有 Web inbox 的通知，before 分页。 |

Work 创建和写命令使用 `Idempotency-Key`；已存在 Work 的写命令还要求 `If-Match: "work-{work_id}-v{row_version}"`，缺少为 428，格式/版本不匹配为 409。预算命令同时检查 budget_version。修订只发送 requirement 可编辑字段，不能直接回传包含审计字段的 GET 对象。冲突时刷新核对版本；响应不确定时对同一意图重用键。输入 Schema 见 [`models.py`](../../src/web_api/models.py) 和 [`Requirement`](../../src/work_domain/models.py)。

Requirement 核心字段为 objective、capability_key、spec；另有 constraints、acceptance_criteria、completion_mode、timing、resource_requests、deliverable_policy。能力是 reminder / research_report / generic_work / artifact_build；注册 executor 决策由服务端完成。daily 要求 ongoing；once 带 offset。旧 `/api/v1/tasks` 及 Task schedule / resources 入口已退休，没有兼容双写层。

## Conversation、Run 与模型诊断

Conversation 支持创建、列表、读取、改名、消息列表及发送；消息请求包含 content、agent_strategy、file_ids。同一 Conversation 仅一个 active chat Run，不排斥后台 Work。

- `GET /api/v1/runs/{run_id}`：discriminated snapshot。chat variant 有 assistant_message；work variant 无该字段，Run 包含 work_id / requirement_revision 和执行策略。
- `GET /api/v1/runs/{run_id}/trace`、`events`：Trace / SSE。
- `POST /api/v1/runs/{run_id}/cancel`、`retry`：Run 控制；取消 Work Run 不等于停止 Work。retry 依据 failure.retryable 与副作用安全条件拒绝或创建新 Run。
- `GET /api/v1/runs/{run_id}/research/evidence`、`report`：Research Work 的证据 / 报告。
- `GET /api/v1/runs/{run_id}/model-inputs`、`GET /api/v1/model-inputs/{snapshot_id}`：账号内模型输入快照。none 只允许列表最小元数据，单快照为 403；summary 为摘要，full_safe 额外返回已保存 provider_request_body。

Run SSE 在订阅/缓冲后读取权威快照，snapshot 为第一帧；握手期间的终态不会因先读取快照而丢失，终态快照不再发送缓冲 delta。恢复仍遵循 stream/seq 与权威查询，不重放历史 started。Work feed 与 Chat feed 独立，Work 终态不发布 Chat 消息。

full_safe 不在查询时再作 Secret 脱敏，不应把凭据放入输入。快照存在不证明分发；诊断须结合 dispatch、失败分类与 HTTP 状态。模型 code / retryability 见[可靠性](../architecture/reliability.md)。

## 长期文件 Workspace

- `POST /api/v1/workspace/uploads` 创建账户上传；`POST /api/v1/conversations/{conversation_id}/uploads` 创建对话上传；使用响应中的 content URL 完成内容上传。
- `GET /api/v1/workspace`；`POST /api/v1/workspace/directories` / `files`；`PATCH/DELETE /api/v1/workspace/nodes/{node_id}`：稳定 ID 管理目录 / entry。新建 parent_id 是选中目录，name 独立输入。
- `GET /api/v1/workspace/search`：name、content_type、purpose、work_id、source_run_id、from_date、to_date、summary，after / limit 分页。
- `GET /api/v1/workspace/space`、`files/{file_id}/retention`、`nodes/{node_id}/trace`：唯一文件空间、保留与来源解释。
- `GET /api/v1/workspace/nodes/{node_id}/versions`；`POST .../upgrade` / `.../versions`：修订、原位升级及 expected_revision / hash CAS。
- Conversation 的 `/api/v1/conversations/{conversation_id}/resources` GET / POST / DELETE grant 管理发现/读取/写入权限。Work 使用上表的 resources 入口。
- `GET /api/v1/runs/{run_id}/workspace/search`、`resources`、`published-files`：只在该 Run 的冻结范围内搜索或查询。

已知目录/名称约束返回 409 `workspace_conflict`，`error.details.reason` 如 name_exists / invalid_name / invalid_parent，附 request_id；未知数据库错误不伪装成冲突。上传供对话使用是保存后另行 grant，所有者能看到文件不等于 Agent 有权限。移除 entry 不立即释放对象字节。详见[Workspace](../architecture/workspace-v4.1.md)。

## Artifact 与身份

`POST/GET /api/v1/messages/{message_id}/artifacts`、`GET /api/v1/artifacts/{artifact_id}`、`GET/POST .../versions`、`GET /api/v1/artifact-versions/{artifact_version_id}` 管理消息来源成果及版本。异步构建创建 Work / Run；生产版本与采用版本分别追溯。

HTML 手工修改以最近成功版本为 parent，不自动替换 Work 的原交付/验收证据。下载 `.html`，保存到空间为 `.html.txt` 文本源码副本，保存不自动授权。已领取版本的生产 Run failed/cancelled 时由 Worker 终态事务收敛 running 版本；取消使用 `failed` 与 `artifact_cancelled`，不新增状态。059 迁移使 API 对 artifact_versions 仅 SELECT/INSERT，Worker 保留生成发布所需权限。

Account 入口为 `GET /api/v1/me`；QQ challenge 位于 `/api/v1/identity-bindings/qq/challenges`。账户、渠道和所有权检查不会因页面切换被绕过。操作流程见[功能指南](../operations/web-workbench.md)。
