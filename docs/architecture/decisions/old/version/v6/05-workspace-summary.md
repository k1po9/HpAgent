# 多用户工作目录方案实施总结

> 版本: v6  
> 日期: 2026-05-08  
> 状态: 已完成  
> 测试: 45/45 通过 (22 nsjail + 23 workspace)

---

## 1. 实施范围

| 目标 | 状态 | 实现 |
|------|------|------|
| 用户级持久化工作区 | 已完成 | `WorkspaceManager.ensure_user()` + 目录骨架 |
| 会话完整目录结构 | 已完成 | `WorkspaceManager.create_session()` 创建 11 个子目录 |
| SQLite 元数据存储 | 已完成 | `WorkspaceDB` — users / sessions / artifacts 三表 |
| nsjail workspace 绑载 | 已完成 | `NsjailConfig.bind_mounts` + `--bindmount` / `--bindmount_ro` |
| per-session 沙箱 | 已完成 | `SandboxManager.create_session_sandbox()` |
| 会话生命周期管理 | 已完成 | create → active → completed/failed → cleanup |
| Temporal Activity 集成 | 已完成 | `prepare_workspace_activity` + `finalize_workspace_activity` |

---

## 2. 文件变更汇总

### 新增文件 (4)

| 文件 | 行数 | 角色 |
|------|------|------|
| `src/workspace/__init__.py` | ~50 | 模块导出 |
| `src/workspace/models.py` | ~110 | User / Session / Artifact 数据类 + SessionStatus 枚举 |
| `src/workspace/db.py` | ~250 | SQLite 数据库层（WAL 模式，级联删除，索引） |
| `src/workspace/manager.py` | ~310 | WorkspaceManager — 目录管理、会话生命周期、nsjail 集成 |
| `test/test_workspace.py` | ~260 | 23 个测试用例 |

### 修改文件 (4)

| 文件 | 变更 | 描述 |
|------|------|------|
| `src/sandbox/nsjail.py` | +20 行 | `bind_mounts` 字段 + `exta_bind_mounts`/`override_work_dir` 参数 |
| `src/sandbox/sandbox_manager.py` | +70 行 | `workspace_manager` 属性 + `create_session_sandbox()` |
| `src/harness/activities.py` | +80 行 | `_workspace_manager` 注入 + 2 个新 Activity |
| `src/orchestration/worker.py` | +20 行 | 初始化 WorkspaceManager → SandboxManager → Activities |

---

## 3. 目录结构（落地效果）

```
users_workspace/
└── <user_uuid>/                          # 首次消息自动创建
    ├── user_profile.yaml                 # 用户偏好
    ├── skills/                           # 自定义技能（预留）
    ├── persistent/                       # 跨 session 资产（预留）
    └── sessions/
        └── sess_<timestamp>_<random>/    # 每次新 Workflow 创建
            ├── session.yaml              # {session_id, user_uuid, status, task_summary, tags}
            ├── conversation/
            │   ├── messages.jsonl        # 完整对话记录
            │   └── summary.md            # 自动生成摘要
            ├── execution/
            │   ├── plan.yaml             # 工具调用计划与结果链
            │   └── logs/                 # 按步骤的 stdout/stderr
            ├── workspace/                # nsjail --bindmount → /work
            │   ├── input/                # 初始输入文件
            │   ├── scratch/              # 中间文件
            │   └── output/               # 最终产出
            └── resources/
                └── resource_manifest.yaml
```

---

## 4. 核心设计决策

### 4.1 为什么用 SQLite 而非 PostgreSQL？

- **MVP 定位**: 少量用户（< 100）、少量并发（单 Worker）
- **零运维**: 无需额外服务，数据库文件随 workspace 目录
- **WAL 模式**: 读写并发足够
- **可迁移**: 未来数据量增长可切换 PostgreSQL（表结构兼容）

### 4.2 为什么 session_id 含时间戳？

- uuid4 碰撞概率极低（122 bits），但添加时间戳提供:
  - 可读性：从 ID 直接推断创建时间
  - 排序性：文件系统列表中自然按时间排列
  - 二次防碰撞：时间戳 + 随机数

### 4.3 为什么 workspace 作为 nsjail bind mount？

- **持久化**: 沙箱退出后文件仍然存在
- **可见性**: 宿主可直接查看 workspace/output/ 中的产出
- **隔离性**: 每次挂载仅暴露该会话的 workspace 子目录
- **只读技能**: skills/ 以只读绑载，防止篡改

### 4.4 nsjail bind mount 参数格式

```
nsjail --mode o --chroot / \
  --bindmount /host/users_workspace/u1/sessions/s1/workspace:/work \
  --bindmount_ro /host/users_workspace/u1/skills:/skills \
  -- python3 runner.py calculator '{"expr":"2+2"}'
```

---

## 5. 数据流

### 5.1 新会话启动

```
NapCat message → handle_message()
  ├─ AccountService.resolve() → account_id (= user_uuid)
  ├─ WorkspaceManager.ensure_user(account_id)      ← 幂等创建用户目录
  ├─ WorkspaceManager.create_session(...)          ← 创建完整目录树 + DB insert
  ├─ SandboxManager.create_session_sandbox(...)    ← 创建 nsjail 沙箱 + bind mounts
  └─ Temporal Client.start_workflow()
       └─ user_message 含 workspace_user_uuid + workspace_session_id
```

### 5.2 工具执行时

```
execute_tool_activity → sandbox.execute()
  └─ NsjailExecutor.execute()
       └─ nsjail --bindmount <workspace_dir>:/work -- python3 runner.py ...
            └─ runner.py 在 /work 目录下操作文件（input → scratch → output）
```

### 5.3 会话结束时

```
finalize_workspace_activity(session_id, status="completed")
  ├─ WorkspaceManager.end_session()    ← 更新 session.yaml + DB status
  └─ WorkspaceManager.register_artifact()  ← 索引产出文件
```

---

## 6. 测试覆盖

```
test/test_workspace.py — 23 passed

TestWorkspaceDB (8 tests):
  upsert_and_get_user              PASSED
  get_nonexistent_user             PASSED
  upsert_user_update               PASSED
  insert_and_get_session           PASSED
  update_session_status            PASSED
  list_sessions                    PASSED
  insert_and_list_artifacts        PASSED
  delete_session_cascades          PASSED  ← 级联删除验证

TestWorkspaceManager (14 tests):
  ensure_user_creates_directories  PASSED  ← 11 个子目录验证
  ensure_user_idempotent           PASSED
  create_session_full_structure    PASSED
  create_session_auto_id           PASSED
  end_session                      PASSED
  list_sessions                    PASSED
  get_nsjail_mounts                PASSED  ← bind mount 参数验证
  get_session_work_dir             PASSED
  get_session_output_dir           PASSED
  register_artifact                PASSED
  register_artifact_nonexistent    PASSED
  cleanup_expired_sessions         PASSED
  session_id_uniqueness            PASSED  ← 20 个 ID 不碰撞
  multi_user_isolation             PASSED  ← 用户数据隔离验证

TestWorkspaceConfig (1 test):
  root_resolves_to_absolute        PASSED
```

---

## 7. 新增 Activity

| Activity | 签名 | 用途 |
|----------|------|------|
| `prepare_workspace_activity` | (user_uuid, session_id, task_summary) → {ok, workspace_path} | 确保工作区就绪（幂等） |
| `finalize_workspace_activity` | (session_id, status, task_summary, artifacts) → {ok, artifacts_count} | 结束会话 + 注册产出物 |

---

## 8. 配置字段

```yaml
# config.yaml 新增字段（均有默认值）
workspace_root: "users_workspace"     # 工作区根目录
workspace_db: ""                      # SQLite 数据库路径，空=root/workspace.db
```

---

## 9. 安全性

| 维度 | 措施 |
|------|------|
| 用户隔离 | 每个 user_uuid 独立目录，路径由 WorkspaceManager 统一生成 |
| 会话隔离 | 每次新 Workflow 创建新 session 子目录，含唯一时间戳 |
| nsjail 绑载 | 仅暴露该会话的 workspace/（读写）和 skills/（只读） |
| 路径遍历防护 | WorkspaceManager 内部使用 Path.resolve() + 前缀检查 |
| 数据库安全 | SQLite 文件位于 root 目录内，参数化查询防注入 |
| 清理策略 | cleanup_expired_sessions() 按 max_age_days 定期清理 |

---

## 10. 未实施项（后续迭代）

| 项目 | 优先级 | 说明 |
|------|--------|------|
| 对话内容写入 JSONL | 高 | 当前目录已创建，需在 Activity 中追加写入 |
| execution/plan.yaml 更新 | 高 | 工具执行链需要写入计划文件 |
| 清理后台任务 | 中 | 需 cron/interval 定时调用 cleanup_expired_sessions |
| skills/ 解析与执行 | 中 | skill.yaml 解析 + nsjail 只读挂载 |
| persistent/ 同步 | 低 | 会话结束后将 output/ 中重要文件同步到 persistent/ |
| 磁盘配额检查 | 低 | 在 create_session 前检查用户已使用空间 |
