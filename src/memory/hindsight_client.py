"""
HindsightClient —— Hindsight 记忆服务 REST API 封装（v0.6.1）。

核心操作:
  1. retain(events, user_id, session_id)  → 从对话提取记忆存入 bank
  2. recall(query, user_id, session_id)    → 语义检索相关记忆
  3. reflect(user_id)                      → 深度记忆推理

隔离策略: 每个用户（account_id）独立 bank，bank 级物理隔离。
bank_id = hpagent-u-{user_id}，首次使用自动创建。

降级策略: 所有方法在 Hindsight 不可用时返回安全默认值（空结果/0）。
"""
from __future__ import annotations

import asyncio
import logging
import time as time_mod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("HpAgent.Memory")

# Hindsight v0.6.1 API 基础路径
_API_PREFIX = "/v1/default/banks"


@dataclass
class HindsightMetrics:
    """Hindsight 客户端可观测性指标。

    采集维度:
      - retain: 成功率 + 延迟（P50/P99 需外部聚合）
      - recall:  命中率 + 延迟
      - reflect: 成功率
      - degraded: 降级次数
    """

    retain_success: int = 0
    retain_failure: int = 0
    retain_latency_ms: List[float] = field(default_factory=list)
    recall_success: int = 0
    recall_failure: int = 0
    recall_latency_ms: List[float] = field(default_factory=list)
    reflect_success: int = 0
    reflect_failure: int = 0
    degraded: int = 0

    def snapshot(self) -> Dict[str, Any]:
        """返回当前指标快照（用于日志/监控导出）。"""
        def _avg(vals: List[float]) -> float:
            return sum(vals) / len(vals) if vals else 0.0

        return {
            "retain": {
                "success": self.retain_success,
                "failure": self.retain_failure,
                "avg_latency_ms": round(_avg(self.retain_latency_ms), 1),
                "p99_latency_ms": round(sorted(self.retain_latency_ms)[int(len(self.retain_latency_ms) * 0.99)] if len(self.retain_latency_ms) >= 100 else _avg(self.retain_latency_ms), 1),
            },
            "recall": {
                "success": self.recall_success,
                "failure": self.recall_failure,
                "avg_latency_ms": round(_avg(self.recall_latency_ms), 1),
                "p99_latency_ms": round(sorted(self.recall_latency_ms)[int(len(self.recall_latency_ms) * 0.99)] if len(self.recall_latency_ms) >= 100 else _avg(self.recall_latency_ms), 1),
            },
            "reflect": {
                "success": self.reflect_success,
                "failure": self.reflect_failure,
            },
            "degraded": self.degraded,
        }

    def reset(self) -> None:
        """重置所有计数器（保留 latency 列表用于滚动窗口）。"""
        self.retain_success = 0
        self.retain_failure = 0
        self.recall_success = 0
        self.recall_failure = 0
        self.reflect_success = 0
        self.reflect_failure = 0
        self.degraded = 0
        # 保留最近 1000 个延迟样本
        if len(self.retain_latency_ms) > 1000:
            self.retain_latency_ms = self.retain_latency_ms[-1000:]
        if len(self.recall_latency_ms) > 1000:
            self.recall_latency_ms = self.recall_latency_ms[-1000:]


@dataclass
class MemoryItem:
    """召回的单条记忆。"""

    content: str
    relevance: float = 1.0
    memory_type: str = ""          # world / experience / observation
    source_session_id: str = ""
    created_at: str = ""

    @classmethod
    def from_recall_result(cls, data: Dict[str, Any]) -> "MemoryItem":
        # Hindsight v0.6.1 RecallResult 不含任何评分字段（score/similarity/
        # relevance/distance 均不存在），服务端按相关度降序返回。
        # relevance 初始设为 0，由调用方根据列表序位计算合成分数。
        return cls(
            content=data.get("text", ""),
            relevance=0.0,
            memory_type=data.get("type", ""),
            source_session_id=data.get("document_id", ""),
            created_at=data.get("mentioned_at", data.get("occurred_start", "")),
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryItem":
        return cls(
            content=data.get("content", ""),
            relevance=data.get("relevance", 1.0),
            memory_type=data.get("memory_type", data.get("type", "")),
            source_session_id=data.get("source_session_id", data.get("session_id", data.get("document_id", ""))),
            created_at=data.get("created_at", ""),
        )


class HindsightClient:
    """Hindsight 记忆服务客户端（v0.6.1），per-user bank 隔离。

    每个 user_id（即 account_id）对应一个独立 bank:
      bank_id = "hpagent-u-{user_id}"

    用法::

        client = HindsightClient(base_url="http://localhost:8001")
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
        retain_mission: str = "",
        reflect_mission: str = "",
        retain_timeout: float = 30.0,
        recall_timeout: float = 10.0,
        reflect_timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.enabled = enabled
        self._prompts = prompt_loader
        self._ready_banks: set[str] = set()
        self.metrics = HindsightMetrics()
        self._retain_mission = retain_mission
        self._reflect_mission = reflect_mission
        self.retain_timeout = retain_timeout
        self.recall_timeout = recall_timeout
        self.reflect_timeout = reflect_timeout

    # ── HTTP helpers ──────────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _bank_id_for(self, user_id: str) -> str:
        return f"hpagent-u-{user_id}"

    async def _request(
        self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
        timeout: float | None = None,
        retry_on_5xx: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """发送 HTTP 请求，失败返回 None。

        错误分类:
          - 超时: 不重试（已占用时间），记录指标
          - 429 限流: 指数退避重试（最多 2 次）
          - 5xx 服务端错误: retry_on_5xx=True 时退避重试，False 时立即降级
          - 4xx 客户端错误: 不重试（参数问题）
          - 连接错误: 标记降级，返回 None
        """
        if not self.enabled:
            return None
        _timeout = timeout if timeout is not None else self.timeout
        url = f"{self.base_url}{path}"
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=_timeout) as client:
                    resp = await client.request(
                        method, url, json=body, headers=self._headers()
                    )
                    resp.raise_for_status()
                    return resp.json()
            except httpx.TimeoutException:
                logger.warning("DEGRADATION: Hindsight timeout %s %s (attempt %d/3)", method, path, attempt + 1)
                self.metrics.degraded += 1
                return None  # 超时不重试，已占用时间
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status == 429:
                    if attempt < 2:
                        wait = 2 ** attempt
                        logger.warning("Hindsight rate-limited (429), retrying in %ds (attempt %d/3)", wait, attempt + 1)
                        await asyncio.sleep(wait)
                        continue
                    logger.warning("DEGRADATION: Hindsight rate-limited (429), exhausted retries")
                    self.metrics.degraded += 1
                    return None
                elif status >= 500:
                    if retry_on_5xx and attempt < 2:
                        wait = 2 ** attempt
                        logger.warning("Hindsight server error %s, retrying in %ds (attempt %d/3)", status, wait, attempt + 1)
                        await asyncio.sleep(wait)
                        continue
                    logger.warning("DEGRADATION: Hindsight server error %s, %s", status,
                                   "exhausted retries" if retry_on_5xx else "no retry (fast-degrade)")
                    self.metrics.degraded += 1
                    return None
                else:
                    logger.warning("Hindsight client error %s %s: %s", method, path, status)
                    return None  # 4xx 不重试
            except asyncio.CancelledError:
                logger.info("Hindsight API cancelled (activity timeout): %s %s", method, path)
                raise
            except Exception as e:
                logger.warning("DEGRADATION: Hindsight unreachable %s %s: %s", method, path, e)
                self.metrics.degraded += 1
                return None
        return None

    async def _post(self, path: str, body: Dict[str, Any], timeout: float | None = None,
                    retry_on_5xx: bool = True) -> Optional[Dict[str, Any]]:
        return await self._request("POST", path, body, timeout=timeout, retry_on_5xx=retry_on_5xx)

    async def _put(self, path: str, body: Dict[str, Any], timeout: float | None = None) -> Optional[Dict[str, Any]]:
        return await self._request("PUT", path, body, timeout=timeout)

    # ── Bank 管理 ─────────────────────────────────────────────────────────

    async def _ensure_bank(self, bank_id: str) -> bool:
        """确保 bank 存在（幂等 PUT），成功返回 True。

        已就绪的 bank 记录在 _ready_banks 中，避免重复 HTTP 调用。
        """
        if bank_id in self._ready_banks:
            return True
        if not self.enabled:
            return False
        bank_body: Dict[str, str] = {}
        if self._retain_mission:
            bank_body["retain_mission"] = self._retain_mission
        if self._reflect_mission:
            bank_body["reflect_mission"] = self._reflect_mission
        result = await self._put(
            f"{_API_PREFIX}/{bank_id}",
            bank_body,
        )
        if result is not None:
            self._ready_banks.add(bank_id)
            logger.debug("Hindsight bank ensured: %s", bank_id)
        else:
            logger.warning("Hindsight bank creation failed: %s", bank_id)
        return bank_id in self._ready_banks

    # ═══════════════════════════════════════════════════════════════════════════
    # API 1: retain —— 从对话事件中提取并存储记忆
    # ═══════════════════════════════════════════════════════════════════════════

    async def retain(
        self,
        events: List[Dict[str, Any]],
        user_id: str,
        session_id: str,
        async_retain: bool = True,
        channel_type: str = "",
        group_id: str = "",
        sender_name: str = "",
        iso_timestamp: str = "",
        scope: str = "",
    ) -> int:
        """从对话事件中提取可记忆信息并持久化。

        Hindsight 服务端在 retain 时自动执行:
          - 事实提取 (fact extraction)
          - 实体识别 (entity extraction)
          - 观察整合 (observation consolidation，如 enable_observations 开启)

        Args:
            events:        对话事件列表 [{"role": "user", "content": "..."}, ...]。
            user_id:       用户 ID。
            session_id:    会话 ID。
            async_retain:  是否异步提交（默认 True，不阻塞调用方）。
            channel_type:  渠道类型（napcat/console/web），用于 context 和 tags。
            group_id:      群 ID（群聊时）。
            sender_name:   发送者名称，用于 context 描述。
            iso_timestamp: ISO 8601 格式时间戳，保留原始时序。
            scope:         对话范围（"private" / "group"）。

        Returns:
            已提交的 memory item 数量，失败返回 0。
        """
        bank_id = self._bank_id_for(user_id)
        if not await self._ensure_bank(bank_id):
            self.metrics.retain_failure += 1
            return 0

        # 构建 context 描述（渠道感知 + 群/私聊上下文）
        context_str = self._build_context(channel_type, group_id, sender_name, scope)

        # 构建标签体系（不含 user:{id}——bank 已提供用户级隔离）
        item_tags_base = self._build_tags(session_id, channel_type, group_id, scope)

        # 合并所有事件为单条 item，使用会话级 document_id。
        # Hindsight 要求 batch 内 document_id 唯一，且单条全文提交能
        # 让 LLM 利用完整上下文做更准确的事实提取。
        merged_content = "\n\n".join(
            f"[{e.get('role', 'user')}]: {e['content']}"
            for e in events
            if e.get("content")
        )
        if not merged_content:
            return 0

        items = [{
            "content": merged_content,
            "context": context_str,
            "document_id": f"session:{session_id}",
            "timestamp": iso_timestamp,
            "tags": list(item_tags_base),
            "metadata": {
                "session_id": session_id,
                "sender_name": sender_name,
            },
        }]

        t0 = time_mod.monotonic()
        result = await self._post(
            f"{_API_PREFIX}/{bank_id}/memories",
            {"items": items, "async": async_retain},
            timeout=self.retain_timeout,
        )
        elapsed_ms = (time_mod.monotonic() - t0) * 1000
        self.metrics.retain_latency_ms.append(elapsed_ms)
        if result is None:
            self.metrics.retain_failure += 1
            return 0
        self.metrics.retain_success += 1
        return result.get("items_count", 0)

    # ── retain helpers ────────────────────────────────────────────────────

    @staticmethod
    def _build_context(
        channel_type: str,
        group_id: str,
        sender_name: str,
        scope: str,
    ) -> str:
        """构建渠道感知的 context 描述，注入 LLM 提取 prompt 中。"""
        if channel_type == "napcat":
            if scope == "group" and group_id:
                return f'QQ group chat in "{sender_name}" ({group_id})' if sender_name else f"QQ group chat ({group_id})"
            if scope == "private":
                return f"QQ private chat with {sender_name} ({sender_name})" if sender_name else "QQ private chat"
            return "QQ napcat chat"
        if channel_type == "console":
            return "Console CLI chat"
        if channel_type == "web":
            return "Web chat"
        return "chat"

    @staticmethod
    def _build_tags(
        session_id: str,
        channel_type: str,
        group_id: str,
        scope: str,
    ) -> List[str]:
        """构建标签列表。不含 user:{id}——bank 级隔离已保证用户间互斥。"""
        tags: List[str] = []
        if session_id:
            tags.append(f"session:{session_id}")
        if channel_type:
            tags.append(f"channel:{channel_type}")
        if scope:
            tags.append(f"scope:{scope}")
        if group_id:
            tags.append(f"group:{group_id}")
        return tags

    # ═══════════════════════════════════════════════════════════════════════════
    # API 2: recall —— 语义检索相关记忆
    # ═══════════════════════════════════════════════════════════════════════════

    async def recall(
        self,
        query: str,
        user_id: str,
        session_id: str = "",
        top_n: int = 5,
        tags_match: str = "any_strict",
        query_timestamp: str = "",
        budget: str = "mid",
        group_id: str = "",
        scope: str = "",
        channel_type: str = "",
    ) -> List[MemoryItem]:
        """检索与当前上下文最相关的历史记忆。

        检索流程:
          - 语义向量检索 (pgvector cosine)
          - 关键词 BM25 检索
          - 知识图谱关联
          - 时序衰减
        结果经 Reranker 精排后返回。

        Args:
            query:           检索查询（通常为当前用户消息）。
            user_id:         用户 ID。
            session_id:      会话 ID（用于时序相关性加权）。
            top_n:           返回的最大记忆数。
            tags_match:      标签匹配策略（"any_strict" / "any" / "all" / tag_groups）。
            query_timestamp: ISO 8601 时间锚点，用于时序衰减。
            budget:          检索预算（"low" / "mid" / "high"）。
            group_id:        群 ID（群聊时传入，扩展召回范围）。
            scope:           对话范围（"private" / "group"）。
            channel_type:    渠道类型。

        Returns:
            MemoryItem 列表，失败或无关时返回空列表。
        """
        bank_id = self._bank_id_for(user_id)
        if not await self._ensure_bank(bank_id):
            self.metrics.recall_failure += 1
            return []

        recall_tags = self._build_recall_tags(group_id)

        body: Dict[str, Any] = {
            "query": query,
            "tags": recall_tags,
            "tags_match": tags_match,
            "max_tokens": 4096,
            "budget": budget,
        }
        if query_timestamp:
            body["query_timestamp"] = query_timestamp

        t0 = time_mod.monotonic()
        result = await self._post(
            f"{_API_PREFIX}/{bank_id}/memories/recall",
            body,
            timeout=self.recall_timeout,
            retry_on_5xx=False,
        )
        elapsed_ms = (time_mod.monotonic() - t0) * 1000
        self.metrics.recall_latency_ms.append(elapsed_ms)
        if result is None:
            self.metrics.recall_failure += 1
            return []
        raw_items = result.get("results", [])
        if not raw_items:
            return []
        self.metrics.recall_success += 1
        items = [MemoryItem.from_recall_result(m) for m in raw_items]
        # Hindsight v0.6.1 不返回 per-item 评分，按序位计算合成相关度：
        # 列表按相关度降序排列，第 1 条最相关 → relevance 趋近 1.0
        total = len(items)
        for i, item in enumerate(items):
            item.relevance = round((total - i) / total, 4) if total > 1 else 1.0
        return items[:top_n]

    @staticmethod
    def _build_recall_tags(group_id: str) -> List[str]:
        """构建 recall 时的标签过滤列表。不含 user:{id}——bank 级隔离已保证。

        私聊: 空列表（bank 内所有记忆都属于该用户）
        群聊: group:{id}（过滤该群的共享记忆）
        """
        return [f"group:{group_id}"] if group_id else []

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

        Args:
            user_id: 用户 ID。

        Returns:
            产生的洞察数量（当前以响应文本非空计为 1），失败返回 0。
        """
        bank_id = self._bank_id_for(user_id)
        if not await self._ensure_bank(bank_id):
            self.metrics.reflect_failure += 1
            return 0

        result = await self._post(
            f"{_API_PREFIX}/{bank_id}/reflect",
            {
                "query": (
                    f"Analyze all recent interactions for user {user_id}. "
                    "Identify patterns, preferences, and generate insights."
                ),
                "tags": [],
                "budget": "low",
            },
            timeout=self.reflect_timeout,
        )
        if result is None:
            self.metrics.reflect_failure += 1
            return 0
        text = result.get("text", "")
        if text:
            self.metrics.reflect_success += 1
            return 1
        self.metrics.reflect_failure += 1
        return 0

    # ═══════════════════════════════════════════════════════════════════════════
    # 便捷方法
    # ═══════════════════════════════════════════════════════════════════════════

    def get_metrics(self) -> Dict[str, Any]:
        """返回当前指标快照。"""
        return self.metrics.snapshot()

    def log_metrics(self) -> None:
        """以结构化日志输出当前指标。"""
        snap = self.metrics.snapshot()
        logger.info(
            "Hindsight metrics | retain: %s/%s (avg %sms) | recall: %s/%s (avg %sms) | reflect: %s/%s | degraded: %s",
            snap["retain"]["success"], snap["retain"]["success"] + snap["retain"]["failure"],
            snap["retain"]["avg_latency_ms"],
            snap["recall"]["success"], snap["recall"]["success"] + snap["recall"]["failure"],
            snap["recall"]["avg_latency_ms"],
            snap["reflect"]["success"], snap["reflect"]["success"] + snap["reflect"]["failure"],
            snap["degraded"],
        )

    async def recall_formatted(
        self,
        query: str = "",
        user_id: str = "",
        session_id: str = "",
        top_n: int = 5,
        tags_match: str = "any_strict",
        query_timestamp: str = "",
        budget: str = "mid",
        group_id: str = "",
        scope: str = "",
        channel_type: str = "",
        *,
        items: List[MemoryItem] | None = None,
    ) -> str:
        """召回记忆并格式化为 prompt 段落。

        可通过 items= 传入已召回的记忆列表（避免重复 HTTP 调用）。
        未传入 items 时自动调用 recall()。

        Returns:
            可直接注入 system prompt 的格式化文本，无记忆时返回空字符串。
        """
        if items is None:
            items = await self.recall(
                query, user_id, session_id, top_n,
                tags_match=tags_match,
                query_timestamp=query_timestamp,
                budget=budget,
                group_id=group_id,
                scope=scope,
                channel_type=channel_type,
            )
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
