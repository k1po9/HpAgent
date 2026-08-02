# NapCat API 参考手册 (精选)

> 来源: [NapCat 接口文档](https://napcat.apifox.cn/5430207m0.md)  
> 提取范围: 与 HpAgent 记忆/对话系统直接相关的接口和数据结构

---

## 一、统一响应格式 — BaseResponse

所有 API 返回此结构，`data` 字段具体类型因接口而异。

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | `string` | `"ok"` / `"failed"` |
| `retcode` | `number` | 0=成功, 1400=参数错误, 1401=权限不足, 1404=不存在 |
| `data` | `any` | 业务数据 |
| `message` | `string` | 消息 |
| `wording` | `string` | 提示 |
| `stream` | `string` | `"stream-action"` / `"normal-action"` |

---

## 二、核心消息模型

### 2.1 OB11Message — 完整消息对象 (事件上报)

OneBot v11 WebSocket 推送的消息体，是 HpAgent 的内存侧入口数据。

| 字段 | 类型 | 说明 |
|------|------|------|
| `self_id` | `number` | 机器人 QQ 号 |
| `user_id` | `number\|string` | 发送者 QQ 号 |
| `group_id` | `number\|string` | 群号 (群聊时) |
| `group_name` | `string` | 群名称 (群聊时) |
| `message_id` | `number` | 消息 ID |
| `time` | `number` | 消息时间戳 (Unix) |
| `message_type` | `"private"\|"group"` | 消息类型 |
| `sub_type` | `"friend"\|"group"\|"normal"` | 消息子类型 |
| `message` | `OB11MessageData[] \| string` | 消息内容 (消息段数组或纯文本) |
| `message_format` | `string` | 消息格式 |
| `message_seq` | `number` | 消息序列号 |
| `real_id` | `number` | 真实 ID |
| `real_seq` | `string` | 真实序列号 |
| `sender` | `OB11Sender` | 发送者信息 |
| `target_id` | `number` | 目标 ID |
| `temp_source` | `number` | 临时会话来源 |

### 2.2 OB11Sender — 发送者信息

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `user_id` | `number\|string` | ✓ | QQ 号 |
| `nickname` | `string` | ✓ | 昵称 |
| `card` | `string` | | 群名片 (群聊时有值) |
| `role` | `string` | | 角色 (owner/admin/member) |
| `sex` | `string` | | 性别 |
| `age` | `number` | | 年龄 |
| `area` | `string` | | 地区 |
| `level` | `string` | | 等级 |
| `title` | `string` | | 专属头衔 |

> **记忆模块价值**: `nickname` + `card` 可用于构建人类可读的 context 描述，替代裸 QQ 号。

### 2.3 OB11MessageData — 消息段类型 (23 种)

消息内容 `message` 字段为消息段数组，每个元素有 `type` + `data`：

| type | 对应 Schema | 说明 | data 关键字段 |
|------|-----------|------|-------------|
| `text` | `OB11MessageText` | 纯文本 | `text: string` |
| `at` | `OB11MessageAt` | @提及 | `qq: string`, `name: string` |
| `reply` | `OB11MessageReply` | 回复引用 | `id: string` (消息ID), `seq: number` |
| `image` | `OB11MessageImage` | 图片 | `file`, `url`, `summary`, `sub_type` |
| `record` | `OB11MessageRecord` | 语音 | `file`, `url`, `path` |
| `video` | `OB11MessageVideo` | 视频 | `file`, `url`, `thumb` |
| `file` | `OB11MessageFile` | 文件 | `file`, `name`, `url`, `path` |
| `face` | `OB11MessageFace` | QQ 表情 | (QQ 内置) |
| `mface` | `OB11MessageMFace` | 动态表情 | (商城表情) |
| `poke` | `OB11MessagePoke` | 戳一戳 | `type: string`, `id: string` |
| `dice` | `OB11MessageDice` | 骰子 | `result: number` |
| `rps` | `OB11MessageRPS` | 猜拳 | `result: number` |
| `contact` | `OB11MessageContact` | 联系人推荐 | `type: "qq"\|"group"`, `id: string` |
| `location` | `OB11MessageLocation` | 位置 | `lat`, `lon`, `title`, `content` |
| `json` | `OB11MessageJson` | JSON 卡片 | `data: string\|object` |
| `xml` | `OB11MessageXml` | XML 卡片 | `data: string` |
| `markdown` | `OB11MessageMarkdown` | Markdown | `content: string` |
| `music` (id) | `OB11MessageIdMusic` | 平台音乐 | `type: "qq"\|"163"\|"xm"`, `id: string` |
| `music` (custom) | `OB11MessageCustomMusic` | 自定义音乐 | `url`, `audio`, `title`, `image` |
| `node` | `OB11MessageNode` | 合并转发节点 | `id`, `user_id`, `nickname`, `content` |
| `forward` | `OB11MessageForward` | 合并转发 | `id: string` |
| `onlinefile` | `OB11MessageOnlineFile` | 在线文件 | `msgId`, `elementId`, `fileName`, `fileSize` |
| `flashtransfer` | `OB11MessageFlashTransfer` | QQ 闪传 | `fileSetId: string` |

> **记忆模块价值**: `reply` 段可用于重建对话上下文链；`at` 段可判断机器人是否被 @；非 `text` 类型消息在记忆提取时可以特殊处理（如图片 OCR、文件摘要）。

### 2.4 OB11MessageMixType — 发送消息体

```yaml
OB11MessageMixType:
  anyOf:
    - type: array        # 消息段数组
      items: OB11MessageData
    - type: string       # 纯文本 (CQ 码/纯文本)
    - OB11MessageData    # 单个消息段
```

---

## 三、核心 API

### 3.1 发送消息

#### POST /send_msg — 通用发送

**请求**:
| 参数 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `message_type` | `"private"\|"group"` | | 消息类型 |
| `user_id` | `string` | | 用户 QQ (私聊) |
| `group_id` | `string` | | 群号 (群聊) |
| `message` | `OB11MessageMixType` | ✓ | 消息内容 |
| `auto_escape` | `bool\|string` | | 纯文本模式 (不解析 CQ 码) |
| `timeout` | `number` | | 自定义发送超时(毫秒) |

**响应**: `data.message_id` (number), `data.res_id`, `data.forward_id`

#### POST /send_private_msg — 发送私聊消息

参数同上，隐含 `message_type=private`。

#### POST /send_group_msg — 发送群消息

参数同上，隐含 `message_type=group`。

#### POST /delete_msg — 撤回消息

**请求**: `message_id: number|string`  
**响应**: `data: null`

---

### 3.2 获取消息

#### POST /get_msg — 根据消息 ID 获取详情

**请求**: `message_id: number|string`

**响应 data**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `message_id` | `number` | 消息 ID |
| `real_id` | `number` | 真实 ID |
| `message_seq` | `number` | 消息序号 |
| `time` | `number` | 发送时间 |
| `message_type` | `string` | 消息类型 |
| `sender` | `object` | 发送者 |
| `message` | `object` | 消息内容 (消息段数组) |
| `raw_message` | `string` | 原始消息文本 |
| `user_id` | `number\|string` | 发送者 QQ |
| `group_id` | `number\|string` | 群号 (群聊) |
| `emoji_likes_list` | `string[]` | 表情回应列表 |

> **记忆模块价值**: 可获取历史消息的完整消息段结构，用于记忆回溯和引用验证。

#### POST /get_group_msg_history — 获取群历史消息

**请求**:
| 参数 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `group_id` | `string` | ✓ | 群号 |
| `message_seq` | `string` | | 起始消息序号 |
| `count` | `number` | | 获取数量 (默认 20) |
| `reverse_order` | `bool` | | 反向排序 |

**响应**: `data.messages: OB11Message[]`

> **记忆模块价值**: 新会话/机器人重启后恢复群聊上下文；定期抓取历史消息补充记忆。

---

### 3.3 群组信息

#### POST /get_group_detail_info — 获取群详细信息

**请求**: `group_id: string`  
**响应**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `group_id` | `number` | 群号 |
| `group_name` | `string` | 群名称 |
| `member_count` | `number` | 成员数量 |
| `max_member_count` | `number` | 最大成员数 |
| `group_all_shut` | `number` | 全员禁言状态 |
| `group_remark` | `string` | 群备注 |

> **记忆模块价值**: `group_name` 可替代裸 `group_id` 用于 memory context 的人类可读描述。

#### POST /get_group_member_list — 获取群成员列表

**请求**: `group_id: string`, `no_cache?: bool`  
**响应**: `data: OB11GroupMember[]`

#### POST /get_group_member_info — 获取指定成员信息

**请求**: `group_id: string`, `user_id: string`, `no_cache?: bool`  
**响应**: `data: OB11GroupMember`

#### OB11GroupMember

| 字段 | 类型 | 说明 |
|------|------|------|
| `group_id` | `number` | 群号 |
| `user_id` | `number` | QQ 号 |
| `nickname` | `string` | 昵称 |
| `card` | `string` | 群名片 |
| `sex` | `string` | 性别 |
| `age` | `number` | 年龄 |
| `join_time` | `number` | 入群时间戳 |
| `last_sent_time` | `number` | 最后发言时间戳 |
| `level` | `string` | 等级 |
| `qq_level` | `number` | QQ 等级 |
| `role` | `string` | `"owner"` / `"admin"` / `"member"` |
| `title` | `string` | 专属头衔 |
| `area` | `string` | 地区 |
| `unfriendly` | `boolean` | 是否不良记录 |
| `title_expire_time` | `number` | 头衔过期时间 |
| `card_changeable` | `boolean` | 是否可改名 |
| `shut_up_timestamp` | `number` | 禁言截止时间戳 |
| `is_robot` | `boolean` | 是否机器人 |
| `qage` | `number` | Q龄 |

> **记忆模块价值**: 拥有最丰富的用户画像数据 — `nickname`, `card`, `role`, `join_time`, `last_sent_time` 均可用于构建记忆 context。结合 `card_changeable` 判断名称稳定性。

---

### 3.4 用户信息

#### POST /get_stranger_info — 获取陌生人/非好友信息

**请求**: `user_id: string`, `no_cache?: bool`

**响应 data**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_id` | `number` | QQ 号 |
| `uid` | `string` | UID |
| `nickname` | `string` | 昵称 |
| `age` | `number` | 年龄 |
| `qid` | `string` | QID |
| `qqLevel` | `number` | QQ 等级 |
| `sex` | `string` | 性别 |
| `long_nick` | `string` | 个性签名 |
| `reg_time` | `number` | 注册时间戳 |
| `is_vip` | `boolean` | 是否 VIP |
| `is_years_vip` | `boolean` | 是否年费 VIP |
| `vip_level` | `number` | VIP 等级 |
| `remark` | `string` | 备注 |
| `status` | `number` | 状态 |
| `login_days` | `number` | 登录天数 |

> **记忆模块价值**: `nickname` + `remark` 可构建用户友好名称；`reg_time` + `login_days` 体现用户资历。

#### POST /get_login_info — 获取机器人自身信息

**请求**: 无参数  
**响应**: `data: OB11User`

#### OB11User

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_id` | `number` | QQ 号 |
| `nickname` | `string` | 昵称 |
| `remark` | `string` | 备注 |
| `sex` | `string` | 性别 |
| `age` | `number` | 年龄 |
| `level` | `number` | 等级 |
| `qid` | `string` | QID |
| `login_days` | `number` | 登录天数 |
| `birthday_year/month/day` | `number` | 生日 |
| `phone_num` | `string` | 手机号 |
| `email` | `string` | 邮箱 |
| `category_id` / `categoryName` | `number/string` | 好友分组 |

---

### 3.5 通知/事件

#### OB11Notify — 通知信息 (入群请求等)

| 字段 | 类型 | 说明 |
|------|------|------|
| `request_id` | `number` | 请求 ID |
| `invitor_uin` | `number` | 邀请者 QQ |
| `invitor_nick` | `string` | 邀请者昵称 |
| `group_id` | `number` | 群号 |
| `group_name` | `string` | 群名称 |
| `message` | `string` | 附言 |
| `checked` | `boolean` | 是否已处理 |
| `actor` | `number` | 操作者 QQ |
| `requester_nick` | `string` | 申请者昵称 |

#### OB11ActionMessage — 快速操作消息

| 字段 | 类型 | 说明 |
|------|------|------|
| `self_id` | `number` | 机器人 QQ |
| `user_id` | `number` | 用户 QQ |
| `time` | `number` | 时间戳 |
| `message_type` | `string` | 消息类型 |
| `sender` | `{user_id, nickname, card, role}` | 发送者 |
| `raw_message` | `string` | 原始消息 |

---

### 3.6 OB11Group — 群基本信息

| 字段 | 类型 | 说明 |
|------|------|------|
| `group_id` | `number` | 群号 |
| `group_name` | `string` | 群名称 |
| `group_remark` | `string` | 群备注 |
| `member_count` | `number` | 成员数 |
| `max_member_count` | `number` | 最大成员数 |
| `group_all_shut` | `number` | 全员禁言 |

---

### 3.7 FileBaseData — 文件消息段基类

所有多媒体消息段 (image/video/record/file) 继承此结构：

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `file` | `string` | ✓ | 文件路径 / URL / `file:///` |
| `path` | `string` | | 文件路径 |
| `url` | `string` | | 文件 URL |
| `name` | `string` | | 文件名 |
| `thumb` | `string` | | 缩略图 |

---

## 四、频道 (QQ Guild) 接口

#### POST /get_guild_list — 获取频道列表

获取当前帐号已加入的频道。

#### POST /get_guild_service_profile — 获取频道个人信息

获取当前帐号在频道中的个人资料。

---

## 五、事件上报类型 (post_type)

基于 OneBot v11 协议，WebSocket 推送的一级分类：

| post_type | detail_type 示例 | 说明 |
|-----------|-----------------|------|
| `message` | `private`, `group` | 消息事件 |
| `notice` | `group_upload`, `group_admin`, `group_increase`, `group_decrease`, `group_ban`, `friend_add`, `group_recall`, `friend_recall`, `notify` | 通知事件 |
| `request` | `friend`, `group` | 请求事件 (加好友/加群/邀请) |
| `meta_event` | `lifecycle`, `heartbeat` | 元事件 |

---

## 六、对 HpAgent 记忆模块的关键启示

### 6.1 可用于富化 memory context 的数据

从 `OB11Message` + `OB11Sender` 组合可提取：

```python
# 高质量 context 示例
context = (
    f"QQ {msg.message_type} chat"
    + (f" in group "{group_name}" ({msg.group_id})" if msg.group_id else "")
    + f", sender: {sender.card or sender.nickname} ({sender.user_id})"
    + (f" [role: {sender.role}]" if sender.role else "")
)
```

### 6.2 可缓存以提升效率的查询

| 数据 | API | 缓存策略 |
|------|-----|---------|
| `bot_user_id` | `get_login_info` | 启动时一次 |
| `group_name` | `get_group_detail_info` | 惰性查询 + TTL 1h |
| `member_card` | `get_group_member_info` | 惰性查询 + TTL 30min |
| `friend_remark` | `get_stranger_info` | 惰性查询 + TTL 1h |

### 6.3 消息段对记忆提取的影响

- **text**: 直接提取
- **reply**: 需先获取被引用消息 (`get_msg`) 以重建完整上下文
- **at**: 需区分 @bot 还是 @他人 —— 影响回复意图判断
- **image**: 可通过 `summary` 字段获取图片描述；完整 OCR 需调用 `图片 OCR 识别` 接口
- **json/xml/markdown**: 结构化内容可能含有链接、卡片信息
- **node/forward**: 合并转发需展开后才能做记忆提取

### 6.4 记忆模块的 tag 维度扩展建议

基于 NapCat 的数据能力，确认之前在 best-practices 文档中的建议完全可行：

| Tag | 数据来源 | 获取方式 |
|-----|---------|---------|
| `channel:napcat` | 系统已知 | 固定值 |
| `scope:private` | `message_type` | 直接读取 |
| `scope:group` | `message_type` | 直接读取 |
| `group:{group_id}` | `group_id` | 直接读取 |
| `group_name:{name}` (metadata) | `group_name` / API 查询 | 事件自带 / 缓存查询 |
| `sender_name:{card}` (metadata) | `sender.card` | 事件自带 |
