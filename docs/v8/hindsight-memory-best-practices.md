# Hindsight 记忆模块重新设计

> 基于 Hindsight v0.6.1 最佳实践 + NapCat API 数据能力，逐层设计 NapCat → Hindsight 的完整信息传递方案。

---

## 一、问题诊断：当前数据流失链路

### 1.1 数据流全景（现状）

```
NapCat WebSocket (OneBot v11 JSON)                      
  │  字段: self_id, user_id, group_id, group_name,      
  │        message_type, sub_type, message_id, time,     
  │        sender: {user_id, nickname, card, role, ...}, 
  │        message: [消息段数组], raw_message, ...        
  │                                                      
  ▼                                                      
NapCatChannel.normalize_message()          ← 第1流失点  
  │  提取: sender_id, content(=raw_message),             
  │        metadata{post_type, detail_type, sub_type,     
  │                 group_id(仅群聊)}                      
  │  丢弃: sender.nickname, sender.card, sender.role,    
  │        group_name, message_id, self_id,               
  │        消息段结构(message[]), time                     
  │                                                      
  ▼                                                      
UnifiedMessage                             ← 载体本身字段不足
  │  sender_id, content, channel_type,                   
  │  metadata, timestamp                                 
  │                                                      
  ▼                                                      
Worker.handle_message() → user_message dict              
  │  透传: metadata, timestamp                            
  │                                                      
  ▼                                                      
HarnessRunner.process_turn()               ← 第2流失点  
  │  metadata = user_message["metadata"]  ← 提取了但不用  
  │  turn_events = [{"role":"user", "content":content}]   
  │  丢弃: metadata, timestamp, channel_type, sender_id   
  │                                                      
  ▼                                                      
HindsightClient.retain(events, user_id, session_id)       
  │  context = "role=user"                               
  │  tags = ["user:{id}", "session:{id}"]                
  │  metadata = {"role": role, "session_id": session_id}  
  │  document_id = "{session_id}-{i}"                    
  │  timestamp = 缺失                                      
  │                                                      
  ▼                                                      
Hindsight 服务端                                           
  → 记忆质量低下，无渠道/群组/时间维度                        
```

### 1.2 两个关键流失点

**流失点 1 — `napcat.py:129`**: `normalize_message()` 只提取了 `raw_message` 作为 content，丢弃了 `sender.nickname`、`sender.card`、`group_name`、`message_id`、消息段结构等。这些数据在原始 OneBot JSON 中**已经存在且免费**。

```python
# 当前: 只取文本
content = data.get("raw_message", "") or data.get("message", "")

# 丢弃了: sender.nickname, sender.card, group_name...
```

**流失点 2 — `runner.py:104`**: `process_turn()` 收到了完整的 `user_message`（含 `metadata`、`timestamp`），但构造 `turn_events` 时只取了 `content`。

```python
# 当前: metadata 提取了但从未交给 Hindsight
metadata = user_message["metadata"]  # line 81
...
turn_events = [{"role": "user", "content": user_content}]  # line 104, metadata 丢了
```

### 1.3 不改收发 API 的边界说明

以下接口**已经正常工作，不修改**：

| 接口 | 位置 | 原因 |
|------|------|------|
| `send_message()` | napcat.py:222-286 | 收发逻辑已验证正确 |
| `_handle_message()` | napcat.py:290-326 | WebSocket 处理正常 |
| `_main_logic()` | napcat.py:328-354 | 连接管理正常 |
| `send_group_msg` / `send_private_msg` API 调用 | napcat.py:256-274 | OneBot API 使用正确 |

以下接口**可以增强（只增字段不改逻辑）**：

| 接口 | 位置 | 增强方向 |
|------|------|---------|
| `normalize_message()` | napcat.py:73-220 | 提取更多字段到 UnifiedMessage |
| `UnifiedMessage` 数据类 | common/types.py:151-198 | 增加可选字段承载渠道富数据 |
| `handle_message()` | worker.py:260-313 | 传递新字段到 user_message dict |
| `process_turn()` | runner.py:63-240 | 构建 MemoryEvent 传递到 retain |
| `HindsightClient.retain()` | hindsight_client.py:162-220 | 接收富数据，生成完整 tags/metadata/context |

---

## 二、重新设计的架构

### 2.1 目标数据流

```
NapCat WebSocket (OneBot v11 JSON)
  │
  ▼
NapCatChannel.normalize_message()    [增强] 提取更多字段
  │  UnifiedMessage 新增字段:
  │  - sender_name, sender_card, sender_role
  │  - group_name, message_id, self_id
  │  - message_segments (消息段摘要)
  │  - platform_ts (OneBot time 字段)
  │
  ▼
Worker.handle_message()              [增强] 映射到 user_message dict
  │
  ▼
HarnessRunner.process_turn()         [增强] 构建 MemoryPayload
  │  MemoryPayload:
  │  - events (role+content+timestamp)
  │  - channel_context (完整渠道上下文)
  │  - tags (预计算的 tag 列表)
  │
  ▼
HindsightClient.retain()             [重写] 生成高质量 retain 请求
  │  context, tags, metadata, timestamp, document_id, observation_scopes
  │  全部从 MemoryPayload 生成
  │
  ▼
Hindsight 服务端
  → 高质量记忆，支持按渠道/群组/时间过滤
```

### 2.2 设计原则

1. **只增不删** — 所有新增字段为 Optional，不影响已有渠道（Console/Web）
2. **数据就近提取** — NapCat 特有字段在 `normalize_message()` 中提取，下游只透传
3. **收/发分离** — `normalize_message()` (收) 和 `send_message()` (发) 是独立路径，增强前者不影响后者
4. **渐进式** — 每一层独立增强，不依赖后续层的改动

---

## 三、Layer 1: NapCatChannel.normalize_message() 增强

### 3.1 当前提取 vs 可用数据

| OneBot JSON 字段 | 当前提取 | 建议提取 |
|-----------------|---------|---------|
| `raw_message` / `message` | ✓ (content) | ✓ |
| `sender.user_id` | ✓ (sender_id) | ✓ |
| `sender.nickname` | ✗ | **→ sender_name** |
| `sender.card` | ✗ | **→ sender_card** |
| `sender.role` | ✗ | **→ sender_role** |
| `group_name` | ✗ | **→ group_name** |
| `group_id` | ✓ (仅群聊) | ✓ |
| `message_id` | ✗ | **→ message_id** |
| `time` | ✗ | **→ platform_ts** |
| `self_id` | ✗ | **→ bot_id** |
| `message_type` | ✓ (metadata) | ✓ |
| `sub_type` | ✓ (metadata) | ✓ |
| `message[]` 结构 | ✗ | **→ segments_summary** |
| `message_seq` | ✗ | **→ message_seq** |

### 3.2 修改方案

在 `normalize_message()` 的消息事件分支（post_type == "message"）中，**增加**以下提取逻辑（不改动已有逻辑）：

```python
# ── 消息事件增强提取 (在 line 131 之后追加) ──
if post_type == "message":
    # ... 已有逻辑保持不变 ...

    # 新增: 发送者富信息
    sender_data = data.get("sender", {})
    sender_name = sender_data.get("nickname", "")
    sender_card = sender_data.get("card", "")
    sender_role = sender_data.get("role", "")

    # 新增: 群名称 (群聊时 OneBot 可能直接给)
    group_name = data.get("group_name", "")

    # 新增: 消息标识
    message_id = str(data.get("message_id", ""))
    message_seq = data.get("message_seq")
    self_id = str(data.get("self_id", ""))

    # 新增: 平台时间戳 (OneBot time 字段)
    platform_ts = data.get("time")

    # 新增: 消息段摘要 (提取非 text 段的类型列表 + text 段文本)
    raw_message_obj = data.get("message", "")
    segments_summary = _extract_segments_summary(raw_message_obj)
```

消息段摘要提取函数（纯工具函数，不影响收发）：

```python
def _extract_segments_summary(message: Any) -> dict:
    """从消息段数组中提取摘要信息。

    返回:
        {
            "text": "纯文本部分拼接",
            "types": ["text", "image", "at"],  # 出现的段类型
            "has_media": True,                  # 是否含图片/语音/视频/文件
            "at_bot": True,                     # 是否 @了机器人
            "reply_to": "12345",               # 引用回复的消息ID
            "image_count": 2,
            "record_count": 0,
        }
    """
    if isinstance(message, str):
        return {"text": message, "types": ["text"], "has_media": False,
                "at_bot": False, "reply_to": "", "image_count": 0, "record_count": 0}

    if not isinstance(message, list):
        return {"text": str(message), "types": [], "has_media": False,
                "at_bot": False, "reply_to": "", "image_count": 0, "record_count": 0}

    texts = []
    types = []
    has_media = False
    at_bot = False
    reply_to = ""
    image_count = 0
    record_count = 0

    for seg in message:
        if not isinstance(seg, dict):
            continue
        stype = seg.get("type", "")
        types.append(stype)
        sdata = seg.get("data", {})

        if stype == "text":
            texts.append(sdata.get("text", ""))
        elif stype == "image":
            image_count += 1
            has_media = True
        elif stype == "record":
            record_count += 1
            has_media = True
        elif stype in ("video", "file"):
            has_media = True
        elif stype == "at":
            qq = str(sdata.get("qq", ""))
            # 判断是否 @了机器人 (后续由 bot_id 校验)
            if qq:
                at_bot = True  # 先标记，后续用 bot_id 精确判断
        elif stype == "reply":
            reply_to = str(sdata.get("id", "") or sdata.get("seq", ""))

    return {
        "text": "".join(texts),
        "types": types,
        "has_media": has_media,
        "at_bot": at_bot,
        "reply_to": reply_to,
        "image_count": image_count,
        "record_count": record_count,
    }
```

### 3.3 不影响收发 API 的证明

- `normalize_message()` 只被 `_handle_message()` 调用（接收路径）
- `send_message()` 读取 `UnifiedMessage.metadata["detail_type"]` 和 `metadata["group_id"]`——这些字段未变
- `send_message()` 不读取我们将要新增的任何字段
- 增强后的 `UnifiedMessage` 只是携带了更多数据，`send_message()` 不使用它们

---

## 四、Layer 2: UnifiedMessage 增强

### 4.1 新增字段（全部 Optional，兼容 Console/Web 渠道）

```python
@dataclass
class UnifiedMessage:
    # ── 已有字段（不变）──
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    account_id: str = ""
    sender_id: str = ""
    channel_type: ChannelType = ChannelType.CONSOLE
    content: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    media_urls: List[str] = field(default_factory=list)

    # ── 新增: NapCat 发送者富信息 ──
    sender_name: str = ""          # sender.nickname — QQ 昵称
    sender_card: str = ""          # sender.card — 群名片（群聊时）
    sender_role: str = ""          # sender.role — owner/admin/member

    # ── 新增: 会话上下文 ──
    group_id: str = ""             # 群号（从 metadata 提升到顶层，方便下游直接读取）
    group_name: str = ""           # 群名称

    # ── 新增: 消息标识 ──
    platform_message_id: str = ""  # OneBot message_id（不同于系统 message_id）
    platform_ts: float = 0.0       # OneBot time 字段（平台时间戳）
    bot_id: str = ""               # self_id — 机器人 QQ 号

    # ── 新增: 消息段摘要 ──
    segment_types: List[str] = field(default_factory=list)   # ["text", "image", "at"]
    has_media: bool = False
    at_bot: bool = False
    reply_to_msg_id: str = ""                                # 被引用的消息ID
```

### 4.2 兼容性保证

- 所有新增字段有默认值 → Console/Web 渠道构造 `UnifiedMessage` 时无需传入
- `to_event()` 方法可选增强：将新增字段写入 `Event.content`（使事件历史也带上渠道上下文）
- `send_message()` 不依赖任何新增字段 → 发送路径零影响

---

## 五、Layer 3: Worker.handle_message() 增强（透传层）

### 5.1 当前代码（worker.py:271-280）

```python
user_message = {
    "content": message.content,
    "sender_id": message.sender_id,
    "channel_type": ch_type,
    "session_id": session_id,
    "account_id": account_id,
    "metadata": message.metadata,      # ← 已有
    "timestamp": message.timestamp,    # ← 已有
}
```

### 5.2 增强：透传新增字段

```python
user_message = {
    # 已有
    "content": message.content,
    "sender_id": message.sender_id,
    "channel_type": ch_type,
    "session_id": session_id,
    "account_id": account_id,
    "metadata": message.metadata,
    "timestamp": message.timestamp,

    # 新增: 发送者富信息
    "sender_name": message.sender_name,
    "sender_card": message.sender_card,
    "sender_role": message.sender_role,

    # 新增: 群组上下文
    "group_id": message.group_id or message.metadata.get("group_id", ""),
    "group_name": message.group_name,

    # 新增: 消息标识
    "platform_message_id": message.platform_message_id,
    "platform_ts": message.platform_ts,
    "bot_id": message.bot_id,

    # 新增: 消息段摘要
    "segment_types": message.segment_types,
    "has_media": message.has_media,
    "at_bot": message.at_bot,
    "reply_to_msg_id": message.reply_to_msg_id,
}
```

### 5.3 下游兼容：dict key 不存在时回退

所有下游代码通过 `.get()` 读取这些字段，缺失时使用空字符串默认值。无需修改 Temporal Workflow 定义（Temporal 接受任意 dict）。

---

## 六、Layer 4: HarnessRunner 接入 MemoryPayload

### 6.1 核心概念：MemoryPayload

```
MemoryPayload — 从 user_message 中提取的完整记忆上下文

  包含:
  - events:     [{"role":"user", "content":"...", "timestamp":"..."}, ...]
  - channel_context:  {"channel_type":"napcat", "platform":"qq",
                        "scope":"group", "group_id":"123456", 
                        "group_name":"xxx", "sender_name":"昵称", ...}
  - session_context:  {"session_id":"...", "account_id":"..."}
```

### 6.2 process_turn() 修改

```python
async def process_turn(self, user_message: Dict[str, Any]) -> Dict[str, Any]:
    # ... 已有初始化代码 ...

    # ── 构建 MemoryPayload（新增） ──
    memory_payload = {
        "channel_context": {
            "channel_type": channel_type_str,
            "platform": "qq" if channel_type_str == "napcat" else channel_type_str,
            "scope": _derive_scope(user_message),
            "group_id": user_message.get("group_id", ""),
            "group_name": user_message.get("group_name", ""),
            "sender_name": user_message.get("sender_name", ""),
            "sender_card": user_message.get("sender_card", ""),
            "sender_role": user_message.get("sender_role", ""),
            "bot_id": user_message.get("bot_id", ""),
            "segment_types": user_message.get("segment_types", []),
            "has_media": user_message.get("has_media", False),
            "reply_to_msg_id": user_message.get("reply_to_msg_id", ""),
        },
        "session_context": {
            "session_id": session_id,
            "account_id": account_id,
        },
    }

    # ... 已有的事件构建逻辑 ...

    # 构建 turn_events 时——保持原有结构，增加 timestamp
    turn_events: List[Dict[str, Any]] = [
        {
            "role": "user",
            "content": user_content,
            "timestamp": user_message.get("platform_ts") or user_message.get("timestamp"),
        }
    ]

    # ... 已有的模型调用 + 工具执行逻辑 ...

    # ── retain 时传入 MemoryPayload ──
    await self._session.retain_memories(
        turn_events, account_id, session_id,
        memory_payload=memory_payload,  # ← 新增参数
    )
```

`_derive_scope()` 辅助函数：

```python
def _derive_scope(user_message: dict) -> str:
    """从 metadata 推导对话范围。"""
    meta = user_message.get("metadata", {})
    detail_type = meta.get("detail_type", "")
    if detail_type == "group":
        return "group"
    elif detail_type == "guild":
        return "guild"
    elif detail_type in ("private", "friend"):
        return "private"
    # 从 channel_type 推断
    if user_message.get("group_id"):
        return "group"
    return "private"
```

---

## 七、Layer 5: SessionStore + HindsightClient.retain() 重写

### 7.1 SessionStore.retain_memories() 扩展

```python
async def retain_memories(
    self,
    events: list[dict],
    account_id: str,
    session_id: str,
    memory_payload: dict | None = None,  # ← 新增可选参数
) -> int:
    count = 0
    if self._hindsight:
        try:
            count = await self._hindsight.retain(
                events, account_id, session_id,
                memory_payload=memory_payload,  # ← 透传
            )
        except Exception as e:
            logger.warning(...)
    await self._backup_to_file(session_id, None, events)
    return count
```

### 7.2 HindsightClient.retain() 重写

这是改动最大的部分。当前 `retain()` 只接受 `(events, user_id, session_id)`，现在需要接受 `memory_payload` 并据此生成完整的 Hindsight retain 请求。

```python
async def retain(
    self,
    events: List[Dict[str, Any]],
    user_id: str,
    session_id: str,
    memory_payload: Optional[Dict[str, Any]] = None,
) -> int:
    if not await self._ensure_bank():
        return 0

    # ── 从 memory_payload 中提取渠道上下文 ──
    cc = (memory_payload or {}).get("channel_context", {})
    sc = (memory_payload or {}).get("session_context", {})

    channel_type = cc.get("channel_type", "")
    platform = cc.get("platform", "")
    scope = cc.get("scope", "")
    group_id = cc.get("group_id", "")
    group_name = cc.get("group_name", "")
    sender_name = cc.get("sender_name", "")
    sender_card = cc.get("sender_card", "")
    sender_role = cc.get("sender_role", "")

    # ── 1. 构建 tags ──
    tags = [
        f"user:{user_id}",
        f"session:{session_id}",
        f"channel:{channel_type}",
        f"scope:{scope}",
    ]
    if group_id:
        tags.append(f"group:{group_id}")
    if platform:
        tags.append(f"platform:{platform}")

    # ── 2. 构建 context ──
    display_name = sender_card or sender_name or "unknown"
    if scope == "group" and group_name:
        context = (
            f"QQ group chat, group "{group_name}" ({group_id}), "
            f"sender: {display_name} ({sender_role or 'member'})"
        )
    elif scope == "group":
        context = (
            f"QQ group chat, group_id={group_id}, "
            f"sender: {display_name}"
        )
    elif scope == "private":
        context = f"QQ private chat, sender: {display_name}"
    elif scope == "guild":
        context = f"QQ guild channel, sender: {display_name}"
    else:
        context = f"QQ chat, sender: {display_name}"

    # ── 3. 构建 metadata ──
    metadata = {
        "platform": platform,
        "channel_type": channel_type,
        "scope": scope,
        "session_id": session_id,
    }
    if group_id:
        metadata["group_id"] = group_id
    if group_name:
        metadata["group_name"] = group_name
    if sender_name:
        metadata["sender_name"] = sender_name
    if sender_card:
        metadata["sender_card"] = sender_card
    if sender_role:
        metadata["sender_role"] = sender_role

    # ── 4. 构建 items ──
    # 关键变更: 整个 conversation 作为 1 条 item（而非 N 条独立事件）
    # 每条事件格式化为带时间戳的行
    conversation_lines = []
    for event in events:
        role = event.get("role", "user")
        content = event.get("content", "")
        ts = event.get("timestamp")
        if not content:
            continue
        if ts:
            # 将 Unix timestamp 转为 ISO 8601
            from datetime import datetime, timezone
            ts_iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            conversation_lines.append(f"[{ts_iso}] {role}: {content}")
        else:
            conversation_lines.append(f"{role}: {content}")

    if not conversation_lines:
        return 0

    conversation_text = "\n".join(conversation_lines)

    # 取第一条 event 的时间戳作为整个 document 的时间锚点
    doc_timestamp = events[0].get("timestamp") if events else None
    if doc_timestamp:
        from datetime import datetime, timezone
        doc_ts_iso = datetime.fromtimestamp(
            float(doc_timestamp), tz=timezone.utc
        ).isoformat()
    else:
        doc_ts_iso = None

    # ── 5. 构建 observation_scopes ──
    observation_scopes = _build_observation_scopes(user_id, channel_type, scope, group_id)

    items = [{
        "content": conversation_text,
        "context": context,
        "document_id": f"session:{session_id}",
        "tags": tags,
        "metadata": metadata,
        "update_mode": "append",
    }]
    if doc_ts_iso:
        items[0]["timestamp"] = doc_ts_iso

    result = await self._post(
        f"{_API_PREFIX}/{self._bank_id}/memories",
        {
            "items": items,
            "observation_scopes": observation_scopes,
        },
    )
    if result is None:
        return 0
    return result.get("items_count", 0)
```

### 7.3 observation_scopes 构建

```python
def _build_observation_scopes(
    user_id: str, channel_type: str, scope: str, group_id: str
) -> list[list[str]]:
    """根据渠道上下文构建多维度 observation scopes。"""
    scopes = [
        [f"user:{user_id}"],                         # 用户级
    ]
    if channel_type:
        scopes.append([f"channel:{channel_type}"])    # 渠道级
    if scope:
        scopes.append([f"scope:{scope}"])             # 对话范围级
    if group_id:
        scopes.append([f"group:{group_id}"])          # 群组级
        scopes.append([f"user:{user_id}", f"group:{group_id}"])  # 用户+群组

    return scopes
```

### 7.4 关键变更总结

| 维度 | 当前 | 新设计 |
|------|------|--------|
| 每条 event 一个 item | ✓ | ✗ — 整个 turn 1 条 item |
| document_id | `"{sess}-{i}"` | `"session:{sess}"` (稳定 upsert) |
| update_mode | 未设 (默认 replace) | `"append"` (增量追加) |
| context | `"role=user"` | `"QQ group chat, group "xxx", sender: 昵称"` |
| tags | 2个 | 5-6个 (user/session/channel/scope/group/platform) |
| metadata | 2字段 | 8+字段 (含 platform/scope/group_name/sender_name 等) |
| timestamp | 缺失 | ISO 8601 从平台时间戳转换 |
| observation_scopes | 未设 | custom 多维度 |

---

## 八、Layer 6: Recall 增强

### 8.1 当前 recall 调用

```python
# runner.py line 114 / 151
items = await self._hindsight.recall(query, account_id, session_id, top_n)
```

### 8.2 增强：携带场景标签 + 富化查询

```python
async def recall(
    self,
    query: str,
    user_id: str,
    session_id: str = "",
    top_n: int = 5,
    recall_context: Optional[Dict[str, Any]] = None,  # ← 新增
) -> List[MemoryItem]:
    if not await self._ensure_bank():
        return []

    # 构建查询标签: user 必须匹配，限制在当前渠道范围
    tags = [f"user:{user_id}"]
    if recall_context:
        channel_type = recall_context.get("channel_type", "")
        scope = recall_context.get("scope", "")
        group_id = recall_context.get("group_id", "")
        if channel_type:
            tags.append(f"channel:{channel_type}")

    # 富化 query
    enriched_query = query
    if recall_context:
        scope = recall_context.get("scope", "")
        group_name = recall_context.get("group_name", "")
        if scope == "group" and group_name:
            enriched_query = (
                f"{query} [Context: QQ group chat "{group_name}"]"
            )
        elif scope == "private":
            enriched_query = f"{query} [Context: QQ private chat]"

    # 选择合适的 budget: 群聊噪声大，用 high
    budget = "high" if (recall_context or {}).get("scope") == "group" else "mid"

    result = await self._post(
        f"{_API_PREFIX}/{self._bank_id}/memories/recall",
        {
            "query": enriched_query,
            "tags": tags,
            "tags_match": "any_strict",     # ← 严格隔离，不含 untagged
            "max_tokens": 4096,
            "budget": budget,
        },
    )
    # ... 后续不变 ...
```

### 8.3 Runner 中 recall 调用增强

```python
# 构建 recall_context（与 memory_payload 的 channel_context 相同）
recall_context = memory_payload["channel_context"]

items, formatted = await self._session.recall_memories(
    query=user_content,
    account_id=account_id,
    session_id=session_id,
    top_n=5,
    recall_context=recall_context,  # ← 新增
)
```

---

## 九、完整字段映射表

### 9.1 OneBot JSON → UnifiedMessage → Hindsight

```
OneBot JSON                              UnifiedMessage              Hindsight retain
─────────────────────────────────────────────────────────────────────────────────────
self_id                              →   bot_id                   →   metadata[bot_id]
user_id                              →   sender_id
sender.user_id                       →   sender_id
sender.nickname                      →   sender_name              →   context, metadata[sender_name]
sender.card                          →   sender_card              →   context, metadata[sender_card]
sender.role                          →   sender_role              →   metadata[sender_role]
group_id                             →   group_id (顶层+metadata)  →   tags[group:{id}], metadata[group_id]
group_name                           →   group_name               →   context, metadata[group_name]
message_type                         →   metadata[detail_type]    →   tags[scope:{type}]
sub_type                             →   metadata[sub_type]
message_id                           →   platform_message_id
time (Unix ts)                       →   platform_ts              →   timestamp (ISO 8601)
raw_message                          →   content
message[] (数组)                     →   segment_types,           →   (用于判断消息类型,
                                          has_media, at_bot           非文本不提取记忆)
post_type                            →   metadata[post_type]
```

### 9.2 Hindsight 各字段的生成规则

| Hindsight 字段 | 生成规则 |
|---------------|---------|
| `content` | 整个 turn 格式化为 `[ISO_ts] role: content` 的多行文本 |
| `context` | `"QQ {scope} chat, {group/sender} context"` |
| `document_id` | `"session:{session_id}"` — 稳定 key，append 模式 |
| `tags` | `[user:{uid}, session:{sid}, channel:{type}, scope:{private\|group}, group:{gid}?, platform:qq]` |
| `metadata` | `{platform, channel_type, scope, session_id, group_id?, group_name?, sender_name?, sender_card?, sender_role?}` |
| `timestamp` | ISO 8601，从 `platform_ts` 转换 |
| `observation_scopes` | `[[user:{uid}], [channel:{type}], [scope:{s}], [group:{gid}?], [user:{uid},group:{gid}?]]` |

---

## 十、Bank 配置优化

### 10.1 retain_mission 领域化

```python
retain_mission = (
    "You are extracting memories from QQ chat conversations. "
    "The context field tells you whether this is a private chat, group chat, "
    "or guild channel — use this to correctly scope the information.\n\n"
    "EXTRACT:\n"
    "1. User preferences — communication style, formality, response preferences.\n"
    "2. Personal context — ongoing projects, deadlines, relationships, domain knowledge.\n"
    "3. Technical decisions — tools mentioned, workflows, APIs discussed.\n"
    "4. Stated facts — explicit declarations about the user ('I'm a backend dev', 'I use Vim').\n"
    "5. Group role — if in a group chat, the user's role and relationship to the group.\n"
    "6. Cross-channel patterns — note if preferences differ between private and group chats.\n\n"
    "IGNORE:\n"
    "- Greetings, small talk, pure emoji/表情 messages.\n"
    "- System messages, bot commands, format-only messages.\n"
    "- Pure acknowledgement ('ok', 'thanks', 'got it').\n"
    "- Sticker-only messages, image-only messages without text context."
)

observations_mission = (
    "Synthesize durable patterns from QQ chat interactions. "
    "Identify:\n"
    "- Evolving user preferences across conversations.\n"
    "- Behavioral differences between private and group chat contexts.\n"
    "- The user's role and engagement patterns in each group.\n"
    "- Recurring topics or requests the user brings up.\n"
    "- Tools and workflows the user consistently prefers.\n"
    "Flag contradictions with prior observations. "
    "Focus on durable patterns — not transient states."
)
```

### 10.2 Entity Labels

```json
{
  "entity_labels": [
    {
      "key": "interaction_scope",
      "description": "The conversation scope of the interaction",
      "type": "value",
      "tag": true,
      "values": [
        {"value": "qq_private", "description": "QQ private (friend) chat"},
        {"value": "qq_group", "description": "QQ group chat"},
        {"value": "qq_guild", "description": "QQ guild channel"}
      ]
    },
    {
      "key": "message_content_type",
      "description": "Dominant content type of the message",
      "type": "value",
      "values": [
        {"value": "text_only", "description": "Pure text message"},
        {"value": "mixed_media", "description": "Text with images/files"},
        {"value": "media_only", "description": "Image/file without text context"}
      ]
    }
  ]
}
```

### 10.3 Dispositions

```python
dispositions = {
    "skepticism": 3,   # 适度质疑 — 用户偏好可能变化
    "literalism": 2,   # 灵活理解 — 对话风格多样
    "empathy": 4,      # 温暖个性化 — QQ 聊天场景
}
```

---

## 十一、Reflect 与 Mental Models 策略

### 11.1 Reflect 调度

在 `activities.py` 中增加定时 reflect：

```
调度策略                      触发条件
─────────────────────────────────────────
每次会话结束后                异步 reflect(account_id)
每 10 次 retain 后           累计计数 → reflect
跨维度 reflect               reflect_by_scope(account_id, "group")
```

### 11.2 Mental Models 建议

系统成熟后（observation 体系运行 1-2 周），创建：

```python
# 用户综合画像
create_mental_model(
    bank_id="hpagent",
    name=f"User Profile - {account_id}",
    source_query=(
        f"Summarize user's technical background, preferred tools, "
        "communication style, ongoing projects, and key preferences. "
        "Note differences between private and group chat behavior."
    ),
    tags=[f"user:{account_id}"],
    trigger={"refresh_after_consolidation": True},
)

# QQ 渠道行为画像
create_mental_model(
    bank_id="hpagent",
    name=f"QQ Behavior - {account_id}",
    source_query=(
        f"Summarize user's interaction patterns on QQ: active groups, "
        "role in each group, typical topics discussed, frequency of interaction."
    ),
    tags=[f"user:{account_id}", "channel:napcat"],
)
```

---

## 十二、实施优先级

| 优先级 | 改动 | 涉及文件 | 风险 | 收益 |
|--------|------|---------|------|------|
| **P0** | UnifiedMessage 新增字段 | `common/types.py` | 低 (仅加 Optional 字段) | 打通全链路数据通道 |
| **P0** | normalize_message() 增强提取 | `sandbox/channels/napcat.py` | 低 (只增不改) | 让数据进入系统 |
| **P0** | worker.handle_message() 透传 | `orchestration/worker.py` | 低 (透传 dict) | 数据到达 runner |
| **P0** | HindsightClient.retain() 重写 | `memory/hindsight_client.py` | 中 (核心逻辑变更) | 记忆质量质变 |
| **P0** | HindsightClient.recall() 增强 | `memory/hindsight_client.py` | 低 (加参数) | 检索精度提高 |
| **P1** | process_turn() 构建 MemoryPayload | `harness/runner.py` | 低 (只加不删) | P0 的上游配套 |
| **P1** | SessionStore 扩展 | `session/store.py` | 低 (新增可选参数) | P0 的下游配套 |
| **P1** | Bank 配置更新 (mission/dispositions) | `_ensure_bank()` | 低 (配置文本) | 提取精度提高 |
| **P2** | Entity Labels 配置 | Bank 配置 API | 低 | 硬过滤能力 |
| **P2** | Observation scopes 多维度 | retain 调用时 | 低 | 跨维度模式分析 |
| **P3** | Reflect 定期调度 | `activities.py` + Temporal Schedule | 中 | 深度洞察 |
| **P3** | Mental Models | Bank 管理 API | 低 | 延迟优化 |

### 12.1 不影响收发 API 的验证清单

- [x] `send_message()` — 不读取任何新增字段
- [x] `send_group_msg` / `send_private_msg` — payload 不变
- [x] `_handle_message()` — 回调签名不变，只多带了数据
- [x] `_main_logic()` — WebSocket 连接管理不变
- [x] `normalize_message()` — 返回类型仍是 `UnifiedMessage`，只是多了字段值
- [x] `to_event()` — Event.content dict 可扩展，解析时用 `.get()` 兼容旧格式
- [x] Temporal Workflow 入参 — dict 传递，新增 key 不影响已有逻辑
- [x] Console/Web 渠道 — UnifiedMessage 新增字段全有默认值

---

## 附录 A: 关键代码位置索引

| 文件 | 行号 | 改动类型 |
|------|------|---------|
| `src/common/types.py` | 151-198 | UnifiedMessage 增加可选字段 |
| `src/sandbox/channels/napcat.py` | 73-220 | normalize_message() 增加字段提取 |
| `src/sandbox/channels/napcat.py` | (新) | 添加 _extract_segments_summary() 工具函数 |
| `src/orchestration/worker.py` | 271-280 | handle_message() 透传新字段 |
| `src/harness/runner.py` | 63-240 | process_turn() 构建 MemoryPayload |
| `src/memory/hindsight_client.py` | 162-220 | retain() 重写 |
| `src/memory/hindsight_client.py` | 226-269 | recall() 增强 |
| `src/memory/hindsight_client.py` | 133-156 | _ensure_bank() 更新 mission |
| `src/session/store.py` | 233-256 | retain_memories() 扩展参数 |

## 附录 B: 验证方法

1. **单元测试**: `_extract_segments_summary()` 对每种消息段类型验证
2. **集成测试**: 发送 OneBot JSON → 检查 UnifiedMessage 字段完整性
3. **端到端测试**: 发送真实 QQ 消息 → 检查 Hindsight 中存储的 tags/metadata/context
4. **回归测试**: Console 渠道发送消息 → 确认不受影响
5. **Recall 验证**: 同一用户私聊和群聊分别查询 → 确认 `tags_match="any_strict"` 正确隔离
