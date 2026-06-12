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
  渠道身份（含风格） → 跨渠道检测 → 工具纪律 → 环境感知
    → 记忆注入 → 会话内摘要 → 跨会话上下文

所有 prompt 文本从 YAML 文件加载（config/prompts/），由 PromptLoader 提供，
可通过编辑 YAML 文件实时调整，无需改代码。
"""
import logging
from typing import List, Dict, Any, Optional

from common.types import Event, EventType, ChannelType
from common.token_counter import estimate_tokens
from harness.prompts import PromptLoader

logger = logging.getLogger(__name__)


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
            enable_tool_guidance=True,           # 注入工具使用纪律
        )
        messages = builder.build(events, max_turns=20)

    构造参数:
        prompt_loader:        PromptLoader 实例，提供所有 prompt 文本。
        system_prompt:        自定义系统 prompt；为空则根据渠道自动选择身份。
        enable_tool_guidance: 是否注入工具使用纪律提示。
    """

    def __init__(
        self,
        prompt_loader: Optional[PromptLoader] = None,
        system_prompt: str = "",
        enable_tool_guidance: bool = True,
    ):
        self._prompts = prompt_loader
        self._system_prompt = system_prompt
        self._enable_tool_guidance = enable_tool_guidance

    # ── 对外接口: build() ──────────────────────────────────────────────────

    def build(
        self,
        events: List[Event],
        max_turns: int = 20,
        channel_type: Optional[ChannelType] = None,
        recalled_memories: str = "",
        *,
        token_budget: int = 0,
        generation_headroom: int = 4000,
        in_session_summary: str = "",
        extra_context: str = "",
        remaining_turns: int = 0,
        max_tool_turns: int = 0,
        group_context_text: str = "",
    ) -> List[Dict[str, Any]]:
        """将历史事件序列转换为 LLM 标准 messages 结构。

        token_budget > 0 时启用 token 感知截断：
          - system prompt 先扣减预算
          - 从右向左累加 event token 成本，超出剩余预算时停止
        token_budget = 0 时走旧路径（max_turns*2 截断），向后兼容。

        Args:
            events:             历史事件列表。
            max_turns:          最多保留对话轮次（token_budget=0 时生效）。
            channel_type:       可选强制指定渠道。
            recalled_memories:  从 Hindsight 召回的格式化记忆文本。
            token_budget:       上下文总 token 预算（0=关闭 token 感知）。
            generation_headroom: 留给模型输出的 token 空间。
            in_session_summary: 会话内摘要（P1-1 压缩产物）。
            extra_context:      额外注入 system prompt 的上下文文本。

        Returns:
            [{"role":"system","content":"..."}, {"role":"user",...}, ...]
        """
        # 提取 CONTEXT_INHERIT 事件，注入 extra_context
        context_inherit_parts: list[str] = []
        for e in events:
            if e.event_type == EventType.CONTEXT_INHERIT:
                inherit_text = e.content.get("summary", "") if isinstance(e.content, dict) else ""
                if inherit_text:
                    context_inherit_parts.append(inherit_text)
        if context_inherit_parts:
            extra_context = (
                "## 跨会话上下文\n\n以下信息继承自之前的会话：\n"
                + "\n".join(context_inherit_parts)
                + ("\n\n" + extra_context if extra_context else "")
            )

        system_content = self._build_system_prompt(
            events, channel_type, recalled_memories,
            in_session_summary=in_session_summary,
            extra_context=extra_context,
            remaining_turns=remaining_turns,
            max_tool_turns=max_tool_turns,
            group_context_text=group_context_text,
        )
        messages: List[Dict[str, Any]] = []
        if system_content:
            messages.append({"role": "system", "content": system_content})

        # 过滤：只保留会被 LLM 消费的事件类型
        filtered_events = [e for e in events if e.event_type in (
            EventType.USER_MESSAGE, EventType.MODEL_MESSAGE, EventType.TOOL_RESULT,
        )]

        # 截断路径选择
        if token_budget > 0:
            # —— Token 感知截断 ——
            available = token_budget - generation_headroom
            if available <= 0:
                available = token_budget // 2

            sys_est = estimate_tokens(system_content)
            remaining = available - sys_est

            usable: list[Event] = []
            running_cost = 0
            for event in reversed(filtered_events):
                if event.event_type == EventType.TOOL_RESULT:
                    text = event.content.get("result", "") if isinstance(event.content, dict) else str(event.content)
                elif event.event_type == EventType.USER_MESSAGE:
                    text = event.content.get("content", "") if isinstance(event.content, dict) else str(event.content)
                elif event.event_type == EventType.MODEL_MESSAGE:
                    text = event.content.get("text", "") if isinstance(event.content, dict) else str(event.content)
                else:
                    text = ""

                cost = estimate_tokens(text) + 4
                if running_cost + cost > remaining and usable:
                    break
                usable.append(event)
                running_cost += cost

            filtered_events = list(reversed(usable))
            logger.debug(
                "Token-aware truncation: budget=%d available=%d sys_est=%d used=%d events=%d",
                token_budget, available, sys_est, running_cost, len(filtered_events),
            )
        elif len(filtered_events) > max_turns * 2:
            # —— 旧路径：按轮次截断 ——
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
        *,
        in_session_summary: str = "",
        extra_context: str = "",
        remaining_turns: int = 0,
        max_tool_turns: int = 0,
        group_context_text: str = "",
    ) -> str:
        """按渠道 + 固定顺序拼接最终的系统提示词。

        顺序: 渠道身份（含风格） → 跨渠道检测 → 工具纪律 → 环境感知
             → 记忆注入 → 会话内摘要 → 跨会话上下文 → 轮次限制
             → 群聊近期对话（末尾，紧邻下方 user 消息，方便模型关联）
        """
        parts: List[str] = []

        channel = self._detect_channel(events, channel_type)
        identity = self._pick_identity(channel)
        parts.append(identity)

        cross_channel = self._build_cross_channel_hint(events)
        if cross_channel:
            parts.append(cross_channel)

        if self._enable_tool_guidance and self._prompts:
            # 优先加载渠道特定的工具约束（如 tool_enforcement_napcat），
            # 找不到则回退到通用 tool_enforcement
            guidance = ""
            if channel:
                ch_key = self._prompts.identity_map.get(channel.value, channel.value)
                guidance = self._prompts.get_guidance(f"tool_enforcement_{ch_key}")
            if not guidance:
                guidance = self._prompts.get_guidance("tool_enforcement")
            if guidance:
                parts.append(guidance)

        env_hints = self._build_environment_hints()
        if env_hints:
            parts.append(env_hints)

        if recalled_memories:
            parts.append(recalled_memories)

        # P1-1: 会话内摘要（在记忆之后）
        if in_session_summary:
            parts.append(
                "## 历史摘要\n\n"
                "以下为早轮对话的摘要（而非完整记录），仅作背景参考：\n"
                + in_session_summary
            )

        # P2-2: 跨会话上下文 / 其他额外注入
        if extra_context:
            parts.append(extra_context)

        # 剩余轮次感知：提醒模型规划工具调用节奏
        if remaining_turns > 0 and max_tool_turns > 0:
            parts.append(
                f"## 轮次限制\n\n"
                f"当前对话最多允许 {max_tool_turns} 轮工具调用，剩余可用轮次：{remaining_turns}。\n"
                f"请在剩余轮次内完成当前任务。如果剩余轮次较少，优先给出结论而不是继续探索。"
            )

        # 群聊近期对话放在 system prompt 末尾，紧邻后续的 user 消息
        # 这样模型能更自然地将群聊内容与当前提问关联
        if group_context_text:
            parts.append(
                "## 群聊近期对话\n\n"
                "以下是本群最近的消息记录，用于理解当前对话背景：\n"
                + group_context_text
            )

        return "\n\n".join(parts)

    def _pick_identity(self, channel: Optional[ChannelType]) -> str:
        """根据渠道选择身份声明 prompt（含风格引导，已合并至 identities.yaml）。

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
        """返回运行环境提示，由 YAML 配置提供内容。"""
        return self._prompts.get_environment("docker") if self._prompts else ""

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

        注：截断由 HarnessRunner._apply_truncation 统一处理并保存完整内容，
        这里仅做安全网截断（50000 字符），防止异常大结果撑爆上下文。
        """
        content = event.content
        if isinstance(content, dict):
            result = content.get("result", "")
            error = content.get("error")
            if error:
                return f"工具执行失败：{error}"
            text = str(result)
            if len(text) > 50000:
                return text[:50000] + (
                    f"\n...[工具输出过长已截断：完整内容 {len(text)} 字符]"
                )
            return text
        return str(content)
