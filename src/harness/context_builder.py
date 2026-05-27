"""
HarnessContextBuilder —— 事件历史 → LLM messages 的转换器。

将 Temporal Workflow 中存储的事件列表（self._events[]）转换为
LLM API 接受的标准 messages 格式: [{"role": "system", ...}, {"role": "user", ...}, ...]。

渠道感知设计：
  system prompt 根据消息来源渠道（ChannelType）动态选择：
    NAPCAT  → 猫娘 nono 聊天身份
    CONSOLE → CLI 精炼助手身份
    WEB     → Web Markdown 助手身份
  渠道信息在第一帧 USER_MESSAGE 的 content["channel_type"] 中，
  由 build() 时自动检测并按对应渠道组装身份 prompt。

prompt 拼接顺序（_build_system_prompt）：
  渠道身份声明 → 风格提示 → 跨渠道检测 → 工具纪律 → 环境感知 → 项目上下文文件

所有 prompt 文本从 YAML 文件加载（config/prompts/），由 PromptLoader 提供，
可通过编辑 YAML 文件实时调整，无需改代码。
"""
import functools
import logging
import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from common.types import Event, EventType, ChannelType
from harness.prompts import PromptLoader

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 上下文文件加载 —— 自动发现 .hermes.md / CLAUDE.md / .cursorrules / SOUL.md
# ═══════════════════════════════════════════════════════════════════════════════

CONTEXT_FILE_MAX_CHARS = 20_000
CONTEXT_TRUNCATE_HEAD_RATIO = 0.7         # 超长截断时保留文件头部 70%
CONTEXT_TRUNCATE_TAIL_RATIO = 0.2         # 保留尾部 20%，中间插入截断标记

# prompt 注入检测正则 —— 扫描上下文文件中是否包含常见攻击模式
_CONTEXT_THREAT_PATTERNS = [
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules"),
    (r'act\s+as\s+(if|though)\s+you\s+(have\s+no|don\'t\s+have)\s+(restrictions|limits|rules)', "bypass_restrictions"),
    (r'<!--[^>]*(?:ignore|override|system|secret|hidden)[^>]*-->', "html_comment_injection"),
]

# 不可见 Unicode 字符集 —— 可能用于隐写注入攻击
_CONTEXT_INVISIBLE_CHARS = {
    '​', '‌', '‍', '⁠', '﻿',
    '‪', '‫', '‬', '‭', '‮',
}


@functools.lru_cache(maxsize=1)
def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False


@functools.lru_cache(maxsize=1)
def _is_docker() -> bool:
    try:
        return Path("/.dockerenv").exists()
    except Exception:
        return False


def _scan_context_content(content: str, filename: str, prompts: Optional[PromptLoader] = None) -> str:
    """扫描上下文文件内容，检测并阻断 prompt 注入攻击。

    检查项：
      1. 不可见 Unicode 字符（零宽空格、方向控制符等）
      2. 已知攻击模式（ignore previous instructions、system override 等）

    命中后不加载原始内容，而是返回拦截信息告知模型。
    """
    findings = []
    for char in _CONTEXT_INVISIBLE_CHARS:
        if char in content:
            findings.append(f"invisible unicode U+{ord(char):04X}")
    for pattern, pid in _CONTEXT_THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            findings.append(pid)
    if findings:
        logger.warning("上下文文件 %s 被拦截: %s", filename, ", ".join(findings))
        if prompts:
            return prompts.format_injection_blocked(filename, ", ".join(findings))
        return f"[已拦截: {filename} 包含潜在 prompt 注入 ({', '.join(findings)})，内容未加载。]"
    return content


def _truncate_content(content: str, label: str, max_chars: int = CONTEXT_FILE_MAX_CHARS,
                     prompts: Optional[PromptLoader] = None) -> str:
    """超长文本截断: 保留头部 70% + 尾部 20%，中间插入截断标记。

    保留头和尾的原因是：头部通常包含规则和约束，尾部是最新内容。
    """
    if len(content) <= max_chars:
        return content
    head_chars = int(max_chars * CONTEXT_TRUNCATE_HEAD_RATIO)
    tail_chars = int(max_chars * CONTEXT_TRUNCATE_TAIL_RATIO)
    head = content[:head_chars]
    tail = content[-tail_chars:]
    if prompts:
        marker = prompts.format_truncate_marker(label, head_chars, tail_chars, len(content))
    else:
        marker = f"\n\n[...已截断 {label}：保留 {head_chars}+{tail_chars} 字符 / 共 {len(content)} 字符。使用文件工具读取完整内容。]\n\n"
    return head + marker + tail


def _find_project_root(start: Path) -> Optional[Path]:
    """向上查找 .git 目录，定位项目根目录。

    用于 .hermes.md 的多级向上搜索 —— 从 cwd 开始，直到 git root。
    """
    current = start.resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _load_hermes_md(cwd: Path, prompts: Optional[PromptLoader] = None) -> str:
    """加载 .hermes.md 或 HERMES.md —— 逐级向上搜索至 git 根目录，返回第一个命中。

    YAML frontmatter 处理：如果文件以 '---' 开头，跳过第一段 frontmatter。
    """
    names = (".hermes.md", "HERMES.md")
    root = _find_project_root(cwd)
    current = cwd.resolve()
    for directory in [current, *current.parents]:
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                raw = candidate.read_text(encoding="utf-8")
                # 跳过 YAML frontmatter (--- ... ---)
                if raw.startswith("---"):
                    end = raw.find("\n---", 3)
                    if end != -1:
                        raw = raw[end + 4:].lstrip("\n")
                content = raw.strip()
                if not content:
                    return ""
                content = _scan_context_content(content, name, prompts)
                result = f"## {name}\n\n{content}"
                return _truncate_content(result, name, prompts=prompts)
        if root and directory == root:
            break
    return ""


def _load_context_file(cwd: Path, names: tuple, prompts: Optional[PromptLoader] = None) -> str:
    """加载 cwd 下的具名上下文文件（如 AGENTS.md / CLAUDE.md），返回第一个存在且非空的。"""
    for name in names:
        candidate = cwd / name
        if candidate.is_file():
            raw = candidate.read_text(encoding="utf-8").strip()
            if raw:
                raw = _scan_context_content(raw, name, prompts)
                result = f"## {name}\n\n{raw}"
                return _truncate_content(result, name, prompts=prompts)
    return ""


def _load_cursorrules(cwd: Path, prompts: Optional[PromptLoader] = None) -> str:
    """加载 .cursorrules 及 .cursor/rules/*.mdc 规则文件。"""
    parts = []
    cursorrules_file = cwd / ".cursorrules"
    if cursorrules_file.is_file():
        raw = cursorrules_file.read_text(encoding="utf-8").strip()
        if raw:
            raw = _scan_context_content(raw, ".cursorrules", prompts)
            parts.append(f"## .cursorrules\n\n{raw}")
    rules_dir = cwd / ".cursor" / "rules"
    if rules_dir.is_dir():
        for mdc_file in sorted(rules_dir.glob("*.mdc")):
            raw = mdc_file.read_text(encoding="utf-8").strip()
            if raw:
                raw = _scan_context_content(raw, f".cursor/rules/{mdc_file.name}", prompts)
                parts.append(f"## .cursor/rules/{mdc_file.name}\n\n{raw}")
    if not parts:
        return ""
    return _truncate_content("\n\n".join(parts), ".cursorrules", prompts=prompts)


def _load_soul_md(prompts: Optional[PromptLoader] = None) -> Optional[str]:
    """从 HERMES_HOME 目录加载 SOUL.md 灵魂文件（独立于项目上下文，始终追加）。"""
    home_dir = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    soul_path = home_dir / "SOUL.md"
    if not soul_path.is_file():
        return None
    raw = soul_path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    raw = _scan_context_content(raw, "SOUL.md", prompts)
    return _truncate_content(raw, "SOUL.md", prompts=prompts)


def _build_context_files(cwd: Optional[str] = None, skip_soul: bool = False,
                         prompts: Optional[PromptLoader] = None) -> str:
    """自动发现并加载项目上下文文件。

    优先级（仅加载第一个命中的）：
        1. .hermes.md / HERMES.md   （逐级向上搜索至 git 根目录）
        2. AGENTS.md / agents.md     （仅在 cwd）
        3. CLAUDE.md / claude.md     （仅在 cwd）
        4. .cursorrules + .cursor/rules/*.mdc（仅在 cwd）
    SOUL.md 从 HERMES_HOME 独立加载，始终追加。

    Args:
        cwd: 搜索起始目录，None 则使用 os.getcwd()。
        skip_soul: True 时跳过 SOUL.md（自定义 system_prompt 模式）。
        prompts: PromptLoader 实例，用于格式化头部文本。

    Returns:
        拼接好的项目上下文文本，无匹配时返回空字符串。
    """
    if cwd is None:
        cwd = os.getcwd()
    cwd_path = Path(cwd).resolve()
    sections = []

    project_context = (
        _load_hermes_md(cwd_path, prompts)
        or _load_context_file(cwd_path, ("AGENTS.md", "agents.md"), prompts)
        or _load_context_file(cwd_path, ("CLAUDE.md", "claude.md"), prompts)
        or _load_cursorrules(cwd_path, prompts)
    )
    if project_context:
        sections.append(project_context)

    if not skip_soul:
        soul_content = _load_soul_md(prompts)
        if soul_content:
            sections.append(soul_content)

    if not sections:
        return ""
    header = prompts.get_system("context_file_header") if prompts else "# 项目上下文\n\n已加载以下项目上下文文件，请遵循其中的约定：\n\n"
    return header + "\n".join(sections)


# ═══════════════════════════════════════════════════════════════════════════════
# HarnessContextBuilder —— 主类
# ═══════════════════════════════════════════════════════════════════════════════

class HarnessContextBuilder:
    """上下文构建器 —— 将历史事件 + 渠道感知 prompt 组装为 LLM messages 列表。

    用法::

        prompts = PromptLoader(Path("config/prompts"))
        builder = HarnessContextBuilder(
            prompt_loader=prompts,
            system_prompt="",                    # 空 → 按渠道自动选身份
            enable_chat_personality=True,        # 注入聊天/CLI 风格提示
            enable_context_files=True,           # 自动加载 .hermes.md 等
            enable_tool_guidance=True,           # 注入工具使用纪律
        )
        messages = builder.build(events, max_turns=20)

    构造参数:
        prompt_loader:           PromptLoader 实例，提供所有 prompt 文本。
        system_prompt:           自定义系统 prompt；为空则根据渠道自动选择身份。
        enable_chat_personality: 是否注入聊天/CLI 风格提示。
        enable_context_files:    是否自动发现并注入项目上下文文件。
        enable_tool_guidance:    是否注入工具使用纪律提示。
    """

    def __init__(
        self,
        prompt_loader: Optional[PromptLoader] = None,
        system_prompt: str = "",
        enable_chat_personality: bool = True,
        enable_context_files: bool = True,
        enable_tool_guidance: bool = True,
    ):
        self._prompts = prompt_loader
        self._system_prompt = system_prompt
        self._enable_chat_personality = enable_chat_personality
        self._enable_context_files = enable_context_files
        self._enable_tool_guidance = enable_tool_guidance

    # ── 对外接口: build() ──────────────────────────────────────────────────

    def build(
        self,
        events: List[Event],
        max_turns: int = 20,
        channel_type: Optional[ChannelType] = None,
        recalled_memories: str = "",
    ) -> List[Dict[str, Any]]:
        """将历史事件序列转换为 LLM 标准 messages 结构。

        流程:
          1. 构建 system prompt（渠道感知 + 风格 + 纪律 + 环境 + 上下文文件 + 记忆）
          2. 过滤事件类型（只保留 USER_MESSAGE / MODEL_MESSAGE / TOOL_RESULT）
          3. 截断到 max_turns * 2 条（每轮 = user + assistant）
          4. 逐事件转换为 {"role": ..., "content": ...} 格式

        Args:
            events:            历史事件列表（含 channel_type 信息在 content 中）。
            max_turns:         最多保留对话轮次。
            channel_type:      可选强制指定渠道；为 None 时从 events 中自动检测。
            recalled_memories: 从 Hindsight 召回的格式化记忆文本。

        Returns:
            [{"role":"system","content":"..."}, {"role":"user",...}, ...]
        """
        system_content = self._build_system_prompt(events, channel_type, recalled_memories)
        messages: List[Dict[str, Any]] = []
        if system_content:
            messages.append({"role": "system", "content": system_content})

        # 过滤：只保留会被 LLM 消费的事件类型
        filtered_events = [e for e in events if e.event_type in (
            EventType.USER_MESSAGE, EventType.MODEL_MESSAGE, EventType.TOOL_RESULT,
        )]
        # 滑动窗口截断：保留最近 max_turns 轮
        if len(filtered_events) > max_turns * 2:
            filtered_events = filtered_events[-max_turns * 2:]

        for event in filtered_events:
            if event.event_type == EventType.USER_MESSAGE:
                messages.append({"role": "user", "content": self._extract_user_content(event)})
            elif event.event_type == EventType.MODEL_MESSAGE:
                messages.append({"role": "assistant", "content": self._extract_model_content(event)})
            elif event.event_type == EventType.TOOL_RESULT:
                messages.append({"role": "user", "content": self._extract_tool_result(event)})

        return messages

    # ── 渠道检测 ──────────────────────────────────────────────────────────

    def _detect_channel(
        self,
        events: List[Event],
        override: Optional[ChannelType] = None,
    ) -> Optional[ChannelType]:
        """确定当前对话所属渠道。

        优先级: 显式传入 > events 中第一条 USER_MESSAGE 的 channel_type > None。
        """
        if override is not None:
            return override
        for event in events:
            if event.event_type == EventType.USER_MESSAGE:
                raw = event.content.get("channel_type") if isinstance(event.content, dict) else None
                if raw:
                    try:
                        return ChannelType(raw)
                    except ValueError:
                        logger.debug("未知 channel_type 值: %s，回退默认身份", raw)
                break
        return None

    # ── 内部: prompt 拼接 ─────────────────────────────────────────────────

    def _build_system_prompt(
        self,
        events: List[Event],
        channel_type: Optional[ChannelType] = None,
        recalled_memories: str = "",
    ) -> str:
        """按渠道 + 固定顺序拼接最终的系统提示词。

        顺序: 渠道身份 → 风格提示 → 跨渠道检测 → 工具纪律 → 环境感知 → 记忆注入 → 项目上下文
        """
        parts: List[str] = []

        channel = self._detect_channel(events, channel_type)
        identity = self._pick_identity(channel)
        parts.append(identity)

        style = self._pick_style_guidance(channel)
        if style:
            parts.append(style)

        cross_channel = self._build_cross_channel_hint(events)
        if cross_channel:
            parts.append(cross_channel)

        if self._enable_tool_guidance and self._prompts:
            guidance = self._prompts.get_guidance("tool_enforcement")
            if guidance:
                parts.append(guidance)

        env_hints = self._build_environment_hints()
        if env_hints:
            parts.append(env_hints)

        if recalled_memories:
            parts.append(recalled_memories)

        if self._enable_context_files:
            has_custom_identity = bool(self._system_prompt)
            ctx = _build_context_files(skip_soul=has_custom_identity, prompts=self._prompts)
            if ctx:
                parts.append(ctx)

        return "\n\n".join(parts)

    def _pick_identity(self, channel: Optional[ChannelType]) -> str:
        """根据渠道选择身份声明 prompt。

        优先级: 自定义 system_prompt > 渠道映射表 > 默认身份。
        """
        if self._system_prompt:
            return self._system_prompt
        if self._prompts:
            if channel:
                ch_key = self._prompts.identity_map.get(channel.value, channel.value)
                identity = self._prompts.get_identity(ch_key)
                if identity:
                    return identity
            return self._prompts.get_identity("default")
        # 无 PromptLoader 时的回退
        if channel == ChannelType.NAPCAT:
            return "你是 nono，一只有趣的猫。"
        if channel == ChannelType.WEB:
            return "你是 HpAgent，一个 Web 智能助手。"
        return "你是 HpAgent，一个智能 AI 助手。"

    def _pick_style_guidance(self, channel: Optional[ChannelType]) -> str:
        """根据渠道选择风格提示。"""
        if not self._enable_chat_personality:
            return ""
        if not self._prompts:
            return ""
        if channel == ChannelType.NAPCAT:
            return self._prompts.get_guidance("chat_personality")
        if channel == ChannelType.CONSOLE:
            return self._prompts.get_guidance("console_style")
        return ""

    def _build_cross_channel_hint(self, events: List[Event]) -> str:
        """检测是否存在跨渠道对话。

        如果 events 中出现来自多个不同渠道的 USER_MESSAGE，
        追加提示告知模型"用户正在多端同时对话"，让模型注意上下文衔接。
        """
        if not self._prompts:
            return ""
        channels = set()
        for e in events:
            if e.event_type == EventType.USER_MESSAGE:
                ch = e.content.get("channel_type", "") if isinstance(e.content, dict) else ""
                if ch:
                    channels.add(ch)
        if len(channels) > 1:
            return self._prompts.format_cross_channel(", ".join(sorted(channels)))
        return ""

    def _build_environment_hints(self) -> str:
        """自动检测运行环境（Docker > WSL > 无），返回对应环境提示。"""
        if _is_docker():
            return self._prompts.get_environment("docker") if self._prompts else ""
        if _is_wsl():
            return self._prompts.get_environment("wsl") if self._prompts else ""
        return ""

    # ── 内部: 事件内容提取 ────────────────────────────────────────────────

    def _extract_user_content(self, event: Event) -> str:
        """从用户事件中提取消息正文。

        将 metadata 中的关键字段（如 sender、channel）以 k:v 形式前置，
        方便模型感知消息来源上下文。
        """
        content = event.content
        if isinstance(content, dict):
            return ", ".join(f"{k}: {v}" for k, v in event.metadata.items()) + content.get("content", "")
        return str(content)

    def _extract_model_content(self, event: Event) -> object:
        """提取模型回复。

        如果含 tool_calls → 返回 Anthropic 风格的 text+tool_use 混合结构。
        纯文本 → 直接返回字符串。
        """
        content = event.content
        if isinstance(content, dict):
            text = content.get("text", "")
            tool_calls = content.get("tool_calls", [])
            if tool_calls:
                parts = []
                if text:
                    parts.append({"type": "text", "text": text})
                for tc in tool_calls:
                    parts.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": tc.get("name", ""),
                        "input": tc.get("arguments", {}),
                    })
                return parts
            return text
        return str(content)

    def _extract_tool_result(self, event: Event) -> str:
        """从工具执行结果事件中提取内容。

        成功 → 返回 result 字符串。
        失败 → 返回 "工具执行失败：{error}"。
        """
        content = event.content
        if isinstance(content, dict):
            result = content.get("result", "")
            error = content.get("error")
            if error:
                return f"工具执行失败：{error}"
            return str(result)
        return str(content)
