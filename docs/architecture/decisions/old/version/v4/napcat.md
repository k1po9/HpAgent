好的，我们先来梳理完整的 OneBot v11 事件体系，然后调整 `normalize_message` 方法。  

你的 `ChannelType` 枚举是消息来源渠道（**从哪个入口接入**），不是聊天场景（群聊/私聊），所以标准化时全部标记为 `NAPCAT` 是正确的。  

---

## 1. OneBot v11 完整事件类型定义

### 1.1 消息事件 (post_type: `message`)

| message_type | sub_type 可能值 | 说明 |
|---|---|---|
| `private` | `friend` / `group` / `other` | 私聊消息 |
| `group` | `normal` / `anonymous` / `notice` | 群聊消息 |
| `guild` | — | **NapCat 扩展**：频道消息（留白） |

---

### 1.2 通知事件 (post_type: `notice`)

| notice_type | sub_type 可能值 | 说明 |
|---|---|---|
| `group_upload` | — | 群文件上传 |
| `group_admin` | `set` / `unset` | 群管理员变动 |
| `group_decrease` | `leave` / `kick` / `kick_me` | 群成员减少 |
| `group_increase` | `approve` / `invite` | 群成员增加 |
| `group_ban` | `ban` / `lift_ban` | 群禁言 |
| `friend_add` | — | 好友添加 |
| `group_recall` | — | 群消息撤回 |
| `friend_recall` | — | 好友消息撤回 |
| `poke` | — | 戳一戳 |
| `lucky_king` | — | 运气王 |
| `honor` | — | 群荣誉变更 |
| `group_card` | — | 群名片变更 |
| `offline_file` | — | 离线文件上传 |
| `client_status` | — | 客户端状态变更 |
| `notify` | — | **NapCat 扩展**：其他通知（留白） |

---

### 1.3 请求事件 (post_type: `request`)

| request_type | sub_type 可能值 | 说明 |
|---|---|---|
| `friend` | — | 加好友请求 |
| `group` | `add` / `invite` | 加群请求 / 邀请登录号入群 |

---

### 1.4 元事件 (post_type: `meta_event`)

| meta_event_type | sub_type 可能值 | 说明 |
|---|---|---|
| `lifecycle` | `enable` / `disable` / `connect` | OneBot 启用、停用、WebSocket 连接成功，`connect` 仅在正向/反向 WebSocket 下触发 |
| `heartbeat` | — | 心跳包，定时发送，包含状态信息，默认关闭，间隔 15000ms |

---

## 2. 修改后的 `normalize_message`

```python
async def normalize_message(self, raw_message: Any) -> UnifiedMessage:
    """
    将 NapCat/OneBot v11 WebSocket 上报的各类事件标准化为 UnifiedMessage。

    根据 OneBot v11 标准，post_type 分为四类：
      - message    消息事件（私聊/群聊/频道）
      - notice     通知事件（群文件上传、管理员变动、成员增减、禁言、好友添加等）
      - request    请求事件（加好友、加群/邀请入群）
      - meta_event 元事件（生命周期、心跳）
    
    Args:
        raw_message: 原始数据，可以是 JSON 字符串或已解析的 dict

    Returns:
        统一格式的 UnifiedMessage 对象，其中 channel_type 统一为 NAPCAT，
        metadata 中包含 post_type、detail_type、sub_type 等原始上下文。
    """
    # —— 1. 安全解析 JSON ——
    if isinstance(raw_message, str):
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            raise ValueError(f"NapCat protocol error: Invalid JSON message: {raw_message}")
    elif isinstance(raw_message, dict):
        data = raw_message
    else:
        raise ValueError(f"Unsupported message type: {type(raw_message)}")

    post_type = data.get("post_type")
    if not post_type:
        raise ValueError("Missing 'post_type' in NapCat event")

    # —— 2. 初始化元数据与提取字段 ——
    metadata: dict[str, Any] = {
        "post_type": post_type,
        "detail_type": None,      # 具体子类型，如 private/group/group_upload
        "sub_type": None,         # 更细致的子类型，如 friend/kick/enable
    }

    sender_id = ""                # 触发事件的用户标识（发送者/操作者/申请者）
    content = ""                  # 文本内容，消息事件有值，其他事件通常为空字符串
    channel_type = ChannelType.NAPCAT  # 所有事件均来源于 NapCat 通道

    # —— 3. 根据 post_type 分支处理 ——
    if post_type == "message":
        message_type = data.get("message_type", "")
        metadata["detail_type"] = message_type
        metadata["sub_type"] = data.get("sub_type", "")
        
        # 提取发送者 ID 和消息内容
        sender_id = str(data.get("sender", {}).get("user_id", ""))
        content = data.get("raw_message", "") or data.get("message", "")

        # 群聊 —— 保存群 ID
        if message_type == "group":
            group_id = data.get("group_id")
            if group_id is not None:
                metadata["group_id"] = group_id
        # NapCat 扩展：频道消息（guild）—— 预留字段但暂不做专项处理
        elif message_type == "guild":
            metadata["guild_id"] = data.get("guild_id")
            metadata["channel_id"] = data.get("channel_id")

    elif post_type == "notice":
        notice_type = data.get("notice_type", "")
        metadata["detail_type"] = notice_type
        metadata["sub_type"] = data.get("sub_type", "")

        # 绝大多数通知事件都围绕群展开
        # 统一提取 group_id：来源于群的通知事件都会带有 group_id
        group_id = data.get("group_id")
        if group_id is not None:
            metadata["group_id"] = group_id

        # 根据具体 notice_type 提取不同的 sender_id 和 content
        if notice_type in ("group_upload",):
            # 群文件上传：user_id 是上传者
            sender_id = str(data.get("user_id", ""))
        elif notice_type in ("group_admin",):
            # 群管理员变动：user_id 是被设置/取消管理员的成员
            sender_id = str(data.get("user_id", ""))
        elif notice_type in ("group_decrease", "group_increase", "group_ban"):
            # 成员减少/增加/禁言：operator_id 是操作者，user_id 是目标
            # 以操作者作为 sender_id 更符合“谁触发了事件”的语义
            sender_id = str(data.get("operator_id", "") or data.get("user_id", ""))
        elif notice_type == "group_recall":
            # 群消息撤回：operator_id 是撤回者，user_id 是原消息发送者
            sender_id = str(data.get("operator_id", ""))
            msg_id = data.get("message_id")
            if msg_id is not None:
                content = f"msg_id:{msg_id}"
        elif notice_type == "poke":
            # 戳一戳：user_id 是发起者，target_id 是被戳对象
            sender_id = str(data.get("user_id", ""))
            content = str(data.get("target_id", ""))
        elif notice_type in ("friend_add", "friend_recall", "client_status",
                             "honor", "lucky_king", "group_card", "offline_file"):
            # 好友 / 个人状态等相关事件（sender_id 直接取 user_id）
            sender_id = str(data.get("user_id", ""))
        else:
            # 未明确列出的通知类型（如 NapCat 扩展的 notify 等）
            # 尽量提取 user_id，content 留空
            sender_id = str(data.get("user_id", ""))

        # content 为空字符串是常态，上层可通过 metadata 自行解析

    elif post_type == "request":
        request_type = data.get("request_type", "")
        metadata["detail_type"] = request_type
        metadata["sub_type"] = data.get("sub_type", "")

        # 请求事件：user_id 是发送请求的 QQ 号
        sender_id = str(data.get("user_id", ""))
        content = data.get("comment", "")  # 验证信息

        # 群请求需要保存 group_id
        if request_type == "group":
            group_id = data.get("group_id")
            if group_id is not None:
                metadata["group_id"] = group_id
            # 如果是邀请登录号入群，sub_type 为 "invite"

        # 保存 flag，后续处理请求（同意/拒绝）时需要用到
        metadata["flag"] = data.get("flag", "")

    elif post_type == "meta_event":
        meta_event_type = data.get("meta_event_type", "")
        metadata["detail_type"] = meta_event_type
        metadata["sub_type"] = data.get("sub_type", "")

        # 元事件无外部触发者，使用机器人自身 QQ 占位
        sender_id = str(data.get("self_id", ""))
        content = ""  # 默认无文本

        # 如果后续需要分析心跳状态，可在此组装
        if meta_event_type == "heartbeat":
            # status 字段结构与 get_status API 返回值相同
            # 暂不处理，按需可由上层从 data["status"] 中读取
            pass

    else:
        # 未来可能新增的 post_type，保证不崩溃
        metadata["detail_type"] = post_type
        logger.warning(f"Unknown post_type: {post_type}, treating as generic event")

    # —— 4. 构建并返回统一消息 ——
    channel_message = ChannelMessage(
        sender_id=sender_id,
        content=content,
        channel_type=channel_type,
        metadata=metadata,
    )

    return channel_message.to_unified_message(session_id="")
```

---

## 3. 补充说明

| 要点 | 说明 |
|---|---|
| **`channel_type`** | 统一填 `ChannelType.NAPCAT`，因为所有事件都从 NapCat 接入。群聊/私聊等业务场景维度通过 `metadata["detail_type"]` 体现。 |
| **`sender_id`** | 遵循 OneBot 语义：消息事件取 `sender.user_id`；通知事件优先取 `operator_id`，无则取 `user_id`；请求事件取 `user_id`；元事件取 `self_id`。 |
| **`content`** | 消息事件保留原始文本；大多数通知/元事件留空；撤回事件填充 `msg_id`；戳一戳填充 `target_id`。 |
| **扩展事件** | `guild` 频道消息、`notify` 通知等 NapCat 扩展已通过 `else` 分支安全兜底，待后续按需补充。 |
| **`metadata["flag"]`** | 请求事件特有字段，用于后续调用 `set_friend_add_request` / `set_group_add_request` API 处理。 |