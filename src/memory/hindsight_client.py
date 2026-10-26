"""Hindsight 1.1.0 adapter. Official SDK owns transport and wire models.

No automatic write retries: operation identity/reconciliation belongs to the caller.
"""

from __future__ import annotations

import asyncio
import math
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List

import aiohttp
from hindsight_client import Hindsight
from hindsight_client_api.exceptions import ApiException
from hindsight_client_api.models.bank_config_update import BankConfigUpdate
from hindsight_client_api.models.create_bank_request import CreateBankRequest
from hindsight_client_api.models.recall_request import RecallRequest
from hindsight_client_api.models.update_document_observation_scopes import (
    UpdateDocumentObservationScopes,
)
from hindsight_client_api.models.update_document_request import UpdateDocumentRequest
from hindsight_client_api.models.update_memory_request import UpdateMemoryRequest
from pydantic import ValidationError

from memory.quality_config import MemoryQualityConfig, fingerprint
from memory.scope_policy import MemoryScopeError, memory_scope


class HindsightError(RuntimeError):
    """Sanitized failure; never includes provider body or memory content."""

    def __init__(self, code: str, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status


class HindsightUnavailable(HindsightError):
    """Retryable read/service failure; writes require reconciliation."""


class HindsightUncertain(HindsightError):
    """Transport ended without proving the outcome of a write."""


class HindsightRejected(HindsightError):
    """Permanent request/auth/not-found/conflict rejection."""


class HindsightContractError(HindsightError):
    """Response cannot safely be used under the requested contract."""


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
                "p99_latency_ms": round(
                    sorted(self.retain_latency_ms)[int(len(self.retain_latency_ms) * 0.99)]
                    if len(self.retain_latency_ms) >= 100
                    else _avg(self.retain_latency_ms),
                    1,
                ),
            },
            "recall": {
                "success": self.recall_success,
                "failure": self.recall_failure,
                "avg_latency_ms": round(_avg(self.recall_latency_ms), 1),
                "p99_latency_ms": round(
                    sorted(self.recall_latency_ms)[int(len(self.recall_latency_ms) * 0.99)]
                    if len(self.recall_latency_ms) >= 100
                    else _avg(self.recall_latency_ms),
                    1,
                ),
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


@dataclass(frozen=True)
class MemoryScores:
    semantic: float | None = None
    keyword: float | None = None
    reranker: float | None = None
    final: float | None = None


@dataclass
class MemoryItem:
    content: str
    relevance: float = 1.0  # ordering only, not confidence
    memory_type: str = ""
    source_session_id: str = ""  # historical name: document ID
    created_at: str = ""
    memory_id: str = ""
    tags: tuple[str, ...] = ()
    source_fact_ids: tuple[str, ...] = ()
    source_facts: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)
    bank_id: str | None = None
    scores: MemoryScores | None = None

    @classmethod
    def from_recall_result(cls, data: dict[str, Any]) -> MemoryItem:
        return cls(
            content=data.get("text", ""),
            memory_type=data.get("type", ""),
            source_session_id=data.get("document_id") or "",
            created_at=data.get("mentioned_at") or data.get("occurred_start") or "",
            memory_id=data.get("id", ""),
            tags=tuple(data.get("tags") or ()),
            source_fact_ids=tuple(data.get("source_fact_ids") or ()),
            metadata=data.get("metadata") or {},
            scores=MemoryScores(**data["scores"]) if data.get("scores") else None,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryItem:
        item = cls.from_recall_result(data)
        item.content = data.get("content", item.content)
        item.memory_type = data.get("memory_type", item.memory_type)
        item.source_session_id = data.get(
            "source_session_id", data.get("session_id", item.source_session_id)
        )
        item.relevance = data.get("relevance", 1.0)
        item.created_at = data.get("created_at", item.created_at)
        return item


class RecallCandidates(list[MemoryItem]):
    """Per-response counters; no shared last-call state across concurrent Chats."""
    def __init__(self, items, *, remote_count, incomplete_sources, trace_summary=None):
        super().__init__(items)
        self.remote_count = remote_count
        self.incomplete_sources = incomplete_sources
        self.trace_summary = trace_summary or {}


@dataclass(frozen=True)
class RetainReceipt:
    accepted: bool
    items_count: int = 0
    operation_id: str | None = None
    async_processing: bool = False


class HindsightClient:
    def __init__(
        self,
        base_url="http://hindsight:8888",
        api_key="",
        timeout=30.0,
        enabled=True,
        prompt_loader=None,
        retain_mission="",
        reflect_mission="",
        retain_timeout=30.0,
        recall_timeout=10.0,
        reflect_timeout=120.0,
        recall_enabled=True,
        quality_config: MemoryQualityConfig | None = None,
        memory_prompts: dict[str, str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key, self.timeout, self.enabled = api_key, timeout, enabled
        self.recall_enabled = recall_enabled
        self._prompts = prompt_loader
        self._retain_mission, self._reflect_mission = retain_mission, reflect_mission
        self.retain_timeout, self.recall_timeout = retain_timeout, recall_timeout
        self.reflect_timeout = reflect_timeout
        self.metrics = HindsightMetrics()
        self._ready_banks: set[str] = set()
        self.quality_config = quality_config
        self._memory_prompts = dict(memory_prompts or {})
        self._ready_configs: set[tuple[str, str]] = set()
        self._version_checked = False
        if quality_config is not None:
            quality_config.bank_updates(self._memory_prompts)

    @staticmethod
    def _bank_id_for(user_id: str) -> str:
        if not user_id or user_id != user_id.strip():
            raise MemoryScopeError("memory_account_required")
        return f"hpagent-u-{user_id}"

    @asynccontextmanager
    async def _sdk(self, *, write=False, timeout=None, metric=None):
        if not self.enabled:
            raise HindsightRejected("memory_disabled")
        sdk = Hindsight(
            self.base_url,
            api_key=self.api_key or None,
            timeout=timeout or self.timeout,
            max_attempts=1,
        )
        started, failed = time.monotonic(), True
        try:
            yield sdk
            failed = False
        except ApiException as exc:
            status = exc.status
            error = (
                (HindsightUncertain if write else HindsightUnavailable)
                if status and status >= 500
                else (HindsightUnavailable if status == 429 else HindsightRejected)
            )
            raise error("hindsight_http_error", status) from None
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
            error = HindsightUncertain if write else HindsightUnavailable
            raise error("hindsight_transport_error") from None
        except (ValidationError, ValueError, TypeError) as exc:
            if isinstance(exc, MemoryScopeError):
                raise
            raise HindsightContractError("hindsight_schema_error") from None
        finally:
            if metric:
                samples = getattr(self.metrics, f"{metric}_latency_ms")
                samples.append((time.monotonic() - started) * 1000)
                del samples[:-1000]
                if failed:
                    name = f"{metric}_failure"
                    setattr(self.metrics, name, getattr(self.metrics, name) + 1)
                    self.metrics.degraded += 1
            await sdk.aclose()

    async def retain_document(
        self,
        events,
        user_id,
        document_id,
        *,
        async_retain=False,
        operation_id=None,
        channel_type="",
        group_id="",
        sender_name="",
        iso_timestamp="",
        scope="",
        session_id="",
        metadata=None,
        source_context="",
        quality_snapshot=None,
    ):
        policy = memory_scope(user_id, scope, group_id)
        self.check_quality_snapshot(quality_snapshot)
        try:
            stamp = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError
        except ValueError:
            raise MemoryScopeError("memory_source_timestamp_required") from None
        if not document_id or not events:
            raise MemoryScopeError("memory_source_required")
        if async_retain:
            from uuid import UUID

            try:
                UUID(operation_id or "")
            except ValueError:
                raise MemoryScopeError("memory_operation_uuid_required") from None
        content = "\n\n".join(
            f"[{e.get('role', 'user')}]: {e['content']}" for e in events if e.get("content")
        )
        if not content:
            raise MemoryScopeError("memory_content_required")
        item = dict(
            content=content,
            document_id=document_id,
            timestamp=stamp.isoformat(),
            context=source_context or channel_type or "chat",
            tags=list(policy.tags),
            observation_scopes=policy.observation_scopes,
            metadata={str(k): str(v) for k, v in (metadata or {}).items() if v is not None},
        )
        await self.prepare_retain(user_id, quality_snapshot)
        async with self._sdk(write=True, timeout=self.retain_timeout, metric="retain") as sdk:
            if self.quality_config is None and policy.bank_id not in self._ready_banks and (
                self._retain_mission or self._reflect_mission
            ):
                await sdk.banks.create_or_update_bank(
                    policy.bank_id,
                    CreateBankRequest(
                        retain_mission=self._retain_mission or None,
                        reflect_mission=self._reflect_mission or None,
                    ),
                    _request_timeout=self.retain_timeout,
                )
                self._ready_banks.add(policy.bank_id)
            result = await sdk.aretain_batch(
                policy.bank_id, [item], retain_async=async_retain, operation_id=operation_id
            )
        if result.bank_id != policy.bank_id or (
            async_retain and result.operation_id != operation_id
        ):
            raise HindsightContractError("hindsight_retain_identity_mismatch")
        self.metrics.retain_success += 1
        return RetainReceipt(
            result.success, result.items_count, result.operation_id, result.var_async
        )

    async def recall(self, query, user_id, session_id="", top_n=5,
                     tags_match="all_strict", query_timestamp="", budget=None,
                     group_id="", scope="", channel_type=""):
        """Compatibility helper; Chat uses candidates then local authorization."""
        if type(top_n) is not int or top_n <= 0:
            raise MemoryScopeError("memory_top_n_required")
        items = await self.recall_candidates(
            query, user_id, session_id, tags_match=tags_match,
            query_timestamp=query_timestamp, budget=budget, group_id=group_id,
            scope=scope, channel_type=channel_type)
        return items[:top_n]

    async def recall_candidates(
        self,
        query,
        user_id,
        session_id="",
        tags_match="all_strict",
        query_timestamp="",
        budget=None,
        group_id="",
        scope="",
        channel_type="",
    ):
        policy = memory_scope(user_id, scope, group_id)
        if tags_match != "all_strict":
            raise MemoryScopeError("memory_requires_all_strict")
        if not self.recall_enabled or not self.enabled:
            return []
        body = dict(
            query=query,
            tags=list(policy.tags),
            tags_match="all_strict",
            budget=budget or "mid",
            max_tokens=4096,
            include={"source_facts": {"max_tokens": 4096}},
        )
        if self.quality_config is not None:
            await self.ensure_bank_config(user_id, create=False)
            body.update(self.quality_config.recall.request_options())
            body["trace"] = self.quality_config.diagnostics.trace
            if budget is not None:
                body["budget"] = budget
        if query_timestamp:
            stamp = datetime.fromisoformat(query_timestamp.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise MemoryScopeError("memory_query_timestamp_required")
            body["query_timestamp"] = stamp.isoformat()
        async with self._sdk(timeout=self.recall_timeout, metric="recall") as sdk:
            response = await sdk.memory.recall_memories(
                policy.bank_id, RecallRequest(**body), _request_timeout=self.recall_timeout
            )
        data = response.to_dict()
        sources = data.get("source_facts") or {}
        incomplete_sources = 0
        items = []
        for raw in data["results"]:
            if not policy.contains(raw.get("tags") or ()):
                raise HindsightContractError("hindsight_scope_mismatch")
            item = MemoryItem.from_recall_result(raw)
            item.bank_id = policy.bank_id
            if item.memory_type == "observation":
                if not item.source_fact_ids or any(i not in sources for i in item.source_fact_ids):
                    incomplete_sources += 1
                    continue  # incomplete evidence must never enter model context
                facts = tuple(sources[i] for i in item.source_fact_ids)
                if any(
                    not policy.contains(f.get("tags") or ()) or not f.get("document_id")
                    for f in facts
                ):
                    raise HindsightContractError("hindsight_source_scope_mismatch")
                item.source_facts = facts
            elif not item.source_session_id:
                raise HindsightContractError("hindsight_source_missing")
            items.append(item)
        for i, item in enumerate(items):
            item.relevance = (len(items) - i) / len(items)
        self.metrics.recall_success += 1
        trace = data.get('trace') or {}
        summary = trace.get('summary', {}) if isinstance(trace, dict) else {}
        safe_trace = {key: value for key, value in summary.items()
                      if key in {'total_nodes_visited', 'entry_points_found', 'budget_used',
                                 'budget_remaining', 'total_duration_seconds', 'results_returned'}
                      and type(value) in (int, float) and math.isfinite(value)} if isinstance(summary, dict) else {}
        return RecallCandidates(items, remote_count=len(data["results"]),
                                incomplete_sources=incomplete_sources, trace_summary=safe_trace)

    def quality_snapshot(self):
        return (self.quality_config.snapshot(self._memory_prompts)
                if self.quality_config is not None else None)

    def check_quality_snapshot(self, snapshot):
        if snapshot is not None:
            current = self.quality_snapshot()
            if current is None or snapshot.get('config_hash') != current['config_hash']:
                raise HindsightUnavailable("memory_quality_snapshot_mismatch")

    async def prepare_retain(self, user_id, snapshot=None):
        """Configuration I/O precedes the durable source sending intent."""
        self.check_quality_snapshot(snapshot)
        await self.ensure_bank_config(user_id)

    async def check_version(self):
        if not self._version_checked:
            async with self._sdk() as sdk:
                version = await sdk.aget_version()
            if version.api_version != "1.1.0":
                raise HindsightContractError("memory_target_version_mismatch")
            self._version_checked = True

    async def get_bank_config(self, user_id):
        await self.check_version()
        bank = self._bank_id_for(user_id)
        async with self._sdk() as sdk:
            result = (await sdk.banks.get_bank_config(bank, _request_timeout=self.timeout)).to_dict()
        if result["bank_id"] != bank:
            raise HindsightContractError("memory_bank_config_identity_mismatch")
        return result

    @staticmethod
    def config_matches(state, updates):
        # Null resets only the bank override; the resolved parent need not be null.
        return all((state["overrides"].get(k) is None if v is None
                    else state["config"].get(k) == v) for k, v in updates.items())

    async def update_bank_config(self, user_id, updates):
        await self.check_version()
        self._ready_configs.clear()
        async with self._sdk(write=True) as sdk:
            await sdk.banks.update_bank_config(
                self._bank_id_for(user_id), BankConfigUpdate(updates=updates),
                _request_timeout=self.timeout)
        state = await self.get_bank_config(user_id)
        if not self.config_matches(state, updates):
            raise HindsightContractError("memory_bank_config_unverified")
        return state

    async def ensure_bank_config(self, user_id, *, create=True):
        """Initialize new banks; existing bank changes require explicit operator apply."""
        if self.quality_config is None:
            return
        updates = self.quality_config.bank_updates(self._memory_prompts)
        key = (self._bank_id_for(user_id), fingerprint(updates))
        if key in self._ready_configs:
            return
        try:
            state = await self.get_bank_config(user_id)
        except HindsightRejected as exc:
            if exc.status != 404 or not create:
                raise
            # Provision supported fields, then verify the full managed profile
            # through the config resource before admitting any Retain.
            async with self._sdk(write=True) as sdk:
                await sdk.banks.create_or_update_bank(
                    key[0], CreateBankRequest(**{
                        k: v for k, v in updates.items()
                        if k in CreateBankRequest.model_fields and v is not None}),
                    _request_timeout=self.timeout)
            # Fields not exposed by CreateBankRequest still use the config resource.
            state = await self.update_bank_config(user_id, updates)
        if any(state["config"].get(k) for k in (
            "retain_default_strategy", "retain_strategies", "consolidation_strategies")):
            raise HindsightUnavailable("memory_bank_strategy_conflict")
        if not self.config_matches(state, updates):
            raise HindsightUnavailable("memory_bank_config_not_applied")
        self._ready_configs.add(key)

    async def get_operation(self, user_id, operation_id):
        bank = self._bank_id_for(user_id)
        async with self._sdk() as sdk:
            return (
                await sdk.operations.get_operation_status(
                    bank, operation_id, _request_timeout=self.timeout
                )
            ).to_dict()

    async def retry_operation(self, user_id, operation_id):
        bank = self._bank_id_for(user_id)
        async with self._sdk(write=True) as sdk:
            return await sdk.operations.retry_operation(
                bank, operation_id, _request_timeout=self.timeout
            )

    async def delete_operation(self, user_id, operation_id):
        bank = self._bank_id_for(user_id)
        async with self._sdk(write=True) as sdk:
            return await sdk.operations.delete_operation(
                bank, operation_id, _request_timeout=self.timeout
            )

    async def get_document(self, user_id, document_id):
        bank = self._bank_id_for(user_id)
        async with self._sdk() as sdk:
            return await sdk.documents.get_document(
                bank, document_id, _request_timeout=self.timeout
            )

    async def patch_document_scope(self, user_id, document_id, *, scope, group_id=""):
        policy = memory_scope(user_id, scope, group_id)
        body = UpdateDocumentRequest(
            tags=list(policy.tags),
            observation_scopes=UpdateDocumentObservationScopes(
                actual_instance=policy.observation_scopes
            ),
        )
        async with self._sdk(write=True) as sdk:
            return await sdk.documents.update_document(
                policy.bank_id, document_id, body, _request_timeout=self.timeout
            )

    async def delete_document(self, user_id, document_id):
        bank = self._bank_id_for(user_id)
        async with self._sdk(write=True) as sdk:
            return await sdk.documents.delete_document(
                bank, document_id, _request_timeout=self.timeout
            )

    async def get_fact(self, user_id, memory_id):
        bank = self._bank_id_for(user_id)
        async with self._sdk() as sdk:
            return await sdk.memory.get_memory(bank, memory_id, _request_timeout=self.timeout)

    async def patch_fact(self, user_id, memory_id, *, text=None, state=None, reason=None):
        bank = self._bank_id_for(user_id)
        if state not in {None, "valid", "invalidated"} or (text is None and state is None):
            raise MemoryScopeError("memory_fact_patch_required")
        body = UpdateMemoryRequest(
            **{
                k: v
                for k, v in dict(text=text, state=state, reason=reason).items()
                if v is not None
            }
        )
        async with self._sdk(write=True) as sdk:
            return await sdk.memory.update_memory(
                bank, memory_id, body, _request_timeout=self.timeout
            )

    async def list_facts(self, user_id, *, document_id=None, scope=None, state=None, fact_type=None):
        """Exhaustive public listing for governance verification, never Recall ranking."""
        if state == 'all':
            valid = await self.list_facts(user_id, document_id=document_id, scope=scope, state='valid', fact_type=fact_type)
            archived = await self.list_facts(user_id, document_id=document_id, scope=scope, state='invalidated', fact_type=fact_type)
            return valid + archived
        bank = self._bank_id_for(user_id)
        result, offset = [], 0
        async with self._sdk() as sdk:
            while True:
                page = (await sdk.memory.list_memories(
                    bank, document_id=document_id, tags=scope, tags_match='all_strict',
                    state=state, type=fact_type, limit=100, offset=offset,
                    _request_timeout=self.timeout)).to_dict()
                result.extend(page['items'])
                offset += len(page['items'])
                if offset >= page['total']:
                    return result
                if not page['items']:
                    raise HindsightContractError('hindsight_listing_incomplete')

    async def maintenance_evidence(self, user_id, document_id, scope, memory_id=None):
        facts = await self.list_facts(user_id, document_id=document_id, state='all')
        ids = {memory_id} if memory_id else {f['id'] for f in facts}
        observations = await self.list_facts(user_id, scope=scope, fact_type='observation')
        return {'fact_ids': sorted(ids), 'observation_ids': [o['id'] for o in observations
                if ids.intersection(o.get('source_memory_ids') or [])]}

    async def verify_source_absent(self, user_id, document_id, scope, evidence=None):
        if await self.list_facts(user_id, document_id=document_id, state='all'):
            raise HindsightContractError('memory_source_facts_present')
        ids = set((evidence or {}).get('fact_ids', []))
        observations = await self.list_facts(user_id, scope=scope, fact_type='observation')
        if any(ids.intersection(o.get('source_memory_ids') or []) for o in observations):
            raise HindsightContractError('memory_source_observations_present')
        for memory_id in (evidence or {}).get('fact_ids', []) + (evidence or {}).get('observation_ids', []):
            try:
                await self.get_fact(user_id, memory_id)
            except HindsightRejected as exc:
                if exc.status == 404:
                    continue
                raise
            raise HindsightContractError('memory_source_reference_present')

    async def verify_fact_maintenance(self, user_id, memory_id, scope, kind, evidence=None):
        for observation in (evidence or {}).get('observation_ids', []):
            try:
                await self.get_fact(user_id, observation)
            except HindsightRejected as exc:
                if exc.status == 404:
                    continue
                raise
            raise HindsightContractError('memory_old_observation_present')
        if kind == 'invalidate':
            live = await self.list_facts(user_id, scope=scope, state='valid')
            if any(f['id'] == memory_id or memory_id in (f.get('source_memory_ids') or []) for f in live):
                raise HindsightContractError('memory_invalidated_fact_visible')

    async def reflect(self, user_id):
        # Old scheduled workflows may still be in flight. They perform no I/O.
        return 0

    def get_metrics(self):
        return self.metrics.snapshot()

    async def recall_formatted(
        self,
        query: str = "",
        user_id: str = "",
        session_id: str = "",
        top_n: int = 5,
        tags_match: str = "all_strict",
        query_timestamp: str = "",
        budget: str | None = None,
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
                query,
                user_id,
                session_id,
                top_n,
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
            instruction = self._prompts.get_memory("recall_instruction")
            if instruction:
                lines.append(instruction)
                return "\n".join(lines)
        lines.append("请参考以上记忆来个性化你的回复，但不要显式提及「你之前说过」等语句。")
        return "\n".join(lines)
