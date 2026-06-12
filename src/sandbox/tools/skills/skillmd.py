"""
SKILL.md —— agentskills.io 业界标准格式解析器。

将 SKILL.md（YAML frontmatter + Markdown body）转换为
HpAgent 内部 skill definition dict，兼容现有 SkillPipeline 体系。

支持两种模式：
  1. 有 pipeline 字段（HpAgent 扩展）→ 标准流水线 skill
  2. 无 pipeline → 指令型 skill，LLM 调用时返回 body 内容作为参考
"""
import re
import yaml
from pathlib import Path
from typing import Optional


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """从 SKILL.md 文本中分离 YAML frontmatter 和 Markdown body。"""
    # 匹配开头的 --- ... --- 块
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        raise ValueError("SKILL.md missing YAML frontmatter (--- ... ---)")
    frontmatter_text = m.group(1)
    body = text[m.end():].strip()
    frontmatter = yaml.safe_load(frontmatter_text)
    if not isinstance(frontmatter, dict):
        raise ValueError("SKILL.md frontmatter is not a valid YAML mapping")
    return frontmatter, body


def _validate_frontmatter(fm: dict, dir_name: Optional[str] = None):
    """校验 SKILL.md 必填字段。"""
    name = fm.get("name", "")
    if not name or not isinstance(name, str):
        raise ValueError("SKILL.md missing required field: name")
    if len(name) > 64:
        raise ValueError(f"SKILL.md name too long ({len(name)} > 64): {name}")
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name):
        raise ValueError(f"SKILL.md name must be kebab-case: {name}")
    if dir_name is not None and name != dir_name:
        raise ValueError(
            f"SKILL.md name '{name}' does not match directory name '{dir_name}'"
        )
    desc = fm.get("description", "")
    if not desc or not isinstance(desc, str):
        raise ValueError("SKILL.md missing required field: description")
    if len(desc) > 1024:
        raise ValueError(f"SKILL.md description too long ({len(desc)} > 1024)")


def parse_skillmd(filepath: Path) -> tuple[dict, str]:
    """解析 SKILL.md 文件，返回 (frontmatter_dict, body_text)。

    校验 agentskills.io 规范的必填字段（name, description），
    若格式不合规则抛出 ValueError。
    """
    text = filepath.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(text)

    # 确定该 skill 的目录名（用于校验 name == directory）
    parent_dir = filepath.parent.name if filepath.parent.name else None
    _validate_frontmatter(frontmatter, parent_dir)

    return frontmatter, body


def skillmd_to_definition(fm: dict, body: str) -> dict:
    """将 SKILL.md 解析结果转换为 HpAgent skill definition dict。

    HpAgent 扩展字段（可出现在 SKILL.md frontmatter 中）：
      - parameters: JSON Schema → HpAgent 工具参数
      - pipeline:   HpAgent 流水线步骤列表
      - on_error:   错误策略（默认 "stop"）
      - timeout_seconds: 超时秒数（默认 60）
    """
    has_pipeline = bool(fm.get("pipeline", {}).get("steps"))

    definition = {
        "name": fm["name"],
        "description": fm.get("description", ""),
        "type": "pipeline" if has_pipeline else "instruction",
    }

    if has_pipeline:
        definition["parameters"] = fm.get("parameters", {})
        definition["pipeline"] = fm.get("pipeline", {})
        definition["on_error"] = fm.get("on_error", "stop")
        definition["timeout_seconds"] = fm.get("timeout_seconds", 60.0)
    else:
        definition["body"] = body
        # 指令型 skill 也可以声明 parameters（可选参数传给 body 模板使用）
        definition["parameters"] = fm.get("parameters") or {}

    return definition
