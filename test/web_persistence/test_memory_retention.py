"""
MemoryRetentionService —— Web completed Run → Hindsight（doc §34-40 / §62）。

⑥ load_completed_run 只放行 completed Run（queued/running/failed/cancelled → None）
⑦ failed / cancelled Run → retain_completed_run 返回 skipped=True，Hindsight 不被调用
⑧ Hindsight 失败（accepted=False）→ Run 保持 completed，accepted=False 如实返回
- 成功路径提交 web-run:{run_id}，仅 user trigger + assistant 最终回复
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from application.memory_retention import MemoryRetentionService, RetainOutcome
from memory.hindsight_client import RetainReceipt
from web_domain.services import CommandService

from .test_phase_a_invariants import _conversation_and_run

pytestmark = pytest.mark.postgres


def _completed_run(database_url, worker_database_url, account_id) -> tuple[object, object]:
    _, conversation_id, run_id = _conversation_and_run(database_url, account_id)
    worker = CommandService(worker_database_url)
    assert worker.start_run(account_id, run_id)
    assert worker.complete_run(account_id, run_id, "answer")
    return conversation_id, run_id


def test_load_completed_run_only_returns_completed(
    db, database_url, worker_database_url, account_id
):
    service = MemoryRetentionService(worker_database_url, object())

    # queued → 拒绝
    _, _, queued = _conversation_and_run(database_url, account_id)
    assert service.load_completed_run(queued) is None

    # completed → 放行，且角色/状态/内容全部满足 doc §36 校验
    _, run_id = _completed_run(database_url, worker_database_url, account_id)
    row = service.load_completed_run(run_id)
    assert row is not None
    assert row["run_status"] == "completed"
    assert row["assistant_status"] == "completed"
    assert row["assistant_content"] == "answer"
    assert row["trigger_role"] == "user"
    assert row["trigger_status"] == "accepted"
    assert row["account_id"] == account_id
    assert db.execute(
        "SELECT status FROM runs WHERE run_id=%s", (run_id,)
    ).fetchone()[0] == "completed"


@pytest.mark.asyncio
async def test_failed_run_is_skipped_without_hindsight(
    database_url, worker_database_url, account_id
):
    calls = []

    class Hindsight:
        async def retain_document(self, *args, **kwargs):
            calls.append(kwargs)
            return RetainReceipt(accepted=True)

    service = MemoryRetentionService(worker_database_url, Hindsight())
    _, _, run_id = _conversation_and_run(database_url, account_id)
    assert CommandService(worker_database_url).fail_run(account_id, run_id, "boom")
    outcome = await service.retain_completed_run(run_id)
    assert outcome == RetainOutcome(skipped=True)
    assert calls == []


@pytest.mark.asyncio
async def test_cancelled_run_is_skipped_without_hindsight(
    database_url, worker_database_url, account_id
):
    class Hindsight:
        async def retain_document(self, *args, **kwargs):
            raise AssertionError("Hindsight must not be called for cancelled Run")

    service = MemoryRetentionService(worker_database_url, Hindsight())
    _, _, run_id = _conversation_and_run(database_url, account_id)
    CommandService(database_url).cancel_run(account_id, run_id, str(uuid4()))
    outcome = await service.retain_completed_run(run_id)
    assert outcome == RetainOutcome(skipped=True)


@pytest.mark.asyncio
async def test_hindsight_failure_leaves_run_completed_and_surfaces_accepted_false(
    db, database_url, worker_database_url, account_id
):
    calls = []

    class FailingHindsight:
        async def retain_document(self, events, user_id, document_id, **kwargs):
            calls.append((events, user_id, document_id, kwargs))
            return RetainReceipt(accepted=False)

    service = MemoryRetentionService(worker_database_url, FailingHindsight())
    _, run_id = _completed_run(database_url, worker_database_url, account_id)
    outcome = await service.retain_completed_run(run_id)
    assert outcome.skipped is False
    assert outcome.accepted is False
    assert outcome.document_id == f"web-run:{run_id}"
    assert db.execute(
        "SELECT status FROM runs WHERE run_id=%s", (run_id,)
    ).fetchone()[0] == "completed"
    assert len(calls) == 1
    events, user_id, document_id, kwargs = calls[0]
    assert document_id == f"web-run:{run_id}"
    assert events == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "answer"},
    ]
    assert kwargs["channel_type"] == "web"
    assert kwargs["async_retain"] is False
    assert kwargs["metadata"]["source"] == "web"
    assert kwargs["metadata"]["run_id"] == str(run_id)
