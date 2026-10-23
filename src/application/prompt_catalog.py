"""集中加载模型角色提示词；文件错误直接报告，不回退到隐藏提示词。"""

from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache(maxsize=1)
def _catalog() -> dict[str, str]:
    path = Path(__file__).resolve().parents[2] / "config" / "prompts" / "agents.yaml"
    with path.open(encoding="utf-8") as stream:
        values = yaml.safe_load(stream)
    if not isinstance(values, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) or not value.strip()
        for key, value in values.items()
    ):
        raise ValueError(f"提示词配置必须是非空文本映射：{path}")
    return {key: value.strip() for key, value in values.items()}


def agent_prompt(name: str) -> str:
    return _catalog()[name]
