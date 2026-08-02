# 04 — 关键时序（单 Agent）

> 对应视觉文件：[`diagrams/04_sequence.excalidraw`](diagrams/04_sequence.excalidraw)

本文是单 Agent 关键运行时流程和数据流的设计事实源；Excalidraw 只做人工维护的视觉表达。

---

## 5. 关键业务流程与数据流

### 5.1 流程一：QQ 群聊 @bot 对话回合（核心路径）

这是系统最高频的数据流，每一次 QQ 用户 @bot 触发一次完整的 Agentic Loop。

```
参与者: QQ 用户 → QQ 服务器 → NapCat → NapCatChannel → Worker.handle_message()
         → Temporal OrchestrationWorkflow → process_turn_activity()
         → TurnOrchestrator.process_turn() → ChannelRouter.send() → NapCatChannel
         → NapCat → QQ 服务器 → QQ 用户

时序:

1. QQ 用户: 在群聊中发送 "@nono 帮我查一下明天深圳的天气"
2. QQ 服务器: 将消息推送到 QQ 客户端
3. NapCat: 接收 QQ 消息 → 封装为 OneBot v11 JSON:

   {
     "post_type": "message",
     "message_type": "group",
     "group_id": 123456,
     "sender": {"user_id": 111, "nickname": "小明"},
     "message": [{"type": "at", "data": {"qq": "bot_qq"}},
                 {"type": "text", "data": {"text": "查天气"}}]
   }

4. NapCat → NapCatChannel: 通过 WebSocket 发送 JSON 字符串
5. NapCatChannel.normalize_message(raw):
     解析 JSON → 提取 at 段 → 检测 @bot → 剥离 CQ 码 → 构造 UnifiedMessage

   UnifiedMessage(
     sender_id="111",
     content="查天气",
     channel_type=ChannelType.NAPCAT,
     metadata={
       "group_id": 123456,
       "sender_name": "小明",
       "is_at_bot": true
     }
   )

6. NapCatChannel._callback(message) → Worker.handle_message(message)
7. Worker.handle_message():
     a. AccountService.resolve(NAPCAT, "111") → account_id
     b. GroupContextStore.append(group_id, message)  # 写群上下文
     c. 是 @bot → 构造 user_message = {
          "account_id": "...",
          "content": "查天气",
          "channel_type": "napcat",
          "sender_name": "小明",
          "group_id": 123456
        }
     d. Temporal: 以 workflow_id="hpagent-{account_id}" 启动/信号 OrchestrationWorkflow

8. OrchestrationWorkflow.run():
     a. 调用 process_turn_activity(user_message)
        → TurnOrchestrator.process_turn()
     b. 进入等待: 下一条消息信号 或 空闲超时(5分钟)

9. TurnOrchestrator.process_turn():
     9a. HyDE 改写:
         原始查询 "查天气" → ResourcePool.generate(model="fast", hyde_rewrite prompt)
           → "用户希望查询明天深圳的天气情况"

     9b. 记忆召回:
         SessionStore.recall_memories(account_id, "用户希望查询明天深圳的天气情况",
                                      tags="group:123456,channel:napcat")
           → HindsightClient.recall()
             → POST /v1/default/banks/hpagent-u-{account_id}/memories/recall
             → Hindsight: 查询向量嵌入 → pgvector cosine + BM25 + 知识图谱
             → 返回 [{text: "用户小明之前查询过天气，偏好简洁格式", score: 0.92}, ...]
           → 格式化记忆文本 → "# 相关记忆\n- 用户小明之前查询过天气..."

     9c. 上下文构建:
         HarnessContextBuilder.build(events, memories_text, group_context_text)
           → messages = [
               {role: "system", content: "你是 nono，一个 QQ 群聊助手..." + 系统提示词 + 准则 + 记忆},
               ...历史事件转换后的 messages
             ]

     9d. 工具选择:
         Sandbox.select_tools(query="查天气", top_k=8)
           → ToolRegistry.retrieve_for_llm_multi(["查天气"], 8, 12)
             → ToolRetriever.retrieve("查天气", 12)
               → SiliconFlow BGE-M3 embedding → ChromaDB 向量搜索
               → 候选: [("amap_weather", 0.89), ("web_search", 0.72), ...]
               → SiliconFlow BGE Reranker 重排序
               → 返回 Top-8 OpenAI function calling 工具定义

     9e. LLM 推理:
         ResourcePool.generate(messages, model_selector="chat", tools=selected_tools)
           → 遍历降级链 [Mimo pro → ...]
           → ModelClient 发送 POST → 返回 ModelResponse(
               content="我需要查询天气...",
               tool_calls=[{name: "amap_weather", arguments: {city: "深圳"}}]
             )

     9f. 工具执行:
         Sandbox.execute("amap_weather", {city: "深圳"})
           → ToolRegistry.execute("amap_weather", {city: "深圳"})
             → MCPToolManager → 高德地图 MCP 服务器 → HTTP 请求
             → ToolResult(success=True, output="深圳明天: 晴, 25-32°C")
           → 输出截断检查(50K字符阈值，不触发)
           → 结果写回事件流 → 循环到 9e (LLM 继续)

     9g. 回复发送:
         TurnOrchestrator._send_response(response_content)
           → ChannelRouter.send(UnifiedMessage(
               content="深圳明天晴，25-32°C...",
               channel_type=NAPCAT,
               metadata={group_id: 123456, at_trigger: true}
             ))
           → NapCatChannel.send_message():
               构造 OneBot v11 send_msg JSON:
               {
                 "action": "send_group_msg",
                 "params": {
                   "group_id": 123456,
                   "message": "[CQ:at,qq=111] 深圳明天晴，25-32°C ..."
                 }
               }
               发送到所有连接的 NapCat WebSocket 客户端
               (速率限制: 2 秒间隔)

     9h. 记忆留存:
         SessionStore.retain_memories(account_id, all_events, context, tags)
           → HindsightClient.retain()
             → POST /v1/default/banks/hpagent-u-{account_id}/memories
             → Hindsight 异步: LLM 提取事实 → Embedding → pgvector 存储
           → 写入 MEMORY_RETAIN 审计事件

10. OrchestrationWorkflow: 更新最后活动时间 → 进入等待

11. [空转超时] 5 分钟无新消息:
    OrchestrationWorkflow 触发 idle_timeout 分支
      → archive_session_activity()
        → TurnOrchestrator.archive_session()
          → 全量事件 → history.jsonl + meta.yaml
          → 清理 WAL + Redis + 群上下文退订 + RAG 缓存清理
      → 工作流结束
```

**关键数据结构传递**：
- 入站: `OneBot v11 JSON` → `UnifiedMessage`（`sender_id`, `content`, `channel_type`, `metadata`）
- 记忆: `MemoryItem(text, source, score)` → 文本拼接
- LLM: `messages: List[Dict[role, content]]` + `tools: List[Dict[name, description, parameters]]`
- 工具: `ToolCall(name, arguments)` → `ToolResult(success, output, error, metadata)`
- 出站: `UnifiedMessage` → `OneBot v11 send_msg JSON`

---

### 5.2 流程二：系统启动与依赖组装

```
参与者: Docker Compose → hpagent 容器 → main.py → worker.py

时序:

1. Docker Compose:
     ├── 启动 temporal-postgres → 健康检查通过
     ├── 启动 temporal → 健康检查通过
     ├── 启动 hindsight-postgres → 健康检查通过
     ├── 启动 hindsight → 健康检查通过
     └── 启动 redis → 健康检查通过
     ── 全部就绪 → 启动 hpagent ──

2. main.py:
     a. load_dotenv() → 加载 .env 环境变量
     b. load_config("config/config.yaml", "config/models.yaml")
        → AppConfig.from_yaml()
          → 解析 config.yaml → _from_dict() 递归填充 AppConfig dataclass
          → 解析 models.yaml → ModelsConfig.from_yaml()
          → 解析 config/prompts/*.yaml → PromptsConfig.from_dir()
          → 解析 config/agents.yaml → AgentEntry.from_dict()
          → _apply_env_overrides() 应用环境变量
        → 返回 AppConfig

3. worker.py → start_worker(config):
     a. 初始化日志: LogManager(config)
     b. 初始化 CredentialManager + ResourcePool
        → 遍历 models.yaml 的 providers: 注册 ModelEndpoint
        → 遍历 models.yaml 的 chat/fast/embedding等: configure_fallback_group()
        → ResourcePool.initialize_models()
     c. 初始化存储: RedisCache → LocalFileStore
     d. 初始化 SessionStore(redis, hindsight_client, wal_dir)
     e. 初始化 WorkspaceDB(.data/workspace/db.sqlite)
     f. 初始化 ToolVectorStore + ToolRetriever (ChromaDB → tools/vectors/)
     g. 初始化 ToolRegistry → 注册本地工具 (bash/fs_read/write/edit/glob_/grep/reminder)
     h. [可选] 连接 MCP 服务器 → 注册 MCP 工具
     i. [可选] 加载 Skills → 注册 Skill 工具
     j. 初始化 SandboxManager(tool_registry, nsjail_config)
     k. 初始化渠道:
        → 遍历 config.channels.enabled
        → 按 ChannelType 查 _channel_factories 字典
        → 实例化渠道 → 设置 bot_name → 注册到 ChannelRouter
        → channel.start_monitor(handle_message)
     l. 初始化 TaskScheduler → 注册 handler("user_reminder", callback)
     m. 构建 TurnOrchestrator(session_store, sandbox_manager, channel_router,
                          resource_pool, context_builder, agent_config)
     n. 注入到 activities.inject(turn_orchestrator)
     o. 连接 Temporal Server(host:port)
     p. 注册 Activity + Workflow → Worker.run()
     q. 启动后台任务: scheduler._poll_loop, sandbox_cleanup_loop
     r. 进入 asyncio 事件循环 → await Future() (永久运行)
```

---

### 5.3 流程三：多模型降级切换

```
参与者: TurnOrchestrator → ResourcePool → ModelClient(主) → ModelClient(备用1) → ...

时序:

1. TurnOrchestrator: ResourcePool.generate(messages, model_selector="chat", tools=[...])
2. ResourcePool: 查 _fallback_groups["chat"] = [MimoPro, ...]
3. 尝试 MimoPro:
     a. ModelClient.generate(messages, tools)
     b. POST {base_url}/chat/completions → 超时 60s → TimeoutError
     c. ResourcePool 捕获 TimeoutError → logger.warning("DEGRADATION: ...")
4. 尝试下一个条目（如果存在）:
     a. ModelClient2.generate(messages, tools)
     b. POST → 返回 500 → ModelAPIError
     c. ResourcePool 捕获 ModelAPIError → 继续下一个
5. ... 所有条目失败:
     a. ResourcePool 抛出最终 ModelAPIError("All models in fallback group 'chat' failed")
     b. TurnOrchestrator 捕获 → 触发生成兜底回复:
        "抱歉，我暂时无法处理这个消息，请稍后再试。"

6. [成功路径] 某条目返回 ModelResponse:
     a. 记录 [TIMING] 日志 (模型名 + 延迟)
     b. 返回 TurnOrchestrator
```

---

### 5.4 流程四：定时提醒触发

```
参与者: TaskScheduler → 本地 JSON → TurnOrchestrator → QQ 用户

时序:

1. [注册] Worker.handle_user_reminder_request():
     a. TaskScheduler.schedule(task_id, trigger_at=tomorrow_9am,
                               handler_id="user_reminder",
                               params={user_id: "111", group_id: 123456, message: "起床啦"})
     b. TaskScheduler 内部: _tasks[task_id] = {...}; _save() → .data/scheduler/scheduled_tasks.json

2. [后台轮询] TaskScheduler._poll_loop() 循环 (interval=5s):

3. [触发] 当前时间 >= trigger_at:
     a. TaskScheduler: 调用 handler_registry["user_reminder"](params)
     b. handler → TurnOrchestrator 构造提醒消息
     c. ChannelRouter.send(UnifiedMessage(
          content="⏰ 提醒: 起床啦",
          channel_type=NAPCAT,
          metadata={group_id: 123456, at_trigger: true}
        ))
     d. NapCatChannel.send_message() → 发送到 QQ 群
     e. TaskScheduler: 删除任务 (一次性) 或更新 next_run_at (周期性)
     f. _save() → 更新 scheduled_tasks.json

4. QQ 用户: 收到提醒消息
```

---

### 5.5 流程五：会话归档

```
参与者: OrchestrationWorkflow (空闲超时) → TurnOrchestrator → 文件系统 + LLM

时序:

1. 5 分钟无新消息 → OrchestrationWorkflow.idle_timeout 触发
2. 调用 archive_session_activity(session_id, account_id)
3. TurnOrchestrator.archive_session():
     a. SessionStore.archive() → 返回全量事件列表
     b. write_history_jsonl(events, ".data/workspace/{account}/sessions/{id}/history.jsonl")
        → 逐行写入 JSONL 文件
     c. delete_wal(".data/active-sessions/{id}.jsonl") → 删除临时 WAL
     d. generate_session_summary(events):
        → ResourcePool.generate(model="fast", summary_prompt + events)
        → LLM 返回会话摘要文本
     e. write_meta_yaml(summary, tags, tool_stats)
        → 写入 ".data/workspace/{account}/sessions/{id}/meta.yaml"
     f. SessionStore.clear_redis(session_id) → 删除 Redis 缓存
     g. GroupContextStore.unsubscribe(group_id) → 退订群上下文
     h. SandboxManager.destroy(session_id) → 回收沙箱
     i. RAG 工具缓存清理
4. OrchestrationWorkflow: workflow.logger.info("Session archived: ...")
5. 工作流 Complete
```

---

