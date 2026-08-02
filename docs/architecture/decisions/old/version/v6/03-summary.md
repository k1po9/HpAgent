# 沙箱模块重构总结：nsjail 统一执行入口

> 版本: v6  
> 日期: 2026-05-07  
> 状态: 已完成  
> 测试: 22/22 通过

---

## 1. 改造目标达成

| 目标 | 状态 | 实现方式 |
|------|------|---------|
| 抽象统一 nsjail 执行命令 | 已完成 | `NsjailConfig.build_command()` 编译标准化命令行 |
| 工具执行改为 nsjail 子进程 | 已完成 | `NsjailExecutor.execute()` 异步子进程执行 |
| 执行输出写入 Redis | 已完成 | `_persist_result()` → `SET sandbox:result:{id}` |
| 封装为 Temporal Activity | 已完成 | `execute_tool_activity` + `get_tool_result_activity` |

---

## 2. 文件变更汇总

### 新增文件 (3)

| 文件 | 行数 | 描述 |
|------|------|------|
| `src/sandbox/nsjail.py` | ~220 | NsjailConfig 配置对象 + NsjailExecutor 统一执行器 |
| `src/sandbox/runner.py` | ~160 | In-jail 工具执行脚本，在 nsjail 命名空间内运行 |
| `test/test_nsjail.py` | ~220 | 22 个测试用例，覆盖配置编译、runner 直接调用、nsjail 子进程执行 |

### 修改文件 (5)

| 文件 | 变更 | 描述 |
|------|------|------|
| `src/sandbox/__init__.py` | +2 导出 | 新增 `NsjailConfig`, `NsjailExecutor` 导出 |
| `src/sandbox/sandbox.py` | 重构 | `execute()` 从直接调用 `tool.execute()` 改为委托 `NsjailExecutor` |
| `src/sandbox/sandbox_manager.py` | 重构 | 接受 `NsjailConfig` + `redis_cache`，自动创建 `NsjailExecutor` |
| `src/harness/activities.py` | +30 行 | 注入 `redis_cache`，新增 `get_tool_result_activity` |
| `src/orchestration/worker.py` | +30 行 | 初始化 Redis 连接 + NsjailConfig，注入到 Activities |

### 未修改 (保留)

| 模块 | 原因 |
|------|------|
| `sandbox/tools/` (base.py, registry.py, factory.py) | 工具元数据管理不变，仅执行路径改变 |
| `sandbox/channels/` (全部) | 渠道层不受影响 |
| `orchestration/workflow.py` | Workflow 逻辑不变，Activity 签名兼容 |
| `storage/redis.py` | 直接复用，无需修改 |
| `common/` (全部) | 接口和错误定义不变 |

---

## 3. 架构对比

### 3.1 旧架构 (v5)

```
execute_tool_activity
  └─ sandbox.execute(tool_name, args)
       └─ ToolRegistry.execute(tool_name, args)
            └─ tool.execute(**kwargs)           ← 进程内 Python 调用
                 ├─ 无资源限制
                 ├─ 无文件系统隔离
                 ├─ 无网络隔离
                 └─ 异常污染宿主进程
```

### 3.2 新架构 (v6)

```
execute_tool_activity
  └─ sandbox.execute(tool_name, args)
       └─ NsjailExecutor.execute(tool_name, args)
            └─ nsjail --mode o \                ← OS 级隔离子进程
                 --chroot /sandbox \
                 --user nobody \
                 --time_limit 30s \
                 --rlimit_as 256MB \
                 --disable_proc \
                 --iface_no_lo \
                 -- python3 runner.py <tool> <args_json>
                 │
                 ├─ PID namespace 隔离
                 ├─ chroot 文件系统隔离
                 ├─ 网络禁用
                 ├─ 资源硬限制
                 └─ 非特权用户运行
                        │
                        ▼
                 runner.py stdout → JSON 解析 → ToolResult
                        │
                        ▼
                 Redis: SET sandbox:result:{id} → 持久化
```

---

## 4. 核心设计决策

### 4.1 为什么保留 ToolRegistry？

- **工具发现**: LLM 需要知道有哪些工具可用（OpenAI function calling 格式）
- **元数据管理**: `name`、`description`、`parameters` JSON Schema 仍在注册表中
- **接口兼容**: `ISandbox.list_tools()` 签名和返回值不变
- **执行分离**: 注册表只管"有什么工具"，不管"怎么执行"

### 4.2 为什么 runner.py 是独立脚本？

- **nsjail 执行模型**: nsjail 通过 `execve` 启动子进程，需要独立的可执行入口
- **最小依赖**: runner.py 仅使用 Python 标准库 (`sys`, `json`, `traceback`)
- **安全边界**: runner.py 是信任边界内的代码，不导入任何项目模块
- **可替换性**: 未来可替换为其他语言编写的 runner（如静态编译的 Go binary）

### 4.3 为什么每种工具在 runner.py 中重新实现？

- **安全隔离**: runner.py 在 nsjail 内运行，不能导入项目代码（否则失去隔离意义）
- **代码量小**: 三个内置工具各 10-20 行，维护成本低
- **显式安全审计**: 每个工具函数在 runner.py 中一目了然，便于安全审计

### 4.4 Redis 持久化策略

- **Key 格式**: `sandbox:result:{execution_id}`
- **TTL**: 3600 秒（1 小时），避免内存无限增长
- **内容**: tool_name、arguments、result、elapsed_ms、timestamp
- **失败处理**: Redis 写入失败仅记录日志，不阻塞执行返回
- **可选性**: Redis 未配置时自动跳过持久化（优雅降级）

---

## 5. 配置参考

### 5.1 config.yaml 新增字段

```yaml
# nsjail 沙箱配置（所有字段均有默认值）
sandbox_chroot: "/"               # chroot 根目录，生产环境建议专用 rootfs
sandbox_timeout: 30               # 工具执行超时（秒）
sandbox_memory_mb: 256            # 最大内存限制（MB）
sandbox_cpu_seconds: 10           # CPU 时间限制（秒）
sandbox_max_procs: 32             # 最大子进程数
sandbox_max_files: 64             # 最大打开文件数
sandbox_disable_proc: true        # 禁用 /proc
sandbox_disable_network: true     # 禁用网络
sandbox_readonly_root: true       # 根文件系统只读
nsjail_binary: "/usr/bin/nsjail"  # nsjail 二进制路径

# Redis 结果持久化（可选）
redis_url: "redis://localhost:6379"  # 留空则跳过持久化
```

### 5.2 开发环境 vs 生产环境

| 配置项 | 开发环境 | 生产环境 |
|--------|---------|---------|
| `sandbox_chroot` | `/` (宿主文件系统) | `/var/sandbox/chroot` (最小化 rootfs) |
| `sandbox_readonly_root` | `false` | `true` |
| `sandbox_disable_network` | `false` (调试需要) | `true` |
| `sandbox_disable_proc` | `false` | `true` |

---

## 6. 测试结果

```
test/test_nsjail.py - 22 passed in 1.02s

TestNsjailConfig (6 tests):
  build_command_basic              PASSED
  build_command_rw_mode            PASSED
  build_command_network_enabled    PASSED
  build_command_proc_enabled       PASSED
  build_command_resource_limits    PASSED
  build_command_json_args_special  PASSED

TestRunnerDirect (9 tests):
  calculator_simple                PASSED
  calculator_complex               PASSED
  calculator_error                 PASSED
  calculator_no_code_execution     PASSED  ← 代码注入被阻止
  web_search                       PASSED
  file_read_nonexistent            PASSED
  unknown_tool                     PASSED
  invalid_json_args                PASSED
  missing_args                     PASSED

TestNsjailExecutor (6 tests):
  calculator_via_nsjail            PASSED  ← nsjail 子进程执行成功
  calculator_code_injection_blocked PASSED ← 双重防护
  web_search_via_nsjail            PASSED
  unknown_tool_via_nsjail          PASSED
  execution_id_unique              PASSED
  retrieve_result_without_redis    PASSED

Config defaults (1 test):
  nsjail_config_defaults           PASSED  ← 验证默认值安全
```

---

## 7. 后续工作建议

### 7.1 短期 (本周)

1. **生产 chroot 构建**: 使用 `debootstrap` 或 Alpine 构建最小化 rootfs
2. **Dockerfile 更新**: 添加 `nsjail` 安装 + chroot 镜像层
3. **工具扩展**: 在 `runner.py` 中添加新的安全工具（如 HTTP 请求、JSON 解析）

### 7.2 中期 (本月)

1. **监控仪表板**: 在 Redis 中聚合执行统计（成功率、平均耗时、工具调用频次）
2. **nsjail 日志收集**: 配置 `--log` 将 nsjail 日志写入文件，接入日志系统
3. **工具热加载**: 支持在不停 Worker 的情况下更新 `runner.py` 中的工具

### 7.3 长期

1. **多语言 runner**: 用 Go/Rust 实现 runner，进一步减少攻击面
2. **seccomp 过滤**: 在 nsjail 中配置 seccomp 规则，限制允许的系统调用
3. **cgroup v2 集成**: 利用 `--use_cgroupv2` 实现更精细的资源控制
4. **工具市场**: 支持从远程仓库加载第三方工具到 runner

---

## 8. 回退方案

如果 nsjail 出现问题，可快速回退到进程内执行模式：

1. 在 `config.yaml` 中设置 `sandbox_mode: "direct"`
2. Worker 检测到此配置时，创建不包含 `NsjailExecutor` 的 Sandbox
3. 或直接 git revert 本次提交

由于接口完全兼容（`ISandbox.execute()` 签名不变），回退不需要修改 Workflow 或 Activity 代码。

---

## 9. 安全性声明

本次改造将沙箱安全等级从 **无隔离** 提升至 **OS 级隔离**：

- **进程隔离**: PID namespace 防止工具代码访问宿主进程
- **文件系统隔离**: chroot + 只读挂载限制文件访问范围
- **网络隔离**: `--iface_no_lo` 阻止网络通信
- **资源限制**: rlimit 硬限制防止 DoS
- **用户隔离**: `nobody` 用户无特权
- **信息隔离**: `--disable_proc` 防止 /proc 信息泄漏

代码注入测试验证：`__import__('os').system('ls')` 在 `eval()` 中被 `__builtins__={}` 阻止，即使绕过也会受 nsjail 资源限制约束。
