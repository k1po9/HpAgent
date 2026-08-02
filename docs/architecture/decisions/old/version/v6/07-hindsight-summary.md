# Hindsight 记忆系统接入实施总结

> 版本: v6  
> 日期: 2026-05-09  
> 状态: 已完成  
> 测试: 65/65 通过 (22 nsjail + 23 workspace + 20 hindsight)

---

## 1. 实施范围

| 目标 | 状态 | 实现 |
|------|------|------|
| Hindsight REST API 客户端封装 | 已完成 | `HindsightClient` — retain / recall / reflect 三大 API |
| Docker Compose 服务定义 | 已完成 | hindsight-postgres (pgvector) + hindsight |
| Temporal Activity 集成 | 已完成 | recall_activity / retain_activity / reflect_activity |
| 上下文构建器记忆注入 | 已完成 | `build_context_activity` 接受 recalled_memories |
| Workflow 记忆编排 | 已完成 | recall 在模型调用前，retain 在每轮后 |
| 降级策略 | 已完成 | Hindsight 不可用时所有调用返回安全默认值 |

---

## 2. 文件变更汇总

### 新增文件 (3)

| 文件 | 行数 | 角色 |
|------|------|------|
| `src/memory/__init__.py` | 18 | 模块导出 |
| `src/memory/hindsight_client.py` | 225 | HindsightClient — Hindsight REST API 封装，3 个核心 API + 降级策略 |
| `test/test_hindsight.py` | 185 | 20 个测试用例 |

### 修改文件 (6)

| 文件 | 变更 | 描述 |
|------|------|------|
| `docker-compose.yaml` | +23 行 | 新增 hindsight-postgres (pgvector) + hindsight 服务 |
| `src/requirements.txt` | +1 行 | 新增 hindsight-client 依赖 |
| `src/harness/activities.py` | +115 行 | _hindsight_client 注入 + 3 个新 Activity |
| `src/harness/context_builder.py` | +6 行 | build() 和 _build_system_prompt() 接受 recalled_memories |
| `src/orchestration/workflow.py` | +35 行 | _process_turn() 新增 recall/retain 编排 |
| `src/orchestration/worker.py` | +25 行 | HindsightClient 初始化 + 注入 + Activity 注册 |

---

## 3. 核心设计决策

### 3.1 为什么 HindsightClient 是独立的 async HTTP 客户端？

- Hindsight 是独立部署的 REST 服务，与 HpAgent 通过网络通信
- 使用 httpx（项目已有依赖）而非追加新的 HTTP 库
- 所有方法都是 async，与 Temporal Activity 的异步模型一致

### 3.2 为什么 recall 在关键路径上？

- 记忆只有在模型"看到"时才有价值——必须在 system prompt 组装前完成召回
- recall Activity 有 10s 超时控制，超时后 context builder 收到空记忆
- 降级路径: Hindsight 不可用 → recall 返回空列表 → system prompt 无记忆段落 → 模型正常回复

### 3.3 为什么 retain 不在子 Workflow 中异步触发？

- 简化设计：Temporal 的 `execute_activity` 已在 send_response 之后调用，用户已收到回复
- retain 的额外延迟（~200-500ms）不阻塞用户感知到的响应时间
- 避免引入子 Workflow 管理复杂度

### 3.4 为什么 reflect 的调度不在代码中设置？

- Temporal Schedule 更适合通过 Web UI 或独立部署脚本创建
- reflect_activity 已注册在 Worker 上，可被任何调用方触发
- 调度配置可通过 Temporal Web UI (`localhost:8088`) 手动创建，或通过 CLI 工具

---

## 4. 数据流

### 4.1 Agentic Loop（每轮）

```
_process_turn(user_message)
  ├─ recall_activity(query, account_id, session_id)    ← 检索相关记忆
  │    └─ HindsightClient.recall() → POST /api/v1/recall
  │         → 语义+B M25+图谱+时序 → Reranker 精排
  │    ← {memories: [...], formatted: "# 相关记忆\n..."}
  │
  ├─ build_context_activity(events, channel, memories)  ← 组装 system prompt（含记忆）
  │    └─ HarnessContextBuilder.build(recalled_memories=...)
  │         → system prompt 新增"# 相关记忆"段落
  │
  ├─ get_available_tools_activity()
  ├─ call_model_activity(context, tools)                ← LLM 看到记忆
  ├─ execute_tool_activity() × N（如有 tool_calls）
  ├─ send_response_activity(final_content)              ← 用户收到回复
  │
  └─ retain_activity(turn_events, account_id, session_id)  ← 异步提取并存储记忆
       └─ HindsightClient.retain() → POST /api/v1/retain
            → LLM 提取偏好/事实/决策/关系
            → bge-m3 Embedding 编码
            → pgvector 持久化
       ← {stored: N}
```

### 4.2 定期反思（Temporal Schedule，每 6h）

```
reflect_activity(account_id)
  └─ HindsightClient.reflect() → POST /api/v1/reflect
       → 记忆关联 + 矛盾检测 + 知识抽象 + 经验总结
  ← {insights: N}
```

---

## 5. 降级行为矩阵

| 场景 | recall | retain | reflect | 核心流程 |
|------|--------|--------|---------|----------|
| Hindsight 未配置 | 返回 `[]` | 返回 `0` | 返回 `0` | 正常 |
| Hindsight 不可达 | 返回 `[]` | 返回 `0` | 返回 `0` | 正常 |
| API 超时 | 返回 `[]` | 返回 `0` | 返回 `0` | 正常 |
| API 返回异常 | 返回 `[]` | 返回 `0` | 返回 `0` | 正常 |
| 一切正常 | 返回记忆列表 | 持久化 N 条 | 产生 M 条洞察 | 增强 |

---

## 6. 配置字段

```yaml
# config.yaml 新增字段（均有默认值）
hindsight:
  base_url: "http://hindsight:8000"     # Hindsight 服务地址
  api_key: ""                            # API 密钥（可选）
  timeout: 30.0                          # 请求超时秒数
  enabled: true                          # 是否启用记忆功能
```

---

## 7. Docker Compose 新增服务

```yaml
hindsight-postgres:
  image: pgvector/pgvector:pg16          # pgvector 扩展的 PostgreSQL 16
  environment:
    POSTGRES_USER: hindsight
    POSTGRES_PASSWORD: hindsight
    POSTGRES_DB: hindsight

hindsight:
  image: hindsight:latest
  environment:
    - DATABASE_URL=postgresql://...       # 连接到 pgvector
    - EMBEDDING_MODEL=BAAI/bge-m3        # 中文优化 Embedding 模型
    - RERANKER_MODEL=BAAI/bge-reranker-v2-m3  # 中文优化 Reranker
```

---

## 8. 测试覆盖

```
test/test_hindsight.py — 20 passed

TestMemoryItem (4 tests):
  from_dict_full                   PASSED
  from_dict_minimal                PASSED
  from_dict_alt_keys               PASSED
  default_values                   PASSED

TestHindsightClientConfig (5 tests):
  default_init                     PASSED
  custom_init                      PASSED
  base_url_strips_trailing_slash   PASSED
  headers_without_api_key          PASSED
  headers_with_api_key             PASSED

TestHindsightClientDisabled (4 tests):
  recall/retain/reflect_disabled   PASSED  ← 降级路径验证
  recall_formatted_disabled        PASSED

TestRecallFormatted (2 tests):
  empty_memories                   PASSED
  formatted_structure              PASSED

TestHindsightClientNoServer (4 tests):
  recall/retain/reflect_no_server  PASSED  ← 服务不可用降级验证
  recall_formatted_no_server       PASSED

TestMemoryItemSorting (1 test):
  sort_by_relevance                PASSED
```

---

## 9. 未实施项（后续迭代）

| 项目 | 优先级 | 说明 |
|------|--------|------|
| Temporal Schedule 自动注册 | 高 | reflect 定时任务需通过 Web UI 或 CLI 手动创建 |
| recall 查询优化 | 中 | 当前用原始 user message 作为 query，可改为 LLM 提取的搜索意图 |
| 记忆衰减/清理 | 中 | 依赖 Hindsight 服务端的 reflect 清理策略 |
| 跨用户记忆共享 | 低 | 团队协作场景下共享记忆（需 Hindsight 服务端支持） |
| 记忆可视化 Dashboard | 低 | 查看/编辑/删除用户记忆的管理界面 |
| 记忆导出/迁移 | 低 | 从 Hindsight 导出记忆快照 |
