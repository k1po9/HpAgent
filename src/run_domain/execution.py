"""Execution authority and ownership checks; caller owns the transaction boundary."""

from __future__ import annotations

from uuid import UUID

from run_domain.execution_context import execution_fence


class LeaseConflict(RuntimeError):
    code = "execution_lease_conflict"


class StaleFencingToken(RuntimeError):
    code = "stale_fencing_token"


class RunNotExecutable(RuntimeError):
    code = "run_not_executable"


class SegmentClosed(RuntimeError):
    code = "execution_segment_closed"


class TranscriptVersionConflict(RuntimeError):
    code = "transcript_version_conflict"


def lock_execution(uow, account_id, run_id, execution_id=None):
    if execution_id is None and execution_fence.get() is not None:
        execution_id = execution_fence.get()[2]
    row = uow.execute(
        "SELECT * FROM run_executions WHERE account_id=%s AND run_id=%s "
        "AND ((%s::uuid IS NULL AND role='root') OR execution_id=%s) FOR UPDATE",
        (
            UUID(account_id),
            UUID(run_id),
            UUID(execution_id) if execution_id else None,
            UUID(execution_id) if execution_id else None,
        ),
    ).fetchone()
    if row is None:
        raise RunNotExecutable("Execution does not belong to the Run and Account")
    return row


def require_active_run(uow, account_id, run_id):
    from persistence.repositories import AccountRepository
    from run_domain.lifecycle import RunLifecycleService
    from web_domain.errors import DomainError

    try:
        run = RunLifecycleService.lock(uow, UUID(account_id), UUID(run_id))
        if not AccountRepository().require_active(uow, UUID(account_id)):
            raise RunNotExecutable("Account is disabled")
        if run["source_kind"] == "work":
            RunLifecycleService.check_work(uow, run)
        if run["status"] not in {"queued", "running"}:
            raise RunNotExecutable("Run cannot execute")
        return run
    except DomainError as exc:
        raise RunNotExecutable("Run no longer owns progression") from exc


def assert_execution_fence(uow):
    fence = execution_fence.get()
    if fence is None:
        return
    account_id, run_id, execution_id, token = fence
    from persistence.memory_context import check_execution
    try:
        require_active_run(uow, account_id, run_id)
        check_execution(uow, account_id, run_id, execution_id)
        execution = lock_execution(uow, account_id, run_id, execution_id)
        if execution["status"] not in {"queued", "running"}:
            raise RunNotExecutable("Execution is terminal or fenced")
    except RunNotExecutable as exc:
        raise StaleFencingToken("late execution cannot read or commit durable state") from exc
    row = uow.execute(
        "SELECT fencing_token FROM execution_attempt_leases WHERE account_id=%s "
        "AND run_id=%s AND execution_id=%s AND fencing_token=%s "
        "AND lease_expires_at>clock_timestamp() FOR SHARE",
        (UUID(account_id), UUID(run_id), UUID(execution_id), token),
    ).fetchone()
    if row is None:
        raise StaleFencingToken("late execution cannot read or commit durable state")


def require_owned_record(uow, table, column, value):
    fence = execution_fence.get()
    if fence is None:
        return
    account, run, execution, _ = fence
    row = uow.execute(
        f"SELECT 1 FROM {table} WHERE {column}=%s AND account_id=%s AND run_id=%s "
        "AND execution_id=%s",
        (value, UUID(account), UUID(run), UUID(execution)),
    ).fetchone()
    if row is None:
        raise StaleFencingToken("record is outside the Execution context")
