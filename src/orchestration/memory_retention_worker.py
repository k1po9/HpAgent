"""MemoryRetentionWorker —— 消费 retain_memory Outbox → Hindsight。

Phase F F-04（doc §42-47）。独立后台循环，与 Web Dispatcher（start/cancel）
完全分离：只 claim ``retain_memory``，只恢复 ``retain_memory`` 过期 lease。

关键保证:
  - 内存失败绝不反向改变 Run / Message 的 completed 终态（doc §44）。
  - Hindsight 失败 → mark_retryable_failure（简单退避 1/2/4/8/16/30/60s）；
    超限 → dead_letter，Run 仍保持 completed。
  - 日志只含 run_id/account_id/document_id/attempt/error_code，不含内容。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from common.logging import log_event

logger = logging.getLogger("HpAgent.MemoryRetentionWorker")

RETAIN_RETRY_BACKOFF_SECONDS: tuple[int, ...] = (1, 2, 4, 8, 16, 30, 60)


async def run_memory_retention_loop(
    outbox: Any,
    service: Any,
    worker_id: str,
    *,
    idle_seconds: float = 0.5,
    max_attempts: int = 8,
    backoff: Sequence[int] = RETAIN_RETRY_BACKOFF_SECONDS,
) -> None:
    """循环消费 retain_memory Outbox 事件并写入 Hindsight。"""
    while True:
        try:
            events = await asyncio.to_thread(
                outbox.claim, worker_id, {"retain_memory"}, 10
            )
            for event in events:
                await _handle_event(
                    outbox, service, worker_id, event,
                    max_attempts=max_attempts, backoff=backoff,
                )
            if not events:
                await asyncio.sleep(idle_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Memory retention iteration failed")
            await asyncio.sleep(1)


async def _handle_event(
    outbox: Any,
    service: Any,
    worker_id: str,
    event: dict[str, Any],
    *,
    max_attempts: int,
    backoff: Sequence[int],
) -> None:
    event_id = UUID(str(event["outbox_event_id"]))
    run_id = UUID(str(event["run_id"]))
    attempt = int(event["attempt_count"])
    try:
        outcome = await service.retain_completed_run(run_id)
        if outcome.skipped or outcome.accepted:
            await asyncio.to_thread(outbox.mark_processed, event_id, worker_id)
            if outcome.skipped:
                log_event(logger, logging.WARNING, "memory_retain_skipped", "memory",
                          run_id=str(run_id), execution_id=str(run_id), surface="web",
                          outbox_event_id=str(event_id), attempt_count=attempt, status="skipped")
        else:
            raise RuntimeError("hindsight_retain_rejected")
    except Exception as exc:
        error_code = "memory_retain_exhausted" if attempt >= max_attempts else "memory_retain_failed"
        if attempt >= max_attempts:
            await asyncio.to_thread(
                outbox.dead_letter,
                event_id, worker_id, error_code, str(exc)[:1000],
            )
            log_event(logger, logging.ERROR, "memory_retain_dead_letter", "memory",
                      run_id=str(run_id), execution_id=str(run_id), surface="web",
                      outbox_event_id=str(event_id),
                      attempt_count=attempt, status="failed", error_code=error_code)
        else:
            delay = backoff[min(attempt - 1, len(backoff) - 1)]
            await asyncio.to_thread(
                outbox.mark_retryable_failure,
                event_id, worker_id, error_code, str(exc)[:1000],
                datetime.now(UTC) + timedelta(seconds=int(delay)),
            )
            log_event(logger, logging.WARNING, "memory_retain_retry", "memory",
                      run_id=str(run_id), execution_id=str(run_id), surface="web",
                      outbox_event_id=str(event_id),
                      attempt_count=attempt, status="degraded", error_code=error_code,
                      retry_in_seconds=int(delay))
