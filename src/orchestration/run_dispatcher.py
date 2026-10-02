"""Database adapters for the unified finite Run execution entrypoint."""

import asyncio
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent_activities.store import AgentDataStore, RunNotExecutable, StaleFencingToken
from orchestration.execution_strategy import StrategyRegistry
from orchestration.run_lifecycle_contracts import RunLifecycleInput
from persistence.uow import UnitOfWork
from web_domain.errors import DomainError


class RunStrategyActivities:
    def __init__(self, database):
        self.database = database

    @activity.defn(name="load_run_strategy_activity")
    async def load_strategy(self, request: RunLifecycleInput):
        request.validate()

        def load():
            with UnitOfWork(self.database) as uow:
                run = uow.execute(
                    "SELECT * FROM runs WHERE run_id=%s", (UUID(request.run_id),)
                ).fetchone()
                if run is None:
                    raise ValueError("Run unavailable")
                if run["source_kind"] == "work":
                    from run_domain.lifecycle import RunLifecycleService

                    RunLifecycleService.check_work(uow, run)
                    snapshot = run["input_snapshot"]
                    if snapshot.get("requirement_revision") != run["requirement_revision"]:
                        raise ValueError("Run input revision mismatch")
                return StrategyRegistry().validate_run(run).to_dict()

        try:
            return await asyncio.to_thread(load)
        except DomainError as exc:
            raise ApplicationError(
                "Run no longer executable", type="run_not_executable", non_retryable=True
            ) from exc
        except ValueError as exc:
            raise ApplicationError(
                "unsupported execution strategy",
                type="unsupported_execution_strategy",
                non_retryable=True,
            ) from exc


class ReminderActivities:
    def __init__(self, database):
        self.database = database
        self.store = AgentDataStore(database)

    @activity.defn(name="execute_reminder_activity")
    async def execute(self, request: RunLifecycleInput):
        request.validate()
        try:
            attempt = activity.info().attempt
        except RuntimeError:
            attempt = 1
        try:
            return await asyncio.to_thread(self._execute, request.run_id, attempt)
        except (DomainError, RunNotExecutable, StaleFencingToken) as exc:
            raise ApplicationError(
                "Run no longer executable", type="run_not_executable", non_retryable=True
            ) from exc
        except ValueError as exc:
            raise ApplicationError("Reminder contract rejected", non_retryable=True) from exc

    def _execute(self, run_id, attempt=1):
        from agent_activities.fencing import fence_scope
        from agent_workflows.lifecycle_contracts import SegmentInput
        from run_domain.lifecycle import RunLifecycleService
        from work_domain.models import continuation

        identity = self.store.run_identity(run_id)
        if identity["status"] == "succeeded":
            return {"run_id": run_id, "status": "succeeded"}
        StrategyRegistry().validate_run(identity)
        if identity["executor_key"] != "reminder":
            raise ValueError("Run does not use reminder executor")
        segment = SegmentInput(
            2,
            run_id,
            identity["account_id"],
            f"reminder:{run_id}:{attempt}",
            execution_id=identity["execution_id"],
        )
        token = self.store.acquire_segment(segment)
        try:
            with fence_scope(identity["account_id"], run_id, token, identity["execution_id"]):
                # Intent, registered attempt, receipt and terminal result commit together.
                # No model, sandbox, external channel or filesystem is involved.
                with UnitOfWork(self.database) as uow:
                    run = RunLifecycleService.lock(uow, UUID(identity["account_id"]), UUID(run_id))
                    AgentDataStore._assert_fence(uow)
                    RunLifecycleService.check_work(uow, run)
                    spec = run["input_snapshot"]["requirement"]["spec"]
                    operation_id = f"reminder:{run_id}:enqueue:v1"
                    uow.execute(
                        "INSERT INTO execution_operations(operation_id,run_id,operation_type) VALUES (%s,%s,'deterministic') ON CONFLICT DO NOTHING",
                        (operation_id, UUID(run_id)),
                    )
                    from agent_activities.fencing import execution_fence
                    from run_domain.results import ResultReceiptService

                    ResultReceiptService.register_attempt(uow, operation_id, execution_fence.get())
                    from delivery.service import (
                        DeliveryReceiptHandler,
                        enqueue,
                        ensure_inbox,
                        reconcile_reminder_receipt,
                    )

                    run["execution_id"] = UUID(identity["execution_id"])
                    work = RunLifecycleService.check_work(uow, run)
                    target = spec.get("target_ref", "account_inbox")
                    target = (
                        ensure_inbox(uow, run["account_id"], run["work_id"])
                        if target == "account_inbox"
                        else UUID(target)
                    )
                    audience = uow.execute(
                        "SELECT audience FROM delivery_targets WHERE account_id=%s AND target_id=%s",
                        (run["account_id"], target),
                    ).fetchone()["audience"]
                    payload = {"content": spec["content"]}
                    if audience == "group":
                        payload["summary"] = (
                            f"Work {work['work_id']}: 提醒已到期，请登录原账户查看提醒内容。"
                        )
                    intent_id = enqueue(
                        uow,
                        run["account_id"],
                        f"reminder:{run['wakeup_id']}",
                        payload,
                        work=work,
                        run=run,
                        operation_id=operation_id,
                        purpose="fulfillment",
                        target_id=target,
                    )
                    revalidated = reconcile_reminder_receipt(uow, work, run, intent_id, target)
                    result = {
                        "schema_version": 1,
                        "kind": "notification_enqueued",
                        "evidence": [],
                        "continuation": continuation(
                            "awaiting_delivery", "reminder_enqueued", receipt_ref=str(intent_id)
                        ),
                    }
                    AgentDataStore._complete_operation(
                        uow,
                        operation_id,
                        f"operation:{operation_id}",
                        {"intent_id": str(intent_id), "side_effect_class": "idempotent_write"},
                    )
                    RunLifecycleService(self.database).finish_in_uow(
                        uow, run, "succeeded", result=result
                    )
                    if revalidated:
                        notification = uow.execute(
                            "SELECT * FROM notifications WHERE notification_id=%s", (intent_id,)
                        ).fetchone()
                        DeliveryReceiptHandler.apply(uow, notification, revalidated)
                    uow.execute(
                        "UPDATE run_executions SET result_ref=%s WHERE run_id=%s AND role='root'",
                        (f"operation:{operation_id}", UUID(run_id)),
                    )
                    return {"run_id": run_id, "status": "succeeded"}
        finally:
            self.store.release_segment(segment)
