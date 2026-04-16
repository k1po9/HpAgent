# 原生工具 + Skills + MCP 架构设计

核心要求：
1. **三类独立开放注册**：Agent 可自主创建/注册 原生工具、MCP 工具、Skills
2. **工具列表动态读入**：无缓存、实时读取注册器，支持热更新
3. **配置驱动初始化**：通过 `tool_config` 统一传入所有参数
4. **流程完全闭环**：Agent 生成工具 → 注册 → 列表查询 → 参数校验 → 执行 → 绑定 Skill
5. **极致解耦**：三类工具互不干扰，职责单一，易扩展

---

# 一、核心架构设计思路
## 1. 顶层设计原则
- **三权分立**：原生工具 / MCP 工具 / Skills 各有独立协议、独立注册器、独立管理
- **开放注册**：所有注册接口**暴露给 Agent**，支持 Agent 动态创建/注册新实体
- **配置入口唯一**：初始化仅传入 `tool_config`，所有默认参数、连接信息由配置决定
- **动态无状态**：工具列表**实时读取注册器**，不缓存、支持热插拔
- **闭环自治**：Agent 无需外部介入，可完成工具全生命周期管理

## 2. 架构分层（自顶向下）
```
Agent 核心（调用方）
        ↓ 【唯一入口】
ToolService 工具总服务（统一调度、开放所有接口）
        ↓
┌─────────────────────────────────────────────┐
│  三大独立注册器 （开放给Agent自主注册）          │
│  ├── NativeToolRegistry  原生工具注册器       │
│  ├── MCPToolRegistry     MCP工具注册器       │
│  └── SkillRegistry       Skills策略注册器    │
└─────────────────────────────────────────────┘
        ↓
┌─────────────────────────────────────────────┐
│  核心支撑模块                                 │
│  ├── ToolConfig       统一配置（初始化传入）    │
│  ├── ToolFactory      工具工厂（Agent生成工具）│
│  └── ParamValidator   参数校验               │
└─────────────────────────────────────────────┘
        ↓
┌─────────────────────────────────────────────┐
│  实现层                                       │
│  ├── NativeTools    原生工具基类/实现         │
│  ├── MCPTools       MCP代理/客户端            │
│  └── Skills         Skill基类/规则            │
└─────────────────────────────────────────────┘
```

## 3. 核心满足点
✅ **三类开放注册**：Agent 可调用 3 个注册器的 `register()` 方法
✅ **动态列表**：每次获取列表**实时遍历注册器**，动态生成
✅ **配置驱动**：`tool_config` 包含 MCP 连接、默认工具、参数规则
✅ **Agent 生成工具**：通过 `ToolFactory` 工厂类，Agent 传入必要参数 即可生成skills
✅ **流程闭环**：创建 → 注册 → 列表 → 校验 → 执行 → 绑定

---

# 二、tools/ 目录解耦结构
**所有文件按职责拆分，三类工具完全隔离，注册接口全部开放**
```
tools/
├── __init__.py                 # 包导出：暴露服务、配置、工厂、注册器
├── config.py                   # 【核心】ToolConfig 配置定义（初始化传入）
├── protocol.py                 # 统一协议：Tool协议、Skill协议
├── registry/                   # 【关键】三大独立注册器（开放给Agent）
│   ├── __init__.py
│   ├── native_registry.py      # 原生工具注册器
│   ├── mcp_registry.py         # MCP工具注册器
│   └── skill_registry.py       # Skills注册器
├── factory.py                   # 工具工厂：Agent自主生成原生/MCP工具
├── validator.py                # 参数校验模块
├── service.py                   # 总服务层：Agent唯一调用入口
├── native/                      # 原生工具实现层
│   ├── __init__.py
│   └── base.py                  # 原生工具基类
├── mcp/                         # MCP工具适配层
│   ├── __init__.py
│   ├── mcp_proxy.py             # MCP工具代理（适配统一协议）
│   └── mcp_client.py            # MCP客户端（配置驱动连接）
└── skills/                      # Skills策略层
    ├── __init__.py
    └── base.py                  # Skill基类
```

---

# 三、核心配置定义（ToolConfig）
**初始化唯一入参**，承载所有工具层的配置参数
```python
# tools/config.py
from dataclasses import dataclass, field
from typing import dict, list

@dataclass
class ToolConfig:
    """工具层统一配置（Agent 初始化时传入）"""
    # 基础配置
    enable_native: bool = True          # 是否启用原生工具
    enable_mcp: bool = True             # 是否启用MCP工具
    enable_skills: bool = True          # 是否启用Skills

    # MCP 连接配置
    mcp_servers: list[dict] = field(default_factory=list)  # MCP服务器地址、认证参数
    mcp_timeout: int = 30

    # 默认工具（初始化自动注册）
    default_native_tools: list[str] = field(default_factory=list)
    default_mcp_tools: list[str] = field(default_factory=list)
    default_skills: list[dict] = field(default_factory=list)

    # 校验配置
    validate_before_execute: bool = True  # 执行前自动校验参数
```

---

# 四、核心模块职责详解
## 1. 协议层（protocol.py）
定义三类实体的**标准契约**，Agent 生成工具必须遵守
- `Tool`：原生 + MCP 统一工具协议
- `Skill`：策略协议（绑定工具、指令、约束）

## 2. 【核心】三大独立注册器（registry/）
**完全开放给 Agent**，支持独立注册、查询、删除
- `NativeToolRegistry`：管理所有原生工具
- `MCPToolRegistry`：管理所有 MCP 代理工具
- `SkillRegistry`：管理所有 Skills，支持按工具名绑定

## 3. 工具工厂（factory.py）
**Agent 自主生成新工具的核心**
- 无需手写类，Agent 传入 `name/description/parameters/execute_func`
- 自动生成**合规原生工具** / **MCP 代理工具**
- 生成后直接调用注册器完成注册

## 4. 总服务层（service.py）
**Agent 唯一调用入口**，暴露所有能力：
- 动态列表：`list_all_tools()`
- 开放注册：`register_native()` / `register_mcp()` / `register_skill()`
- 工具生成：`create_native_tool()` / `create_mcp_tool()`
- 参数校验：`validate_params()`
- 执行工具：`execute_tool()`
- Skill 绑定：`bind_skill_to_tool()`

## 5. 实现层
- `native/`：原生工具基类，本地执行逻辑
- `mcp/`：MCP 客户端 + 代理，配置驱动连接远程服务
- `skills/`：Skill 基类，存储工具使用策略

---

# 五、Agent 全流程闭环（核心流程）
## 初始化阶段
1. Agent 构造 `ToolConfig`（传入 MCP 地址、默认工具、开关）
2. Agent 初始化 `ToolService(config)`
3. 服务层**自动加载默认工具**，完成初始注册

## 动态工具生命周期（Agent 自主操作）
```
1. 生成工具  →  ToolFactory.create_native/mcp_tool()
2. 注册工具  →  调用对应注册器 register()
3. 查询列表  →  list_all_tools() 【动态读入，实时更新】
4. 参数校验  →  validate_params()
5. 执行工具  →  execute_tool() （原生/MCP无差异）
6. 绑定策略  →  register_skill() + bind_skill_to_tool()
```

## 关键特性保障
1. **动态列表**：每次调用 `list_all_tools()`，都会**实时遍历三大注册器**，合并返回最新工具
2. **开放注册**：Agent 可随时调用注册接口，新增/删除工具，无需重启
3. **配置驱动**：所有连接、开关、默认工具都由 `tool_config` 控制
4. **自主生成**：Agent 通过工厂类，无需定义新类，即可生成合规工具

---