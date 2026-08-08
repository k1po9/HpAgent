"""MemoryRetentionService —— Web completed Run → 单个 Hindsight document。

Phase F F-04（doc §34-40）。职责只有一条链：
  run_id → 读 PostgreSQL → 构造 document → 调用 retain_document。

不做：Outbox polling、Temporal、SSE、Redis（那些归 MemoryRetentionWorker /
Dispatcher）。retain 数据只来自已提交的 DB Message，绝不来自
ExecutionResult.memory_observations（doc §40）。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from persistence.uow import UnitOfWork

logger = logging.getLogger("HpAgent.MemoryRetention")


@dataclass(frozen=True)
class RetainOutcome:
    """retain 决策结果。

    skipped=True  → 该 Run 不可 retain（非 completed 终态），Outbox 直接
                    mark_processed（Run / Message 状态永不改变）。
    accepted=True → Hindsight 已接受；accepted=False → Outbox 重试。
    """

    skipped: bool = False
    accepted: bool = False
    document_id: str | None = None


class MemoryRetentionService:
    """从 PostgreSQL 读取 completed Run 并构造用户可见的 Hindsight document。"""

    def __init__(self, database_url: object, hindsight_client: Any):
        self._database_url = database_url
        self._hindsight = hindsight_client

    def load_completed_run(self, run_id: UUID) -> dict[str, Any] | None:
        """读取 Run + trigger user Message + produced assistant Message。

        校验（doc §36）:
          run.status == 'completed'
          assistant.status == 'completed' 且 content 非空
          trigger.role == 'user' 且 trigger.status == 'accepted'
          account_id / conversation_id 由 join 天然一致

        任何一条不满足 → 返回 None（failed/cancelled/running/queued 都拒绝，
        doc §39）。
        """
        with UnitOfWork(self._database_url) as uow:
            row = uow.execute(
                "SELECT r.run_id, r.account_id, r.conversation_id, r.session_id,"
                "r.status AS run_status,"
                "t.role AS trigger_role, t.status AS trigger_status,"
                "t.content AS trigger_content,"
                "a.status AS assistant_status, a.content AS assistant_content "
                "FROM runs r "
                "JOIN messages t ON t.account_id=r.account_id "
                "AND t.conversation_id=r.conversation_id "
                "AND t.message_id=r.trigger_message_id "
                "JOIN messages a ON a.account_id=r.account_id "
                "AND a.conversation_id=r.conversation_id "
                "AND a.produced_by_run_id=r.run_id "
                "WHERE r.run_id=%s",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        if row["run_status"] != "completed":
            return None
        if row["assistant_status"] != "completed":
            return None
        if not row["assistant_content"]:
            return None
        if row["trigger_role"] != "user" or row["trigger_status"] != "accepted":
            return None
        return row

    async def retain_completed_run(self, run_id: UUID) -> RetainOutcome:
        """把 completed Run 作为 ``web-run:{run_id}`` 提交到 Hindsight。

        只 retain [user] trigger + [assistant] 最终回复（doc §5.4/§37）。
        """
        run = await asyncio.to_thread(self.load_completed_run, run_id)
        if run is None:
            return RetainOutcome(skipped=True)
        document_id = f"web-run:{run['run_id']}"
        events = [
            {"role": "user", "content": run["trigger_content"]},
            {"role": "assistant", "content": run["assistant_content"]},
        ]
        logger.info(
            "memory_retain_started run_id=%s account_id=%s document_id=%s",
            run["run_id"], run["account_id"], document_id,
        )
        t0 = asyncio.get_running_loop().time()
        receipt = await self._hindsight.retain_document(
            events,
            user_id=str(run["account_id"]),
            document_id=document_id,
            async_retain=False,
            channel_type="web",
            scope="private",
            metadata={
                "source": "web",
                "run_id": str(run["run_id"]),
                "conversation_id": str(run["conversation_id"]),
                "session_id": str(run["session_id"]),
            },
        )
        latency_ms = (asyncio.get_running_loop().time() - t0) * 1000
        if receipt.accepted:
            logger.info(
                "memory_retain_succeeded run_id=%s account_id=%s document_id=%s "
                "latency_ms=%.1f",
                run["run_id"], run["account_id"], document_id, latency_ms,
            )
        else:
            logger.warning(
                "memory_retain_failed run_id=%s account_id=%s document_id=%s "
                "latency_ms=%.1f",
                run["run_id"], run["account_id"], document_id, latency_ms,
            )
        return RetainOutcome(
            skipped=False, accepted=receipt.accepted, document_id=document_id
        )
