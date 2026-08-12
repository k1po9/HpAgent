# HpAgent Observability 前置收口与日志规范化整改指导

> 适用分支：`feat/hpagent-web`
> 前置状态：架构收口已完成，QQ / Web 已共享 `AgentExecutionFacade + DefaultBrainActionLoop`。
> 本轮目标：**不再进行大范围架构重构**，只修复 Observability 建设前的剩余小缺口，并统一结构化日志语义，为后续日志可视化、筛选、指标统计和 Trace 建设建立稳定基础。

---

## 0. 本轮原则

本轮属于 **Observability V0 / Pre-Observability Cleanup**。

不要借此机会再次进行大规模目录迁移。

必须遵守：

1. 不改变 Agent 行为。
2. 不改变 Prompt。
3. 不改变模型选择策略。
4. 不改变 Temporal Workflow 业务语义。
5. 不改变 Web Run 状态机。
6. 不改变 Hindsight recall / retain 语义。
7. 不改变 Sandbox 行为。
8. 不重新设计 PostgreSQL schema。
9. 不引入 Grafana / Loki / Elasticsearch / Jaeger。
10. 不引入完整 OpenTelemetry Collector。
11. 先把**事件、字段、错误码、生命周期**统一，再建设 Dashboard / Metrics / Trace。

最终目标不是“日志越多越好”，而是：

```text
一条用户请求
可以通过一个稳定 ID
关联到整个执行生命周期
```

---

## 1. 当前已经具备的基础

当前项目已经存在统一结构化日志入口：

```python
common.logging.log_event(...)
```

并写入：

```text
.data/logs/hpagent.jsonl
.data/logs/hpagent-error.log
```

现有主要事件已经包括：

```text
run_created
run_completed
run_failed
run_cancelled

context_assembly_started
context_assembly_completed
context_assembly_failed

agent_execution_started
agent_execution_completed
agent_execution_failed

model_call_started
model_call_completed
model_call_failed

tool_execution_started
tool_execution_completed
tool_execution_failed

memory_recall_started
memory_recall_completed
memory_recall_degraded

memory_retain_started
memory_retain_completed
memory_retain_failed

outbox_event_*
temporal_workflow_*
```

本轮不要推翻这些命名。

目标是补齐：

```text
字段一致性
生命周期完整性
错误码一致性
QQ/Web 一致性
最终模型调用日志
```

---

## 2. P0：修复 shared identity validation 的依赖位置

当前存在：

```text
bootstrap/qq.py
    ↓
orchestration/web_workers.py
    ↓
validate_unified_account_backend()
```

这是不合理的依赖方向。

`validate_unified_account_backend()` 本质是 QQ / Web 共用的 PostgreSQL Identity 启动校验，不应属于 `web_workers.py`。

### 修改目标

建议移动到：

```text
src/account/validation.py
```

例如：

```python
def validate_unified_account_backend(worker_database_url: str | None) -> None:
    if not worker_database_url:
        raise RuntimeError(
            "WORKER_DATABASE_URL is required for the unified account backend"
        )
```

最终：

```text
bootstrap/qq.py ─────┐
                     ├─> account.validation
web_workers.py ──────┘
```

### 验收

```bash
rg "validate_unified_account_backend" src test
```

必须满足：

- `bootstrap/qq.py` 不再 import `orchestration.web_workers`
- QQ / Web 使用同一个 shared validator
- 行为不变
- 相关测试通过

---

## 3. 建立统一日志 Schema

所有项目自己的关键生命周期日志，应尽量使用：

```python
log_event(...)
```

不要继续新增只有自然语言、无法稳定查询的关键日志，例如：

```python
logger.info("Agent finished for %s", run_id)
```

普通开发 Debug/Info 可以保留，但以下类型必须结构化：

```text
lifecycle
failure
retry
degradation
recovery
```

---

## 4. Canonical Log Fields

### 4.1 基础字段

由 formatter / `log_event()` 统一产生：

```text
ts
level
logger
msg
event
component
```

### 4.2 Correlation 字段

统一允许：

```text
request_id
run_id
execution_id
workflow_id
conversation_id
session_id
account_id
surface
```

其中：

```text
surface = web | qq
```

如果未来增加渠道：

```text
surface = discord | telegram | ...
```

不要再用 `source/platform/channel` 表达同一个“入口面”概念。

`channel_type` 可以继续保留，用于 QQ 内部更细分类，例如：

```text
napcat
official_qq
```

但它不能替代 `surface`。

---

## 5. Correlation 字段语义

### request_id

表示一次 HTTP Request。

只保证 API 层可用。

不要为了 Agent 强行生成假的 `request_id`。

### run_id

只属于 Web Run。

```text
Web:
run_id = PostgreSQL runs.run_id

QQ:
不产生 run_id
```

禁止：

```text
QQ run_id = execution_id
```

### execution_id

表示一次 Agent Execution。

Web：

```text
execution_id == run_id
```

当前行为可保持。

QQ：

```text
qq-turn-{uuid}
```

必须稳定。

### workflow_id

表示 Temporal Workflow 的业务 ID。

有则记录，没有则不写，不伪造。

### conversation_id

Web 应尽量全链路携带。

QQ 当前没有统一 Conversation 实体，不伪造。

### session_id / account_id

Web / QQ Agent 运行时都应尽量携带。

---

## 6. 禁止伪造空字段

推荐：

```python
conversation_id=None
```

然后由 `log_event()` 过滤。

不要写：

```text
conversation_id=""
conversation_id="unknown"
conversation_id="?"
run_id="N/A"
```

否则后续 Dashboard 会产生无意义维度。

---

## 7. P0：Web Agent Host correlation 补齐

当前 Web Agent Host 的事件字段比 QQ 少。

本轮需要统一。

### agent_execution_started

推荐：

```json
{
  "event": "agent_execution_started",
  "component": "agent",
  "run_id": "...",
  "execution_id": "...",
  "conversation_id": "...",
  "session_id": "...",
  "account_id": "...",
  "surface": "web",
  "status": "started"
}
```

### agent_execution_completed

推荐：

```json
{
  "event": "agent_execution_completed",
  "component": "agent",
  "run_id": "...",
  "execution_id": "...",
  "conversation_id": "...",
  "session_id": "...",
  "account_id": "...",
  "surface": "web",
  "status": "success",
  "elapsed_ms": 1234
}
```

### agent_execution_failed

必须补 `error_code`：

```json
{
  "event": "agent_execution_failed",
  "component": "agent",
  "run_id": "...",
  "execution_id": "...",
  "conversation_id": "...",
  "session_id": "...",
  "account_id": "...",
  "surface": "web",
  "status": "failed",
  "elapsed_ms": 1234,
  "error_code": "tool_timeout"
}
```

---

## 8. Web Host 错误码统一

如果异常是：

```python
StableExecutionFailure
```

使用：

```python
exc.code
```

否则统一：

```text
internal_error
```

不要将 Python Exception 类型直接作为业务 `error_code`。

推荐：

```text
error_code = 稳定机器码
error.type = Python 异常类型
error.message = 实际错误
error.stack = 堆栈
```

Exception 信息交给 `logger.exception(...)` 和 JSON formatter。

---

## 9. P0：QQ Agent Host status 统一

QQ 已经具有较完整 correlation，但 status 建议统一。

不要混合：

```text
running
completed
success
done
ok
failed
error
```

生命周期统一使用：

```text
started
success
failed
cancelled
degraded
skipped
```

因此：

```text
agent_execution_started
status=started

agent_execution_completed
status=success

agent_execution_failed
status=failed
```

---

## 10. P0：补齐 DefaultBrainActionLoop 最终模型调用日志

这是当前明确的日志盲区。

普通 Tool Loop 已有：

```text
model_call_started
model_call_completed
model_call_failed
```

但 `max_tool_turns` 耗尽后执行：

```python
generate_final_decision(...)
```

时，也必须作为一次正式 Model Call 记录。

### 修改要求

增加：

```text
model_call_started
model_call_completed
model_call_failed
```

并建议：

```text
turn = max_tool_turns + 1
phase = forced_final
```

示例：

```json
{
  "event": "model_call_started",
  "component": "model",
  "execution_id": "...",
  "run_id": "...",
  "surface": "web",
  "turn": 21,
  "phase": "forced_final",
  "status": "started"
}
```

完成：

```json
{
  "event": "model_call_completed",
  "component": "model",
  "execution_id": "...",
  "turn": 21,
  "phase": "forced_final",
  "status": "success",
  "elapsed_ms": 842,
  "stop_reason": "..."
}
```

失败：

```json
{
  "event": "model_call_failed",
  "component": "model",
  "execution_id": "...",
  "turn": 21,
  "phase": "forced_final",
  "status": "failed",
  "elapsed_ms": 30001,
  "error_code": "model_timeout"
}
```

普通循环模型调用建议增加：

```text
phase=agent_turn
```

以后可以区分正常 Agent 决策和 forced final。

---

## 11. Model 日志字段规范

推荐：

```text
event
component=model
execution_id
run_id
conversation_id
session_id
account_id
surface
turn
phase
status
elapsed_ms
error_code
stop_reason
tool_count
```

可选：

```text
model
provider
```

但只有 BrainEngine 能可靠返回**实际使用的 endpoint**时才增加。

不要根据 config 第一项猜测实际模型。

---

## 12. Tool 日志规范

继续使用：

```text
tool_execution_started
tool_execution_completed
tool_execution_failed
```

统一字段：

```text
component=tool
execution_id
run_id
conversation_id
session_id
account_id
surface
turn
tool_call_id
tool
status
elapsed_ms
error_code
```

优先复用稳定错误码：

```text
tool_timeout
run_timeout
tool_failed
side_effect_audit_unavailable
cancelled
```

不要把 stderr 或工具异常文本直接作为 `error_code`。

---

## 13. P0：Memory Recall 统一 Web / QQ

Web 已有：

```text
memory_recall_started
memory_recall_completed
memory_recall_degraded
```

QQ 需要补齐同一 lifecycle。

推荐在 QQ 的 Context / Memory adapter 边界记录，而不是 Hindsight HTTP Client、SessionStore、Agent Loop 三层同时打同一事件。

推荐 QQ：

```text
QQLegacyContextProvider.recall_long_term()
```

附近记录。

统一字段：

```text
component=memory
operation=recall
execution_id
run_id
conversation_id
session_id
account_id
surface
status
elapsed_ms
result_count
error_code
```

### degraded 定义

如果 Hindsight 不可用，但系统允许继续执行：

```text
event=memory_recall_degraded
status=degraded
```

不是 `failed`。

只有 recall 失败会导致整个 execution 失败时才使用：

```text
memory_recall_failed
```

---

## 14. P0：Memory Retain 统一 Web / QQ

Web 已有：

```text
memory_retain_started
memory_retain_completed
memory_retain_failed
```

QQ retain 也补齐相同事件。

推荐在：

```text
TurnMemoryQQRetentionSink.retain()
```

包围 `retain_document()`。

示例：

```json
{
  "event": "memory_retain_started",
  "component": "memory",
  "execution_id": "...",
  "session_id": "...",
  "account_id": "...",
  "surface": "qq",
  "document_id": "qq-execution:...",
  "status": "started"
}
```

成功：

```json
{
  "event": "memory_retain_completed",
  "component": "memory",
  "execution_id": "...",
  "document_id": "...",
  "surface": "qq",
  "status": "success",
  "elapsed_ms": 123
}
```

失败：

```json
{
  "event": "memory_retain_failed",
  "component": "memory",
  "execution_id": "...",
  "document_id": "...",
  "surface": "qq",
  "status": "failed",
  "elapsed_ms": 123,
  "error_code": "memory_retain_failed"
}
```

### 注意

本轮只补日志。

不要顺手改变：

```text
retain failure 是否导致 QQ execution failure
```

保持当前真实语义，并在最终报告中说明。

---

## 15. Context Assembly 日志规范

Web 已有：

```text
context_assembly_started
context_assembly_completed
context_assembly_failed
```

统一字段：

```text
component=context
execution_id
run_id
conversation_id
session_id
account_id
surface
status
elapsed_ms
message_count
error_code
```

Web Loader 在完成 base load 后已经知道：

```text
account_id
conversation_id
session_id
```

`context_assembly_completed` 应尽量全部携带。

QQ 不建议为了日志重构整个 context 流程。

如果能够自然增加：

```text
context_assembly_started
context_assembly_completed
context_assembly_failed
```

可以增加。

如果需要较大改造，本轮允许 QQ 暂时只补 `memory_recall_*`。

---

## 16. API request_id 传播原则

不要把 HTTP `request_id` 当成整个异步 Agent 链路的唯一 ID。

Web：

```text
HTTP Request
 -> DB commit
 -> Outbox
 -> Temporal
```

HTTP 结束后真正的跨异步主键应该是：

```text
run_id
```

因此：

```text
request_id   = HTTP 排障
run_id       = Web 业务执行链路
execution_id = Agent 执行链路
```

当前 Web：

```text
run_id == execution_id
```

可以继续保持，但字段名字仍都保留，因为语义不同。

---

## 17. 主关联关系

### Web

```text
request_id
    ↓
run_id
    ↓
execution_id
    ↓
tool_call_id
```

### QQ

```text
workflow_id
    ↓
execution_id
    ↓
tool_call_id
```

同时：

```text
account_id
session_id
```

用于用户/会话维度筛选。

---

## 18. component 统一枚举

关键 lifecycle event 的 `component` 统一使用：

```text
api
run
outbox
workflow
context
agent
model
tool
memory
workspace
identity
```

Logger 名称可以继续：

```text
HpAgent.BrainActionLoop
HpAgent.WebExecutionHost
```

但 `component` 必须稳定。

---

## 19. Event 命名规范

统一：

```text
<entity>_<operation>_<state>
```

例如：

```text
agent_execution_started
agent_execution_completed
agent_execution_failed

model_call_started
model_call_completed
model_call_failed

tool_execution_started
tool_execution_completed
tool_execution_failed

memory_recall_started
memory_recall_completed
memory_recall_degraded
```

禁止新增：

```text
agent_error
something_failed
process_done
request_processing
execution_log
operation_completed
```

事件名应直接表达：

```text
谁
做什么
结果如何
```

---

## 20. status 统一

允许：

```text
started
success
failed
cancelled
degraded
skipped
```

领域本身的 Run 状态：

```text
queued
running
completed
failed
cancelled
```

可以继续作为 `run_status`。

但通用结构化生命周期 `status` 使用统一集合。

---

## 21. elapsed_ms 统一

所有耗时字段统一：

```text
elapsed_ms
```

推荐：

```python
round((time.monotonic() - started_at) * 1000)
```

不要新增：

```text
latency
latency_ms
duration
duration_ms
elapsed
```

历史日志不要求一次全部重写。

本轮修改涉及的 lifecycle 必须统一。

---

## 22. error_code 统一

`error_code` 必须：

```text
机器稳定
低基数
可统计
```

优先复用：

```text
internal_error

run_timeout
model_timeout
model_unavailable

tool_timeout
tool_failed

context_build_failed
memory_isolation_violation

workspace_recovery_required

side_effect_audit_unavailable

cancelled
```

Memory 可增加：

```text
memory_recall_failed
memory_retain_failed
```

Python Exception 类型不要作为稳定业务 error_code。

---

## 23. Exception 信息

真正异常信息由：

```python
logger.exception(...)
```

进入 JSON：

```json
{
  "error": {
    "type": "...",
    "message": "...",
    "stack": "..."
  }
}
```

因此：

```text
error_code
```

只负责机器分类。

---

## 24. 不要过度封装 Logging

当前：

```python
log_event(logger, level, event, component, **fields)
```

已经够用。

本轮禁止为了“规范”创建：

```text
AgentLogger
ModelLogger
ToolLogger
MemoryLogger
LogManagerFactory
ObservabilityServiceFactory
```

如果重复 correlation 构造明显，可以考虑一个非常薄的 helper，但不是强制项。

---

## 25. 未来 Metrics 的高基数约束

本轮还没接 Prometheus，但现在就要避免错误设计。

未来 Metrics **不要**使用：

```text
run_id
execution_id
request_id
conversation_id
session_id
account_id
tool_call_id
```

作为 labels。

这些字段属于：

```text
Logs / Trace
```

未来 Metrics 适合：

```text
surface
component
event
status
error_code
tool
model
provider
```

其中 `tool/model/provider` 也要控制基数。

---

## 26. 推荐最终 Web 生命周期

```text
request_received
    ↓
run_created
    ↓
outbox_event_claimed
    ↓
temporal_workflow_started
    ↓
context_assembly_started
    ↓
context_assembly_completed
    ↓
agent_execution_started
    ↓
memory_recall_started
memory_recall_completed/degraded
    ↓
model_call_started
model_call_completed
    ↓
tool_execution_started
tool_execution_completed
    ↓
...
    ↓
agent_execution_completed
    ↓
run_completed
    ↓
memory_retain_started
    ↓
memory_retain_completed
```

---

## 27. 推荐最终 QQ 生命周期

```text
message_received
    ↓
workflow
    ↓
process_turn_activity
    ↓
agent_execution_started
    ↓
memory_recall_started
memory_recall_completed/degraded
    ↓
model_call_started
model_call_completed
    ↓
tool_execution_started
tool_execution_completed
    ↓
...
    ↓
agent_execution_completed
    ↓
memory_retain_started
memory_retain_completed
```

注意实际 reply / retain 顺序以当前代码为准。

不要为了让流程图更漂亮而修改业务顺序。

---

## 28. Failure Chain 示例

Tool timeout：

```text
tool_execution_started
    ↓
tool_execution_failed
error_code=tool_timeout
    ↓
agent_execution_failed
error_code=tool_timeout
    ↓
run_failed
error_code=tool_timeout
```

这不属于重复日志，因为三个事件分别表达：

```text
Tool Boundary
Agent Boundary
Run Boundary
```

但如果 Facade 只是透明传播异常，不应再重复记录同层 `agent_execution_failed`。

---

## 29. 日志级别规范

### DEBUG

```text
内部细节
配置选择
中间状态
```

### INFO

```text
*_started
*_completed
run_created
workflow_started
```

### WARNING

```text
degraded
retry
fallback
recoverable anomaly
```

### ERROR

```text
*_failed
无法完成当前业务操作
```

`memory_recall_degraded` 建议 WARNING。

---

## 30. 现有普通日志整理策略

全仓搜索：

```bash
rg 'logger\.(info|warning|error|exception)' src
```

不要把所有日志都机械改成 `log_event()`。

只处理：

```text
关键 lifecycle
关键 failure
关键 recovery
关键 degradation
```

普通开发日志可继续保留。

---

## 31. 重点检查文件

至少检查：

```text
src/common/logging.py

src/agent_execution/brain_action_loop.py
src/agent_execution/qq_host.py
src/agent_execution/web_host.py
src/agent_execution/web_adapters.py

src/application/context_assembly.py
src/application/memory.py
src/application/memory_retention.py

src/orchestration/web_dispatcher.py
src/orchestration/web_reconciler.py
src/orchestration/web_activities.py
src/harness/activities.py

src/bootstrap/qq.py
src/orchestration/web_workers.py
```

实际路径发生变化时，以代码为准。

---

## 32. Logging Schema Tests

增加轻量测试，至少验证：

```text
log_event filters None
log_event keeps correlation fields
JSON formatter emits ts/event/component
exception emits error.type/message/stack
```

不要断言具体 timestamp。

---

## 33. Model Lifecycle Tests

至少覆盖：

```text
normal model success
normal model timeout
normal model failure

forced_final success
forced_final timeout
forced_final failure
```

重点验证：

```text
forced_final 也产生 model_call_started/completed/failed
```

---

## 34. Web Agent Host Tests

started / completed / failed 都验证：

```text
run_id
execution_id
conversation_id
session_id
account_id
surface=web
```

failed 必须存在：

```text
error_code
```

---

## 35. QQ Agent Host Tests

验证：

```text
workflow_id
execution_id
session_id
account_id
surface=qq
```

并验证：

```text
status=started/success/failed
```

---

## 36. QQ Memory Tests

至少验证：

```text
memory_recall_started
memory_recall_completed
memory_recall_degraded

memory_retain_started
memory_retain_completed
memory_retain_failed
```

不要求精确比较：

```text
ts
elapsed_ms
```

只验证字段存在、类型合理。

---

## 37. 实际 JSONL 验收

完成后真实运行至少一条 Web 请求。

按 `run_id`：

```bash
jq -c 'select(.run_id=="<RUN_ID>")'   .data/logs/hpagent.jsonl
```

按 `execution_id`：

```bash
jq -c 'select(.execution_id=="<EXECUTION_ID>")'   .data/logs/hpagent.jsonl
```

按 component：

```bash
jq -c 'select(.component=="model")'   .data/logs/hpagent.jsonl
```

查看失败：

```bash
jq -c 'select(.status=="failed")'   .data/logs/hpagent.jsonl
```

统计 error_code：

```bash
jq -r '
  select(.error_code != null)
  | .error_code
' .data/logs/hpagent.jsonl | sort | uniq -c | sort -nr
```

统计 event：

```bash
jq -r '.event // empty' .data/logs/hpagent.jsonl | sort | uniq -c | sort -nr
```

---

## 38. 真实 Web E2E 验收

至少跑一次：

```text
Browser
 -> hpagent-api
 -> PostgreSQL
 -> Outbox
 -> Temporal
 -> WebExecutionHost
 -> AgentExecutionFacade
 -> DefaultBrainActionLoop
 -> Model
 -> Tool（若消息触发）
 -> Reply
 -> SSE
 -> Memory Retain
```

建议选择一个明确触发低副作用工具的测试消息，例如读取 workspace 文件并总结。

具体内容由 Codex 根据当前工具能力决定。

### 日志验收

同一个 `run_id` 至少能够关联：

```text
run
context
agent
model
tool
memory
```

---

## 39. QQ 回归

如果当前环境可连接 QQ：

真实执行一次：

```text
QQ message
 -> Temporal
 -> QQExecutionHost
 -> Model
 -> Tool
 -> Reply
 -> Retain
```

按：

```text
execution_id
```

过滤完整日志。

如果环境没有 QQ，至少执行现有 QQ characterization/unit tests。

---

## 40. CI 验收

本轮最终提交必须确认：

```text
GitHub Actions CI
```

状态为：

```text
completed + success
```

不要只以本地测试通过作为闭环。

---

## 41. 本轮不要继续解决的债务

除非直接阻塞日志测试，否则本轮不要处理：

```text
worker.py 进一步拆 shared/runtime
account_service.py 历史文件物理删除
Multi-Agent experimental package
前端 bundle >500KB
nsjail 特定环境测试问题
```

---

## 42. 完成后的日志样例

一次成功 Web Tool Run 应接近：

```json
{"event":"agent_execution_started","component":"agent","run_id":"r1","execution_id":"r1","conversation_id":"c1","session_id":"s1","account_id":"a1","surface":"web","status":"started"}
{"event":"memory_recall_started","component":"memory","run_id":"r1","execution_id":"r1","session_id":"s1","account_id":"a1","surface":"web","status":"started"}
{"event":"memory_recall_completed","component":"memory","run_id":"r1","execution_id":"r1","surface":"web","status":"success","elapsed_ms":120,"result_count":4}
{"event":"model_call_started","component":"model","run_id":"r1","execution_id":"r1","surface":"web","turn":1,"phase":"agent_turn","status":"started"}
{"event":"model_call_completed","component":"model","run_id":"r1","execution_id":"r1","surface":"web","turn":1,"phase":"agent_turn","status":"success","elapsed_ms":840,"tool_count":1}
{"event":"tool_execution_started","component":"tool","run_id":"r1","execution_id":"r1","surface":"web","turn":1,"tool_call_id":"call1","tool":"fs_read","status":"started"}
{"event":"tool_execution_completed","component":"tool","run_id":"r1","execution_id":"r1","surface":"web","turn":1,"tool_call_id":"call1","tool":"fs_read","status":"success","elapsed_ms":23}
{"event":"model_call_started","component":"model","run_id":"r1","execution_id":"r1","surface":"web","turn":2,"phase":"agent_turn","status":"started"}
{"event":"model_call_completed","component":"model","run_id":"r1","execution_id":"r1","surface":"web","turn":2,"phase":"agent_turn","status":"success","elapsed_ms":612,"tool_count":0}
{"event":"agent_execution_completed","component":"agent","run_id":"r1","execution_id":"r1","conversation_id":"c1","session_id":"s1","account_id":"a1","surface":"web","status":"success","elapsed_ms":1750}
{"event":"memory_retain_started","component":"memory","run_id":"r1","conversation_id":"c1","account_id":"a1","surface":"web","status":"started"}
{"event":"memory_retain_completed","component":"memory","run_id":"r1","surface":"web","status":"success","elapsed_ms":180}
```

不要求字段顺序一致。

---

## 43. 最终验收 Checklist

### Dependency

- [ ] `bootstrap/qq.py` 不再依赖 `web_workers.py` 的 identity validator
- [ ] shared identity validator 已移动到公共模块

### Agent

- [ ] Web Agent started/completed/failed correlation 一致
- [ ] QQ Agent status 统一
- [ ] Agent failure 具有稳定 `error_code`

### Model

- [ ] 普通调用有完整 lifecycle
- [ ] forced final 有完整 lifecycle
- [ ] Model 使用统一 `elapsed_ms`
- [ ] Model failure 使用稳定 error_code

### Tool

- [ ] Tool lifecycle 字段统一
- [ ] `tool_call_id` 可关联
- [ ] `tool_timeout` 与 `run_timeout` 可区分

### Memory

- [ ] Web recall lifecycle 完整
- [ ] QQ recall lifecycle 补齐
- [ ] Web retain lifecycle 完整
- [ ] QQ retain lifecycle 补齐
- [ ] degraded 与 failed 语义明确

### Correlation

- [ ] Web 可通过 `run_id` 查询完整链路
- [ ] QQ 可通过 `execution_id` 查询完整链路
- [ ] 不伪造不存在的 ID
- [ ] `surface` 统一为 `web` / `qq`

### Schema

- [ ] `status` 使用统一集合
- [ ] `elapsed_ms` 命名统一
- [ ] `error_code` 为稳定低基数机器码
- [ ] Python Exception 存在 `error.type/message/stack`
- [ ] lifecycle event 使用统一命名规则

### Runtime

- [ ] Web 真实 E2E 跑通
- [ ] JSONL 能通过 jq 按 `run_id` 查询
- [ ] QQ characterization 或真实回归通过
- [ ] CI 完整成功

---

## 44. 最终输出报告

本轮结束后新增：

```text
docs/operations/observability-preflight-report.md
```

至少记录：

### Changed

修改了哪些模块。

### Schema

最终字段表：

```text
field
meaning
applicability
example
```

### Events

最终事件列表。

### Error Codes

最终稳定 `error_code` 列表。

### Web E2E

记录：

```text
run_id
关键事件序列
是否完整
```

### QQ Regression

记录测试或真实执行结果。

### CI

记录最终状态。

### Remaining Observability Work

只保留下一阶段：

```text
Metrics
Dashboard
Trace
Log viewer / filter UI
```

---

## 45. 下一阶段

完成本轮后，再进入 Observability V1。

推荐顺序：

```text
V1.1
日志浏览 / run_id 聚合

V1.2
基础 Metrics

V1.3
Grafana Dashboard

V1.4
OpenTelemetry Trace

V1.5
Web 内置 Observability 页面
```

不要反过来先安装一堆观测基础设施，再回来修日志。

---

## 46. 最终判断标准

任意 Web Run：

```bash
jq 'select(.run_id=="...")' hpagent.jsonl
```

应该直接看到：

```text
Run
Context
Memory Recall
Agent
Model
Tool
Agent Result
Memory Retain
```

任意 QQ Execution：

```bash
jq 'select(.execution_id=="...")' hpagent.jsonl
```

应该看到：

```text
Agent
Memory Recall
Model
Tool
Reply Boundary
Memory Retain
```

做到这一点后，日志才真正从：

```text
程序输出
```

升级成：

```text
可观测的执行事件流
```

此时再建设 Dashboard、Metrics 和 Trace。
