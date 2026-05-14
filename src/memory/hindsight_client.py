"""
HindsightClient —— Hindsight 记忆服务 REST API 封装。

3 个核心 API:
  1. retain(events, user_id, session_id)  → 提取并存储记忆
  2. recall(query, user_id, session_id)    → 多路语义检索
  3. reflect(user_id)                      → 深度记忆推理

降级策略: 所有方法在 Hindsight 不可用时返回安全默认值（空结果/0）。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("HpAgent.Memory")


@dataclass
class MemoryItem:
    """召回的单条记忆。"""

    content: str
    relevance: float = 1.0
    memory_type: str = ""          # preference / fact / decision / experience
    source_session_id: str = ""
    created_at: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryItem":
        return cls(
            content=data.get("content", ""),
            relevance=data.get("relevance", 1.0),
            memory_type=data.get("memory_type", data.get("type", "")),
            source_session_id=data.get("source_session_id", data.get("session_id", "")),
            created_at=data.get("created_at", ""),
        )


class HindsightClient:
    """Hindsight 记忆服务客户端。

    用法::

        client = HindsightClient(base_url="http://hindsight:8000")
        memories = await client.recall("用户偏好", user_id="u1", session_id="s1")
        count = await client.retain(events, user_id="u1", session_id="s1")
        insights = await client.reflect(user_id="u1")

    Args:
        base_url:  Hindsight 服务地址。
        api_key:   API 密钥（可选）。
        timeout:   请求超时秒数。
        enabled:   是否启用；False 时所有方法返回默认值。
    """

    def __init__(
        self,
        base_url: str = "http://hindsight:8000",
        api_key: str = "",
        timeout: float = 30.0,
        enabled: bool = True,
        prompt_loader=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.enabled = enabled
        self._prompts = prompt_loader

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def _post(self, path: str, body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """发送 POST 请求，失败返回 None。"""
        if not self.enabled:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}{path}",
                    json=body,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.TimeoutException:
            logger.warning("Hindsight API timeout: %s", path)
            return None
        except httpx.HTTPStatusError as e:
            logger.warning("Hindsight API error %s: %s", path, e.response.status_code)
            return None
        except asyncio.CancelledError:
            logger.info("Hindsight API cancelled (activity timeout): %s", path)
            raise
        except Exception as e:
            logger.warning("Hindsight API unreachable %s: %s", path, e)
            return None

    # ═══════════════════════════════════════════════════════════════════════════
    # API 1: retain —— 从对话事件中提取并存储记忆
    # ═══════════════════════════════════════════════════════════════════════════

    async def retain(
        self,
        events: List[Dict[str, Any]],
        user_id: str,
        session_id: str,
    ) -> int:
        """从对话事件中提取可记忆信息并持久化。

        Hindsight 服务端使用 LLM 提取:
          - 用户偏好 (preference):   "我喜欢简洁的回答"
          - 事实信息 (fact):         "我在开发一个 Go 后端项目"
          - 决策记录 (decision):     "决定使用 Redis 而非 Memcached"
          - 关系信息 (relationship): "经常与 alice 协作"

        Args:
            events:    对话事件列表 [{"role": "user", "content": "..."}, ...]。
            user_id:   用户 ID。
            session_id: 会话 ID。

        Returns:
            已存储的记忆数量，失败返回 0。
        """
        result = await self._post("/api/v1/retain", {
            "events": events,
            "user_id": user_id,
            "session_id": session_id,
        })
        if result is None:
            return 0
        return result.get("stored", 0)

    # ═══════════════════════════════════════════════════════════════════════════
    # API 2: recall —— 多路语义检索相关记忆
    # ═══════════════════════════════════════════════════════════════════════════

    async def recall(
        self,
        query: str,
        user_id: str,
        session_id: str = "",
        top_n: int = 5,
    ) -> List[MemoryItem]:
        """检索与当前上下文最相关的历史记忆。

        多路检索融合:
          - 语义向量检索 (pgvector cosine)
          - 关键词 BM25 检索
          - 知识图谱关联
          - 时序衰减
        结果经 Reranker 精排后返回。

        Args:
            query:      检索查询（通常为当前用户消息 + 对话摘要）。
            user_id:    用户 ID。
            session_id: 会话 ID（用于时序相关性加权）。
            top_n:      返回的最大记忆数。

        Returns:
            MemoryItem 列表，失败或无关时返回空列表。
        """
        result = await self._post("/api/v1/recall", {
            "query": query,
            "user_id": user_id,
            "session_id": session_id,
            "top_n": top_n,
        })
        if result is None:
            return []
        memories = result.get("memories", [])
        if not memories:
            return []
        items = [MemoryItem.from_dict(m) for m in memories]
        # 按相关性降序，截断到 top_n
        items.sort(key=lambda m: m.relevance, reverse=True)
        return items[:top_n]

    # ═══════════════════════════════════════════════════════════════════════════
    # API 3: reflect —— 深度记忆推理
    # ═══════════════════════════════════════════════════════════════════════════

    async def reflect(self, user_id: str) -> int:
        """触发深度记忆推理与知识抽象。

        Hindsight 服务端执行:
          - 记忆关联: 发现分散记忆之间的隐含联系
          - 矛盾检测: 识别冲突的记忆并标记
          - 知识抽象: 从碎片记忆提炼高层知识
          - 经验总结: 从多次交互中归纳模式
          - 清理过期: 删除失效/低价值记忆

        Args:
            user_id: 用户 ID。

        Returns:
            产生的洞察数量，失败返回 0。
        """
        result = await self._post("/api/v1/reflect", {
            "user_id": user_id,
        })
        if result is None:
            return 0
        return result.get("insights", 0)

    # ═══════════════════════════════════════════════════════════════════════════
    # 便捷方法
    # ═══════════════════════════════════════════════════════════════════════════

    async def recall_formatted(
        self,
        query: str,
        user_id: str,
        session_id: str = "",
        top_n: int = 5,
    ) -> str:
        """召回记忆并格式化为 prompt 段落。

        Args:
            query:      检索查询。
            user_id:    用户 ID。
            session_id: 会话 ID。
            top_n:      最大记忆数。

        Returns:
            可直接注入 system prompt 的格式化文本，无记忆时返回空字符串。
        """
        items = await self.recall(query, user_id, session_id, top_n)
        if not items:
            return ""
        lines = ["# 相关记忆", ""]
        for item in items:
            type_tag = f"[{item.memory_type}] " if item.memory_type else ""
            lines.append(f"- {type_tag}{item.content}")
        lines.append("")
        if self._prompts:
            instruction = self._prompts.get_system("recall_instruction")
            if instruction:
                lines.append(instruction)
                return "\n".join(lines)
        lines.append("请参考以上记忆来个性化你的回复，但不要显式提及「你之前说过」等语句。")
        return "\n".join(lines)
