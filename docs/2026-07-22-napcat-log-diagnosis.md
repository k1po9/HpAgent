# 2026-07-22 NapCat 对话日志问题诊断与整改方案

## 实施状态（2026-07-22）

本轮已完成问题 1、7、8 及相关模型参数治理：

- 问题 1：模型条目级 `extra_body` 已支持解析、校验、与 provider 默认值递归合并，并能进入最终请求；MiniMax fast/chat 使用 `thinking.type=disabled`，reasoning 使用 `adaptive`。
- 问题 7：endpoint 改为按 `category:index:provider:model` 注册，同一远端模型的 fast/chat/reasoning 配置不再互相覆盖。
- 问题 8：沙箱日志改为输出实际注册的 native/reminder/MCP/Skill 数量、Bash 是否注册及其真实隔离状态；nsjail 缺失日志改为准确的 fail-closed 描述。
- 模型日志：区分请求注入的工具数量与响应实际返回的工具调用，并记录 endpoint、provider、model、format、max tokens、thinking 模式等非敏感配置。
- 其他准确性修复：Hindsight timeout 日志明确标记 `no retry`，模型退避日志明确使用 endpoint 而不是容易误解的 model。
- 尚未处理：问题 2 的 `<think>` 响应兜底清洗，以及问题 3~6 的路由、记忆、Tool RAG 和 token 优化。

后续各问题章节仍保留修复前代码，用于解释原始日志根因。

---

## 1. 结论摘要

本次日志中的 MiniMax-M3 两次调用均成功，没有模型服务 400 或连接故障。主要问题集中在 HpAgent 自身的配置解析、响应边界、请求编排和工具检索策略：

| 优先级 | 问题 | 日志证据 | 直接影响 |
|---|---|---|---|
| P0 | MiniMax thinking 关闭参数未生效 | 最终回复含 `<think>...</think>` | 内部推理和角色提示泄露给用户 |
| P0 | 渠道发送前没有响应净化 | `Sent message to NapCat clients: <think>...` | 泄露内容被原样发送并写入日志 |
| P1 | 简单问题无条件执行 HyDE 改写 | `fast` 调用耗时约 6 秒 | 首包延迟和模型成本增加 |
| P1 | 简单问题无条件执行长期记忆召回 | Hindsight recall 超时约 3 秒 | 关键路径被可选服务阻塞 |
| P1 | Tool RAG 低置信度时仍强行返回工具 | reranker 最高分 `0.0072`，仍返回 12 个工具 | 无关工具污染上下文 |
| P1 | 上下文缺少有效 token 预算 | chat 输入达到 4433 tokens | 延迟、费用和模型干扰增加 |
| P2 | 同一模型在不同类别间共享并覆盖客户端配置 | `fast/chat/reasoning` 均可映射为 `minimax:MiniMax-M3` | 类别级 timeout/extra_body 等配置不可靠 |
| P2 | 沙箱状态日志语义不够准确 | `native=off, nsjail=off`，但提醒工具仍注册 | 运维人员容易误判实际工具暴露面 |

从收到“你是谁”到发送回复约 21 秒。两次模型调用合计输入约 4747 tokens、输出约 295 tokens；对于三字身份问答，这条链路明显过重。

---

## 2. 问题一：MiniMax thinking 关闭参数未进入正式请求

### 2.1 日志表现

最终发送内容以如下文本开头：

```text
<think>The user is asking "你是谁" ...</think>
```

这证明 MiniMax 仍然生成了显式思考内容。

### 2.2 代码错误

当前 `config/models.yaml` 把配置写在具体模型条目下：

```yaml
chat:
  - provider: minimax
    model: "${MINIMAX_FLAGSHIP_MODEL}"
    extra_body:
      enable_thinking: false
```

但正式配置模型 `ModelEntry` 没有 `extra_body` 字段：

```python
# src/orchestration/config.py
@dataclass
class ModelEntry:
    provider: str = ""
    model: str = ""
    max_tokens: int = 2048
    timeout: float = 30.0
```

`ModelsConfig.from_yaml()` 也没有解析模型条目中的 `extra_body`。随后 `resolve_endpoint()` 只传递 provider 层的 `extra_body`：

```python
"extra_body": provider.extra_body,
```

因此，正式 HpAgent 请求中的 `extra_body` 实际为空。`ModelClient._build_payload()` 本身支持透传，但上游没有把配置交给它：

```python
if self._extra_body:
    payload.update(self._extra_body)
```

此外，`enable_thinking: false` 不是 MiniMax-M3 文档要求的字段。正确格式为：

```yaml
thinking:
  type: disabled
```

### 2.3 为什么测试脚本可能产生不同结论

`scripts/test-models.py` 会直接读取模型条目中的 `extra_body` 并合并进请求，因此它与正式运行路径不一致：

```python
"extra_body": m.get("extra_body") or {},
...
payload.update(cfg["extra_body"])
```

所以：

- 正式 HpAgent：当前模型条目下的 `extra_body` 根本没有发送。
- `test-models.py`：`enable_thinking` 会发送，但字段不符合 MiniMax-M3 协议，仍不能可靠关闭 thinking。

### 2.4 方案

#### 短期方案：provider 级统一关闭

现有代码已经支持 provider 级 `extra_body`，可先改为：

```yaml
providers:
  minimax:
    base_url: "${MINIMAX_API_BASE_URL}"
    api_key: "${MINIMAX_API_KEY}"
    api_format: "openai"
    extra_body:
      thinking:
        type: disabled
```

该方案无需修改 Python，但会对所有 MiniMax 类别统一关闭 thinking，包括 `reasoning`。

#### 长期方案：支持 provider 默认值与模型级覆盖

给 `ModelEntry` 增加字段：

```python
extra_body: dict = field(default_factory=dict)
```

解析模型配置：

```python
extra_body=item.get("extra_body") or {},
```

解析端点时合并 provider 默认值和模型覆盖值：

```python
extra_body = {**provider.extra_body, **entry.extra_body}
```

建议对 MiniMax 参数增加启动期校验，发现 `enable_thinking` 时直接报出可操作错误，避免静默失效。

### 2.5 验收标准

- 启动时打印脱敏后的 MiniMax 请求能力配置，例如 `thinking=disabled`。
- 单元测试断言 `_build_payload()` 包含：

  ```json
  {"thinking": {"type": "disabled"}}
  ```

- 使用“你是谁”回归测试时，响应中不再出现 `<think>`，输出 token 数明显下降。

---

## 3. 问题二：缺少响应净化边界，思考内容被发送并记录

### 3.1 日志表现

日志不是简单记录了模型原始响应，而是明确显示：

```text
Sent message to NapCat clients: <think>...</think>
```

`<think>` 中包含“扮演 nono”“不要说自己是 AI”等内部角色约束。这属于内部提示泄露。

### 3.2 代码错误

OpenAI 兼容响应解析直接取 `message.content`：

```python
# src/resources/model_client.py
content_text = message.get("content", "") or ""
```

这里只清理了 XML 工具调用，没有识别或隔离 `<think>`、`reasoning_content` 等推理字段。

NapCat 渠道随后把 `message.content` 原样写入 payload，并用 INFO 级别记录完整消息：

```python
payload = {
    "action": "send_private_msg",
    "params": {"user_id": user_id, "message": message.content},
}
...
logger.info(f"Sent message to NapCat clients: {message.content}")
```

架构上缺少统一的“模型输出 → 用户可见内容”安全边界。只依赖供应商正确执行 thinking 参数是不够的。

### 3.3 方案

建立统一 `ResponseSanitizer`，放在模型响应进入会话事件和渠道发送之前，而不是只在 NapCat 内处理：

```python
_THINK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.I | re.S)

def sanitize_user_visible_text(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()
```

还应分别处理：

- 非流式响应中的 `<think>...</think>`。
- 流式响应中跨 chunk 的 `<think>` 状态，不能逐 chunk 用简单正则。
- 供应商返回的独立 `reasoning_content`/`reasoning_details` 字段：用于审计时应单独存储且默认不向渠道暴露。
- 清洗后为空：返回安全兜底文案或触发一次禁用 thinking 的重试，不能发送空消息。

发送日志应改成结构化摘要，不记录完整正文：

```python
logger.info(
    "Sent message to NapCat clients: target=%s chars=%d",
    target_id,
    len(message.content),
)
```

如果必须保留正文用于开发调试，应放到显式开启的 DEBUG 日志，并执行脱敏和长度截断。

### 3.4 验收标准

- 构造包含单个、多段、跨流 chunk 的 `<think>` 响应，渠道最终均不可见。
- INFO 日志不再包含完整用户消息、模型回复或内部推理。
- 会话历史中不保存可被下一轮重新注入的 `<think>` 内容。

---

## 4. 问题三：所有请求都先调用 fast 模型执行 HyDE 改写

### 4.1 日志表现

用户只问“你是谁”，系统仍先调用一次 `fast` 模型：

```text
Model call MiniMax-M3 latency=5967ms tokens={'in': 314, 'out': 196}
ResourcePool: fast ... latency=5981ms
```

这一步约占总延迟的 28%。

### 4.2 架构错误

`HarnessRunner.process_turn()` 的单 Agent 和多 Agent 路径都会无条件调用：

```python
recall_query, self._last_hyde_context = await self._brain.rewrite_recall_query(...)
```

而 `BrainEngine.rewrite_recall_query()` 只要存在 `hyde_rewrite` prompt 和模型，就一定调用 `model_selector="fast"`。当前没有：

- 简单意图快速判断。
- 最短文本或身份/寒暄白名单。
- HyDE 延迟预算。
- 基于记忆需求的条件执行。
- 改写结果缓存。

### 4.3 方案

#### 短期方案：确定性规则跳过

在调用 HyDE 前增加低成本 gate：

```python
def should_rewrite_for_memory(text: str) -> bool:
    normalized = text.strip().lower()
    if normalized in {"你是谁", "你好", "在吗", "谢谢", "再见"}:
        return False
    if len(normalized) <= 4 and not any(k in normalized for k in ("之前", "上次", "记得")):
        return False
    return True
```

#### 长期方案：统一请求路由

引入轻量 `TurnRoute`：

```text
DIRECT_CHAT       → 不做 HyDE、不召回记忆、不检索工具
MEMORY_CHAT       → HyDE（可选）+ recall，不注入工具
TOOL_CHAT         → 工具检索，按需 recall
COMPLEX_AGENT     → 完整链路
```

路由优先采用规则和已有上下文；只有不确定时才使用小模型分类，避免为了节省一次调用反而增加一次调用。

### 4.4 验收标准

- “你是谁”“你好”等请求只发生一次 chat 模型调用。
- 简单对话 P95 延迟目标建议小于 8 秒。
- 日志记录 route 和每阶段耗时，能解释为什么执行或跳过 HyDE。

---

## 5. 问题四：长期记忆召回位于关键路径并发生超时

### 5.1 日志表现

```text
DEGRADATION: Hindsight timeout POST .../memories/recall (attempt 1/3)
```

本次请求在继续执行前等待了约 3 秒。

### 5.2 代码与架构问题

`HarnessRunner` 在进入 chat 循环前无条件等待：

```python
_mem_items, memories_text = await self._memory.recall_memories(...)
```

默认 `recall_timeout` 为 3 秒。`HindsightClient._request()` 对 timeout 会立即降级、不重试，但日志仍写成 `attempt 1/3`，容易让运维人员误以为后面还有两次重试：

```python
except httpx.TimeoutException:
    logger.warning("... (attempt %d/3)", attempt + 1)
    return None
```

因此这里有两个问题：

1. 可选记忆服务同步阻塞主回复。
2. 超时日志文案与实际行为不一致。

### 5.3 方案

- 与问题三的路由结合：`DIRECT_CHAT` 完全跳过 recall。
- 需要记忆时，将 HyDE 与其他独立准备工作并发执行；能使用原始 query 时，不必等待 HyDE 才发起 recall。
- 根据 Hindsight 的正常 P95 调整超时。若服务通常无法在 3 秒内返回，应先优化服务/数据库，而不是盲目放大客户端超时。
- 加入短时 circuit breaker：连续超时后在若干秒内直接跳过，避免每条消息重复支付 3 秒。
- 将超时日志改为 `no retry`，例如：

  ```text
  Hindsight recall timeout after 3.0s; degraded without retry
  ```

- 监控 recall success rate、P50/P95/P99、超时率和 circuit 状态。

### 5.4 验收标准

- Hindsight 不可用时，简单对话延迟不增加 3 秒。
- 日志准确说明 timeout 是否重试。
- 连续故障时 circuit breaker 生效，恢复后自动半开探测。

---

## 6. 问题五：Tool RAG 低置信度时仍强行注入 12 个工具

### 6.1 日志表现

“你是谁”召回了提醒和股票工具。Chroma 相似度约为 `0.31~0.34`，reranker 最高分仅 `0.0072`：

```text
reranker scores too low (max=0.0072 < 0.05), falling back to ChromaDB
retrieve: final 12 tools: list_reminders, create_reminder, ...
```

### 6.2 代码错误

`ToolRetriever.retrieve()` 把“reranker 无法确认相关性”解释为“使用 Chroma 排名”：

```python
if max_rerank < 0.05:
    scores = chroma_scores
```

随后无条件取前 `top_k`：

```python
for name in tool_names[:top_k]:
    ...
```

这只有排序阈值，没有拒绝阈值。即使所有候选都不相关，也必然返回最多 12 个工具。

另一个排序问题位于 `Sandbox.select_tools()`：

```python
result.sort(key=lambda d: scores.get(tool_name, 0.0))
```

注释明确写着“低分在前，高分在后”。如果后续存在硬截断或模型对前部工具更敏感，这会让低相关工具占据更有利位置。通常应按相关度降序排列，required 工具另行固定置顶。

### 6.3 方案

增加两级拒绝策略：

```python
if reranker_succeeded and max_rerank < rerank_reject_threshold:
    return []

filtered = [
    name for name in tool_names
    if chroma_scores.get(name, 0.0) >= chroma_min_similarity
]
```

建议配置化：

```yaml
tool_rag:
  top_k: 6
  chroma_min_similarity: 0.45
  rerank_reject_threshold: 0.05
  empty_on_low_confidence: true
```

阈值应通过真实查询集标定，以上数值仅作初始值。工具排序改为高分优先：

```python
result.sort(key=lambda d: scores.get(tool_name, 0.0), reverse=True)
```

同时在请求路由层直接让身份问答和寒暄进入 `DIRECT_CHAT`，从源头跳过 Tool RAG。

### 6.4 验收标准

- “你是谁”返回零个工具。
- “提醒我明天开会”能稳定召回提醒工具。
- “查询某股票 K 线”能召回对应行情工具。
- 建立离线评测集，统计 Recall@K、Precision@K、空召回正确率及平均注入 token。

---

## 7. 问题六：上下文与工具定义导致 token 膨胀

### 7.1 日志表现

最终 chat 调用：

```text
tokens={'in': 4433, 'out': 99}
```

用户输入仅为“你是谁”，输入 token 却达到 4433。

### 7.2 架构错误

`HarnessRunner._build_context()` 调用 `HarnessContextBuilder.build()` 时没有传入 `token_budget`：

```python
return self._ctx.build(
    events=events,
    ...,
    max_turns=20,
)
```

而 `HarnessContextBuilder.build()` 的默认值是：

```python
token_budget: int = 0
```

`0` 表示关闭 token 感知截断，只使用固定轮数截断。工具 schema 又在上下文之外整体传入模型，本轮一次注入 12 个工具，导致短请求也承担完整系统提示和大量工具定义。

`BrainEngine.snapshot_context()` 虽然估算 token，但它发生在请求构造之后，主要用于审计，不能阻止超预算请求。

### 7.3 方案

- 给不同模型/渠道配置输入预算，而不是只配置输出 `max_tokens`。
- 在最终调用前统一估算：系统提示 + 历史 + 记忆 + 工具 schema。
- 按优先级裁剪：低相关工具 → 低相关记忆 → 较旧历史 → 可选指导文本。
- 简单路由不注入工具纪律、工具 schema 和无关环境提示。
- 将 `top_k` 从 12 降到经过评测的最小值，初始可尝试 4~6。
- 记录 `prompt_tokens_by_source`，例如：

  ```json
  {
    "identity": 320,
    "guidance": 550,
    "history": 40,
    "memory": 0,
    "tools": 3100,
    "total": 4010
  }
  ```

### 7.4 验收标准

- “你是谁”输入 token 建议控制在 1000 以内。
- 所有模型调用在发出前执行总预算检查。
- 日志可定位 token 主要来自系统提示、历史、记忆还是工具。

---

## 8. 问题七：同一 provider/model 在类别间发生客户端配置覆盖

### 8.1 日志与配置背景

日志显示 `fast` 和 `chat` 都调用 `minimax:MiniMax-M3`；当前配置还可能让 `reasoning` 使用同一模型。

### 8.2 架构错误

`ResourcePool.initialize_models()` 使用以下 ID 保存客户端：

```python
client_id = f"{ep.provider}:{ep.model}"
self._model_clients[client_id] = {"client": client, "priority": 0}
```

初始化顺序是 `fast → chat → embedding → image → reasoning`。当 fast、chat、reasoning 使用同一个 provider/model 时，后注册客户端会覆盖先注册客户端。结果是：

- fast 配置的 `max_tokens=1024`、`timeout=15` 可能被 chat/reasoning 覆盖。
- 将来即使支持模型条目级 `extra_body`，类别级 thinking 设置仍可能互相覆盖。
- fallback group 保存的是同一个 ID，无法找回被覆盖的类别配置。

### 8.3 方案

把 endpoint 实例身份与远端模型身份分开。最直接的方式是在 ID 中加入类别和序号：

```python
client_id = f"{category}:{index}:{ep.provider}:{ep.model}"
```

或者为每个模型条目增加稳定配置 ID：

```yaml
chat:
  - id: minimax-chat
    provider: minimax
    model: "${MINIMAX_FLAGSHIP_MODEL}"
```

`provider:model` 仍可作为监控标签，但不能作为客户端配置实例的唯一主键。

### 8.4 验收标准

- fast/chat/reasoning 使用同一远端模型时，分别保留自己的 timeout、max_tokens 和 extra_body。
- 启动日志打印每个 group 到 endpoint instance ID 的映射。
- 添加重复 provider/model、不同配置的单元测试。

---

## 9. 问题八：沙箱状态日志容易误读，但本次不是直接故障

### 9.1 日志表现

```text
Session sandbox created ... native=off, nsjail=off
```

### 9.2 实际含义

当前 `config/config.yaml` 明确配置：

```yaml
sandbox:
  native_tools_enabled: false
  nsjail_enabled: false
```

这表示：

- 大多数本地文件和 Bash 工具没有注册。
- Bash 的 nsjail 执行器没有创建。
- MCP 和 Skills 工具不受这两个开关直接控制。
- `create_reminder/list_reminders/cancel_reminder` 在 `SandboxManager` 中会无条件注册，虽然日志显示 `native=off`。

因此，不能简单得出“所有本地工具都在无隔离执行”的结论；但日志确实没有准确表达提醒工具、MCP、Skills 的实际可用状态。

### 9.3 方案

- 将启动日志改为按类别输出实际注册数量：

  ```text
  sandbox created: native_general=0 reminder=3 mcp=68 skills=3 bash_nsjail=off
  ```

- 如果启用 Bash，生产环境必须同时启用并验证 nsjail；若 nsjail 初始化失败，应 fail closed，不能静默退回进程内执行。
- 对 MCP/Skills 单独定义权限、超时、网络和审计策略，不把它们笼统归入 nsjail 状态。

### 9.4 验收标准

- 日志可以准确回答“当前模型能看到并调用哪些类别的工具”。
- Bash 开启而 nsjail 不可用时，启动失败或 Bash 工具拒绝执行。
- 权限回归测试覆盖 native、reminder、MCP、Skill 四类工具。

---

## 10. 日志中不是故障的项目

以下日志属于正常行为或容易误读，不应作为本次根因：

1. `NapCat client connected`：连接成功。
2. `deleting 22 old tools`：向量库中原有 96 个工具、当前注册 74 个，增量同步删除陈旧工具，随后正常完成。
3. 两次 `Model call MiniMax-M3 ... stop=end_turn`：模型 API 调用成功；问题发生在请求参数和响应治理，不是模型服务不可用。
4. 模型日志中的 `tools=None`：这里记录的是响应中的工具调用名称为空，不代表第二次请求没有注入工具 schema。
5. Hindsight 日志中的 `attempt 1/3`：timeout 分支实际立即返回，没有继续重试；问题是日志文案不准确。

---

## 11. 推荐整改顺序

### 第一阶段：立即止血（P0）

1. 将 MiniMax provider 配置改为 `thinking: {type: disabled}`。
2. 增加统一响应净化，阻止 `<think>` 进入会话历史和渠道。
3. INFO 日志停止记录完整回复正文。
4. 增加请求 payload 单元测试和 `<think>` 回归测试。

### 第二阶段：降低延迟与成本（P1）

1. 引入 `DIRECT_CHAT/MEMORY_CHAT/TOOL_CHAT/COMPLEX_AGENT` 路由。
2. 简单对话跳过 HyDE、Hindsight 和 Tool RAG。
3. reranker 低于拒绝阈值时返回零工具。
4. 工具按相关度降序，`top_k` 从 12 下调并通过评测确定。
5. 在模型调用前执行包含工具 schema 的总 token 预算。

### 第三阶段：修复配置隔离与可观测性（P2）

1. endpoint 实例 ID 加入 category/config ID，消除覆盖。
2. 支持模型条目级 `extra_body` 覆盖 provider 默认值。
3. 增加分阶段耗时和 token 来源指标。
4. 修正 Hindsight timeout 和沙箱能力日志。

---

## 12. 建议的目标链路

```text
收到用户消息
  │
  ├─ 规则/轻量路由
  │    ├─ DIRECT_CHAT ─────────────────────────────┐
  │    ├─ MEMORY_CHAT ── 可选 HyDE ── recall ─────┤
  │    ├─ TOOL_CHAT ─── Tool RAG + 拒绝阈值 ──────┤
  │    └─ COMPLEX_AGENT ── 完整 ReAct 链路 ───────┤
  │                                               │
  ├─ 构建上下文并执行总 token 预算                 │
  ├─ 调用具备独立 category/config ID 的模型客户端  │
  ├─ 统一 ResponseSanitizer                        │
  ├─ 记录脱敏审计信息                              │
  └─ 发送用户可见内容 ◀────────────────────────────┘
```

该设计将“是否需要记忆”“是否需要工具”“是否允许展示推理”“使用哪个模型配置实例”变成显式决策，避免所有请求无条件走最昂贵的完整链路。
