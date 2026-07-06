# 01 — QQ 机器人系统上下文图（单 Agent）

> 对应绘图文件: `docs/draw/01_system_context_qq_robot.excalidraw`
>
> 本图展示 HpAgent 作为 QQ 聊天机器人与所有外部角色/系统的交互边界，聚焦单 Agent（QQ 渠道）场景。

---

## 核心系统名称

**HpAgent** — 基于"手脑分离"架构的 QQ 智能聊天机器人，集成 Hindsight 长期记忆与沙箱化工具执行。

---

## 外部角色清单

按图元摆放建议分为三组：**人类用户**、**基础设施**、**AI/工具服务**。

### 第一组：人类用户

| 编号 | 角色 | 类型 | 简要说明 |
|------|------|------|----------|
| U-01 | QQ 用户（群聊） | 人 | 在 QQ 群内发消息，@bot 触发对话；非 @ 消息仅收录群上下文 |
| U-02 | QQ 用户（私聊） | 人 | 通过 QQ 私聊窗口与机器人一对一交互 |

### 第二组：基础设施 & 桥接

| 编号 | 角色 | 类型 | 简要说明 |
|------|------|------|----------|
| I-01 | QQ 服务器（腾讯） | 系统 | QQ 消息底层传输；NapCat 登录 QQ 号与其通信 |
| I-02 | NapCat QQ 客户端 | 系统 | OneBot v11 协议桥接：QQ 消息 ↔ WebSocket JSON；连接 HpAgent ws://0.0.0.0:8082 |
| I-03 | Temporal Server | 系统 | 工作流编排引擎（gRPC :7233）；管理每用户会话生命周期，信号驱动回合调度 |
| I-04 | Redis | 系统 | 会话热数据缓存（24h TTL）+ 群上下文滑动窗口 + PubSub 事件总线 |

### 第三组：AI & 工具服务

| 编号 | 角色 | 类型 | 简要说明 |
|------|------|------|----------|
| A-01 | LLM 提供商 — Mimo（主） | 系统 | 主力对话模型（chat）和快速模型（fast），OpenAI API 兼容 |
| A-02 | LLM 提供商 — MiniMax（推理） | 系统 | 深度推理降级备选 |
| A-03 | LLM 提供商 — 阿里百炼（快） | 系统 | 快速模型降级备选 |
| A-04 | SiliconFlow（Embedding/Rerank） | 系统 | BGE-M3 向量嵌入 + BGE Reranker 重排序 |
| A-05 | Hindsight（vectorize.io） | 系统 | 长期记忆服务：pgvector 语义检索 + LLM 事实提取 + 知识图谱 |
| A-06 | Tavily MCP（搜索） | 系统 | Web 搜索 + 网页内容提取 |
| A-07 | 股票 SDK MCP | 系统 | A股/港股/美股行情、K线、资金流向、龙虎榜 |
| A-08 | 高德地图 MCP | 系统 | 地理编码、POI 搜索、路径规划 |
| A-09 | 八字/菜谱/12306 MCP | 系统 | 生活服务：运势、菜谱、火车票查询 |
| A-10 | ChromaDB（本地） | 系统 | 工具向量库，RAG 动态工具选择 |
| A-11 | 本地文件系统（workspace） | 系统 | 会话归档 history.jsonl、调度任务 JSON、沙箱工作区 |

---

## 数据流向

> 每条流的编号对应图中箭头标签。

### 主数据流（5 条核心流）

#### DF-01: QQ 消息接入 & 智能回复（核心对话流）

```
QQ 用户
  → (QQ 消息 / @提及 / 图片)
  → QQ 服务器
  → (OneBot v11 JSON)
  → NapCat
  → (WebSocket JSON: post_type=message)
  → HpAgent
  → [HyDE 改写 → Hindsight 记忆召回 → 上下文构建 → LLM 推理 → 工具执行循环]
  → (最终回复文本)
  → NapCat
  → (OneBot v11 send_msg JSON)
  → QQ 服务器
  → (QQ 消息)
  → QQ 用户
```

#### DF-02: 工具调用（MCP 外部服务）

```
HpAgent
  → (tool_call: 搜索/股票/地图/菜谱...)
  → MCP 工具服务 (Tavily / 股票SDK / 高德 / 八字 / 菜谱 / 12306)
  → (工具结果 JSON)
  → HpAgent
  → [LLM 继续推理]
```

#### DF-03: 长期记忆（存 & 取）

```
[HpAgent → Hindsight: 写入]
HpAgent
  → (HTTP POST: /banks/{bank_id}/memories, 会话事件 + 上下文 + tags)
  → Hindsight
  → [LLM 事实提取 + BGE-M3 向量嵌入]
  → pgvector 持久化

[HpAgent ← Hindsight: 召回]
HpAgent
  → (HTTP POST: /banks/{bank_id}/memories/recall, HyDE 改写查询 + tags 过滤)
  → Hindsight
  → (pgvector 余弦 + BM25 + 知识图谱 混合检索结果)
  → HpAgent
  → [注入系统提示词 "相关记忆" 段]
```

#### DF-04: 会话编排（Temporal 生命周期）

```
HpAgent
  → (StartWorkflow / SignalWorkflow)
  → Temporal Server
  → (时间片分配 / 空闲超时信号 / 新消息信号)
  → HpAgent
  → [回合调度 / 空闲超时 → 会话归档]

HpAgent
  → (CompleteWorkflow / 归档事件)
  → Temporal Server
  → (工作流持久化到 PostgreSQL)
```

#### DF-05: 定时提醒 & 调度推送

```
[注册]
HpAgent
  → (用户提醒 JSON: trigger_at / cron_expr)
  → 本地 JSON 文件 (.data/scheduler/scheduled_tasks.json)

[触发]
调度器轮询
  → (检查到期任务)
  → 本地 JSON 文件
  → (到期任务)
  → HpAgent
  → (提醒消息)
  → QQ 用户
```

### 辅助数据流

#### DF-06: 群上下文采集（非 @ 消息）

```
QQ 用户
  → (群消息, 未 @bot)
  → QQ 服务器 → NapCat → HpAgent
  → (写入 Redis 群上下文滑动窗口，不触发回复)
```

#### DF-07: 工具 RAG 动态选择

```
HpAgent
  → (用户查询 embedding)
  → SiliconFlow (BGE-M3)
  → ChromaDB (向量相似度检索)
  → (候选工具列表)
  → [可选: SiliconFlow BGE Reranker 重排序]
  → (Top-K 工具定义)
  → LLM function calling
```

#### DF-08: 会话归档

```
HpAgent (空闲超时 / 主动结束)
  → (Redis 读取全量事件)
  → (写入 history.jsonl + meta.yaml)
  → 本地文件系统 (.data/workspace/{account}/sessions/{id}/)
  → [LLM 生成会话摘要 → 写入 meta.yaml]
  → (清除 Redis WAL + 群上下文退订)
```

---

## 图元布局建议

```
┌──────────────────────────────────────────────────────────────┐
│                    第三组: AI & 工具服务                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │
│  │  Mimo    │  │ MiniMax  │  │ 阿里百炼  │  │ SiliconFlow │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬──────┘  │
│       │             │             │                │          │
│  ┌────┴─────────────┴─────────────┴────────────────┴──────┐  │
│  │                      HpAgent                           │  │
│  │              (核心系统 / 手脑分离架构)                    │  │
│  └──┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬───┘  │
│     │      │      │      │      │      │      │      │       │
│  ┌──┴──┐┌──┴──┐┌──┴──┐┌──┴──┐┌──┴──┐┌──┴──┐┌──┴──┐┌──┴──┐  │
│  │Hind-││Chro-││Tav- ││股票 ││高德 ││八字 ││菜谱 ││本地 │  │
│  │sight││maDB ││ily  ││SDK ││地图 ││MCP ││MCP ││FS  │  │
│  └─────┘└─────┘└─────┘└─────┘└─────┘└─────┘└─────┘└─────┘  │
└──────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────┐
│             第二组: 基础设施 & 桥接              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ Temporal │  │  Redis   │  │  NapCat  │    │
│  │ Server   │  │          │  │  客户端   │    │
│  └──────────┘  └──────────┘  └────┬─────┘    │
│                                   │           │
│                            ┌──────┴──────┐    │
│                            │  QQ 服务器   │    │
│                            └──────┬──────┘    │
└───────────────────────────────────┼───────────┘
                                    │
┌───────────────────────────────────┼───────────┐
│             第一组: 人类用户        │            │
│                            ┌──────┴──────┐    │
│                            │  QQ 用户     │    │
│                            │ (群聊/私聊)  │    │
│                            └─────────────┘    │
└───────────────────────────────────────────────┘
```

---

## 图例说明

| 图元类型 | 表示 |
|----------|------|
| 矩形（实线边框） | 外部系统 / 服务 |
| 矩形（双线边框） | 核心系统 HpAgent |
| 人形图标 | 人类用户 |
| 实线箭头 + 标签 | 数据流（标注数据类型或协议） |
| 虚线箭头 | 可选/降级路径（如 LLM fallback） |
| WebSocket 符号 | ws:// 长连接 |
| HTTP 符号 | REST API 调用 |
| gRPC 符号 | gRPC 调用 |
