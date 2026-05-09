# 多用户工作目录方案评估与迁移文档

> 版本: v6  
> 日期: 2026-05-08  
> 评估对象: users_workspace/ 多用户持久化工作目录方案

---

## 1. 现状分析

### 1.1 当前架构的问题

| 问题 | 影响 | 严重度 |
|------|------|--------|
| 无用户级文件隔离 | 所有用户共享同一个 nsjail 工作区，文件互相可见/覆盖 | 高 |
| 无会话持久化目录 | 工具执行产生的文件（如 `file_read` 输出、计算结果）无法保留 | 高 |
| 沙箱为全局单例 | 一个 `SandboxManager` + 一个默认沙箱服务所有用户 | 中 |
| AccountService 纯内存 | 重启丢失用户绑定映射 | 中 |
| 无工具执行记录 | 除了 Redis 缓存的短期结果，无持久化审计记录 | 中 |
| nsjail 无绑载挂载 | workspace 目录无法挂载到沙箱内部 | 高 |

### 1.2 不受影响的部分

| 模块 | 状态 | 说明 |
|------|------|------|
| `orchestration/workflow.py` | 不变 | Workflow 逻辑与工作目录方案解耦 |
| `sandbox/channels/` | 不变 | 渠道层不涉及文件系统 |
| `sandbox/tools/` | 不变 | 工具元数据管理不变 |
| `storage/redis.py` | 不变 | Redis 缓存继续用于短期结果 |
| `resources/` | 不变 | 模型调用与工作目录无关 |

---

## 2. 目标架构评估

### 2.1 目录结构

```
users_workspace/
└── <user_uuid>/
    ├── skills/                  # 用户自定义技能（跨 session）
    │   └── <skill_name>/
    │       ├── skill.yaml
    │       └── code/
    ├── sessions/
    │   └── <session_id>/
    │       ├── session.yaml     # 元数据
    │       ├── conversation/
    │       │   ├── messages.jsonl
    │       │   └── summary.md
    │       ├── execution/
    │       │   ├── plan.yaml
    │       │   └── logs/
    │       ├── workspace/       # nsjail 绑载挂载
    │       │   ├── input/
    │       │   ├── scratch/
    │       │   └── output/
    │       └── resources/
    │           └── resource_manifest.yaml
    ├── persistent/              # 跨 session 资产
    └── user_profile.yaml
```

**评估: 结构合理。** 会话内子目录划分清晰（conversation/execution/workspace/resources），职责单一。

### 2.2 SQLite 数据库

| 表 | 字段 | 必要性 |
|----|------|--------|
| `users` | uuid, username, profile_path, persistent_dir, created_at | 必须 —— 用户是 workspace 的根 |
| `sessions` | session_id, user_uuid, status, task_summary, session_dir, plan_file, conversation_file, output_dir, tags, created_at, updated_at | 必须 —— 会话是 workspace 的核心维度 |
| `artifacts` | artifact_id, session_id, file_path, file_type, file_size, checksum, created_at | 必须 —— 产出物索引，避免扫描文件系统 |

**评估: 表设计合理。** 使用 SQLite 而非 PostgreSQL 是 MVP 的正确选择——零运维、零配置、嵌入式。索引 `(user_uuid, created_at DESC)` 覆盖主要查询模式。

### 2.3 nsjail 绑载挂载

nsjail 提供 `--bindmount/-B` (读写) 和 `--bindmount_ro/-R` (只读) 选项：

```bash
nsjail --mode o --chroot / \
  --bindmount /host/user/workspace:/work:rw \
  --bindmount_ro /host/user/skills:/skills:ro \
  -- python3 runner.py calculator '{"expr":"2+2"}'
```

**评估: 原生支持。** nsjail 命令行直接支持 `source:dest` 格式的绑载挂载，无需配置文件。

### 2.4 与现有组件的集成

| 集成点 | 变更 | 风险 |
|--------|------|------|
| `NsjailConfig` | 新增 `bind_mounts: list[str]` 字段 | 低 —— 可选字段，默认空列表 |
| `NsjailConfig.build_command()` | 遍历 bind_mounts 追加 `--bindmount` / `--bindmount_ro` 参数 | 低 —— 纯追加逻辑 |
| `NsjailExecutor.execute()` | 接受可选的 `work_dir` 和 `bind_mounts` 覆盖 | 低 —— 参数向后兼容 |
| `SandboxManager` | 新增 `workspace_manager` 属性，`create_session_sandbox()` 方法 | 中 —— 新增方法，不改变现有调用 |
| `worker.py` | 初始化 `WorkspaceManager`，传入 `SandboxManager` 和 activities | 中 —— 启动流程增加一个依赖 |
| `harness/activities.py` | 新增 `_workspace_manager` + `prepare_workspace_activity` | 低 —— 新增 Activity |

### 2.5 会话生命周期

```
创建 → 初始化目录 + 写入 session.yaml + DB INSERT
  ↓
执行 → Agent 在 workspace/ 内操作，conversation/ 和 execution/ 实时写入
  ↓
结束 → 更新 status=completed/failed，output/ 保留产出
  ↓
清理 → 后台定期扫描，超过 TTL 删除目录 + DB UPDATE status=deleted
```

**评估: 清晰完整。** 每个阶段责任明确，清理策略可配置。

---

## 3. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| nsjail bind mount 失败 | 低 | 中 (工具无工作区) | 启动时校验挂载路径存在，Activity 层 fallback 到临时目录 |
| SQLite 并发写入冲突 | 低 | 低 | 使用 WAL 模式 + `timeout=5` 重试；单 Worker 场景几乎无冲突 |
| 会话目录磁盘占满 | 中 | 高 | 实现定期清理 + 磁盘配额检查 + 告警 |
| 用户 UUID 碰撞 | 极低 | 高 | 使用 `uuid.uuid4()` (122 bits 随机) |
| workspace 路径过长 | 低 | 低 | 限制 user_uuid + session_id 总路径长度 < 200 字符 |

---

## 4. 实施计划

### Phase 1: 基础设施 (本次)
- [x] `workspace/models.py` —— 数据模型
- [ ] `workspace/db.py` —— SQLite 数据库层
- [ ] `workspace/manager.py` —— WorkspaceManager
- [ ] `workspace/__init__.py` —— 模块导出

### Phase 2: 集成 (本次)
- [ ] `nsjail.py` —— 增加 bind_mounts 支持
- [ ] `sandbox_manager.py` —— 集成 WorkspaceManager
- [ ] `worker.py` —— 初始化 WorkspaceManager
- [ ] `activities.py` —— 增加 workspace Activity + 注入

### Phase 3: 测试与文档 (本次)
- [ ] `test_workspace.py` —— 单元 + 集成测试
- [ ] `05-workspace-summary.md` —— 总结文档

### Phase 4: 后续
- [ ] 会话清理后台任务（cron/interval）
- [ ] `user_profile.yaml` 读写
- [ ] `skills/` 目录的 skill.yaml 解析
- [ ] `persistent/` 与 `output/` 的同步逻辑

---

## 5. 兼容性

| 维度 | 影响 | 说明 |
|------|------|------|
| 现有 API | 零破坏 | 所有新增参数都是可选的 |
| 现有测试 | 通过 | `test_nsjail.py` 22 个测试无需修改 |
| 配置文件 | 向后兼容 | 新增 `workspace_root`, `workspace_db` 有默认值 |
| Temporal Workflow | 无需修改 | 新增 Activity 是独立的，不影响现有 Activity |

---

## 6. 结论

**建议立刻实施。** 方案设计成熟，风险可控，与现有架构集成自然。SQLite 选型契合 MVP 的"少量用户、少量并发"定位。nsjail 原生 bind mount 支持使得工作目录挂载零额外成本。
