"""
TaskScheduler —— 通用定时任务调度器。

设计原则：
  - 单轮询协程处理所有任务（不按任务起 Temporal Workflow）
  - JSON 文件持久化，重启恢复
  - task_type + handler 注册模式，业务无关
  - 支持一次性任务（trigger_at）和周期任务（cron_expr）

未来可扩展 task_type：
  - "user_reminder"      → 通过 NapCat 推送提醒
  - "memory_reflect"     → 触发特定用户记忆反思
  - "daily_report"       → 发送日报
"""

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Awaitable, Optional

logger = logging.getLogger("HpAgent.Scheduler")


@dataclass
class ScheduledTask:
    """通用定时任务数据模型。"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_type: str = ""            # 任务类型（对应 handler 注册 key）
    trigger_at: float = 0.0        # 下次触发 Unix 时间戳
    cron_expr: str = ""            # 空=一次性，非空=周期（如 "0 8 * * *"）
    params: dict = field(default_factory=dict)  # 业务参数（handler 解释）
    status: str = "pending"        # pending / triggered / cancelled
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_type": self.task_type,
            "trigger_at": self.trigger_at,
            "cron_expr": self.cron_expr,
            "params": self.params,
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScheduledTask":
        return cls(
            id=d.get("id", ""),
            task_type=d.get("task_type", ""),
            trigger_at=d.get("trigger_at", 0.0),
            cron_expr=d.get("cron_expr", ""),
            params=d.get("params", {}),
            status=d.get("status", "pending"),
            created_at=d.get("created_at", 0.0),
        )


class TaskScheduler:
    """通用定时任务调度器。

    使用方式:
        scheduler = TaskScheduler(data_dir)
        scheduler.register_handler("user_reminder", my_handler)
        await scheduler.load()
        task = asyncio.create_task(scheduler.poll_loop())

    然后业务方调用:
        await scheduler.schedule(ScheduledTask(
            task_type="user_reminder",
            trigger_at=time.time() + 1800,
            params={"content": "开会", ...},
        ))
    """

    def __init__(self, data_dir: Path):
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self._file = data_dir / "scheduled_tasks.json"
        self._tasks: dict[str, ScheduledTask] = {}
        self._handlers: dict[str, Callable[..., Awaitable[None]]] = {}
        self._lock = threading.Lock()

    # ── Handler 注册 ──────────────────────────────────────────────────────

    def register_handler(
        self, task_type: str, handler: Callable[..., Awaitable[None]]
    ) -> None:
        """注册任务类型处理器。handler 接收 ScheduledTask 作为唯一参数。"""
        self._handlers[task_type] = handler
        logger.info("Scheduler handler registered: %s", task_type)

    # ── 持久化 ────────────────────────────────────────────────────────────

    async def load(self) -> int:
        """从 JSON 文件加载已有任务。返回加载数。"""
        if not self._file.exists():
            logger.info("Scheduler: no persisted tasks file at %s", self._file)
            return 0

        try:
            loop = asyncio.get_running_loop()
            raw = await loop.run_in_executor(
                None, self._file.read_text, "utf-8"
            )
            data = json.loads(raw)
            if not isinstance(data, list):
                logger.warning("Scheduler: invalid JSON format, starting fresh")
                return 0

            loaded = 0
            for item in data:
                task = ScheduledTask.from_dict(item)
                if task.status == "pending":
                    self._tasks[task.id] = task
                    loaded += 1
                elif task.status == "cancelled":
                    # 保留已取消的任务记录（供审计），但不加入活跃池
                    pass
                elif task.status == "triggered" and task.cron_expr:
                    # 已触发的一次性任务不恢复，但周期任务需要恢复
                    self._tasks[task.id] = task
                    loaded += 1

            logger.info(
                "Scheduler: loaded %d tasks from %s", loaded, self._file
            )
            return loaded
        except json.JSONDecodeError as e:
            logger.warning("Scheduler: corrupted JSON file, starting fresh: %s", e)
            return 0
        except Exception as e:
            logger.warning("Scheduler: failed to load tasks: %s", e)
            return 0

    async def _save(self) -> None:
        """原子写入 JSON 文件。"""
        loop = asyncio.get_running_loop()
        tmp_path = self._file.with_suffix(".tmp")

        def _write():
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(
                    [t.to_dict() for t in self._tasks.values()],
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(tmp_path, self._file)

        await loop.run_in_executor(None, _write)

    # ── CRUD ──────────────────────────────────────────────────────────────

    async def schedule(self, task: ScheduledTask) -> str:
        """调度一个新任务。返回 task.id。"""
        with self._lock:
            self._tasks[task.id] = task
        await self._save()
        logger.info(
            "Scheduler: scheduled task %s type=%s trigger_at=%.0f",
            task.id, task.task_type, task.trigger_at,
        )
        return task.id

    async def cancel(self, task_id: str) -> bool:
        """取消指定任务。返回是否成功。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status == "cancelled":
                return False
            task.status = "cancelled"
        await self._save()
        logger.info("Scheduler: cancelled task %s", task_id)
        return True

    def list_by_filter(
        self,
        task_type: str = "",
        status: str = "",
        **param_filters,
    ) -> list[ScheduledTask]:
        """按条件过滤任务列表。

        Args:
            task_type: 任务类型过滤，空=不过滤
            status: 状态过滤，空=不过滤
            **param_filters: params 字段的键值匹配

        Returns:
            匹配的任务列表（按 created_at 升序）。
        """
        with self._lock:
            results = []
            for task in self._tasks.values():
                if task_type and task.task_type != task_type:
                    continue
                if status and task.status != status:
                    continue
                match = True
                for key, val in param_filters.items():
                    if task.params.get(key) != val:
                        match = False
                        break
                if match:
                    results.append(task)
            results.sort(key=lambda t: t.created_at)
            return results

    # ── 轮询循环 ──────────────────────────────────────────────────────────

    async def poll_loop(self, interval: float = 15.0) -> None:
        """后台轮询循环（asyncio.create_task 启动）。

        每 interval 秒:
          1. now = time.time()
          2. 遍历 pending 任务，trigger_at <= now 则调用 handler
          3. 周期任务: 计算 cron_next() 更新 trigger_at
          4. 一次性任务: 标记 status="triggered"
          5. 保存
        """
        while True:
            await asyncio.sleep(interval)

            now = time.time()
            triggered_ids: list[str] = []

            with self._lock:
                for task in list(self._tasks.values()):
                    if task.status != "pending":
                        continue
                    if task.trigger_at > now:
                        continue

                    handler = self._handlers.get(task.task_type)
                    if handler is None:
                        logger.warning(
                            "Scheduler: no handler for task_type=%s (task=%s)",
                            task.task_type, task.id,
                        )
                        task.status = "cancelled"
                        continue

                    # 先处理周期更新，再调用 handler
                    if task.cron_expr:
                        next_ts = self._cron_next(task.cron_expr, now)
                        if next_ts is None:
                            logger.warning(
                                "Scheduler: invalid cron_expr=%s, cancelling task %s",
                                task.cron_expr, task.id,
                            )
                            task.status = "cancelled"
                            continue
                        task.trigger_at = next_ts
                        # 周期任务保持 pending
                    else:
                        # 一次性任务 → 标记为 triggered
                        task.status = "triggered"

                    triggered_ids.append(task.id)

            # 在锁外调用 handler，避免死锁
            for task_id in triggered_ids:
                task = self._tasks.get(task_id)
                if task is None:
                    continue
                handler = self._handlers.get(task.task_type)
                if handler is None:
                    continue
                try:
                    await handler(task)
                    logger.info(
                        "Scheduler: handler executed for task %s type=%s",
                        task_id, task.task_type,
                    )
                except Exception as e:
                    logger.error(
                        "Scheduler: handler failed for task %s: %s", task_id, e
                    )

            if triggered_ids:
                await self._save()

    @staticmethod
    def _cron_next(cron_expr: str, now: float) -> Optional[float]:
        """计算 cron 表达式的下一次触发时间戳。"""
        try:
            from croniter import croniter
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(now, tz=timezone.utc)
            cron = croniter(cron_expr, dt)
            return cron.get_next(float)
        except (ValueError, KeyError) as e:
            logger.warning("Invalid cron expression '%s': %s", cron_expr, e)
            return None
