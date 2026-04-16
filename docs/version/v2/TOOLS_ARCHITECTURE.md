# Tools 层架构使用指南

## 架构概览

根据 `docs/version/v2/tool架构.md` 设计，已实现完整的工具层架构，采用**三类独立开放注册**模式。

## 目录结构

```
src/tools/
├── __init__.py                 # 包导出：暴露所有核心组件
├── config.py                   # ToolConfig 配置类
├── protocol.py                 # 统一协议：Tool、Skill、ToolType
├── registry/                   # 三大独立注册器
│   ├── __init__.py
│   ├── native_registry.py      # 原生工具注册器
│   ├── mcp_registry.py          # MCP工具注册器
│   └── skill_registry.py        # Skills策略注册器
├── factory.py                   # 工具工厂
├── validator.py                # 参数校验模块
├── service.py                   # 总服务层（Agent唯一调用入口）
├── native/                      # 原生工具基类
│   ├── __init__.py
│   └── base.py
├── mcp/                         # MCP适配层
│   ├── __init__.py
│   └── mcp_proxy.py
└── skills/                      # Skills策略层
    ├── __init__.py
    └── base.py
```

## 核心特性

### 1. 三类独立开放注册
- **原生工具**：本地执行的工具
- **MCP工具**：远程MCP服务器代理
- **Skills**：工具使用策略和约束

### 2. 配置驱动
所有配置通过 `ToolConfig` 统一管理：
```python
from src.tools import ToolConfig, ToolService

config = ToolConfig(
    enable_native=True,
    enable_mcp=False,
    enable_skills=True,
    validate_before_execute=True
)
```

### 3. 动态列表
每次调用 `list_all_tools()` 都会实时遍历注册器：
```python
service = ToolService(config)
tools = service.list_all_tools()  # 实时读取，无缓存
```

### 4. Agent 全流程闭环
```
1. 生成工具  →  create_native_tool() / create_mcp_tool()
2. 注册工具  →  register_native() / register_mcp()
3. 查询列表  →  list_all_tools() 【动态读入】
4. 参数校验  →  validate_params()
5. 执行工具  →  execute_tool()
6. 绑定策略  →  register_skill() + bind_skill_to_tool()
```

## 使用示例

### 示例 1: 基础使用

```python
import asyncio
from src.tools import ToolConfig, ToolService, NativeTool, ToolType


class CalculatorTool(NativeTool):
    name = "calculator"
    description = "Perform basic arithmetic calculations"
    parameters = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Mathematical expression"
            }
        },
        "required": ["expression"]
    }
    tool_type = ToolType.NATIVE
    
    async def _execute_impl(self, expression: str) -> str:
        result = eval(expression)
        return f"{expression} = {result}"


async def main():
    config = ToolConfig()
    service = ToolService(config)
    
    # 注册工具
    service.register_native(CalculatorTool())
    
    # 查询工具列表
    tools = service.list_all_tools()
    print(f"Available tools: {[t['name'] for t in tools]}")
    
    # 执行工具
    result = await service.execute_tool("calculator", {"expression": "2 + 2"})
    print(f"Result: {result}")


asyncio.run(main())
```

### 示例 2: 动态创建工具

```python
import asyncio
from src.tools import ToolConfig, ToolService


async def custom_function(text: str) -> str:
    return text.upper()


async def main():
    config = ToolConfig()
    service = ToolService(config)
    
    # 通过工厂创建工具
    tool = await service.create_native_tool(
        name="text_upper",
        description="Convert text to uppercase",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string"}
            },
            "required": ["text"]
        },
        execute_func=custom_function
    )
    
    # 直接使用
    result = await service.execute_tool("text_upper", {"text": "hello"})
    print(f"Result: {result}")


asyncio.run(main())
```

### 示例 3: 参数校验

```python
from src.tools import ToolConfig, ToolService, NativeTool, ToolType


class SafeCalculator(NativeTool):
    name = "safe_calc"
    description = "Safe calculator with validation"
    parameters = {
        "type": "object",
        "properties": {
            "num1": {"type": "number", "minimum": 0, "maximum": 1000},
            "num2": {"type": "number", "minimum": 0, "maximum": 1000},
            "operation": {"type": "string", "enum": ["add", "subtract"]}
        },
        "required": ["num1", "num2", "operation"]
    }
    tool_type = ToolType.NATIVE
    
    async def _execute_impl(self, num1: float, num2: float, operation: str) -> float:
        if operation == "add":
            return num1 + num2
        elif operation == "subtract":
            return num1 - num2


config = ToolConfig(validate_before_execute=True)
service = ToolService(config)
service.register_native(SafeCalculator())

# 参数校验失败示例
valid, errors = service.validate_params("safe_calc", {"num1": -5, "num2": 10, "operation": "add"})
print(f"Valid: {valid}, Errors: {errors}")
```

### 示例 4: MCP 工具代理

```python
from src.tools import ToolConfig, ToolService, MCPProxyTool


config = ToolConfig(enable_mcp=True)
service = ToolService(config)

# 创建 MCP 代理工具
mcp_tool = MCPProxyTool(
    name="filesystem_read",
    description="Read file from filesystem",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"}
        },
        "required": ["path"]
    },
    server_name="filesystem",
    mcp_method="read_file"
)

service.register_mcp(mcp_tool)
```

## API 参考

### ToolService 核心方法

| 方法 | 说明 |
|------|------|
| `list_all_tools()` | 获取所有工具定义（实时读取） |
| `register_native(tool)` | 注册原生工具 |
| `register_mcp(tool)` | 注册MCP工具 |
| `register_skill(skill)` | 注册Skill策略 |
| `get_tool(name)` | 获取工具实例 |
| `validate_params(name, params)` | 验证参数 |
| `execute_tool(name, params)` | 执行工具 |
| `bind_skill_to_tool(skill_name, tool_name)` | 绑定Skill到工具 |
| `create_native_tool(...)` | 创建并注册原生工具 |
| `create_mcp_tool(...)` | 创建并注册MCP工具 |

### ToolConfig 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enable_native` | bool | True | 启用原生工具 |
| `enable_mcp` | bool | False | 启用MCP工具 |
| `enable_skills` | bool | True | 启用Skills |
| `mcp_servers` | list[dict] | [] | MCP服务器配置 |
| `mcp_timeout` | int | 30 | MCP超时时间（秒） |
| `validate_before_execute` | bool | True | 执行前校验参数 |

## 代码行数统计

| 文件 | 行数 | 说明 |
|------|------|------|
| config.py | 18 | 配置类 |
| protocol.py | 28 | 协议定义 |
| validator.py | 59 | 参数校验 |
| factory.py | 44 | 工具工厂 |
| service.py | 107 | 总服务层 |
| registry/native_registry.py | 35 | 原生注册器 |
| registry/mcp_registry.py | 35 | MCP注册器 |
| registry/skill_registry.py | 40 | Skill注册器 |
| native/base.py | 18 | 原生基类 |
| mcp/mcp_proxy.py | 17 | MCP代理 |
| skills/base.py | 14 | Skill基类 |

**所有文件均 ≤ 200 行** ✅

## 下一步

1. **实现更多原生工具** - 如文件操作、网络请求等
2. **实现 MCP 客户端** - 连接真实的 MCP 服务器
3. **扩展 Skill 策略** - 添加更复杂的工具使用策略
4. **集成到 Agent** - 将 ToolService 集成到 AgentRunner
5. **添加测试套件** - 完善单元测试和集成测试
