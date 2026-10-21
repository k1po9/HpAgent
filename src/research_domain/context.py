"""Execution-local Research identity shared by stage repositories."""

from contextvars import ContextVar
from functools import wraps

research_request: ContextVar = ContextVar("research_request", default=None)


def invocation_id():
    request = research_request.get()
    return request.invocation_id if request else None


def research_key(run_id):
    return invocation_id() or str(run_id)


def research_activity(function):
    @wraps(function)
    async def wrapped(self, request):
        token = research_request.set(request if getattr(request, "invocation_id", None) else None)
        try:
            return await function(self, request)
        finally:
            research_request.reset(token)

    return wrapped


def research_identity(run_id):
    from uuid import NAMESPACE_URL, uuid5

    return uuid5(NAMESPACE_URL, invocation_id()) if invocation_id() else run_id


def local_research_activity(function):
    @research_activity
    @wraps(function)
    async def wrapped(self, request):
        from temporalio.exceptions import ApplicationError

        if not request.invocation_id or not request.account_id or not request.execution_id:
            raise ApplicationError(
                "Research requires an admitted capability identity", non_retryable=True
            )
        return await function(self, request)

    return wrapped
