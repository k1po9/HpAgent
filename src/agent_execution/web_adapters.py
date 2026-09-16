"""W3-A forwarding surface; implementation ownership moved. Remove in W3-B."""

import asyncio
from uuid import UUID

from agent_execution.facade import ExecutionResult
from application.chat_execution import PostgresWebRequestLoader as PostgresWebRequestLoader
from application.chat_execution import WebExecutionContextProvider as WebExecutionContextProvider
from application.chat_execution import logger as logger
from web_domain.lifecycle import WebRunLifecycleService


class LifecycleWebReplySink:
    def __init__(self, lifecycle: WebRunLifecycleService):
        self._lifecycle = lifecycle

    async def complete(self, run_id: str, result: ExecutionResult) -> None:
        await asyncio.to_thread(self._lifecycle.complete, UUID(run_id), result.content)
