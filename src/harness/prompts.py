"""
PromptLoader —— 从 YAML 文件加载所有 Agent prompt，解除硬编码。

目录结构:
  config/prompts/
    ├── identities.yaml    # 渠道身份声明 + 行为风格 (napcat/console/web/default)
    ├── guidance.yaml      # 工具使用纪律
    ├── environment.yaml   # 运行环境提示 (docker/wsl)
    └── system.yaml        # 系统级提示 (跨渠道/记忆召回指令)

其他模块通过 PromptLoader 的具名方法获取 prompt 文本，
prompt 内容可随时通过编辑 YAML 文件调整，无需改代码。
"""
from __future__ import annotations

import logging
from typing import Dict, Any

logger = logging.getLogger("HpAgent.PromptLoader")

DEFAULT_IDENTITY = (
    "你是一个智能 AI 聊天助手。"
    "你友善、博学、直接，能协助用户处理广泛的任务。"
    "请始终使用中文与用户交流，除非用户明确要求使用其他语言。"
)


class PromptLoader:
    """Agent prompt 查询接口，数据来自 AppConfig.prompts。

    用法::

        loader = PromptLoader(config.prompts)
        identity = loader.get_identity("console")
        guidance = loader.get_guidance("tool_enforcement")
        env_hint = loader.get_environment("docker")
    """

    def __init__(self, prompts_config=None):
        """从 PromptsConfig 初始化。

        Args:
            prompts_config: PromptsConfig 实例。None 时使用空默认值。
        """
        # 延迟导入避免循环依赖
        from orchestration.config import PromptsConfig
        cfg = prompts_config if prompts_config is not None else PromptsConfig()
        self._identities: Dict[str, str] = dict(cfg.identities)
        self._guidance: Dict[str, str] = dict(cfg.guidance)
        self._environment: Dict[str, str] = dict(cfg.environment)
        self._system: Dict[str, str] = dict(cfg.system)
        self._tool_summary: Dict[str, Any] = dict(cfg.tool_summary)

    # ── 渠道身份 ──────────────────────────────────────────────────────────

    def get_identity(self, channel: str, default: str = "") -> str:
        """获取指定渠道的身份声明 prompt（含行为风格，已合并至 identities.yaml）。

        Args:
            channel: 渠道名 (napcat / console / web)。
            default: 若渠道未找到且 default 为空，返回内置 DEFAULT_IDENTITY。

        Returns:
            身份声明文本。
        """
        if channel and channel in self._identities:
            return self._identities[channel]
        if default:
            return default
        return self._identities.get("default", DEFAULT_IDENTITY)

    @property
    def identity_map(self) -> Dict[str, str]:
        """渠道 → YAML key 的映射表（用于渠道检测后的查找）。"""
        cmap = {}
        # identities.yaml 中 channel_map 字段定义了映射
        raw = self._identities.get("channel_map", {})
        if isinstance(raw, dict):
            return raw
        return cmap

    # ── 行为引导 ──────────────────────────────────────────────────────────

    def get_guidance(self, name: str) -> str:
        """获取指定名称的行为引导 prompt。

        Args:
            name: 引导名 (tool_enforcement)。

        Returns:
            引导文本，未找到返回空字符串。
        """
        return self._guidance.get(name, "")

    # ── 运行环境 ──────────────────────────────────────────────────────────

    def get_environment(self, name: str) -> str:
        """获取指定名称的环境提示。

        Args:
            name: 环境名 (docker / wsl)。

        Returns:
            环境提示文本，未找到返回空字符串。
        """
        return self._environment.get(name, "")

    # ── 系统级提示 ────────────────────────────────────────────────────────

    def get_system(self, key: str) -> str:
        """获取系统级提示文本。

        Args:
            key: cross_channel / recall_instruction。

        Returns:
            对应文本，未找到返回空字符串。
        """
        return self._system.get(key, "")

    def format_cross_channel(self, channels: str) -> str:
        """格式化跨渠道提示。"""
        template = self.get_system("cross_channel")
        if not template:
            return ""
        return template.format(channels=channels)

    # ── 工具结果摘要 ──────────────────────────────────────────────────────

    def get_tool_summary(self, key: str, default: str = "") -> str:
        """获取工具摘要相关 prompt 文本。"""
        return self._tool_summary.get(key, default)

    def get_tool_summary_hint(self, tool_name: str) -> str:
        """获取指定工具的摘要关注点 hint，未匹配则返回 _default。"""
        hints_raw = self._tool_summary.get("hints", {})
        if isinstance(hints_raw, dict):
            return hints_raw.get(tool_name, hints_raw.get("_default", "提取输出中的关键信息和数据。"))
        return "提取输出中的关键信息和数据。"

    def get_tool_summary_template(self) -> str:
        """获取摘要系统指令模板。"""
        return self._tool_summary.get("system_template",
            "你是工具输出摘要助手。请将以下 {tool_name} 工具的输出浓缩为简洁的中文摘要（不超过 {max_chars} 字符）。\n\n"
        )
