"""
``_build_web_background_tasks`` —— start_worker 的 Web 后台任务组合代码。

回归保护（P0）：Phase F 的 MemoryRetentionService 挂在
``WebWorkerComposition.memory_retention``（组合层），**不在**
``composition.workers`` 上 —— 那里只有 lifecycle/agent。start_worker 用独立
的 ``web_memory_retention = composition.memory_retention`` 传进来。本测试用
假 dispatcher/reconciler/service 真实走到这段组合代码，验证 retain_memory
任务真的会 claim outbox、调用服务、mark processed，并让独立的 lease recovery
sweep 运行。
"""
import asyncio
import uuid

import pytest

from orchestration.worker import WebWorkerComposition, _build_web_background_tasks
from orchestration.web_workers import WebTemporalWorkers


class _FakeDispatcher:
    def __init__(self, outbox):
        self.outbox = outbox
        self.run_once_calls = 0

    async def run_once(self):
        self.run_once_calls += 1
        return 0


class _FakeReconciler:
    async def run_once(self):
        return None


class _FakeOutbox:
    """模拟 OutboxService：一次 claim 返回一个 retain_memory 事件后即空。"""

    def __init__(self):
        self.processed: list[str] = []
        self.recovered: list[tuple] = []
        self._claimed = False

    def claim(self, worker_id, event_types, limit):
        if self._claimed:
            return []
        self._claimed = True
        return [{
            "outbox_event_id": str(uuid.uuid4()),
            "run_id": "22222222-2222-4222-8222-222222222222",
            "event_type": "retain_memory",
            "attempt_count": 1,
        }]

    def mark_processed(self, event_id, worker_id):
        self.processed.append(str(event_id))
        return True

    def mark_retryable_failure(self, *args, **kwargs):
        raise AssertionError("must not retry on accepted outcome")

    def dead_letter(self, *args, **kwargs):
        raise AssertionError("must not dead-letter on accepted outcome")

    def recover_expired(self, *args, **kwargs):
        self.recovered.append(args)
        return 0


class _FakeMemoryRetention:
    def __init__(self):
        self.runs: list[str] = []

    async def retain_completed_run(self, run_id):
        from application.memory_retention import RetainOutcome

        self.runs.append(str(run_id))
        return RetainOutcome(accepted=True, document_id=f"web-run:{run_id}")


@pytest.mark.asyncio
async def test_background_tasks_wire_memory_retention_and_consume_outbox():
    outbox = _FakeOutbox()
    retention = _FakeMemoryRetention()
    (
        dispatcher_task,
        reconciler_task,
        recovery_task,
        memory_retention_task,
        memory_retention_recovery_task,
    ) = _build_web_background_tasks(
        web_dispatcher=_FakeDispatcher(outbox),
        web_reconciler=_FakeReconciler(),
        web_memory_retention=retention,
        lease_timeout_seconds=60,
        recovery_interval_seconds=0.05,
    )

    # Phase F 任务必须存在（P0 回归：曾经在 workers 层上找不到导致 AttributeError）。
    assert memory_retention_task is not None
    assert memory_retention_recovery_task is not None
    assert dispatcher_task is not None
    assert reconciler_task is not None
    assert recovery_task is not None

    # 让 memory-retention 任务真正走到 retain_memory 消费：claim → 服务 → mark processed。
    for _ in range(40):
        if outbox.processed:
            break
        await asyncio.sleep(0.01)
    # 给独立的 retain_memory lease recovery sweep 一个周期。
    for _ in range(40):
        if outbox.recovered:
            break
        await asyncio.sleep(0.01)

    assert outbox.processed, "retain_memory event must be marked processed"
    assert retention.runs == ["22222222-2222-4222-8222-222222222222"]
    assert outbox.recovered, "retain_memory recovery sweep must run"

    for task in (
        dispatcher_task,
        reconciler_task,
        recovery_task,
        memory_retention_task,
        memory_retention_recovery_task,
    ):
        task.cancel()
    for task in (
        dispatcher_task,
        reconciler_task,
        recovery_task,
        memory_retention_task,
        memory_retention_recovery_task,
    ):
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_background_tasks_without_memory_retention_omit_only_memory_tasks():
    outbox = _FakeOutbox()
    (
        dispatcher_task,
        reconciler_task,
        recovery_task,
        memory_retention_task,
        memory_retention_recovery_task,
    ) = _build_web_background_tasks(
        web_dispatcher=_FakeDispatcher(outbox),
        web_reconciler=_FakeReconciler(),
        web_memory_retention=None,  # Hindsight 不可用 → 不启动 retain 消费者
        lease_timeout_seconds=60,
        recovery_interval_seconds=0.05,
    )
    assert memory_retention_task is None
    assert memory_retention_recovery_task is None
    assert dispatcher_task is not None
    assert reconciler_task is not None
    assert recovery_task is not None
    for task in (dispatcher_task, reconciler_task, recovery_task):
        task.cancel()
    for task in (dispatcher_task, reconciler_task, recovery_task):
        try:
            await task
        except asyncio.CancelledError:
            pass


def test_memory_retention_lives_on_composition_not_workers():
    """组合形状契约：memory_retention 在 composition 层，不在 workers 层。"""
    workers = WebTemporalWorkers(lifecycle=object(), agent=object())
    composition = WebWorkerComposition(
        workers=workers,
        dispatcher=object(),
        reconciler=object(),
        memory_retention=object(),
    )
    assert hasattr(composition, "memory_retention")
    assert not hasattr(composition.workers, "memory_retention")
