"""
Orchestration Worker — boots the Temporal Worker that executes the OrchestrationWorkflow.

Boot sequence:
  1. Load config
  2. Initialize dependencies (AccountService, SessionManager, ResourcePool,
     SandboxManager, ContextBuilder, ChannelRouter)
  3. Inject dependencies into Harness Activities (the brain's decomposed operations)
  4. Connect to Temporal Server
  5. Start Worker polling the task queue, executing Workflows + Activities
  6. Start channel listeners → each message starts/signals an OrchestrationWorkflow

Cross-client memory:
  - AccountService resolves channel-type+sender-id → unified account_id
  - workflow_id = "agent-{account_id}" (shared across QQ and Web)
  - ChannelRouter routes responses to the correct channel
"""
import asyncio
import logging
from typing import Optional, Callable, Awaitable, Dict, Any

from temporalio.client import Client
from temporalio.worker import Worker

from harness.activities import (
    inject,
    build_context_activity,
    get_available_tools_activity,
    call_model_activity,
    execute_tool_activity,
    send_response_activity,
)
from orchestration.workflow import OrchestrationWorkflow
from harness.context_builder import HarnessContextBuilder as ContextBuilder
from resources.resource_pool import ResourcePool
from resources.credentials import CredentialManager, ModelEndpoint
from sandbox.sandbox_manager import SandboxManager
from sandbox.tools.factory import ToolFactory
from sandbox.channels.napcat import NapCatChannel
from sandbox.channels.router import ChannelRouter
from account.account_service import AccountService
from session.session_manager import SessionManager
from common.types import UnifiedMessage, ChannelType, SessionMetadata

logger = logging.getLogger("HpAgent.OrchestrationWorker")


async def init_dependencies(config: dict) -> tuple:
    """Initialize all shared dependencies and return them for injection."""
    credential_manager = CredentialManager()
    credential_manager.register_model_chain([
        ModelEndpoint(
            provider="anthropic",
            api_key=config["api_key"],
            base_url=config["base_url"],
            model=config["model"],
        ),
    ])

    resource_pool = ResourcePool(credential_manager)
    await resource_pool.initialize_models()

    sandbox_manager = SandboxManager()
    default_tools = ToolFactory.create_default_tools()
    sandbox_manager.create_sandbox(tools=default_tools)

    context_builder = ContextBuilder()

    account_service = AccountService()
    session_manager = SessionManager()

    channel_router = ChannelRouter()

    return (
        resource_pool,
        sandbox_manager,
        context_builder,
        account_service,
        session_manager,
        channel_router,
    )


async def start_worker(config: dict) -> None:
    """
    Full startup: init deps → connect Temporal → start Worker + channel listeners.

    The Worker handles the OrchestrationWorkflow and Harness Activities
    on the hpagent-task-queue. Channel listeners feed incoming messages
    into new/existing OrchestrationWorkflow executions keyed by account_id.
    """
    (
        pool,
        sandbox_mgr,
        ctx_builder,
        account_service,
        session_manager,
        channel_router,
    ) = await init_dependencies(config)

    # Inject into Harness Activities (brain operations)
    inject(
        context_builder=ctx_builder,
        resource_pool=pool,
        sandbox_manager=sandbox_mgr,
        channel_router=channel_router,
    )

    # Connect to Temporal Server
    temporal_host = config["temporal_host"]
    client = await Client.connect(temporal_host)

    # Start Worker
    worker = Worker(
        client,
        task_queue="hpagent-task-queue",
        workflows=[OrchestrationWorkflow],
        activities=[
            build_context_activity,
            get_available_tools_activity,
            call_model_activity,
            execute_tool_activity,
            send_response_activity,
        ],
    )

    # ── Channel: NapCat ──
    napcat = NapCatChannel()
    channel_router.register(ChannelType.NAPCAT, napcat)

    async def handle_message(message: UnifiedMessage) -> None:
        """Route incoming message: resolve account → find session → start/signal Workflow."""
        if not message.content or not message.content.strip():
            return

        ch_type = (
            message.channel_type.value
            if hasattr(message.channel_type, "value")
            else str(message.channel_type)
        )

        # 1. Channel-specific ID → unified account ID
        account_id = await account_service.resolve(ch_type, message.sender_id)

        # 2. Find or create active session for this account
        active_sessions = await session_manager.list_active_sessions(limit=1)
        session_id = ""
        for s in active_sessions:
            if hasattr(s, "account_id") and s.account_id == account_id:
                session_id = s.session_id
                break

        if not session_id:
            session_id = await session_manager.create_session_with_id(
                creator_id=message.sender_id,
                channel_type=message.channel_type,
                account_id=account_id,
            )

        # 3. workflow_id based on unified account — QQ and Web share the same workflow
        workflow_id = f"agent-{account_id}"

        # 4. Build message dict with full context
        user_message = {
            "content": message.content,
            "sender_id": message.sender_id,
            "channel_type": ch_type,
            "session_id": session_id,
            "account_id": account_id,
            "metadata": message.metadata,
            "timestamp": message.timestamp,
        }

        from temporalio.exceptions import WorkflowAlreadyStartedError
        try:
            # 先尝试启动新工作流
            handle = await client.start_workflow(
                OrchestrationWorkflow.run,
                user_message,
                id=workflow_id,
                task_queue="hpagent-task-queue",
            )
            logger.info(f"Started new orchestration {workflow_id}")
        except WorkflowAlreadyStartedError:
            # 已存在运行中的工作流 → 获取句柄并发送信号
            handle = client.get_workflow_handle(workflow_id)
            await handle.signal(OrchestrationWorkflow.new_message, user_message)
            logger.info(f"Signaled existing orchestration {workflow_id}")
        except Exception as e:
            logger.exception(f"Failed to start or signal workflow {workflow_id}")
            # 可选：通知用户错误
            pass
            

    # Run Worker + channel listener concurrently
    async with worker:
        await napcat.start_monitor(handle_message)
        logger.info(
            "Orchestration Worker started on task_queue='hpagent-task-queue', "
            "NapCat listening on ws://0.0.0.0:8082"
        )
        await asyncio.Future()
