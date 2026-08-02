# 沙箱模块重构方案：nsjail 统一执行入口

> 版本: v6  
> 日期: 2026-05-07  
> 状态: 方案设计

---

## 1. 重构目标

将 sandbox 模块从 **进程内直接调用** 的模式改造为 **nsjail 子进程隔离执行** 模式，实现三个核心目标：

| 目标 | 描述 |
|------|------|
| **安全隔离** | 工具执行在 nsjail 命名空间内运行，OS 级资源隔离（PID/NET/FS/RLIMIT） |
| **统一入口** | 所有工具执行通过标准化 `nsjail` 命令行调用，消除进程内执行风险 |
| **结果持久化** | 执行结果写入 Redis，支持异步查询、审计回溯和跨 Activity 共享 |

---

## 2. 现状分析

### 2.1 当前架构

```
Temporal Workflow
  └─ execute_activity("execute_tool_activity")
       └─ sandbox.execute(tool_name, args)          ← 进程内直接调用
            └─ ToolRegistry.execute(tool_name, args)
                 └─ tool.execute(**kwargs)           ← Python 函数调用，无隔离
```

**核心问题：**

1. `calculator` 工具使用 `eval()` —— 虽有 `__builtins__={}` 限制但仍在宿主进程执行
2. `file_read` 工具无路径沙箱 —— 可读取任意文件系统路径
3. 工具代码异常可能污染宿主进程状态
4. 无资源限制 —— 死循环/内存泄漏直接影响 Worker 进程

### 2.2 组件关系

```
worker.py
  ├── SandboxManager (沙箱池)
  │     └── Sandbox (单个沙箱)
  │           └── ToolRegistry (工具注册表)
  │                 └── BaseTool/DynamicTool (工具实例)
  ├── harness/activities.py (5 个 Temporal Activity)
  │     └── execute_tool_activity → sandbox.execute()
  └── storage/redis.py (已实现, 未使用)
        ├── RedisCache
        └── RedisPubSub
```

---

## 3. 目标架构

### 3.1 核心设计

```
Temporal Workflow
  └─ execute_activity("execute_tool_activity")
       └─ NsjailExecutor.execute(tool_name, args)    ← nsjail 子进程
            ├─ 构建 nsjail 命令
            ├─ subprocess.run(nsjail_cmd)
            ├─ 解析 stdout JSON 结果
            ├─ 写入 Redis (持久化)
            └─ 返回 ToolResult
```

### 3.2 新增模块

```
src/sandbox/
  ├── nsjail.py          ← [NEW] NsjailConfig + NsjailExecutor
  ├── runner.py          ← [NEW] in-jail 工具执行脚本
  ├── sandbox.py         ← [MOD] execute() 委托给 NsjailExecutor
  ├── sandbox_manager.py ← [MOD] 接受 NsjailConfig + Redis
  ├── tools/             ← [保留] 工具元数据 (BaseTool/ToolDefinition)
  └── channels/          ← [不变] 渠道适配层
```

### 3.3 执行流程

```
┌─ Activity: execute_tool_activity ─────────────────────────────┐
│                                                               │
│  1. 生成 execution_id (UUID)                                  │
│  2. 查找工具元数据 (ToolRegistry)                              │
│  3. NsjailExecutor.build_command(tool_name, args)             │
│     ┌──────────────────────────────────────────┐              │
│     │ nsjail --mode o \                        │              │
│     │   --chroot /sandbox \                    │              │
│     │   --user nobody --group nogroup \        │              │
│     │   --time_limit 30 \                      │              │
│     │   --rlimit_as 256 \                      │              │
│     │   --rlimit_nproc 32 \                    │              │
│     │   --disable_proc --iface_no_lo \         │              │
│     │   --really_quiet \                       │              │
│     │   -- /usr/bin/python3 /work/runner.py \  │              │
│     │      calculator '{"expression":"2+2"}'   │              │
│     └──────────────────────────────────────────┘              │
│  4. asyncio.create_subprocess_exec(nsjail_cmd)                │
│  5. 读取 stdout → 解析 JSON 结果                               │
│  6. Redis: SET sandbox:result:{execution_id} → result_json    │
│  7. 返回 ToolResult                                           │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

---

## 4. 模块详细设计

### 4.1 NsjailConfig（配置对象）

```python
@dataclass
class NsjailConfig:
    nsjail_binary: str = "/usr/bin/nsjail"
    chroot_path: str = "/"
    work_dir: str = "/work"
    runner_script: str = "/work/runner.py"
    python_binary: str = "/usr/bin/python3"
    user: str = "nobody"
    group: str = "nogroup"
    hostname: str = "sandbox"
    time_limit: int = 30          # 最大执行秒数
    memory_limit_mb: int = 256    # 地址空间限制 (MB)
    cpu_limit_seconds: int = 10   # CPU 时间限制
    max_processes: int = 32       # 最大进程数
    max_files: int = 64           # 最大打开文件数
    disable_proc: bool = True     # 禁用 /proc
    disable_network: bool = True  # 禁用网络 (--iface_no_lo)
    readonly_root: bool = True    # chroot 只读挂载
```

**设计原则：**
- 每个参数映射到 nsjail 的一个命令行选项
- 提供安全默认值（最小权限原则）
- `build_command()` 方法将配置编译为命令行参数列表

### 4.2 NsjailExecutor（执行器）

```python
class NsjailExecutor:
    """通过 nsjail 子进程执行工具调用的统一入口。"""
    
    def __init__(self, config: NsjailConfig, redis_cache=None):
        ...
    
    def build_command(self, tool_name: str, arguments: dict) -> list[str]:
        """将配置 + 工具名 + 参数编译为 nsjail 命令行。"""
        ...
    
    async def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        """异步执行 nsjail 子进程并返回结果。"""
        ...
```

**错误处理：**
- nsjail 进程启动失败 → `ToolExecutionError`
- 超时 (time_limit) → nsjail 自动 SIGKILL，stdout 为空
- runner 脚本异常 → stdout 输出 `{"success": false, "error": "..."}`
- 非零退出码 → 解析 stderr，包装为错误

### 4.3 runner.py（In-Jail 执行脚本）

```python
#!/usr/bin/env python3
"""在 nsjail 内部执行的工具运行器。

用法: python3 runner.py <tool_name> '<json_arguments>'

输出: JSON 格式结果到 stdout
  {"success": true, "output": ...} 或 {"success": false, "error": "..."}
"""
```

**工具注册：**
- 脚本内置工具执行函数（与现有 `factory.py` 相同逻辑）
- 按名称分发：`TOOLS[tool_name](**args)`
- 所有异常被捕获并序列化为 JSON 错误

### 4.4 Sandbox 改造

```python
class Sandbox(ISandbox):
    def __init__(self, executor: NsjailExecutor, tools: list[BaseTool], ...):
        self._executor = executor        # [NEW] nsjail 执行器
        self._tool_registry = ToolRegistry()  # [保留] 工具元数据
    
    async def execute(self, tool_name, arguments):
        # [NEW] 委托给 nsjail 执行器
        return await self._executor.execute(tool_name, arguments)
    
    async def list_tools(self):
        # [保留] 从 ToolRegistry 获取元数据
        return self._tool_registry.list_definitions()
```

**关键变更：**
- `execute()` 不再调用 `tool.execute()` —— 改为 nsjail 子进程
- `ToolRegistry` 仍然保留，用于工具元数据管理和 `list_tools()`
- `health_check()` 增加 nsjail 二进制存在性检查

### 4.5 SandboxManager 改造

```python
class SandboxManager:
    def __init__(self, nsjail_config: NsjailConfig, redis_cache=None, ...):
        self._nsjail_config = nsjail_config
        self._redis_cache = redis_cache
    
    def create_sandbox(self, tools, ...):
        executor = NsjailExecutor(self._nsjail_config, self._redis_cache)
        sandbox = Sandbox(executor=executor, tools=tools, ...)
        ...
```

### 4.6 Activity 改造

```python
@activity.defn
async def execute_tool_activity(tool_name, arguments):
    # 1. 生成执行 ID
    execution_id = str(uuid.uuid4())
    
    # 2. 沙箱执行 (nsjail 子进程)
    result = await sandbox.execute(tool_name, arguments)
    
    # 3. 持久化到 Redis
    if _redis_cache:
        await _redis_cache.set_json(
            f"sandbox:result:{execution_id}",
            {
                "execution_id": execution_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "result": result.to_dict(),
                "timestamp": time.time(),
            },
            ttl=3600,  # 1 小时 TTL
        )
    
    return result.to_dict()
```

**新增 Activity：**
```python
@activity.defn
async def get_tool_result_activity(execution_id: str):
    """从 Redis 查询历史执行结果。"""
    if not _redis_cache:
        return None
    return await _redis_cache.get_json(f"sandbox:result:{execution_id}")
```

### 4.7 Worker 改造

```python
async def init_dependencies(config: dict):
    # ... 现有初始化 ...
    
    # [NEW] Redis 连接
    redis_client = None
    redis_cache = None
    if config.get("redis_url"):
        import redis.asyncio as aioredis
        redis_client = aioredis.from_url(config["redis_url"])
        redis_cache = RedisCache(redis_client)
    
    # [NEW] nsjail 配置
    nsjail_config = NsjailConfig(
        chroot_path=config.get("sandbox_chroot", "/"),
        time_limit=config.get("sandbox_timeout", 30),
        ...
    )
    
    # [NEW] SandboxManager 接受 nsjail + Redis
    sandbox_manager = SandboxManager(
        nsjail_config=nsjail_config,
        redis_cache=redis_cache,
    )
    ...
    
    # [NEW] 注入 Redis 到 Activities
    inject(..., redis_cache=redis_cache)
```

---

## 5. 数据流对比

### 5.1 旧流程

```
LLM tool_call → execute_tool_activity
  → sandbox.execute("calculator", {"expr": "2+2"})
    → ToolRegistry.execute()
      → tool.execute(expr="2+2")        # 进程内 eval()
        → ToolResult(success=True, output="4")
```

### 5.2 新流程

```
LLM tool_call → execute_tool_activity
  → sandbox.execute("calculator", {"expr": "2+2"})
    → NsjailExecutor.execute("calculator", {"expr": "2+2"})
      → subprocess: nsjail ... -- python3 runner.py calculator '{"expr":"2+2"}'
        → [nsjail namespace] runner.py → eval("2+2") → print('{"success":true,"output":"4"}')
      → stdout: '{"success":true,"output":"4"}'
      → Redis: SET sandbox:result:{uuid} → {...}
      → ToolResult(success=True, output="4")
```

---

## 6. 文件变更清单

| 文件 | 操作 | 描述 |
|------|------|------|
| `src/sandbox/nsjail.py` | **新增** | NsjailConfig + NsjailExecutor |
| `src/sandbox/runner.py` | **新增** | In-jail 工具执行脚本 |
| `src/sandbox/sandbox.py` | **修改** | execute() 委托给 NsjailExecutor |
| `src/sandbox/sandbox_manager.py` | **修改** | 接受 NsjailConfig + Redis |
| `src/sandbox/__init__.py` | **修改** | 导出新模块 |
| `src/harness/activities.py` | **修改** | Redis 持久化 + 新增查询 Activity |
| `src/orchestration/worker.py` | **修改** | 初始化 Redis + nsjail 配置 |
| `test/test_nsjail.py` | **新增** | nsjail 执行测试 |

---

## 7. 安全模型

| 维度 | 旧方案 | 新方案 |
|------|--------|--------|
| 进程隔离 | 无 (宿主进程内) | PID namespace (nsjail) |
| 文件系统 | 无限制 | chroot + 只读挂载 |
| 网络 | 无限制 | --iface_no_lo 禁用 |
| 内存限制 | 无 | --rlimit_as 256MB |
| CPU 限制 | 无 | --rlimit_cpu 10s |
| 时间限制 | 无 | --time_limit 30s |
| 进程数限制 | 无 | --rlimit_nproc 32 |
| 权限 | 宿主用户 | --user nobody |
| /proc 信息泄漏 | 有 | --disable_proc |

---

## 8. 实施计划

### 阶段 1: 核心模块 (nsjail.py + runner.py)
- 实现 NsjailConfig 和 NsjailExecutor
- 实现 runner.py 脚本
- 单元测试验证 nsjail 调用链路

### 阶段 2: 集成改造 (sandbox.py + manager + activities)
- 修改 Sandbox 使用 NsjailExecutor
- 修改 SandboxManager 传递配置
- 修改 Activities 增加 Redis 持久化

### 阶段 3: Worker 集成
- Worker 启动时初始化 Redis 连接
- 配置 nsjail 路径和参数
- 端到端测试

### 阶段 4: 清理与文档
- 移除无用的直接执行路径
- 更新模块注释
- 输出总结文档
