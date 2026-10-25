# HTTP API 参考

Compose 中 FastAPI 监听 `127.0.0.1:8080`。`GET /health/live`、`GET /health/ready` 无需认证。Route 定义按业务位于 [`src/web_api/routes/`](../../src/web_api/routes/)，由 [app.py](../../src/web_api/app.py) 装配；完整路径与 DTO 以应用 OpenAPI 为准，测试位于 `test/web_api/`；下表只列主要操作。

认证入口为 `POST /auth/register`、`POST /auth/login`、`POST /api/v1/auth/logout`。注册包含 username / password，可选 invite_code；省略邀请使用默认自助 entitlement。Browser mutation 要求 Session Cookie、允许的 Public Origin、CSRF 与相应幂等键。Upload Content 使用 `application/octet-stream`，普通请求使用 JSON，event stream 使用 `text/event-stream`。

## Chat 与发布检查消费者

| 请求/事实 | 当前返回与限制 |
| --- | --- |
| `POST /auth/login` | JSON username/password/return_to；成功303并设置Session Cookie，不是JSON成功体 |
| `GET /api/v1/me` | account与csrf_token；后续浏览器写请求同时满足Cookie、Origin与CSRF检查 |
| `POST /api/v1/conversations` / `.../{conversation_id}/messages` | 分别201/202；发送消息返回run，使用稳定幂等键，不因未知响应擅自重发 |
| `GET /api/v1/conversations/{conversation_id}/messages` | 分页条目字段为`items`；助手消息`status=completed`与Run的`succeeded`含义不同 |
| `GET /api/v1/runs/{run_id}` | Run成功终态为`succeeded`；检查成果还须核实消息的`produced_by_run_id`及非空内容 |

生产网关只代理`/api`和`/auth`等已配置路径；API的`/health/ready`从API入口或容器内验证，不能把网关SPA fallback的200作为API就绪。

## Work 命令与查询

| Method / Path | 契约 |
| --- | --- |
| `GET /api/v1/works` | Account 内列表，`before` 分页。 |
| `POST /api/v1/works` | title、结构化 requirement、可选 conversation_id / source_message_id；201，返回 Location。 |
| `GET /api/v1/works/{work_id}` | Work、current requirement、执行/预算/成果等投影，返回 ETag；sources 数组包含准确 conversation_id / source_message_id，无消息锚点时 source_message_id 为 null。 |
| `POST /api/v1/works/{work_id}/revisions` | requirement、change_reason；创建不可变新 revision。 |
| `POST /api/v1/works/{work_id}/pause`、`resume`、`stop`、`advance` | 控制或推进，JSON `{}`；advance 可返回 not_due / waiting_capacity；保护或预算阻塞返回 409，不能以新 Run 绕过。 |
| `PUT /api/v1/works/{work_id}/conversations/{conversation_id}` | 关联交互来源，不导入历史或授予资料权限。 |
| `GET /api/v1/works/{work_id}/runs`、`events` | 执行与 Work event 查询；events 支持数值 `after`。 |
| `GET /api/v1/runs/{run_id}/capability-results` | 账户所有者查询当前 Run 最近 100 个不可变能力结果；items 包含 capability_key、invocation_id、payload、created_at，truncated 表示还有更早结果。不是执行侧 ResultRef 授权。 |
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
| `GET /api/v1/notifications` | 已进入账户私有 Web inbox 的通知，before 分页；完成通知 payload.results 携带与 Work 完成回执一致的准确成果引用。 |

Work 创建和写命令使用 `Idempotency-Key`；已存在 Work 的写命令还要求 `If-Match: "work-{work_id}-v{row_version}"`，缺少为 428，格式/版本不匹配为 409。预算命令同时检查 budget_version。修订只发送 requirement 可编辑字段，不能直接回传包含审计字段的 GET 对象。冲突时刷新核对版本；响应不确定时对同一意图重用键。输入 Schema 见 [`models.py`](../../src/web_api/models.py) 和 [`Requirement`](../../src/work_domain/models.py)。

Work 快照的 `results` 数组从当前完成回执的 typed evidence 解析，包含准确 ref_id/type/file_id/sha256、生产 run_id/requirement_revision、name/content_type/size_bytes 和认证 content_url。未完成或没有对应 typed 引用时为空，不把全部候选输出当成已接受成果。`GET /api/v1/files/{file_id}/content` 仍按当前登录账户鉴权，通知里的 URL 不授予其他账户访问；它也不授予 Agent 跨 Run 使用权限。任务的已发布文件查询可用于下载普通 Markdown，不要求先建立 Workspace 长期入口。

Work 快照另含可空 `progress`：policy_version、failure_limit、no_progress_limit、consecutive_failures、no_progress_count、blocked_reason（no_progress / repeated_failure / null）；可空 `daily_baseline` 为 run_id、previous_run_id、changed。它们来自当前 revision 的权威事实；daily Run admission 另冻结 input_snapshot.daily_baseline_run_id。无变化轮次正常成功，SSE 仍可观察事件，但不产生逐轮通知。预算解除要求 limits 至少一项实际增加；同值命令不会解除 budget_exhausted。具体恢复规则见 [Work](../architecture/work.md#有界推进与静默通知)。

Requirement 核心字段为 objective、capability_key、spec；另有 constraints、acceptance_criteria、completion_mode、timing、resource_requests、deliverable_policy。能力是 reminder / research_report / generic_work / artifact_build；Generic Work 的验收 evidence_types 为 output_exists / check_passed / workspace_saved，并使用服务器求值的准确成果证据；input 命令可显式选取已有不可变能力输出。注册 executor 决策由服务端完成。daily 要求 ongoing；once 带 offset。旧 `/api/v1/tasks` 及 Task schedule / resources 入口已退休，没有兼容双写层。

## Conversation、Run 与模型诊断

Conversation 支持创建、列表、读取、改名、消息列表及发送；消息请求包含 content、agent_strategy、file_ids。同一 Conversation 仅一个 active chat Run，不排斥后台 Work。

- `GET /api/v1/runs/{run_id}`：discriminated snapshot。chat variant 有 assistant_message；work variant 无该字段，Run 包含 work_id / requirement_revision 和执行策略。
- `GET /api/v1/runs/{run_id}/trace`、`events`：Trace / SSE。
- `POST /api/v1/runs/{run_id}/cancel`、`retry`：Run 控制；取消 Work Run 不等于停止 Work。retry 依据 failure.retryable 与副作用安全条件拒绝或创建新 Run。
- `GET /api/v1/runs/{run_id}/research/evidence`、`report`：Research 证据 / 报告；可带 `invocation_id` 精确读取同 Run 的某次研究。省略时仅支持固定 research_report 入口及旧报告；账号不匹配或 invocation 不属于该 Run 返回 404。报告包含准确 `markdown_file_id`；并返回 `capability_result`（旧报告为 null），其中有 outcome、gaps、检查版本和证据引用，无需 HTML 阅读视图成功。
- `GET /api/v1/runs/{run_id}/model-inputs`、`GET /api/v1/model-inputs/{snapshot_id}`：账号内模型输入快照。none 只允许列表最小元数据，单快照为 403；summary 为摘要，full_safe 额外返回已保存 provider_request_body。

Run SSE 在订阅/缓冲后读取权威快照，snapshot 为第一帧；握手期间的终态不会因先读取快照而丢失，终态快照不再发送缓冲 delta。恢复仍遵循 stream/seq 与权威查询，不重放历史 started。Work feed 与 Chat feed 独立，Work 终态不发布 Chat 消息。

full_safe 不在查询时再作 Secret 脱敏，不应把凭据放入输入。快照存在不证明分发；诊断须结合 dispatch、失败分类与 HTTP 状态。模型 code / retryability 见[可靠性](../architecture/execution.md)。

## 长期文件 Workspace

- `POST /api/v1/workspace/uploads` 创建账户上传；`POST /api/v1/conversations/{conversation_id}/uploads` 创建对话上传；使用响应中的 content URL 完成内容上传。
- `GET /api/v1/workspace`；`POST /api/v1/workspace/directories` / `files`；`PATCH/DELETE /api/v1/workspace/nodes/{node_id}`：稳定 ID 管理目录 / entry。新建 parent_id 是选中目录，name 独立输入。
- `GET /api/v1/workspace/search`：name、content_type、purpose、work_id、source_run_id、from_date、to_date、summary，after / limit 分页。
- `GET /api/v1/workspace/space`、`files/{file_id}/retention`、`nodes/{node_id}/trace`：唯一文件空间、保留与来源解释。
- `GET /api/v1/workspace/nodes/{node_id}/versions`；`POST .../upgrade` / `.../versions`：修订、原位升级及 expected_revision / hash CAS。
- Conversation 的 `/api/v1/conversations/{conversation_id}/resources` GET / POST / DELETE grant 管理发现/读取/写入权限。Work 使用上表的 resources 入口。
- `GET /api/v1/runs/{run_id}/workspace/search`、`resources`、`published-files`：只在该 Run 的冻结范围内搜索或查询。

已知目录/名称约束返回 409 `workspace_conflict`，`error.details.reason` 如 name_exists / invalid_name / invalid_parent，附 request_id；未知数据库错误不伪装成冲突。上传供对话使用是保存后另行 grant，所有者能看到文件不等于 Agent 有权限。移除 entry 不立即释放对象字节。详见[Workspace](../architecture/workspace.md)。

## Artifact 与身份

`POST/GET /api/v1/messages/{message_id}/artifacts`、`GET /api/v1/artifacts/{artifact_id}`、`GET/POST .../versions`、`GET /api/v1/artifact-versions/{artifact_version_id}` 管理消息来源成果及版本。异步构建创建有限 Chat Run，不强制创建 Work；生产版本与采用版本分别追溯。

版本查询返回 `result_ref_id`、`producing_invocation_id`、`result_outcome`/`result_summary` 和 `checks`（verifier_key/version、准确 subject/evidence ref、verdict）；queued 或旧非能力版本可为空，空值不代表检查通过。

HTML 手工修改以最近成功版本为 parent，不自动替换 Work 的原交付/验收证据。下载 `.html`，保存到空间为 `.html.txt` 文本源码副本，保存不自动授权。已领取版本的生产 Run failed/cancelled 时由 Worker 终态事务收敛 running 版本；取消使用 `failed` 与 `artifact_cancelled`，queued 版本也会关闭，不新增状态。API 对 artifact_versions 保持 SELECT/INSERT；067 仅开放核对终态 Run 的窄取消函数，Worker 保留生成发布所需权限。

Account 入口为 `GET /api/v1/me`；QQ challenge 位于 `/api/v1/identity-bindings/qq/challenges`。账户、渠道和所有权检查不会因页面切换被绕过。操作流程见[功能指南](../operations/web-workbench.md)。

## 长期记忆治理

四个入口复用当前账户认证，写命令要求可信 Origin、CSRF 和规范 UUID `Idempotency-Key`：

| 方法/路径 | 内容 |
| --- | --- |
| `GET /api/v1/memory/sources?limit=50&offset=0` | 当前账户、仍有授权的来源与 generation、scope、状态；上限 100，`next_offset` 接续 |
| `GET /api/v1/memory/sources/{source_id}` | 来源、动作摘要、已登记晋升副本及事实（含失效事实）；服务不可用时明确 `facts_available=false`，不把空列表当远端事实 |
| `POST /api/v1/memory/sources/{source_id}/actions` | `kind=correct/invalidate/restore/delete`、`generation`、`reason`；前三项要求 `memory_id`，correct 另要求 `text`；不接受 bank/tags/scope |
| `GET /api/v1/memory/actions/{action_id}` | 权威动作状态、失效范围、无正文验证摘要及删除来源集合 |

来源授权由持久化 Run/触发消息的账户、origin 与所属 Conversation 反查；Worker 维护前再次检查。
目标 fact 必须属于该来源及 scope，Observation 不可直接编辑。跨账户资源返回 404；
目标越界拒绝维护，generation 不匹配为 412；同幂等键不同载荷为 409。
API 和动作摘要不返回冻结的纠正请求，幂等回执只保存 action/source 身份；短期回执清理后
仍通过长期 action_key/request_hash 拒绝载荷漂移。

命令返回 202、`action_id` 和状态查询 Location，不能解释为治理完成。
动作 `verified` 才代表已完成相应验证；`failed/uncertain` 仍保持来源阻断。
删除回执列出 `source_ids` 和 `descendant_actions`，完成摘要保留实际来源集合。
`deletion_scope=specified_source_only`、`global_semantic_deletion=false` 始终明确范围；
成功文案应为“指定来源的长期记忆已删除”，不得宣称某项信息已从全部记忆消失。
没有完整管理 UI，也没有单 fact 硬删除或来源删除恢复接口。资源撤权隔离的来源不可通过纠错/恢复重新授权；重新授予文件权限也不清除旧 Run 的记忆禁用标记。无法证明旧发送请求已停止时保持 uncertain，不提供自动消除此不确定性的决议接口。

API 的事实读取/目标预检使用 `HINDSIGHT_URL`、可选 `HINDSIGHT_API_KEY`；Worker 使用同一
服务与认证配置。外部服务失联不影响本地 tombstone 提交；最终状态由 Worker 对账。
