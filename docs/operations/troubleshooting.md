# 故障排查

## API 或 Worker 未 Ready

```bash
docker compose --profile web ps
./scripts/operations/logs.sh hpagent-migrate hpagent-api hpagent app-postgres --tail 200 --no-follow
curl --fail http://127.0.0.1:8080/health/ready
docker compose exec -T hpagent python -m orchestration.worker_health
```

确认迁移成功、角色凭据一致、完整迁移目录和 checksum 正确。API ready 不代表 Worker ready；主 Worker 还校验 schema、Temporal、Lifecycle / Agent Worker 和 Dispatcher。MCP 初始连接期间可能仍为 starting。

若出现 `Failed validating workflow` / `RestrictedWorkflowAccessError`，查看 Workflow 的间接 import 链是否加载 Activity 网络依赖。应保持确定性并调整依赖边界，不能为恢复启动直接关闭全部沙箱限制。当前 8 Workflow 沙箱验证见 `test/test_durable_agent_contract.py`。[部署切换](deployment.md)还说明了旧 schema 的空数据门禁。

## Run 一直 Queued / Work 不推进

先查 hpagent / temporal / app-postgres，确认两个执行队列 Poller 和 Dispatcher。对于 Work，继续看 status、continuation、当前 revision / epoch、active coordinator Run、pending wakeup、schedule desired / applied version、预算和容量。Work 使用 PG due loop，没有 Work Temporal Schedule 可手动触发。

ready 后应有持久 advance wakeup；not_due、waiting_capacity、awaiting_input / delivery 或 blocked 各有不同原因。Run cancel 保留 Work 委托；用户取消后需要明确控制/advance，不靠反复发消息。pausing / stopping 有未知效果时不能手工清 pointer 或标 completed。

## 模型在工具完成后失败

在执行诊断选择 Run，对照 failure code、HTTP 状态、Model Input Snapshot 和日志 scope。工具成功只说明工具 operation 完成；下一轮模型仍可能拒绝 transcript 或响应失败。

| failure code | 当前重试 | 排查 |
| --- | --- | --- |
| `model_request_invalid` | 否 | 本地工具调用/result ID、参数 JSON、角色和配对；在快照/预留前拒绝。 |
| `model_request_rejected` | 否 | HTTP 4xx 的冻结请求格式、模型名称或限制。 |
| `model_response_invalid` | 否 | 返回对象、choices / message 或 Anthropic content、JSON 解析。 |
| `model_access_denied` | 否 | Provider 401/403，凭据/服务权限；与 Account entitlement 拒绝分别查。 |
| `model_rate_limited` | 否 | Provider 402/429，额度/限流；当前契约不自动重试。 |
| `model_connection_failed` | 是 | DNS、容器 TCP/TLS、Proxy 和 endpoint；可能尚无 HTTP 响应。 |
| `model_read_timeout` | 是 | 服务处理时间、read 预算，先排除错误请求。 |
| `model_http_error` | 是 | 当前分类的 HTTP 服务错误，结合受限 provider code / request ID。 |
| `model_unavailable` | 是 | 未识别错误链与分发日志。 |

serializer v2 的 OpenAI 请求需要 type / 嵌套 function / JSON 字符串 arguments；Anthropic 使用独立 system 与 tool_use / tool_result。不覆盖历史快照来修复旧证据。只有新调用才能验证新格式。

Artifact 默认 read 90 秒、connect 最多 5 秒；常规聊天 read 沿用 endpoint timeout。当前端点对宿主机可达而容器失败时，从实际 Worker 网络环境检查。2026-10-04 真实 MiniMax-M3 复验在连接阶段超时，未获得 HTTP 响应；这不能替代或改写此前三次 HTTP 500 证据，详见[实施索引](../implementation/README.md)。实时检查入口 `python scripts/check/models.py --help`；真实请求会调用外部服务。

## 新建目录 409 / 长期资料不可见

目录新建 parent_id 应指向所选目录，name 使用独立新值；已知约束有 `error.details.reason` 与 request_id。授权范围、所有者浏览、Run 固定候选是三项不同条件。

长期文件只保存不会授权。给 Conversation / Work 授予 list_metadata 和 read_content，再创建下一 Run。上传后授权失败使用补授权，不重复上传；当前执行中新增资源不会自动进冻结集合。模型初始只见最多 20 条元数据，更多应分页 list / select / read。[操作指南](web-workbench.md)有完整路径。

## 提醒或 QQ 没有送达

先区分 Run 终态、Work continuation、Notification、Delivery 和验收证据。reminder Run succeeded 可仅表示通知入队；Web accepted 是 inbox 提交，QQ accepted 是 Adapter 发送回执，均非用户已读。

查统一 notifications / delivery_targets / deliveries，核对当前 verified binding、target version、内容范围、lease、part progress 和 receipt。旧 qq_deliveries / reminder_intents 已移除。sending 超期为 uncertain，不自动重发；Work 页面或 resolve API 在核对渠道后作显式决定。群目标只允许摘要，投递重试不再执行生产 Run。

## Memory / Research / Document

Memory：检查 Hindsight health、Hindsight LLM、Embedding / Rerank 和日志；降级不改变 PG 业务权威。Research：查 SearXNG、SEARXNG_URL、Proxy、证据进度与 required Workspace save；报告生成不等于保存完成。

Document：确认 hpagent-document-worker / gotenberg / temporal、文件卷与 hpagent-document Poller。Research Worker 被杀后恢复还须考虑 stage lease 3600 秒与 publish/save Activity 120 秒窗口的已知不匹配；短租约受控验收未证明生产默认快速恢复。不要清租约、未知费用或回执来伪造恢复成功。详细边界见[可靠性](../architecture/reliability.md)。
