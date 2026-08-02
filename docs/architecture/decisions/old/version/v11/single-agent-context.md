# Single-Agent 上下文组成

> HpAgent v11 — 单 Agent 模式下，每次 LLM 调用时送入模型的完整上下文结构分析。

---

## 总体架构

单 Agent 模式采用 **ReAct Loop**（推理-行动循环），核心流程如下：

```
用户消息
  → [记忆召回] Hindsight API 召回长期记忆
  → [工具检索] ChromaDB RAG 检索 Top-K 相关工具
  → [上下文构建] HarnessContextBuilder.build() 组装 system prompt + 对话历史
  → [模型调用] ResourcePool.generate() → ModelClient HTTP POST
  → [工具执行] 如果有 tool_calls，执行后结果注入上下文，继续循环
  → 直到无 tool_calls 或达到 max_tool_turns
```

一次 LLM API 调用发送的上下文由 **System Prompt（系统提示词）** + **Messages（对话历史）** + **Tools（工具定义）** 三大部分组成，外加 **Token 预算管理** 控制总量。

入口调用链：`HarnessRunner.process_turn()` (`src/harness/runner.py:104`) → `_build_context()` (`runner.py:359`) → `HarnessContextBuilder.build()` (`context_builder.py:276`)

---

## 一、System Prompt（系统提示词）

System Prompt 由 `HarnessContextBuilder._build_system_prompt()` (`src/harness/context_builder.py:409`) 按固定顺序拼接，各部分通过双换行 `\n\n` 连接为一个整体字符串，作为 `messages[0]`（`role: "system"`）发送。

### 拼接顺序

```
渠道身份 → 风格提示 → 跨渠道检测 → 工具纪律 → 环境感知
  → 记忆注入 → 会话运行摘要 → 项目上下文 → 额外上下文
```

### 各组件详解

#### 1. 渠道身份声明（Channel Identity）

根据消息来源渠道选择对应的人设 Prompt。

- **来源文件**: `config/prompts/identities.yaml`
- **加载器**: `PromptLoader.get_identity()` (`src/harness/prompts.py:55`)
- **触发逻辑**: `HarnessContextBuilder._pick_identity()` (`context_builder.py:469`)
  - 优先级：显式传入的 `system_prompt` > 渠道映射 > 默认身份
- **渠道映射** (`identities.yaml` 的 `channel_map`):
  - `napcat` → QQ 猫娘 nono 身份
  - `console` → CLI 精炼助手身份
  - `web` → Web Markdown 助手身份
  - `default` → 通用 AI 助手
- **渠道检测** (`_detect_channel`, `context_builder.py:385`): 从 `events` 第一条 `USER_MESSAGE` 的 `content["channel_type"]` 提取

#### 2. 风格引导（Style Guidance）

根据渠道注入行为风格约束。

- **来源文件**: `config/prompts/guidance.yaml`
- **加载器**: `PromptLoader.get_guidance()` (`prompts.py:83`)
- **触发逻辑**: `HarnessContextBuilder._pick_style_guidance()` (`context_builder.py:490`)
  - `NAPCAT` → `chat_personality`（QQ 聊天行为准则：短句、语气、内容规则）
  - `CONSOLE` → `console_style`（终端交互规范：极简、无 Markdown、直奔主题）
  - 其他渠道 → 不注入
- **开关**: `enable_chat_personality`（默认 `True`）

#### 3. 跨渠道检测（Cross-Channel Hint）

如果同一会话中检测到来自多个不同渠道的用户消息，追加提示告知模型"用户正在多端同时对话"。

- **来源文件**: `config/prompts/system.yaml`（`cross_channel` 模板）
- **加载器**: `PromptLoader.format_cross_channel()` (`prompts.py:121`)
- **触发逻辑**: `HarnessContextBuilder._build_cross_channel_hint()` (`context_builder.py:502`)
  - 遍历 events，统计 `channel_type` 种类，> 1 时触发

#### 4. 工具使用纪律（Tool Enforcement）

防止模型只"描述意图"而不实际调用工具。

- **来源文件**: `config/prompts/guidance.yaml`（`tool_enforcement` 字段）
- **加载器**: `PromptLoader.get_guidance("tool_enforcement")` (`prompts.py:83`)
- **触发逻辑**: `context_builder.py:437-440`，`enable_tool_guidance=True` 时注入
- **内容摘要**: "you must use your tools to take action — do not describe what you would do ... execute it right now"

#### 5. 环境感知（Environment Hints）

告知模型其运行环境的基本信息。

- **来源文件**: `config/prompts/environment.yaml`（`docker` 字段）
- **加载器**: `PromptLoader.get_environment("docker")` (`prompts.py:96`)
- **触发逻辑**: `HarnessContextBuilder._build_environment_hints()` (`context_builder.py:520`)
- **内容**: "你拥有一个专属 Linux 工作环境，Git 进行版本管理，所有操作都在 ./ 下。"

#### 6. 记忆注入（Recalled Memories）

从 Hindsight 长期记忆系统召回的格式化记忆文本。

- **来源**: Hindsight API（外部记忆服务，pgvector 向量检索）
- **召回入口**: `SessionStore.recall_memories()` (`src/session/store.py:327`)
  - 查询纯净化（去 @提及、CQ 码）→ `HindsightClient.recall()` → 格式化
- **调用时机**: `runner.py:196-206`，每个 `process_turn` 开始时召回一次
- **格式化**: `HindsightClient.recall_formatted()` 返回模板化文本，直接拼入 system prompt 的 `recalled_memories` 位置
- **参数**: `top_n=5`, `tags_match="any_strict"`, 渠道隔离（`channel_type` + `group_id` + `scope`）

#### 7. 会话运行摘要（Running Summary）

当对话轮次过多时，LLM 对早期对话的压缩摘要。

- **来源**: `HarnessRunner._maybe_compress_history()` (`runner.py:513`)
- **触发**: 每 `compress_interval`（默认 8）轮触发一次
- **生成**: 取早期事件（最近 8 条之前），构建摘要 prompt → 调用 LLM（`summary_budget=2000` tokens）→ 存入 `Session.metadata["running_summary"]`
- **注入位置**: `context_builder.py:449-455`，在记忆之后、项目上下文之前
- **格式**: `"## 历史摘要\n\n以下为早轮对话的摘要（而非完整记录），仅作背景参考：\n" + running_summary`

#### 8. 项目上下文文件（Project Context Files）

自动发现并加载工作目录下的项目约定文件。

- **来源文件**（优先级自上而下，仅加载第一个命中）:
  1. `.hermes.md` / `HERMES.md` — 逐级向上搜索至 git 根目录
  2. `AGENTS.md` / `agents.md` — 仅在 cwd
  3. `CLAUDE.md` / `claude.md` — 仅在 cwd
  4. `.cursorrules` + `.cursor/rules/*.mdc` — 仅在 cwd
- **独立追加**: `SOUL.md` 从 `HERMES_HOME` 目录独立加载，始终追加
- **加载函数**: `_build_context_files()` (`context_builder.py:189`) → 内部调用 `_load_hermes_md()` / `_load_context_file()` / `_load_cursorrules()` / `_load_soul_md()`
- **安全扫描**: 加载前检查不可见 Unicode 字符 + prompt 注入攻击模式，命中则拦截 (`_scan_context_content`, `context_builder.py:58`)
- **截断**: 单文件 > 20,000 字符时，保留头部 70% + 尾部 20%，中间插入截断标记 (`_truncate_content`, `context_builder.py:82`)
- **开关**: `enable_context_files`（默认 `True`）

#### 9. 额外上下文（Extra Context）

包括跨会话上下文继承和其他动态注入。

- **跨会话上下文** (`context_builder.py:308-320`): 从 `CONTEXT_INHERIT` 事件提取 `summary`，格式化为 `"## 跨会话上下文\n\n以下信息继承自之前的会话：\n..."` 注入
- **来源**: `SessionStore.create_session()` (`store.py:115-124`) — 创建新会话时查询上一个 session 摘要并写入 `CONTEXT_INHERIT` 事件
- **extra_context 参数**: 保留给未来扩展使用

---

## 二、Messages（对话历史）

由 `HarnessContextBuilder.build()` (`context_builder.py:327-381`) 将 `Event` 列表转换为 LLM 标准 messages 格式。

### 事件过滤

只保留三种事件类型进入 messages：
- `EventType.USER_MESSAGE` → `role: "user"`
- `EventType.MODEL_MESSAGE` → `role: "assistant"`（含 tool_calls 时用 Anthropic content block 格式）
- `EventType.TOOL_RESULT` → `role: "user"`（tool_result 格式）

### 事件内容提取

| 事件类型 | 提取方法 | 输出格式 |
|---------|---------|---------|
| `USER_MESSAGE` | `_extract_user_content()` (`context_builder.py:526`) | `"key: value, ..." + content` |
| `MODEL_MESSAGE` | `_extract_model_content()` (`context_builder.py:537`) | 纯文本或 `[{type:"text",...}, {type:"tool_use",...}]` |
| `TOOL_RESULT` | `_extract_tool_result()` (`context_builder.py:562`) | 结果字符串或错误信息，安全网截断 50000 字符 |

### Token 感知截断 (`context_builder.py:337-371`)

当 `token_budget > 0` 时启用：
1. 计算 `available = token_budget - generation_headroom`
2. 扣除 system prompt 的估算 token 数
3. 从右向左累加每条 event 的 token 成本（使用 `common/token_counter.py` 的字符估算），超出剩余预算时停止
4. 保留最近的 event（越新的对话越重要）

当 `token_budget = 0` 时走旧路径：按 `max_turns * 2` 条消息截断。

---

## 三、Tools（工具定义）

工具定义不进入 system prompt 或 messages，而是作为独立的 `tools` 参数传入 LLM API。

### 工具检索流程

**入口**: `HarnessRunner._get_tools()` (`runner.py:396`)

```
Sandbox.registry.retrieve_for_llm(query=user_content, top_k=8)
  → ToolRetriever.retrieve(query, top_k)
    → 用户 query → Embedding 向量化
    → ChromaDB 向量检索（cosine similarity）
    → [可选] Reranker 精排（扩大召回 top_k*2，精排后取 top_k）
    → 返回 LangChain BaseTool 列表
  → 每个 tool 转为 OpenAI function 格式
    → {"type": "function", "function": {"name":..., "description":..., "parameters":...}}
```

### 工具来源

| 类别 | 来源 | 说明 |
|------|------|------|
| **native** | `src/sandbox/tools/` 本地工具 | 进程内执行（fs_read/write, bash, web_search 等），绑定 workspace 路径 |
| **mcp** | MCP Server 远端工具 | `src/sandbox/tools/adapters/mcp.py`，通过 `servers.yaml` 配置，可选连接 |
| **skill** | Skill 定义文件 | `tools/skills/*.yaml`，展开为子调用流水线 |

### 工具向量化

`ToolVectorStore` (`src/sandbox/tools/retriever.py:14`) 在启动时将工具定义（`name + description + parameter descriptions`）向量化存入 ChromaDB，持久化路径 `tools/vectors/`。

---

## 四、Token 预算管理

### 配置参数（`AgentConfig`, `src/orchestration/config.py:337`）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `context_budget` | 32000 | 总上下文 token 预算 |
| `generation_headroom` | 4000 | 留给模型输出的 token 空间 |
| `summary_budget` | 2000 | 运行摘要生成最大 token |
| `memories_budget` | 2000 | 召回记忆最大 token |
| `tool_result_max_chars` | 2000 | 工具结果截断阈值（字符数） |
| `tool_result_truncated` | 500 | 截断后保留字符数（本地工具） |

### Token 估算器（`src/common/token_counter.py`）

不依赖 tiktoken，使用保守估算策略：
- CJK 字符：每字符 ≈1 token
- ASCII 字符：每 3 字符 ≈1 token
- 其他 Unicode：每 2 字符 ≈1 token
- 预算计算预留 20% 安全边际

### 工具结果截断（`runner.py:461-505`）

所有工具输出统一走 `_apply_truncation()`：
- MCP 工具：按 `servers.yaml` 中配置的 `truncate_limit`（`None`=不截断）
- 本地工具：超出 `tool_result_max_chars`(2000) 时截断至 `tool_result_truncated`(500)，完整内容写入 `file_store` 备查

---

## 五、数据流一览

```
                    ┌──────────────────────────────────────────┐
                    │        HarnessRunner.process_turn()       │
                    │        src/harness/runner.py:104          │
                    └──────────────────────────────────────────┘
                                      │
           ┌──────────────────────────┼──────────────────────────┐
           │                          │                          │
           ▼                          ▼                          ▼
   ┌───────────────┐       ┌───────────────────┐      ┌──────────────────┐
   │ SessionStore  │       │ SandboxManager    │      │ HarnessContext   │
   │ .recall_      │       │ .get_sandbox()    │      │ Builder.build()  │
   │ memories()    │       │ .retrieve_for_    │      │ context_builder  │
   │ store.py:327  │       │ llm(query, top_k) │      │ .py:276          │
   └───────┬───────┘       └────────┬──────────┘      └────────┬─────────┘
           │                        │                          │
           ▼                        ▼                          ▼
   ┌───────────────┐       ┌───────────────────┐      ┌──────────────────┐
   │ Hindsight API │       │ ToolVectorStore   │      │ _build_system_   │
   │ /recall       │       │ (ChromaDB)        │      │ prompt()         │
   │ pgvector 检索  │       │ + ToolRetriever   │      │ context_builder  │
   │               │       │ + Embedding       │      │ .py:409          │
   └───────────────┘       │ + Reranker        │      └────────┬─────────┘
                           └───────────────────┘               │
                                                               ▼
                                                    ┌──────────────────┐
                                                    │ System Prompt    │
                                                    │ (9 parts)        │
                                                    │ + Messages       │
                                                    │ + Tools          │
                                                    └────────┬─────────┘
                                                             │
                                                             ▼
                                                    ┌──────────────────┐
                                                    │ ResourcePool     │
                                                    │ .generate()      │
                                                    │ → ModelClient    │
                                                    │ → HTTP POST      │
                                                    └──────────────────┘
```

### 关键源码索引

| 组件 | 文件 | 关键行 |
|------|------|--------|
| 主循环入口 | `src/harness/runner.py` | `process_turn()` L104 |
| 上下文构建入口 | `src/harness/runner.py` | `_build_context()` L359 |
| System Prompt 拼装 | `src/harness/context_builder.py` | `_build_system_prompt()` L409 |
| 渠道身份选择 | `src/harness/context_builder.py` | `_pick_identity()` L469 |
| 风格引导选择 | `src/harness/context_builder.py` | `_pick_style_guidance()` L490 |
| 项目上下文加载 | `src/harness/context_builder.py` | `_build_context_files()` L189 |
| Messages 构建 | `src/harness/context_builder.py` | `build()` L276 |
| Token 感知截断 | `src/harness/context_builder.py` | L337-371 |
| Prompt 加载器 | `src/harness/prompts.py` | `PromptLoader` L28 |
| 记忆召回 | `src/session/store.py` | `recall_memories()` L327 |
| 工具检索 | `src/sandbox/tools/retriever.py` | `ToolRetriever.retrieve_for_llm()` L134 |
| 模型调用 | `src/resources/model_client.py` | `ModelClient.generate()` L61 |
| Token 估算 | `src/common/token_counter.py` | `estimate_tokens()` L21 |
| 上下文预算配置 | `src/orchestration/config.py` | `AgentConfig` L337 |
| Prompt YAML 配置 | `config/prompts/*.yaml` | identities / guidance / environment / system |
