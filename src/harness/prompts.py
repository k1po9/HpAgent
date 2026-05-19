"""
PromptLoader —— 从 YAML 文件加载所有 Agent prompt，解除硬编码。

目录结构:
  config/prompts/
    ├── identities.yaml    # 渠道身份声明 (napcat/console/web/default)
    ├── guidance.yaml      # 风格引导 + 工具纪律
    ├── environment.yaml   # 运行环境提示 (docker/wsl)
    └── system.yaml        # 系统级提示 (跨渠道/上下文文件/记忆/截断)

其他模块通过 PromptLoader 的具名方法获取 prompt 文本，
prompt 内容可随时通过编辑 YAML 文件调整，无需改代码。
"""
from __future__ import annotations

import logging
from typing import Dict

logger = logging.getLogger("HpAgent.PromptLoader")

DEFAULT_IDENTITY = (
    "你是 HpAgent，一个智能 AI 聊天助手。"
    "你友善、博学、直接，能协助用户处理广泛的任务。"
    "请始终使用中文与用户交流，除非用户明确要求使用其他语言。"
)


class PromptLoader:
    """Agent prompt 查询接口，数据来自 AppConfig.prompts。

    用法::

        loader = PromptLoader(config.prompts)
        identity = loader.get_identity("console")
        style = loader.get_guidance("console_style")
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

    # ── 渠道身份 ──────────────────────────────────────────────────────────

    def get_identity(self, channel: str, default: str = "") -> str:
        """获取指定渠道的身份声明 prompt。

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

    # ── 风格引导 ──────────────────────────────────────────────────────────

    def get_guidance(self, name: str) -> str:
        """获取指定名称的风格引导 prompt。

        Args:
            name: 引导名 (chat_personality / console_style / tool_enforcement)。

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
            key: cross_channel / context_file_header / truncate_marker /
                 injection_blocked / recall_instruction。

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

    def format_truncate_marker(self, label: str, head_chars: int,
                                tail_chars: int, total_chars: int) -> str:
        """格式化截断标记。"""
        template = self.get_system("truncate_marker")
        if not template:
            return f"\n\n[...已截断 {label}]\n\n"
        return template.format(
            label=label, head_chars=head_chars,
            tail_chars=tail_chars, total_chars=total_chars,
        )

    def format_injection_blocked(self, filename: str, findings: str) -> str:
        """格式化注入拦截消息。"""
        template = self.get_system("injection_blocked")
        if not template:
            return f"[已拦截: {filename} 包含潜在 prompt 注入 ({findings})，内容未加载。]"
        return template.format(filename=filename, findings=findings)
