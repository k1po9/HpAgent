# Hindsight 记忆系统接入评估

> 版本: v6  
> 日期: 2026-05-09  
> 状态: 评估完成，待实施

---

## 1. 架构影响分析

### 1.1 当前记忆机制

| 层面 | 现状 | 问题 |
|------|------|------|
| 身份/人格 | 硬编码在 `context_builder.py` 的字符串常量中 | 无法跨会话持久化个性、偏好、经验 |
| 项目上下文 | 从 `.hermes.md` / `CLAUDE.md` 等文件加载 | 仅限项目规则，不含用户记忆 |
| 会话历史 | `self._events[]` 存储在 Temporal Workflow 中 | Workflow 结束后历史丢失 |
| 跨会话记忆 | 无 | 每次新会话从零开始 |

### 1.2 接入后架构

```
Agentic Loop (每轮):
  context_builder._build_system_prompt()
    ├─ 渠道身份声明           ← 保留（静态身份）
    ├─ 风格提示               ← 保留
    ├─ 工具纪律               ← 保留
    ├─ 环境感知               ← 保留
    ├─ 项目上下文 (.md文件)   ← 保留
    ├─ ★ 记忆注入 (recall)    ← 新增：从 Hindsight 检索相关记忆
    └─ SOUL.md               ← 保留

每轮结束后:
  ★ retain_activity(event)    ← 新增：异步提取并存储记忆

定期任务:
  ★ reflect_activity          ← 新增：Temporal Schedule 定期触发深度反思
```

### 1.3 对现有系统的影响

| 组件 | 变更强度 | 说明 |
|------|----------|------|
| `context_builder.py` | 中 | `_build_system_prompt()` 新增记忆注入段落 |
| `workflow.py` | 中 | `_process_turn()` 在模型调用前插入 recall，结束后触发 retain |
| `activities.py` | 中 | 新增 3 个 Activity (retain/recall/reflect) |
| `worker.py` | 中 | 初始化 HindsightClient，注入到 Activities，注册 Temporal Schedule |
| `docker-compose.yaml` | 中 | 新增 2 个服务 (hindsight-postgres, hindsight) |
| `requirements.txt` | 低 | 新增 hindsight-client 依赖 |
| `config.yaml` | 低 | 新增 hindsight 配置段 |
| `runner.py` | 无 | 不受影响 |
| `nsjail.py` | 无 | 不受影响 |
| `workspace/` | 无 | 不受影响 |
| `sandbox/` | 无 | 不受影响 |

---

## 2. 数据流设计

### 2.1 recall —— 关键路径（同步，每次模型调用前）

```
_recall_for_context(account_id, session_id, query)
  → HindsightClient.recall(query, user_id=account_id, session_id=session_id)
    → POST /api/v1/recall
      → 多路检索: 语义向量 + 关键词BM25 + 知识图谱 + 时序
      → 结果融合去重
      → Reranker 精排 (BAAI/bge-reranker-v2-m3)
  ← List[MemoryItem]
  → 格式化注入 system prompt: "# 相关记忆\n- ...\n- ..."
```

**延迟预算**: recall API 调用预计 100-300ms，作为 Activity 在 Temporal 中执行，计入 `start_to_close_timeout`。

### 2.2 retain —— 非关键路径（异步，每轮后触发）

```
_retain_from_turn(account_id, session_id, event_dict)
  → HindsightClient.retain(event_dict, user_id=account_id, session_id=session_id)
    → POST /api/v1/retain
      → LLM 提取可记忆信息 (偏好/事实/决策/关系)
      → Embedding 编码 (BAAI/bge-m3)
      → 存入 pgvector
  ← {"stored": N}
```

**策略**: Workflow 使用 `workflow.start_activity()` 而非 `execute_activity()` 异步触发，不阻塞主循环。

### 2.3 reflect —— 定时任务（Temporal Schedule）

```
reflect_activity(account_id)
  → HindsightClient.reflect(user_id=account_id)
    → POST /api/v1/reflect
      → 深度推理: 记忆关联、矛盾检测、知识抽象、经验总结
      → 高价值洞察写回记忆库
  ← {"insights": N}
```

**调度**: 每 6 小时为每个活跃用户触发一次，通过 Temporal Schedule 实现。

---

## 3. 安全性评估

| 维度 | 风险 | 缓解措施 |
|------|------|----------|
| 记忆注入 | 用户可能通过对话植入恶意"记忆"，后续被 recall 注入 system prompt | 记忆内容以 `# 相关记忆` 段落注入，与其他 prompt 模块隔离；Hindsight 服务端应有内容过滤 |
| 数据隔离 | 不同用户的记忆必须严格隔离 | Hindsight API 调用时传入 `user_id`，由服务端保证隔离 |
| 网络暴露 | Hindsight 服务暴露 REST API | 仅在内网 `app-network` 中暴露，不映射宿主机端口 |
| 依赖可用性 | Hindsight 不可用时影响核心流程 | recall Activity 失败时返回空记忆列表，不阻塞 agentic loop；retain 异步执行，失败不影响主流程 |

---

## 4. 迁移策略

### 4.1 实施顺序

| 阶段 | 内容 | 回退方案 |
|------|------|----------|
| 1. 部署 | docker-compose 新增 hindsight + hindsight-postgres 服务 | 删除服务定义即可 |
| 2. 客户端 | src/memory/hindsight_client.py 封装 API | 模块可整体移除 |
| 3. Activity | 新增 3 个 Activity，注入可选依赖 | `_hindsight_client is None` 时跳过，不影响现有流程 |
| 4. 集成 | context_builder + workflow 注入调用点 | recall 失败返回空列表，系统降级为无记忆模式 |
| 5. 清理 | 移除 .md 文件从 nsjail mount（如有） | 重新挂载即可恢复 |

### 4.2 降级策略

所有 Hindsight 调用点均设计为 **可选增强**：
- `_hindsight_client` 为 None → 跳过 recall，不注入记忆
- recall API 返回空 → system prompt 无记忆段落
- retain/reflect 失败 → 仅 log warning，不影响主流程

---

## 5. 配置变更

```yaml
# config.yaml 新增
hindsight:
  base_url: "http://hindsight:8000"      # Hindsight 服务地址
  api_key: ""                             # API 密钥（可选）
  recall:
    enabled: true                         # 是否启用记忆召回
    top_n: 5                              # 召回记忆数量
  retain:
    enabled: true                         # 是否启用记忆存储
    async_: true                          # 是否异步执行
  reflect:
    enabled: true                         # 是否启用深度反思
    interval_hours: 6                     # 定时任务间隔
```

---

## 6. 依赖新增

```
# requirements.txt 新增
hindsight-client>=0.1.0
```

### docker-compose.yaml 新增服务

```yaml
hindsight-postgres:
  image: pgvector/pgvector:pg16
  environment:
    POSTGRES_USER: hindsight
    POSTGRES_PASSWORD: hindsight
    POSTGRES_DB: hindsight
  volumes:
    - hindsight-pgdata:/var/lib/postgresql/data

hindsight:
  image: hindsight:latest
  environment:
    - DATABASE_URL=postgresql://hindsight:hindsight@hindsight-postgres:5432/hindsight
    - EMBEDDING_MODEL=BAAI/bge-m3
    - RERANKER_MODEL=BAAI/bge-reranker-v2-m3
  depends_on:
    - hindsight-postgres
```

---

## 7. 风险清单

| 风险 | 等级 | 说明 |
|------|------|------|
| Hindsight 服务不可用 | 中 | 降级为无记忆模式，核心流程不受影响 |
| recall 延迟超标 | 低 | 作为 Temporal Activity 有超时控制，超时后跳过记忆注入 |
| pgvector 数据膨胀 | 低 | Hindsight 服务端应有 TTL/清理策略 |
| 记忆污染 | 中 | 恶意用户可通过对话注入虚假记忆，需服务端内容审核 |
| 中文 Embedding 精度 | 低 | bge-m3 在中文基准测试中表现优异，风险可控 |

---

## 8. 未纳入范围

| 项目 | 原因 |
|------|------|
| Hindsight 服务端源码修改 | Hindsight 作为独立服务部署，本仓库不包含其源码 |
| 记忆可视化/管理 UI | 后续迭代 |
| 记忆导出/迁移工具 | 后续迭代 |
| 多租户计费/配额 | 当前 MVP 阶段不涉及 |
