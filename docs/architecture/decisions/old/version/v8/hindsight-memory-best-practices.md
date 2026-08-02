# Hindsight 记忆最佳实践 —— HpAgent 实施指南

> 基于 [Hindsight 官方文档](https://docs.vectorize.com) v0.6.x + NapCat API 参考 + HpAgent 代码库现状评估  
> 目标：多渠道（QQ/NapCat + Console + Web）接入，以 Hindsight 作为长期记忆模块

---

## 目录

1. [核心原则](#1-核心原则)
2. [数据字段映射 —— NapCat → Hindsight](#2-数据字段映射--napcat--hindsight)
3. [文档 ID 与分段策略](#3-文档-id-与分段策略)
4. [标签体系设计](#4-标签体系设计)
5. [Retain 流程优化](#5-retain-流程优化)
6. [Recall 策略](#6-recall-策略)
7. [时间戳标准化](#7-时间戳标准化)
8. [消息预处理与渠道兼容](#8-消息预处理与渠道兼容)
9. [错误处理与监控](#9-错误处理与监控)
10. [隐私与合规](#10-隐私与合规)
11. [实施清单（按优先级）](#11-实施清单按优先级)

---

## 1. 核心原则

### 1.1 Hindsight 设计假设

| 假设 | 含义 |
|------|------|
| **全文替换，非增量追加** | 同一 `document_id` 的 retain 会删除旧文档及其所有记忆，重新提取。Hindsight 支持 `update_mode: "append"` 模式做增量，但仍会重新处理整个文档 |
| **标签隔离 > 查询过滤** | 记忆的可见性由 tags 控制（数据库层 WHERE），而非在查询 prompt 中做渠道/群过滤 |
| **保留原始内容** | 传入最丰富的表示形式（JSON 对话数组优先），不要做预处理摘要——LLM 需要结构做事实提取 |
| **稳定 ID = 幂等** | 复用相同的 `document_id` 实现幂等 upsert；每次生成随机 UUID 会产生重复记忆 |
| **不同轮次分离 retain/recall** | 不要在同一请求中同时执行 retain 和 recall —— retain 写入的记忆尚未索引 |

### 1.2 HpAgent 架构现状

```
NapCat WS 推送
  → NapCatChannel.normalize_message() → UnifiedMessage
    → Worker.handle_message() → dict
      → Temporal Workflow → HarnessRunner.process_turn()
        → recall (查询开始前) → model → tools → ... → response
        → retain (响应发送后) ← 当前：同步调用，未使用 async_=True

当前实现:
  - hindsight_client.py: retain/recall/reflect 基础封装
  - session/store.py:    retain_memories/recall_memories/reflect 委托层
  - runner.py:           process_turn() 中串联 recall → model → retain
```

**当前代码的合规点（已对齐最佳实践）：**
- `recall` 在每轮开始时调用，`retain` 在发送响应后调用 —— 分离了读写时机（`runner.py:233`）
- `document_id` 使用 `session_id-{index}` 模式，有一定稳定性（`hindsight_client.py:203`）
- tags 已包含 `user:{user_id}` 和 `session:{session_id}`（`hindsight_client.py:188-199`）
- `retain_mission` 和 `reflect_mission` 已在 `_ensure_bank()` 中配置（`hindsight_client.py:139-148`）

**需要改进的核心点：**
- 缺少 `async_=True` 异步写入，retain 在同步路径中虽在响应之后，但未显式以异步提交
- `timestamp` 字段未传递给 Hindsight，丢失时序检索能力
- `context` 字段未充分利用（仅传 `role=user/assistant`，缺少渠道/群上下文）
- 标签体系可扩展（缺 `group:{id}`、`scope:{private|group}` 等）
- recall 使用默认 `tags_match`，未根据场景显式选择隔离策略
- 缺少错误分类和可观测性指标

---

## 2. 数据字段映射 —— NapCat → Hindsight

### 2.1 字段提取来源

NapCat `OB11Message` 上报事件提供以下可用于记忆模块的字段：

| Hindsight 字段 | NapCat 数据来源 | 取值方式 | 优先级 |
|---------------|----------------|---------|-------|
| `user_id` (→ tag `user:{id}`) | `sender.user_id` | 直接读取 | **必须** |
| `session_id` (→ tag `session:{id}`) | 系统生成 | `session-{account_id}` | **必须** |
| `group_id` (→ tag `group:{id}`) | `group_id` (群聊时) | 直接读取 | 强烈建议 |
| `timestamp` | `time` (Unix) | 转为 ISO 8601 | **必须** |
| `content` | `raw_message` / `message` 段数组 | 按消息段类型提取 | **必须** |
| `context` | 由 `message_type` + `group_name` 构建 | 见 2.2 | 强烈建议 |
| `document_id` | `session_id` + 平台 `message_id` | 拼接 | 强烈建议 |
| `metadata.channel` | 固定值 `"napcat"` | 硬编码 | 可选 |
| `metadata.sender_name` | `sender.card` or `sender.nickname` | 直接读取 | 建议 |
| `metadata.message_id` | `message_id` | 直接读取 | 可选（幂等校验用） |

### 2.2 `context` 字段构建规范

Hindsight 官方强烈建议始终设置 `context`，它会被注入 LLM 提取 prompt 中，直接影响事实提取质量。

**推荐模板：**

```python
# 群聊场景
context = f"QQ group chat in "{group_name}" ({group_id})"

# 私聊场景
context = f"QQ private chat with {sender_name} ({sender_id})"

# 通用回退
context = f"QQ {message_type} chat"
```

**反例（当前代码的问题）：**

```python
# hindsight_client.py:202 — 当前仅传 role 信息，缺少渠道/群上下文
"context": f"role={role}"
```

### 2.3 字段精简原则

NapCat 上报的事件包含大量字段（`OB11Sender` 的 sex/age/area/level/title 等），保留原则：

| 保留 | 丢弃 | 理由 |
|------|------|------|
| `user_id` | `sex`, `age`, `area`, `level` | 记忆隔离的最小必要字段 |
| `group_id` | `title`, `role` (sender) | 群维度过滤需要 |
| `sender.card` / `sender.nickname` | `sender.level` | 人类可读的 context 描述 |
| `message_id` | `message_seq`, `real_id`, `real_seq` | 幂等校验仅需 message_id |
| `time` | — | 时序检索必须 |
| `raw_message` | 原始 `message` 段数组 | text 类型直接传文本，非 text 类型（图片/文件）提取描述后传入 |

### 2.4 多媒体消息处理

Hindsight 只处理文本内容，不对图片/音频做 OCR/转录。对非 text 消息段：

- **image**: 优先使用 NapCat 的图片 OCR 接口获取文字，或使用 `summary` 字段描述
- **record (语音)**: 使用语音转文字接口，将转录文本传给记忆
- **file**: 提取文件名和摘要作为记忆内容
- **纯表情/戳一戳/骰子等**: 跳过，不传给 Hindsight

---

## 3. 文档 ID 与分段策略

### 3.1 设计目标

- **幂等性**：同一对话重复 retain 不产生重复记忆
- **可控粒度**：单文档不会过大导致提取质量下降
- **渠道兼容**：所有渠道（NapCat/Console/Web）使用统一规则

### 3.2 推荐方案

**方案 A（推荐，适合中短对话 < 50 轮）：会话级文档 ID**

```python
# 整个会话使用一个 document_id，每次 retain 全文替换
document_id = f"session:{session_id}"
```

- 优点：最简单，Hindsight 官方推荐模式
- 缺点：超长对话（> 100 轮）单次 retain 内容过多

**方案 B（适合长对话 > 50 轮）：会话-轮次分段**

```python
# 按轮次分段，每 20 轮一个文档
segment = turn_number // 20
document_id = f"session:{session_id}-seg{segment}"
```

- 优点：单文档大小可控
- 缺点：同一 conversation 的事实分散在多个文档中，跨段关联较弱

**当前实现评估：**

```python
# hindsight_client.py:203 — 每条消息独立 document_id
"document_id": f"{session_id}-{i}"
```

这会导致每条消息被当作独立文档处理，LLM 无法利用消息间的上下文关系。**应改为会话级或分段级 document_id**。

### 3.3 幂等性保障

结合 NapCat 平台 `message_id` 做幂等校验：

```python
# 在 retain 前检查该 message_id 是否已处理（本地缓存最近 1000 条）
already_retained = await self._check_message_dedup(session_id, platform_message_id)
if already_retained:
    return 0  # 跳过重复上报（网络重连场景）
```

---

## 4. 标签体系设计

### 4.1 标签命名规范

Hindsight 官方推荐的标准命名模式：

| 标签模式 | HpAgent 示例 | 用途 |
|---------|-------------|------|
| `user:{id}` | `user:u_abc123` | 用户隔离（多租户必须） |
| `session:{id}` | `session:s_xyz` | 会话上下文隔离 |
| `group:{id}` | `group:123456789` | 群聊隔离 |
| `scope:{type}` | `scope:private`, `scope:group` | 对话类型标记 |
| `channel:{name}` | `channel:napcat`, `channel:console` | 渠道标记 |

### 4.2 推荐标签组合

**群聊消息 retain：**

```python
tags = [
    f"user:{user_id}",           # 发送者
    f"session:{session_id}",     # 会话
    f"group:{group_id}",         # 群
    f"scope:group",              # 对话类型
    f"channel:napcat",           # 渠道
]
```

**私聊消息 retain：**

```python
tags = [
    f"user:{user_id}",
    f"session:{session_id}",
    f"scope:private",
    f"channel:napcat",
]
```

### 4.3 Recall 时的标签匹配策略

| 场景 | `tags_match` | 说明 |
|------|-------------|------|
| 私聊（完全隔离） | `any_strict` + `tags=["user:{id}"]` | 只返回该用户的记忆 |
| 群聊（群内共享 + 用户个人） | `tag_groups` 组合 | 见下方示例 |
| 跨群知识库 | `any`（宽松） | 允许无标签的全局知识 |

**群聊 recall 的 tag_groups 示例（群内共享知识 + 用户个人偏好）：**

```python
recall(
    query=user_content,
    tag_groups=[
        {"tags": [f"user:{user_id}"], "match": "any_strict"},       # 该用户的个人记忆
        {"tags": [f"group:{group_id}"], "match": "any_strict"},     # OR 该群的共享记忆
    ],
    # 注意：tag_groups 顶层是 AND，所以这里达不到 OR 的效果
    # 正确做法是用 or:
    {"or": [
        {"tags": [f"user:{user_id}"], "match": "any_strict"},
        {"tags": [f"group:{group_id}"], "match": "any_strict"},
    ]},
)
```

**默认建议：** 群聊使用 `tags_match="any_strict"` 同时传入 `user:{id}` 和 `group:{id}`，让同时包含任一标签的记忆浮现。

---

## 5. Retain 流程优化

### 5.1 当前流程

```
runner.py: process_turn()
  ...
  await self._send_response(...)        # 先发送回复
  await self._session.retain_memories(  # 再 retain（同步等待）
      turn_events, account_id, session_id
  )
```

当前 `retain_memories` 使用同步调用（Hindsight SDK 的 `async_=False` 默认值），但因为它位于 `_send_response` 之后，所以不会阻塞用户看到回复。然而它仍然占用 Temporal Activity 的执行时间。

### 5.2 推荐：显式异步 retain

Hindsight 官方建议每轮结束时异步 retain（`async_=True`），将写入放入后台队列。

```python
# hindsight_client.py 改进
async def retain(
    self,
    events: list[dict],
    user_id: str,
    session_id: str,
    async_retain: bool = True,  # 默认异步
) -> int:
    ...
    result = await self._post(
        f"{_API_PREFIX}/{self._bank_id}/memories",
        {
            "items": items,
            "async": async_retain,
        },
    )
```

**注意：** Hindsight 的 `async_=True` 将提取任务放入后台，操作立刻返回，不会阻塞。结合 HpAgent 架构，这可以让 Temporal Activity 更快完成（降低超时风险），同时不丢记忆。

### 5.3 全量对话保留 vs 增量

Hindsight 设计假设 **每次 retain 发送完整的当前对话**（同一 `document_id` 实现覆盖更新）。它不推荐"只发送新消息"的增量追加模式（虽然支持 `update_mode: "append"`），因为重新处理全量对话时 LLM 能利用完整上下文提取更准确的事实。

**推荐：** 使用会话级 `document_id`（方案 A），每次 retain 时传入最近 N 轮对话（如 20 轮），Hindsight 会用新内容替换旧内容并重新提取。

### 5.4 batch retain

当一条消息产生多轮工具调用交互时（agentic loop），可以在 loop 结束后使用 `retain_batch` 一次性提交：

```python
# 对于 multi-agent 或长工具链场景
await client.retain_batch(
    bank_id="hpagent",
    items=[
        {
            "content": full_conversation_text,
            "document_id": f"session:{session_id}",
            "context": context_description,
            "timestamp": iso_timestamp,
            "tags": tags,
        }
    ],
    async_retain=True,
)
```

---

## 6. Recall 策略

### 6.1 查询纯净化

**问题：** 当前 recall query 直接使用 `user_content`（`runner.py:115,152`），在群聊场景中 `user_content` 可能包含 @机器人 等噪声前缀。

**改进：**

```python
# 在 recall 前清理查询
def _clean_recall_query(self, raw_content: str, channel_type: str) -> str:
    """去除 @提及、渠道特定前缀，只保留核心语义内容。"""
    import re
    cleaned = raw_content
    # 去除 @机器人 提及
    cleaned = re.sub(r'\[CQ:at,qq=\d+\]', '', cleaned)
    # 去除 CQ 码
    cleaned = re.sub(r'\[CQ:[^\]]+\]', '', cleaned)
    return cleaned.strip()
```

**不要做的事：**
- 不要把群名、频道名拼进 recall query —— 这会干扰语义检索。群/渠道信息应通过 `tags` 和 `context` 传递。
- 不要把消息发送者名称硬编码到查询中。

### 6.2 `query_timestamp` 使用

当用户查询涉及时间时（"上周我们讨论了什么？"），设置 `query_timestamp` 锚定时间窗口：

```python
await client.recall(
    query=cleaned_query,
    tags=recall_tags,
    tags_match="any_strict",
    query_timestamp=datetime.utcnow().isoformat() + "Z",
    budget="mid",
)
```

### 6.3 budget 选择

| 场景 | budget | 说明 |
|------|--------|------|
| 日常对话 | `mid`（默认） | 平衡覆盖面和延迟 |
| 高频工具调用 | `low` | 50-100ms，agentic loop 内多次调用 |
| 深度追溯（用户显式请求） | `high` | "帮我回想一下之前..." |

### 6.4 recall 结果使用

当前实现将 recall 结果格式化为 system prompt 段落（`recall_formatted()`），这符合 Hindsight 设计。推荐的 system prompt 注入格式：

```
# 相关记忆
- [experience] 用户偏好异步沟通
- [observation] 用户持续使用 Python 进行后端开发（5 条支持记忆，趋势：加强）

请参考以上记忆来个性化你的回复，但不要显式提及"你之前说过"等语句。
```

---

## 7. 时间戳标准化

### 7.1 问题

- NapCat 上报 `time` 字段为 Unix 秒级时间戳（`number`）
- Hindsight 要求 ISO 8601 格式：`"2025-06-01T10:30:00Z"`
- 当前 `hindsight_client.py` 在 retain 时 **未设置 `timestamp` 字段**，Hindsight 使用当前服务器时间，丢失原始时序信息

### 7.2 统一方案

**在 `normalize_message()` 层统一转换（收口）：**

```python
# napcat.py normalize_message() 增强
from datetime import datetime, timezone

def _to_iso_timestamp(unix_ts: int | float) -> str:
    """Unix 秒级时间戳 → ISO 8601 字符串。"""
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# 在 UnifiedMessage 中增加 iso_timestamp 字段，或直接作为 metadata 传入
metadata["iso_timestamp"] = _to_iso_timestamp(data["time"])
```

**在 retain 调用中传递 timestamp：**

```python
# hindsight_client.py retain() 改进
items.append({
    "content": content,
    "context": context_str,
    "document_id": f"session:{session_id}",
    "timestamp": event.get("iso_timestamp", ""),   # 新增
    "tags": item_tags,
    "metadata": {
        "role": role,
        "session_id": session_id,
        "sender_name": event.get("sender_name", ""),  # 新增
    },
})
```

### 7.3 内部时间格式约定

| 层级 | 格式 | 说明 |
|------|------|------|
| Event.timestamp | `float` (Unix epoch) | 内部存储，方便计算 |
| UnifiedMessage.timestamp | `float` (Unix epoch) | 同上 |
| Hindsight API timestamp | ISO 8601 `str` | API 合规 |
| Hindsight `query_timestamp` | ISO 8601 `str` | API 合规 |

---

## 8. 消息预处理与渠道兼容

### 8.1 `@机器人` 精准识别

多用户群聊中，只有真正 @ 机器人的消息才触发记忆操作。识别逻辑应在 NapCat 消息段解析层完成：

```python
# napcat.py normalize_message() 中
message_segments = data.get("message", [])
is_at_bot = False
if isinstance(message_segments, list):
    for seg in message_segments:
        if seg.get("type") == "at" and str(seg.get("data", {}).get("qq")) == str(self_id):
            is_at_bot = True
            break
```

在 worker 层（`handle_message`）决定是否进入完整 agentic loop + 记忆处理。

### 8.2 渠道降级

不同渠道的数据完整度差异：

| 字段 | NapCat | Console | Web |
|------|--------|---------|-----|
| `user_id` | `sender.user_id` | `"console-user"` | session cookie |
| `group_id` | `group_id` | — | — |
| `sender_name` | `sender.card` / `sender.nickname` | `"Console User"` | username |
| `message_id` | `message_id` (number) | UUID | UUID |
| `time` | Unix timestamp | `time.time()` | server timestamp |

处理逻辑应在 `normalize_message()` 中根据 `channel_type` 自动跳过不适用字段：

```python
if channel_type == "napcat":
    tags.append(f"group:{group_id}") if group_id else None
    tags.append(f"scope:{message_type}")  # private / group
elif channel_type == "console":
    tags.append("scope:console")
```

### 8.3 字段校验与容错

对 NapCat 上报的字段做防御性处理：

```python
def _safe_str(value: Any, default: str = "") -> str:
    """安全转为字符串，处理 None/空值。"""
    if value is None:
        return default
    return str(value)

# 使用示例
user_id = _safe_str(data.get("sender", {}).get("user_id"))
if not user_id:
    logger.warning("Message missing user_id, skipping retain")
    return None
```

---

## 9. 错误处理与监控

### 9.1 错误分类

当前 `hindsight_client.py._request()` 对所有异常统一返回 `None`，建议细分为：

| 错误类型 | 策略 | 重试 |
|---------|------|------|
| 超时 (`TimeoutException`) | 记录指标，返回空 | 不重试（已占用时间） |
| 限流 (429) | 退避重试 | 最多 2 次，指数退避 |
| 服务器错误 (5xx) | 退避重试 | 最多 2 次 |
| 客户端错误 (4xx) | 记录日志，不重试 | 不重试（参数问题） |
| 连接错误 | 标记降级 | 30s 后重试 |

### 9.2 关键指标

建议采集的指标（在 `hindsight_client.py` 的记录点）：

| 指标 | 位置 | 说明 |
|------|------|------|
| `hindsight.retain.success` | `retain()` 返回非 0 | 保留成功率 |
| `hindsight.retain.latency_ms` | `retain()` 耗时 | 保留延迟 |
| `hindsight.recall.success` | `recall()` 返回非空 | 召回命中率 |
| `hindsight.recall.latency_ms` | `recall()` 耗时 | 召回延迟 |
| `hindsight.degraded` | 任意方法降级时 | 降级次数 |

当前代码已有 `logger.warning("DEGRADATION: ...")` 日志约定，可在此基础上结构化输出。

### 9.3 降级对用户体验的影响

当 Hindsight 不可用时：
- **recall 降级** → 模型在当前对话上下文中工作，没有长期记忆注入（当前已实现：返回空 `""`）
- **retain 降级** → 本地 JSONL 备份文件仍然写入（`store.py:255` 已实现），可在恢复后重放
- **reflect 降级** → 跳过本次深度推理，下个周期重试

---

## 10. 隐私与合规

### 10.1 数据保留策略

| 数据类型 | 保留期限 | 清理方式 |
|---------|---------|---------|
| 活跃会话事件（Redis） | 24h (TTL) | 自动过期 |
| 长期记忆（Hindsight/pgvector） | 需明确策略 | API 删除 / 定期清理脚本 |
| 本地备份（JSONL） | 30 天 | `cleanup_max_age_days` 配置 |

### 10.2 用户控制

应提供的能力（参考 GDPR 等法规）：

- **数据导出**：用户可请求导出其所有记忆
- **数据删除**：用户可请求删除其所有记忆（调用 Hindsight delete API）
- **记忆可见性声明**：在用户首次交互时告知记忆功能的存在

### 10.3 access control

Hindsight 的 bank 级别隔离 + tags 过滤提供技术基础：

- 私聊记忆仅对发送者可见（`tags_match="any_strict"`）
- 群聊记忆对群成员可见（`tags` 含 `group:{id}`）
- 不跨 bank 共享数据

---

## 11. 实施清单（按优先级）

### P0 

- [ ] **添加 `timestamp` 字段**：在 `hindsight_client.py:retain()` 中为每条 item 设置 ISO 8601 格式的 `timestamp`
- [ ] **改进 `document_id` 设计**：从 `{session_id}-{i}`（逐条独立）改为 `session:{session_id}`（会话级全文替换）
- [ ] **丰富 `context` 字段**：从 `role=user` 改为包含渠道类型 + 群名/私聊描述的完整上下文
- [ ] **使用异步 retain**：`hindsight_client.py:retain()` 增加 `async_=True` 参数，Temporal Activity 更快完成

### P1 

- [ ] **扩展标签体系**：增加 `group:{id}`, `scope:{private|group}`, `channel:{napcat|console|web}`
- [ ] **recall 查询纯净化**：在 `runner.py` 中清理 @提及和 CQ 码后再传给 recall
- [ ] **明确 `tags_match` 策略**：私聊用 `any_strict`，群聊根据需要组合 `tag_groups`
- [ ] **传递 `query_timestamp`**：在 recall 时设置当前时间作为时序锚点

### P2 

- [ ] **字段校验与容错**：`normalize_message()` 层增加类型转换和默认值填补
- [ ] **多媒体消息处理**：图片 OCR / 语音转录 / 文件摘要 → 文本 → 传给记忆
- [ ] **`@机器人` 识别**：在 NapCat 消息段解析时精确判断是否 @ 自己
- [ ] **幂等校验**：基于 NapCat `message_id` 防止重复上报导致重复 retain
- [ ] **错误细分与重试**：将 `_request()` 中的异常按类型分支处理

### P3 

- [ ] **可观测性指标**：结构化日志 + 成功率/延迟统计
- [ ] **Bank 配置调优**：根据实际使用情况迭代 `retain_mission` 和 `reflect_mission`
- [ ] **历史数据迁移**：将旧格式（无 timestamp / 旧 document_id）的数据清理或迁移
- [ ] **reflect 定期调度**：确认 Temporal Schedule 已配置定期触发 `reflect_activity`

---

## 附录 A：Hindsight API 关键参数速查

| 操作 | 关键参数 | 说明 |
|------|---------|------|
| `retain` | `content`, `context`, `document_id`, `timestamp`, `tags`, `async_` | 写入记忆 |
| `recall` | `query`, `tags`, `tags_match`, `budget`, `max_tokens`, `query_timestamp`, `types`, `tag_groups` | 检索记忆 |
| `reflect` | `query`, `tags`, `budget`, `response_schema` | 深度推理 |

## 附录 B：相关文件索引

| 文件 | 职责 |
|------|------|
| `src/memory/hindsight_client.py` | Hindsight REST API 封装 |
| `src/session/store.py` | 会话存储 + 记忆委托层 |
| `src/harness/runner.py` | Agent loop 协调器（串联 recall/model/retain） |
| `src/harness/context_builder.py` | 上下文构建（含记忆注入 system prompt） |
| `src/sandbox/channels/napcat.py` | NapCat 消息标准化入口 |
| `src/orchestration/worker.py` | Worker 启动与依赖组装 |
| `src/orchestration/config.py` | 配置结构定义（含 HindsightConfig） |
| `docs/v8/napcat-api-reference.md` | NapCat API 参考手册 |
