# HpAgent Web Phase F — 统一身份与跨端长期记忆实施指导书

版本：MVP Fast Track v1.0
目标分支：`feat/hpagent-web`
前置状态：Phase E 已完成
核心目标：让 Web 与 QQ 真正成为“同一个 HpAgent、同一个用户、同一个长期记忆”，同时继续保持各自独立的短期 Conversation / Session。

---

# 1. Phase F 最终目标

完成 Phase F 后必须形成：

```text
                    Account A
                       │
            ┌──────────┴──────────┐
            │                     │
      Web Identity           QQ Identity
            │                     │
            └──────────┬──────────┘
                       │
                 account_id=A
                       │
                       ▼
             Hindsight Bank A
             hpagent-u-{A}
                ▲          ▲
                │          │
           Web retain   QQ retain
                │          │
           Web recall   QQ recall
```

用户体验应为：

```text
QQ：
“我以后后端项目优先用 Python。”

        ↓ Hindsight retain

Web 新建一个完全新的 Conversation：

“我之前偏好什么后端语言？”

        ↓

HpAgent 可以召回：
“你之前提到后端项目优先使用 Python。”
```

反方向同样成立：

```text
Web：
“我最近在准备 Temporal 面试。”

        ↓

QQ：
“我最近在准备什么？”

        ↓

召回 Web 长期记忆
```

但是：

```text
Web Conversation A 的逐字聊天记录
≠
Web Conversation B 的短期上下文

QQ Session 的逐字聊天记录
≠
Web Conversation 的短期上下文
```

共享的是 **Hindsight 提炼后的长期记忆**，不是把所有聊天记录混在一起。

---

# 2. Phase E 后的实际基础

Phase F 不重新建立以下能力。

当前 Web 已经存在：

```text
PostgreSQL
├── accounts
├── identity_bindings
├── conversations
├── messages
├── runs
├── sessions
└── outbox_events
```

Web 登录：

```text
username
   ↓
CredentialAdapter
   ↓
identity_bindings(provider='web')
   ↓
account_id
```

Web Agent recall：

```text
Run
 ↓
account_id
 ↓
ContextAssemblyService
 ↓
HindsightClient.recall(
    user_id=account_id
)
 ↓
hpagent-u-{account_id}
```

Web completed Run 已经在同一事务中写：

```text
retain_memory
publish_terminal_event
```

两个 Outbox。

因此：

> Phase F 不需要改 Web Run 状态机，也不需要修改 Phase E SSE。

---

# 3. 当前真正缺少的东西

Phase F 只需要补齐四条链路。

```text
① QQ Identity
      ↓
   PostgreSQL Account

② Web completed Run
      ↓
   retain_memory Outbox
      ↓
   Hindsight

③ QQ successful Turn
      ↓
   stable Hindsight document

④ Web / QQ
      ↓
   same account_id
      ↓
   same Hindsight bank
```

---

# 4. Phase F 不做什么

为了继续快速上线，本阶段不做：

```text
用户自助 QQ/Web 绑定页面
验证码绑定
二维码绑定
Account merge UI
历史 accounts.json 自动迁移
旧 Hindsight bank 自动迁移
记忆管理 UI
查看/编辑/删除记忆页面
记忆点赞
记忆手工纠错
Hindsight webhook
复杂 retain operation tracking
跨 bank 搜索
Conversation 级 memory scope UI
Redis 记忆缓存
独立 Memory 微服务
Memory Temporal Workflow
```

这些都不是 MVP 必需。

---

# 5. Phase F 核心架构原则

## 5.1 Account 是唯一用户身份

以后：

```text
QQ number
Web username
official QQ OpenID
```

都只是：

```text
IdentityBinding
```

不能继续自己充当 Account。

真正用户身份：

```text
accounts.account_id
```

---

## 5.2 Hindsight Bank 永远只由 account_id 决定

继续保持现有规则：

```text
bank_id =
hpagent-u-{account_id}
```

禁止：

```text
bank_id = qq号
bank_id = username
bank_id = conversation_id
bank_id = session_id
```

---

## 5.3 一个成功的用户交互 = 一个 Hindsight Document

新的统一语义：

```text
Web successful Run
→ web-run:{run_id}

QQ successful Turn
→ qq-execution:{execution_id}
```

不要让 Web 和 QQ 使用不同的记忆粒度。

---

## 5.4 Hindsight 只存用户可见语义

retain 内容只需要：

```text
[user]
用户最终输入

[assistant]
HpAgent 最终回答
```

不要 retain：

```text
隐藏 prompt
完整 model input
tool arguments
tool raw output
internal reasoning
run.progress
message.delta
audit event
memory recall result
Temporal History
```

---

## 5.5 Web Run 完成不能等待 Hindsight

正确：

```text
Agent
 ↓
PostgreSQL completed
 ↓
用户已经看到答案

同时：

retain_memory Outbox
 ↓
MemoryRetentionWorker
 ↓
Hindsight
```

错误：

```text
Agent
 ↓
等待 Hindsight retain
 ↓
Hindsight 挂了
 ↓
Run 无法 completed
```

长期记忆始终是最终一致的附加能力。

---

# 6. 新的 Phase F 拆分

建议按照：

```text
F-01
统一 PostgreSQL Account / IdentityBinding

F-02
统一 Hindsight Document Retain API

F-03
修正 QQ retain

F-04
实现 Web MemoryRetentionWorker

F-05
跨端召回与最小验收
```

顺序不要调换。

尤其必须先完成 F-01。

---

# 7. F-01：统一 QQ / Web Account

这是整个 Phase F 最重要的一步。

当前问题：

```text
Web
 ↓
PostgreSQL identity_bindings
 ↓
UUID A

QQ
 ↓
accounts.json
 ↓
UUID B
```

因此当前可能出现：

```text
Web Hindsight:
hpagent-u-A

QQ Hindsight:
hpagent-u-B
```

即使现实中是同一个人，也完全是两个 memory bank。

---

# 8. F-01.1 新建 PostgreSQL Account Resolver

建议：

```text
src/account/postgres_account_service.py
```

提供和旧 AccountService 最小兼容接口：

```python
resolve(channel_type, channel_user_id)

list_all_ids()
```

如果实际 grep 后发现其他方法仍有调用，再补：

```text
find_by_binding
get_account
```

不要为了兼容一次性复制整个旧 AccountService。

---

# 9. QQ Identity 在数据库中的表示

数据库现有 provider 只有：

```text
web
qq
```

不要修改 schema。

NapCat：

```text
provider = "qq"

external_subject_id =
"123456789"

normalized_subject_id =
"napcat:123456789"

metadata =
{
    "channel_type": "napcat"
}
```

Official QQ：

```text
provider = "qq"

external_subject_id =
"<openid>"

normalized_subject_id =
"official_qq:<openid>"

metadata =
{
    "channel_type": "official_qq"
}
```

为什么要带 channel prefix：

```text
provider='qq'
```

同时承载 NapCat QQ 号和 Official QQ OpenID。

它们不是同一个 ID namespace。

---

# 10. Web Identity 保持现状

Web：

```text
provider = web

normalized_subject_id =
username.strip().casefold()
```

必须与当前：

```text
ConfiguredPasswordCredentialAdapter.normalize()
```

完全一致。

不要再创造一套 normalization。

---

# 11. F-01.2 QQ resolve

以后：

```text
ConversationService
      ↓
PostgresAccountService.resolve(
    "napcat",
    sender_id
)
      ↓
identity_bindings
      ↓
accounts
      ↓
account_id
```

返回的一定是 PostgreSQL：

```text
accounts.account_id
```

---

# 12. 不在 Worker 自动绑定不同身份

MVP 不允许：

```text
收到一个 QQ sender_id
 ↓
猜这个人可能是哪个 Web 用户
```

更不能：

```text
前端提交 account_id
```

自动关联。

QQ/Web 两个身份的关联必须提前显式建立。

---

# 13. F-01.3 建立身份 bootstrap 脚本

Phase E 已经在：

```text
web/scripts/e2e-backend.sh
```

实现了 Web account + binding seed。

Phase F 将这个思想正式化。

新增：

```text
scripts/bootstrap_identity.py
```

用途：

```text
python scripts/bootstrap_identity.py \
  --web-subject huangpei \
  --qq-channel napcat \
  --qq-subject 123456789
```

脚本完成：

```text
查询 web binding
查询 qq binding

Case A:
两个都不存在
→ 创建 Account A
→ Web → A
→ QQ → A

Case B:
Web 已属于 A
QQ 不存在
→ QQ → A

Case C:
QQ 已属于 A
Web 不存在
→ Web → A

Case D:
二者已经属于 A
→ no-op

Case E:
Web 属于 A
QQ 属于 B
A != B
→ FAIL
```

MVP 不自动 merge Account。

---

# 14. bootstrap 必须幂等

重复执行：

```text
bootstrap_identity(...)
bootstrap_identity(...)
bootstrap_identity(...)
```

结果仍然只有：

```text
1 Account
1 Web binding
1 QQ binding
```

---

# 15. 不自动迁移 accounts.json

现有：

```text
.data/accounts.json
```

继续保留文件。

但是 PostgreSQL Account 模式启用以后：

```text
不要读取它决定 QQ account_id。
```

也不要自动：

```text
把旧 UUID 导入 PostgreSQL
复制旧 bank
移动 workspace
```

因为这种隐式迁移风险远大于 MVP 收益。

如果未来确实需要保留旧 QQ 长期记忆，再单独做：

```text
legacy-account migration
```

不要夹在 Phase F 主链路里。

---

# 16. F-01.4 Worker 数据库权限

当前：

```text
hpagent_worker
```

已经可以：

```text
SELECT accounts
```

但没有：

```text
SELECT identity_bindings
```

新增 migration：

```text
009_worker_identity_read.sql
```

只增加：

```sql
GRANT SELECT ON identity_bindings
TO hpagent_worker;
```

不要给 Worker：

```text
INSERT accounts
UPDATE accounts
INSERT identity_bindings
```

身份绑定属于管理面。

Worker 只负责读取。

bootstrap 使用 migration/admin credential。

---

# 17. F-01.5 Worker composition

当前：

```python
account_service =
    AccountService(accounts.json)
```

改成：

```text
有 WORKER_DATABASE_URL
且 Web unified account 启用
        ↓
PostgresAccountService

否则
        ↓
legacy AccountService
```

至少日志明确打印：

```text
Account backend: postgres
```

或者：

```text
Account backend: legacy_json
```

不要静默切换。

---

# 18. Reflect Schedule 同时切换

当前 Reflect Schedule：

```text
account_service.list_all_ids()
```

统一后：

```text
SELECT account_id
FROM accounts
WHERE status='active'
```

所以 Web-only Account 也会进入 Hindsight reflect。

这是正确行为。

不需要单独建立 Web reflect。

---

# 19. F-01 完成标准

执行：

```text
Web login
```

得到：

```text
account_id = A
```

然后 QQ 发消息。

QQ execution log 中：

```text
account_id = A
```

数据库：

```text
identity_bindings

web → A
qq  → A
```

做到这一点，F-01 完成。

---

# 20. F-02：统一 Hindsight Retain 接口

目前：

```python
HindsightClient.retain(
    events,
    user_id,
    session_id,
)
```

内部强制：

```text
document_id=session:{session_id}
```

这个接口太 QQ Session 化。

Web Run 不应该冒充 Session。

---

# 21. F-02.1 新增 retain_document

在：

```text
src/memory/hindsight_client.py
```

增加显式 API：

```python
retain_document(
    events,
    user_id,
    document_id,
    *,
    async_retain=False,
    channel_type="",
    group_id="",
    sender_name="",
    iso_timestamp="",
    scope="",
    metadata=None,
)
```

核心参数：

```text
user_id
→ 决定 bank

document_id
→ 决定这个 memory source 的幂等身份
```

二者绝对不能混。

---

# 22. Hindsight payload

例如 Web：

```json
{
  "items": [
    {
      "content": "[user]: 我更喜欢 Python\n\n[assistant]: 好的，我记住了。",
      "context": "Web chat",
      "document_id": "web-run:019...",
      "timestamp": "2026-08-08T...",
      "tags": [
        "channel:web"
      ],
      "metadata": {
        "source": "web",
        "run_id": "019...",
        "conversation_id": "019..."
      }
    }
  ],
  "async": false
}
```

Account ID 不需要放 tag。

bank 已经隔离 Account。

---

# 23. F-02.2 返回 RetainReceipt

不要继续只返回：

```text
int
```

Web Outbox Worker 需要知道：

```text
请求成功
还是
请求失败
```

建议：

```python
@dataclass(frozen=True)
class RetainReceipt:
    accepted: bool
    items_count: int
    operation_id: str | None
    async_processing: bool
```

这样：

```text
accepted=false
→ Outbox retry

accepted=true
→ mark processed
```

---

# 24. Web retain 使用同步模式

MVP 推荐：

```text
async_retain = false
```

注意：

这不是让 Web 请求同步等待。

真正执行环境：

```text
Run completed
    ↓
后台 Memory Worker
    ↓
同步等待 Hindsight
```

用户完全不等待。

好处：

```text
Memory Outbox processed
≈
Hindsight document 已真正处理成功
```

比：

```text
Hindsight 仅仅接受 async job
```

更简单。

以后量大再改 async operation tracking。

---

# 25. document_id 是 Retain 幂等核心

Web：

```text
web-run:{run_id}
```

同一个 Outbox 因网络失败重试：

```text
retain web-run:123
retain web-run:123
retain web-run:123
```

仍然是同一个 document。

因此可以安全重试。

---

# 26. 旧 retain() 不直接删除

为了 QQ 回归风险最低：

保留：

```python
retain(...)
```

但是内部可以改成：

```text
retain(...)
    ↓
retain_document(...)
```

旧调用语义暂时兼容。

不要一次性删除所有 SessionStore memory API。

---

# 27. F-03：修正 QQ Retain

这是我建议对旧 Phase F 计划做的重要修正。

旧计划说：

```text
QQ 继续 document_id=session:{session_id}
```

不建议继续这样。

原因：

当前 QQ Retention Sink 每次成功执行只传：

```text
当前 execution 的 memory_observations
```

并不是：

```text
完整 Session transcript
```

但相同 Hindsight document_id 默认是 replace/upsert。

结果可能变成：

```text
Turn 1
session:S
→ memories A

Turn 2
session:S
→ replace
→ memories B

Turn 1 的 source document 被覆盖
```

---

# 28. QQ 改为 per-turn Document

QQ 已经有稳定：

```text
execution_id
```

它由：

```text
workflow_id
+
message_id
```

确定性生成。

因此：

```text
document_id =
qq-execution:{execution_id}
```

非常适合作为 Hindsight document。

---

# 29. QQ Retain 内容也简化

当前不要继续把所有：

```text
memory_observations
```

直接扔给 Hindsight。

改成：

```python
[
    {
        "role": "user",
        "content": request.user_content,
    },
    {
        "role": "assistant",
        "content": result.content,
    },
]
```

也就是：

> 只 retain 用户实际说的话和最终实际收到的答案。

---

# 30. 为什么不 retain tool result

例如：

```text
Tool:
读取 /home/.../secret...

Tool:
数据库返回 10000 行

Model intermediate:
我准备调用...
```

这些属于执行细节。

长期记忆系统不应该自动把：

```text
工具内部输出
路径
凭证
临时执行数据
中间模型文本
```

当成用户记忆。

---

# 31. QQ Document metadata

建议：

```text
document_id:
qq-execution:{execution_id}

tags:
channel:napcat
scope:private/group
group:{group_id}     # group 时
session:{session_id}

metadata:
source = qq
execution_id = ...
session_id = ...
channel_type = ...
```

---

# 32. QQ recall 基本不需要修改

当前 QQ：

```text
account_id
 ↓
HindsightClient.recall
```

Account 修好以后自然变成：

```text
Postgres Account A
 ↓
hpagent-u-A
```

所以 QQ private：

```text
recall whole Account bank
```

可以看到：

```text
Web memories
QQ memories
```

---

# 33. QQ group 保持现有 group filter

当前：

```text
group_id 存在
 ↓
tags = ["group:{group_id}"]
```

继续保持。

不要 Phase F 顺便重构 group memory scope。

---

# 34. F-04：实现 Web Memory Retention

这是 Web 当前最大的 memory 缺口。

已有：

```text
CommandService.complete_run()
```

事务中已经：

```text
Run → completed
Assistant Message → completed

INSERT retain_memory
INSERT publish_terminal_event

COMMIT
```

所以数据库已经准备好了。

不需要改 complete transaction。

---

# 35. F-04.1 新增 MemoryRetentionService

建议：

```text
src/application/memory_retention.py
```

职责只有：

```text
run_id
 ↓
读取 PostgreSQL
 ↓
构造 Hindsight document
 ↓
调用 retain_document
```

不要让它负责：

```text
Outbox polling
Temporal
SSE
Redis
```

---

# 36. load_completed_run

按：

```text
run_id
```

查询：

```text
Run
trigger user Message
produced assistant Message
Conversation
```

必须验证：

```text
run.status == completed

assistant.status == completed

assistant.content != null

trigger.role == user

trigger.status == accepted

所有 account_id / conversation_id
完全一致
```

---

# 37. Web retain document

构造：

```text
document_id =
web-run:{run_id}
```

内容：

```text
[user]:
trigger_message.content

[assistant]:
assistant_message.content
```

---

# 38. Web metadata

建议：

```text
context:
Web chat

tags:
channel:web

metadata:
source = web
run_id = ...
conversation_id = ...
```

可以保存：

```text
session_id
```

作为 metadata。

但不要：

```text
document_id=session_id
```

---

# 39. 不 retain failed / cancelled Run

正常情况下它们根本不会产生：

```text
retain_memory
```

因为当前 complete transaction 才创建它。

MemoryRetentionService 仍然必须 defensive check：

```text
failed
cancelled
running
queued
```

都拒绝 retain。

---

# 40. 不使用 Web memory_observations

Web retain 数据只从：

```text
PostgreSQL committed Message
```

构造。

不要从：

```text
ExecutionResult.memory_observations
```

构造。

原因：

```text
DB Message
=
用户真正提交 + 用户真正看到的最终回答

memory_observations
=
Agent 内部执行观察
```

长期记忆应以前者为准。

---

# 41. F-04.2 实现 MemoryRetentionWorker

建议：

```text
src/orchestration/memory_retention_worker.py
```

或者保持更少文件：

```text
src/application/memory_retention.py
```

中放 worker class。

不要为它建立 Temporal Workflow。

---

# 42. Worker 消费 retain_memory

流程：

```text
OutboxService.claim(
    worker_id,
    {"retain_memory"}
)
       ↓
MemoryRetentionService
       ↓
Hindsight
       ↓
成功
       ↓
mark_processed
```

---

# 43. Hindsight 失败

例如：

```text
timeout
5xx
connection refused
```

执行：

```text
mark_retryable_failure
```

简单退避：

```text
1s
2s
4s
8s
16s
30s
60s
```

最大可以设置：

```text
8～10 attempts
```

不用追求复杂退避算法。

---

# 44. retain exhausted

多次失败后：

```text
dead_letter
```

但是：

```text
Run 继续保持 completed
Assistant Message 继续保持 completed
```

绝不能：

```text
memory failure
 ↓
completed Run → failed
```

---

# 45. Memory Worker 不要放 hpagent-api

Phase E 的：

```text
TerminalEventPublisher
```

目前放在：

```text
hpagent-api
```

是因为 API 本身持有：

```text
Redis
PostgreSQL
```

但是 MemoryRetentionWorker 需要：

```text
HindsightClient
```

而：

```text
hpagent worker
```

已经拥有 Hindsight。

所以 F 应该：

```text
hpagent Worker
├── QQ Worker
├── Web Agent Worker
├── Web Dispatcher
├── Reconciler
└── MemoryRetentionWorker
```

不要把 Hindsight credential 和 client 再塞入 API。

---

# 46. Hindsight 启动时不可用

如果：

```text
deps.hindsight_client is None
```

不要：

```text
claim retain_memory
→ 全部 dead-letter
```

而应该：

```text
MemoryRetentionWorker 不启动
```

Outbox 保持：

```text
pending
```

Worker 下一次 Hindsight 正常启动后继续消费。

这样记忆不会因为一次启动故障永久丢失。

---

# 47. F-04.3 Retain lease recovery

当前 Web Dispatcher 的 lease recovery 只负责：

```text
start_run
cancel_run
```

这是正确的。

Memory worker 建立自己的 recovery：

```text
owned types =
{"retain_memory"}
```

例如：

```text
lease timeout = 120s
recovery interval = 30s
```

如果：

```text
Memory Worker
claim event
 ↓
进程 crash
```

之后：

```text
processing
 ↓
expired
 ↓
pending
```

避免永久卡死。

---

# 48. Worker 启动集成

当前：

```text
start_worker()
```

已经有：

```text
web_dispatcher_task
web_reconciler_task
web_outbox_recovery_task
```

增加：

```text
memory_retention_task
memory_retention_recovery_task
```

shutdown：

```text
cancel
await
```

按照现有 background task 风格即可。

不要引入新的运行框架。

---

# 49. F-05：跨端 recall

Account 一旦统一以后：

Web recall 已经存在：

```text
ContextAssemblyService
 ↓
HindsightClient.recall(
    user_id=base.account_id
)
```

QQ recall 也已经存在：

```text
QQLegacyContextProvider
 ↓
TurnMemoryService
 ↓
Hindsight
```

所以 F-05 原则上：

> 不需要重新实现 recall。

真正需要的是证明两端现在传进去的是：

```text
相同 account_id
```

---

# 50. 不添加 channel:web recall filter

Web recall 不应该：

```text
tags = ["channel:web"]
```

否则：

```text
QQ → Web
```

跨端记忆立刻失效。

正确：

```text
Web private conversation
 ↓
Account bank semantic recall
```

即搜索：

```text
QQ memories
+
Web memories
```

---

# 51. Conversation 不作为长期记忆 filter

不要：

```text
tags = ["conversation:{conversation_id}"]
```

然后 recall 时严格过滤。

否则：

```text
Conversation A 的记忆
```

无法在：

```text
Conversation B
```

召回。

Conversation ID 可以作为 metadata 保存。

不能成为默认 recall scope。

---

# 52. 短期上下文继续严格隔离

Web：

```text
PostgreSQL Message history
```

只加载：

```text
当前 conversation_id
```

QQ：

```text
SessionStore
```

只加载：

```text
当前 QQ session
```

Hindsight 是另外一条长期记忆输入。

模型上下文逻辑：

```text
System Prompt

+ 当前端自己的 Short-Term Context

+ Account Hindsight Long-Term Memory

+ 当前 User Message
```

不要把：

```text
其他 Conversation 原始 Message
```

直接拼入上下文。

---

# 53. 最重要的状态模型

完成 F 后，系统中的“记忆”必须明确分三种。

```text
① Conversation / Session History
短期上下文
精确原文
渠道隔离

② Hindsight Document
记忆输入来源
每个成功交互一个

③ Hindsight Memory Unit
World Fact
Experience Fact
Observation
由 Hindsight 自动提炼
```

HpAgent 不自己：

```text
手工判断 World Fact
手工生成 Experience Fact
手工合并 Observation
```

这仍然交给 Hindsight。

---

# 54. Web 完整链路

最终：

```text
Browser
 ↓
POST message
 ↓
PostgreSQL
 ↓
Temporal
 ↓
Agent
 ↓
complete_run()
 ↓
┌──────────────────────────────┐
│ PostgreSQL transaction       │
│                              │
│ assistant = completed        │
│ run = completed              │
│ retain_memory Outbox         │
│ publish_terminal_event       │
└──────────────────────────────┘
       │                 │
       │                 │
       ▼                 ▼
Terminal Publisher   MemoryRetentionWorker
       │                 │
       ▼                 ▼
Redis              Hindsight
       │                 │
       ▼                 ▼
Browser            hpagent-u-A
```

两个消费者完全独立。

---

# 55. QQ 完整链路

```text
QQ sender_id
    ↓
PostgresAccountService
    ↓
identity_bindings
    ↓
account_id A
    ↓
QQExecutionHost
    ↓
AgentExecutionFacade
    ↓
final reply
    ↓
QQ Retention Sink
    ↓
retain_document(
    bank=hpagent-u-A,
    document=qq-execution:...
)
```

---

# 56. Phase E 与 F 的边界

F 不应该修改：

```text
SSE Gateway
runFeed.ts
sseClient.ts
assistant-ui Adapter
Run UI
Conversation Sidebar
Terminal Event contract
```

也不新增：

```text
memory.retained SSE
```

用户不需要知道：

```text
Hindsight retain finished
```

长期记忆是后端能力。

---

# 57. 实施文件清单

Flash 开始前重点阅读：

```text
src/web_api/auth.py

src/account/account_service.py

src/application/conversation.py

src/application/context_assembly.py
src/application/memory.py

src/agent_execution/qq_host.py
src/agent_execution/web_host.py

src/memory/hindsight_client.py

src/session/store.py

src/web_domain/services.py
src/web_domain/outbox.py

src/orchestration/worker.py

persistence/migrations/001_phase_a_schema.sql
persistence/migrations/003_runtime_permissions.sql

web/scripts/e2e-backend.sh
```

---

# 58. 推荐新增文件

```text
src/account/postgres_account_service.py

src/application/memory_retention.py

scripts/bootstrap_identity.py

persistence/migrations/
009_worker_identity_read.sql
```

可选：

```text
src/orchestration/memory_retention_worker.py
```

如果 `memory_retention.py` 已经不大，可以不额外拆。

---

# 59. 推荐修改文件

```text
src/memory/hindsight_client.py

src/application/memory.py

src/agent_execution/qq_host.py

src/orchestration/worker.py

docker-compose.yaml
```

可能还需要：

```text
src/orchestration/config.py
```

只有确实需要新增 memory worker 配置时再改。

---

# 60. 不需要修改

原则上不动：

```text
web/

src/web_api/sse.py
src/web_api/terminal_publisher.py

src/orchestration/web_workflow.py

src/agent_execution/brain_action_loop.py

数据库 Conversation / Message / Run schema
```

---

# 61. 推荐实施顺序

Flash 严格按此顺序实施。

```text
Step 1
新增 worker identity SELECT migration

Step 2
实现 PostgresAccountService

Step 3
实现 bootstrap_identity.py

Step 4
让 QQ resolve 使用 PostgreSQL Account

Step 5
手工确认 Web/QQ account_id 相同

Step 6
新增 Hindsight retain_document()

Step 7
把 QQ retain 改为 per-execution document

Step 8
实现 MemoryRetentionService

Step 9
实现 retain_memory Outbox consumer

Step 10
接入 main Worker lifecycle

Step 11
验证 Web → Web memory

Step 12
验证 QQ → Web

Step 13
验证 Web → QQ

Step 14
验证不同 Account 不串记忆
```

不要先写跨端 E2E 再开始修身份。

---

# 62. 最低自动测试

依然遵循快速 MVP，不做巨大矩阵。

至少留下：

```text
1.
PostgresAccountService
同一 QQ binding 返回正确 account_id

2.
Web + QQ binding
可以指向同一 account

3.
不同 active QQ subject
不能映射错 account

4.
retain_document
生成指定 document_id

5.
重复 retain 相同 Web run
document_id 不改变

6.
MemoryRetentionService
只接受 completed Run

7.
failed/cancelled Run
不会 retain

8.
Hindsight failure
Run terminal 状态不变化

9.
QQ retain
使用 per-execution document_id
```

够了。

---

# 63. 必须人工验证的 5 个场景

## Case 1：Web → 新 Web Conversation

Conversation A：

```text
“我以后写后端优先使用 Python。”
```

等 retain 完成。

新建 Conversation B：

```text
“我偏好什么后端语言？”
```

应召回 Python。

---

## Case 2：QQ → Web

QQ：

```text
“我最近在准备 Temporal 面试。”
```

然后新建 Web Conversation：

```text
“我最近在准备什么？”
```

应召回 Temporal。

---

## Case 3：Web → QQ

Web：

```text
“我喜欢回答尽量先给结论。”
```

然后 QQ：

```text
“你记得我的回答偏好吗？”
```

应召回该偏好。

---

## Case 4：短期 Conversation 隔离

Conversation A 中加入大量临时上下文。

Conversation B 不应该直接出现：

```text
Conversation A 的完整逐字消息
```

只有真正被 Hindsight 提炼成长期事实的内容才可能被召回。

---

## Case 5：不同 Account

Account A：

```text
偏好 Python
```

Account B：

```text
偏好 Java
```

两者分别询问。

必须只召回自己的 bank。

---

# 64. Hindsight 故障 Smoke Test

可选但推荐做一次。

停止 Hindsight。

Web 发一条消息。

必须：

```text
Run = completed
Message = completed
浏览器收到最终答案
```

同时：

```text
retain_memory
```

保持：

```text
pending/retry
```

而不是影响聊天成功。

恢复 Hindsight 后：

```text
Memory Worker
 ↓
重新消费
 ↓
memory retained
```

---

# 65. Phase F 不要求即时强一致

用户看到：

```text
run.completed
```

以后：

```text
retain_memory
```

可能还在处理。

因此：

```text
Web completed
→ 立刻创建新 Conversation
→ 第一秒内 recall
```

偶尔没命中允许存在。

这是：

```text
eventual consistency
```

同一 Conversation 不受影响，因为它有 PostgreSQL short-term Message history。

---

# 66. 建议 Memory Worker polling

简单即可：

```text
idle poll:
0.5～1s

retry:
1
2
4
8
16
30
60
```

不需要优化到毫秒。

---

# 67. 可观测性最低要求

日志只需要：

```text
memory_retain_started
memory_retain_succeeded
memory_retain_retry
memory_retain_dead_letter
```

字段：

```text
run_id
account_id
document_id
attempt
latency_ms
error_code
```

不要把：

```text
完整 user content
完整 assistant content
memory fact
```

放日志。

---

# 68. 不需要新增数据库 memory 表

不要建立：

```text
memories
memory_facts
memory_embeddings
```

HpAgent PostgreSQL 只需要知道：

```text
retain_memory Outbox
```

Hindsight 自己管理：

```text
documents
facts
observations
entities
vectors
```

不要重复建设。

---

# 69. Phase F 的关键技术点

这阶段真正需要保留的技术价值是：

```text
Identity Binding
      +
Account-level bank isolation
      +
Transactional Outbox
      +
Idempotent Hindsight document
      +
Asynchronous retention
      +
Cross-channel semantic recall
      +
Short-term / Long-term separation
```

而不是增加更多框架。

---

# 70. Phase F 最终架构

```text
                         PostgreSQL
                             │
                    ┌────────┴────────┐
                    │                 │
              Web Identity       QQ Identity
                    │                 │
                    └──── Account ────┘
                             │
                             │ account_id
                             ▼
                     Hindsight Bank
                     hpagent-u-{id}
                       ▲          ▲
                       │          │
              web-run:{id}   qq-execution:{id}
                       │          │
                       ▲          ▲
                Retention      Retention
                  Worker          Sink
                       │          │
                       │          │
                 Web completed   QQ success
```

Recall：

```text
               Hindsight Bank
                    │
              semantic recall
              ┌─────┴─────┐
              │           │
             Web          QQ
              │           │
       Web short-term   QQ short-term
       Conversation     Session
```

---

# 71. Phase F Definition of Done

满足以下条件即可结束 Phase F：

* Web 与 QQ 身份可以明确绑定到同一 PostgreSQL Account。
* QQ runtime 不再使用 `accounts.json` 作为统一 Account 真相。
* Hindsight bank 继续严格按 PostgreSQL `account_id` 建立。
* Web completed Run 自动异步 retain。
* Web retain document ID 为 `web-run:{run_id}`。
* QQ successful Turn 使用稳定 per-execution document ID。
* QQ 不再用本轮内容反复覆盖同一个 session document。
* Web 与 QQ retain 都只保留 user + final assistant 的用户可见内容。
* failed / cancelled Web Run 不写长期记忆。
* Hindsight 故障不影响 Web Run completed。
* retain 重试不会产生重复 document。
* Web 可以召回 QQ 长期记忆。
* QQ 可以召回 Web 长期记忆。
* 新 Web Conversation 可以召回旧 Web Conversation 的长期事实。
* Web Conversation 之间的原始短期消息不直接串线。
* 不同 Account 的 Hindsight bank 完全隔离。
* QQ 原有模型、工具、Sandbox、回复链路保持正常。
* Phase E SSE / assistant-ui 无需因记忆系统修改协议。

达到以上条件：

```text
Phase F = DONE
```

然后进入 Phase G。

---

# 72. 给 Flash 的最终约束

Phase F 的任务不是“重写记忆系统”。

不要：

```text
重新设计 Hindsight
引入新的向量数据库
自己实现 memory extraction
自己分类 World/Experience/Observation
重写 ContextBuilder
修改 Web SSE
增加前端 Memory 页面
```

只做三件核心事情：

```text
1. 让 QQ 和 Web 真的是同一个 Account

2. 让每次成功交互可靠进入这个 Account 的 Hindsight bank

3. 让两个渠道都从这个 bank recall
```

如果实现开始向其他方向扩散，立即收缩范围。
