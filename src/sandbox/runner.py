#!/usr/bin/env python3
"""In-jail tool runner —— 在 nsjail 沙箱内部执行的工具调度器。

============================================================================
使用方式
============================================================================

  python3 runner.py <tool_name> '<json_arguments>'

示例:
  python3 runner.py calculator '{"expression": "2 + 2"}'
  → {"success": true, "output": "4"}

============================================================================
约定
============================================================================

  - 所有输出写入 stdout，格式为单行 JSON
  - 成功: {"success": true, "output": <any>}
  - 失败: {"success": false, "error": "<message>"}
  - 任何未捕获异常被序列化为失败 JSON
  - 退出码: 0 = 成功, 1 = 参数错误, 2 = 工具未知, 3 = 执行异常

============================================================================
安全
============================================================================

  此脚本在 nsjail 命名空间内以 nobody 用户运行，
  具有 PID/NET/FS/RLIMIT 隔离，不能访问宿主资源。
"""
import json
import sys
import traceback
from typing import Any, Callable, Dict


# ═══════════════════════════════════════════════════════════════════════════════
# 工具实现
# ═══════════════════════════════════════════════════════════════════════════════

def _tool_calculator(expression: str) -> str:
    """安全计算数学表达式。

    __builtins__={} 阻止任意代码执行，仅支持纯数学运算。
    """
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return str(result)
    except Exception as e:
        raise ValueError(f"Expression evaluation failed: {e}")


def _tool_web_search(query: str, limit: int = 5) -> dict:
    """Web 搜索工具 —— 占位实现（返回模拟数据）。

    TODO: 接入真实搜索 API。
    """
    return {
        "query": query,
        "results": [
            {"title": f"Result {i + 1} for {query}", "url": f"https://example.com/{i}"}
            for i in range(min(limit, 3))
        ],
    }


def _tool_file_read(file_path: str) -> str:
    """读取文件内容。

    nsjail chroot 已限制可访问的路径范围，
    因此此处的 open() 调用是安全的。
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise ValueError(f"File not found: {file_path}")
    except PermissionError:
        raise ValueError(f"Permission denied: {file_path}")
    except IsADirectoryError:
        raise ValueError(f"Is a directory: {file_path}")
    except Exception as e:
        raise ValueError(f"File read error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 工具注册表
# ═══════════════════════════════════════════════════════════════════════════════

TOOLS: Dict[str, Callable[..., Any]] = {
    "calculator": _tool_calculator,
    "web_search": _tool_web_search,
    "file_read": _tool_file_read,
}


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """runner.py 主入口 —— 解析参数、分派工具、输出 JSON 结果。"""
    if len(sys.argv) < 3:
        print(json.dumps({
            "success": False,
            "error": "Usage: runner.py <tool_name> <arguments_json>",
        }))
        sys.exit(1)

    tool_name = sys.argv[1]
    args_json = sys.argv[2]

    # 解析参数 JSON
    try:
        arguments = json.loads(args_json)
    except json.JSONDecodeError as e:
        print(json.dumps({
            "success": False,
            "error": f"Invalid JSON arguments: {e}",
        }))
        sys.exit(1)

    if not isinstance(arguments, dict):
        print(json.dumps({
            "success": False,
            "error": "Arguments must be a JSON object",
        }))
        sys.exit(1)

    # 查找工具
    tool_func = TOOLS.get(tool_name)
    if tool_func is None:
        print(json.dumps({
            "success": False,
            "error": f"Unknown tool: {tool_name}",
        }))
        sys.exit(2)

    # 执行工具
    try:
        result = tool_func(**arguments)
        print(json.dumps({
            "success": True,
            "output": result,
        }, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }, ensure_ascii=False))
        sys.exit(3)


if __name__ == "__main__":
    main()
