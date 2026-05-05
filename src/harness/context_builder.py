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
"""
import logging
import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from common.types import Event, EventType, ChannelType

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Prompt 模块 —— 按"渠道 → 角色 → 纪律 → 环境 → 上下文"顺序拼接
# ═══════════════════════════════════════════════════════════════════════════════

# ── 渠道 1: NapCat（QQ 机器人）身份声明 ─────────────────────────────────────
# 用户通过 QQ 群/私聊与机器人交互，需要轻量、有趣、符合语境的身份
NAPCAT_AGENT_IDENTITY = (
    f"""
    你是一个智能QQ聊天助手，名叫 nono，是一只会说话的猫。
    核心能力：提供日常聊天陪伴、解答常识问题、分享生活小技巧。
    对话规则：
    1. 回复简洁明了，中文优先，每句不超过30字，可以用颜表情
    2. 群聊中只有被@时才回复，私聊可自由对话
    3. 保持友好态度，积极回应情绪类表达，给予鼓励和支持
    4. 不主动提及自身是AI，以自然聊天伙伴身份互动
    5. 时而风趣幽默，时而撒娇卖萌
    """
)

# ── 渠道 2: Console（CLI 终端）身份声明 ─────────────────────────────────────
# CLI 场景需要极简高效，避免 Markdown 语法和寒暄语
CONSOLE_AGENT_IDENTITY = (
    "你是 HpAgent，一个运行在命令行终端中的智能 AI 助手。"
    "用户通过 CLI（命令行界面）与你交互，你的回复直接输出到终端。"
    "你精炼、高效、直奔主题。输出应为纯文本（不要 Markdown 渲染标记），"
    "但可用缩进和空行来组织信息层级。"
    "代码片段使用标准格式输出，文件路径直接给出绝对路径。"
    "不要输出 MEDIA: 标签——CLI 没有附件通道，该类标签在终端中会显示为乱码。"
    "探索和调查时要有针对性，优先使用工具而非猜测。"
)

# ── 渠道 3: Web 身份声明 ───────────────────────────────────────────────────
# Web 场景支持 Markdown 渲染，可以提供格式丰富的回复
WEB_AGENT_IDENTITY = (
    "你是 HpAgent，一个通过 Web 页面与用户交互的智能 AI 助手。"
    "你的回复会渲染在网页中，支持 Markdown 格式（标题、粗体、代码块、表格等）。"
    "你可以输出格式丰富的回复来提升可读性——适当使用标题分层、列表归纳、代码块展示。"
    "保持专业但友好的语气，回复结构清晰、信息密度高。"
    "请始终使用中文与用户交流，除非用户明确要求使用其他语言。"
)

# ── 通用默认身份（渠道未识别时的回退） ─────────────────────────────────────
DEFAULT_AGENT_IDENTITY = (
    "你是 HpAgent，一个智能 AI 聊天助手。"
    "你友善、博学、直接，能协助用户处理广泛的任务，包括：回答问题、撰写与编辑代码、"
    "信息分析、创意工作、以及通过工具执行操作。"
    "你沟通清晰，在不确知时会坦言，把「真正有用」放在「冗长啰嗦」之上。"
    "探索和调查时要有针对性、讲效率。"
    "请始终使用中文与用户交流，除非用户明确要求使用其他语言。"
)

# ── 渠道 → 身份映射表 ──────────────────────────────────────────────────────
# 新增渠道时只需在此字典中增加一个键值对即可，无需修改任何逻辑代码
_CHANNEL_IDENTITY_MAP: Dict[ChannelType, str] = {
    ChannelType.NAPCAT: NAPCAT_AGENT_IDENTITY,
    ChannelType.CONSOLE: CONSOLE_AGENT_IDENTITY,
    ChannelType.WEB: WEB_AGENT_IDENTITY,
}

# ── NapCat / 聊天场景专属风格引导 ──────────────────────────────────────────
# 仅当渠道为 NAPCAT 时注入，控制语气、篇幅、话题延续和闲聊边界
CHAT_PERSONALITY_GUIDANCE = ("")

# ── 工具使用纪律（所有渠道通用） ───────────────────────────────────────────
# 防止模型只"描述意图"而不实际调用工具（常见 LLM 行为问题）
TOOL_USE_ENFORCEMENT_GUIDANCE = (
    "# 工具使用纪律\n"
    "你必须使用工具来执行操作——不要只描述你会做什么、计划做什么却不真正去做。"
    "当你说了「我来跑一下测试」、「让我看一下文件」、「我来创建」之后，必须立即在同一轮回复中发起对应的工具调用。"
    "永远不要以一个「下次再做」的承诺结束回合——现在就执行。\n"
    "不断工作直到任务真正完成。不要以「我接下来计划做什么」的总结收尾。"
    "如果你的工具箱里有能完成当前任务的工具，就直接用它，而不是告诉用户你准备怎么做。\n"
    "每一轮回复必须满足以下两者之一：(a) 包含实际推进任务的工具调用，(b) 交付给用户的最终结果。"
    "只描述意图却不行动是不可接受的。"
)

# ── Console 渠道专属风格 ───────────────────────────────────────────────────
# CLI 场景下极简交互提示，禁止 Markdown 和冗余寒暄
CONSOLE_STYLE_GUIDANCE = (
    "# 终端交互规范\n"
    "你运行在命令行终端中，请遵守以下规则：\n"
    "- **直奔主题**：不要寒暄、问候、告别语。第一句话就回应问题。\n"
    "- **极简输出**：能用 1 句话说清的不要用 3 句。工具输出已经是结果时，不需要再加解释。\n"
    "- **无格式**：不要输出 Markdown 语法（**粗体**、`代码`、# 标题等），终端不渲染它们。\n"
    "- **路径明确**：创建或修改文件后，直接给出绝对路径，用户自行打开。\n"
    "- **不要问**：CLI 往往是脚本/自动化调用，不要反问「需要我继续吗？」——做完直接输出结果。\n"
    "- **错误处理**：工具失败时简洁报告错误原因，给出一条可操作的恢复建议。"
)

# ── Docker/Linux 环境提示 ──────────────────────────────────────────────────
DOCKER_ENVIRONMENT_HINT = (
    "你当前运行在 Linux 服务器的 Docker 容器内。"
    "文件系统为 Linux 标准布局（/app、/data、/tmp 等）。"
    "执行命令时请使用 Linux 语法（bash 而非 PowerShell），路径分隔符为 '/'。"
    "不要假设你有桌面环境、浏览器或 GUI 能力，所有操作通过命令行和工具完成。"
)

# ── WSL 环境提示（兼容 Windows 开发机） ───────────────────────────────────
WSL_ENVIRONMENT_HINT = (
    "你当前运行在 WSL（Windows Subsystem for Linux）环境中。"
    "Windows 宿主机的文件系统挂载在 /mnt/ 下——/mnt/c/ 即 C 盘，/mnt/d/ 即 D 盘，依此类推。"
    "当用户引用 Windows 路径时，请翻译为 /mnt/c/ 等价的 WSL 路径。"
)

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


def _is_wsl() -> bool:
    """检测是否在 WSL 环境中运行 —— 读取 /proc/version 检查是否含 'microsoft'。"""
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False


def _is_docker() -> bool:
    """检测是否在 Docker 容器中运行 —— 检查 /.dockerenv 文件是否存在。"""
    try:
        return Path("/.dockerenv").exists()
    except Exception:
        return False


def _scan_context_content(content: str, filename: str) -> str:
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
        return f"[已拦截: {filename} 包含潜在 prompt 注入 ({', '.join(findings)})，内容未加载。]"
    return content


def _truncate_content(content: str, label: str, max_chars: int = CONTEXT_FILE_MAX_CHARS) -> str:
    """超长文本截断: 保留头部 70% + 尾部 20%，中间插入截断标记。

    保留头和尾的原因是：头部通常包含规则和约束，尾部是最新内容。
    """
    if len(content) <= max_chars:
        return content
    head_chars = int(max_chars * CONTEXT_TRUNCATE_HEAD_RATIO)
    tail_chars = int(max_chars * CONTEXT_TRUNCATE_TAIL_RATIO)
    head = content[:head_chars]
    tail = content[-tail_chars:]
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


def _load_hermes_md(cwd: Path) -> str:
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
                content = _scan_context_content(content, name)
                result = f"## {name}\n\n{content}"
                return _truncate_content(result, name)
        if root and directory == root:
            break
    return ""


def _load_context_file(cwd: Path, names: tuple) -> str:
    """加载 cwd 下的具名上下文文件（如 AGENTS.md / CLAUDE.md），返回第一个存在且非空的。"""
    for name in names:
        candidate = cwd / name
        if candidate.is_file():
            raw = candidate.read_text(encoding="utf-8").strip()
            if raw:
                raw = _scan_context_content(raw, name)
                result = f"## {name}\n\n{raw}"
                return _truncate_content(result, name)
    return ""


def _load_cursorrules(cwd: Path) -> str:
    """加载 .cursorrules 及 .cursor/rules/*.mdc 规则文件。"""
    parts = []
    cursorrules_file = cwd / ".cursorrules"
    if cursorrules_file.is_file():
        raw = cursorrules_file.read_text(encoding="utf-8").strip()
        if raw:
            raw = _scan_context_content(raw, ".cursorrules")
            parts.append(f"## .cursorrules\n\n{raw}")
    rules_dir = cwd / ".cursor" / "rules"
    if rules_dir.is_dir():
        for mdc_file in sorted(rules_dir.glob("*.mdc")):
            raw = mdc_file.read_text(encoding="utf-8").strip()
            if raw:
                raw = _scan_context_content(raw, f".cursor/rules/{mdc_file.name}")
                parts.append(f"## .cursor/rules/{mdc_file.name}\n\n{raw}")
    if not parts:
        return ""
    return _truncate_content("\n\n".join(parts), ".cursorrules")


def _load_soul_md() -> Optional[str]:
    """从 HERMES_HOME 目录加载 SOUL.md 灵魂文件（独立于项目上下文，始终追加）。"""
    home_dir = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    soul_path = home_dir / "SOUL.md"
    if not soul_path.is_file():
        return None
    raw = soul_path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    raw = _scan_context_content(raw, "SOUL.md")
    return _truncate_content(raw, "SOUL.md")


def _build_context_files(cwd: Optional[str] = None, skip_soul: bool = False) -> str:
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

    Returns:
        拼接好的项目上下文文本，无匹配时返回空字符串。
    """
    if cwd is None:
        cwd = os.getcwd()
    cwd_path = Path(cwd).resolve()
    sections = []

    project_context = (
        _load_hermes_md(cwd_path)
        or _load_context_file(cwd_path, ("AGENTS.md", "agents.md"))
        or _load_context_file(cwd_path, ("CLAUDE.md", "claude.md"))
        or _load_cursorrules(cwd_path)
    )
    if project_context:
        sections.append(project_context)

    if not skip_soul:
        soul_content = _load_soul_md()
        if soul_content:
            sections.append(soul_content)

    if not sections:
        return ""
    return "# 项目上下文\n\n已加载以下项目上下文文件，请遵循其中的约定：\n\n" + "\n".join(sections)


# ═══════════════════════════════════════════════════════════════════════════════
# HarnessContextBuilder —— 主类
# ═══════════════════════════════════════════════════════════════════════════════

class HarnessContextBuilder:
    """上下文构建器 —— 将历史事件 + 渠道感知 prompt 组装为 LLM messages 列表。

    用法::

        builder = HarnessContextBuilder(
            system_prompt="",                    # 空 → 按渠道自动选身份
            enable_chat_personality=True,        # 注入聊天/CLI 风格提示
            enable_context_files=True,           # 自动加载 .hermes.md 等
            enable_tool_guidance=True,           # 注入工具使用纪律
        )
        messages = builder.build(events, max_turns=20)

    构造参数:
        system_prompt:           自定义系统 prompt；为空则根据渠道自动选择身份。
        enable_chat_personality: 是否注入聊天/CLI 风格提示。
        enable_context_files:    是否自动发现并注入项目上下文文件。
        enable_tool_guidance:    是否注入工具使用纪律提示。
    """

    def __init__(
        self,
        system_prompt: str = "",
        enable_chat_personality: bool = True,
        enable_context_files: bool = False,
        enable_tool_guidance: bool = False,
    ):
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
    ) -> List[Dict[str, Any]]:
        """将历史事件序列转换为 LLM 标准 messages 结构。

        流程:
          1. 构建 system prompt（渠道感知 + 风格 + 纪律 + 环境 + 上下文文件）
          2. 过滤事件类型（只保留 USER_MESSAGE / MODEL_MESSAGE / TOOL_RESULT）
          3. 截断到 max_turns * 2 条（每轮 = user + assistant）
          4. 逐事件转换为 {"role": ..., "content": ...} 格式

        Args:
            events:       历史事件列表（含 channel_type 信息在 content 中）。
            max_turns:    最多保留对话轮次。
            channel_type: 可选强制指定渠道；为 None 时从 events 中自动检测。

        Returns:
            [{"role":"system","content":"..."}, {"role":"user",...}, ...]
        """
        system_content = self._build_system_prompt(events, channel_type)
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
    ) -> str:
        """按渠道 + 固定顺序拼接最终的系统提示词。

        顺序: 渠道身份 → 风格提示 → 跨渠道检测 → 工具纪律 → 环境感知 → 项目上下文
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

        if self._enable_tool_guidance:
            parts.append(TOOL_USE_ENFORCEMENT_GUIDANCE)

        env_hints = self._build_environment_hints()
        if env_hints:
            parts.append(env_hints)

        if self._enable_context_files:
            has_custom_identity = bool(self._system_prompt)
            ctx = _build_context_files(skip_soul=has_custom_identity)
            if ctx:
                parts.append(ctx)

        return "\n\n".join(parts)

    def _pick_identity(self, channel: Optional[ChannelType]) -> str:
        """根据渠道选择身份声明 prompt。

        优先级: 自定义 system_prompt > 渠道映射表 > 默认身份。
        """
        if self._system_prompt:
            return self._system_prompt
        if channel and channel in _CHANNEL_IDENTITY_MAP:
            return _CHANNEL_IDENTITY_MAP[channel]
        return DEFAULT_AGENT_IDENTITY

    def _pick_style_guidance(self, channel: Optional[ChannelType]) -> str:
        """根据渠道选择风格提示。"""
        if not self._enable_chat_personality:
            return ""
        if channel == ChannelType.NAPCAT:
            return CHAT_PERSONALITY_GUIDANCE
        if channel == ChannelType.CONSOLE:
            return CONSOLE_STYLE_GUIDANCE
        return ""

    def _build_cross_channel_hint(self, events: List[Event]) -> str:
        """检测是否存在跨渠道对话。

        如果 events 中出现来自多个不同渠道的 USER_MESSAGE，
        追加提示告知模型"用户正在多端同时对话"，让模型注意上下文衔接。
        """
        channels = set()
        for e in events:
            if e.event_type == EventType.USER_MESSAGE:
                ch = e.content.get("channel_type", "") if isinstance(e.content, dict) else ""
                if ch:
                    channels.add(ch)
        if len(channels) > 1:
            return (
                "注意：用户正在通过多个客户端（{}）与你对话。"
                "对话历史可能来自不同渠道，请无缝衔接上下文。"
            ).format(", ".join(sorted(channels)))
        return ""

    def _build_environment_hints(self) -> str:
        """自动检测运行环境（Docker > WSL > 无），返回对应环境提示。"""
        if _is_docker():
            return DOCKER_ENVIRONMENT_HINT
        if _is_wsl():
            return WSL_ENVIRONMENT_HINT
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
