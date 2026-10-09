"""Explicit transaction boundaries for isolated Activity/provider unit tests.

Production fencing, delegation lookup and dispatch ordering remain in place;
these collaborators supply the database results without opening a connection.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

from agent_activities.fencing import execution_fence


class ExecutionStoreBoundary:
    database_url = object()

    @staticmethod
    def _assert_fence(uow):
        uow.assert_fence()


def install_execution_uow(monkeypatch, *, role="root", tool_names=(), fence_error=None):
    calls = []

    class Transaction:
        def __init__(self, database):
            assert database is not None
            self.checked = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def assert_fence(self):
            assert execution_fence.get() is not None
            calls.append("fence")
            if fence_error is not None:
                raise fence_error
            self.checked = True

        def execute(self, sql, params):
            assert self.checked, "execution lookup must follow fencing"
            assert "FROM run_executions" in sql
            account, run, execution, _ = params
            fence = execution_fence.get()
            assert (account, run, execution) == tuple(UUID(value) for value in fence[:3])
            calls.append("execution")
            return SimpleNamespace(fetchone=lambda: {
                "role": role,
                "resource_scope": {"tool_names": list(tool_names)},
            })

    monkeypatch.setattr("persistence.uow.UnitOfWork", Transaction)
    return calls


def install_model_dispatch_guards(monkeypatch, *, dispatch_error=None):
    calls = []

    class Capacity:
        def __init__(self, database):
            assert database is not None

        @asynccontextmanager
        async def slot(self, run_id, resource):
            assert resource in {"model", "tool"}
            calls.append(("acquire", run_id))
            try:
                yield
            finally:
                calls.append(("release", run_id))

    class Policy:
        def __init__(self, database):
            assert database is not None

        def check_dispatch(self, account_id, run_id):
            assert calls[-1] == ("acquire", run_id)
            calls.append(("authorize", account_id, run_id))
            if dispatch_error is not None:
                raise dispatch_error

    monkeypatch.setattr("resources.capacity.CapacityService", Capacity)
    monkeypatch.setattr("workspace.resources.ResourcePolicy", Policy)
    return calls
