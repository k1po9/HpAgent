"""
Orchestration Worker — boots the Temporal Worker that executes the OrchestrationWorkflow.

Boot sequence:
  1. Load config
  2. Initialize dependencies (ResourcePool, SandboxManager, ContextBuilder, Channel)
  3. Inject dependencies into Harness Activities (the brain's decomposed operations)
  4. Connect to Temporal Server
  5. Start Worker polling the task queue, executing Workflows + Activities
  6. Start channel listeners → each message starts/signals an OrchestrationWorkflow
"""
import asyncio
import logging
from typing import Optional, Callable, Awaitable, Dict, Any

from temporalio.client import Client
from temporalio.worker import Worker

from harness.activities import inject, build_context_activity, get_available_tools_activity, call_model_activity, execute_tool_activity, send_response_activity
from orchestration.workflow import OrchestrationWorkflow
from harness.context_builder import ContextBuilder
from resources.resource_pool import ResourcePool
from resources.credentials import CredentialManager, ModelEndpoint
from sandbox.sandbox_manager import SandboxManager
from sandbox.tools.factory import ToolFactory
from sandbox.channels.napcat import NapCatChannel
from common.types import UnifiedMessage, ChannelType

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

    return resource_pool, sandbox_manager, context_builder


async def start_worker(config: dict) -> None:
    """
    Full startup: init deps → connect Temporal → start Worker + channel listeners.

    The Worker handles the OrchestrationWorkflow and Harness Activities
    on the hpagent-task-queue. Channel listeners feed incoming messages
    into new OrchestrationWorkflow executions.
    """
    pool, sandbox_mgr, ctx_builder = await init_dependencies(config)

    # Inject into Harness Activities (brain operations)
    inject(
        context_builder=ctx_builder,
        resource_pool=pool,
        sandbox_manager=sandbox_mgr,
        channel=None,
    )

    # Connect to Temporal Server
    temporal_host = config.get("temporal_host", "localhost:7233")
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

    async def handle_napcat_message(message: UnifiedMessage) -> None:
        """Start a new OrchestrationWorkflow for each incoming NapCat message."""
        if not message.content or not message.content.strip():
            return
        user_message = {
            "content": message.content,
            "sender_id": message.sender_id,
            "channel_type": message.channel_type.value
                if hasattr(message.channel_type, "value") else str(message.channel_type),
            "session_id": message.session_id,
            "metadata": message.metadata,
            "timestamp": message.timestamp,
        }
        workflow_id = f"hpagent-{message.sender_id}"
        try:
            handle = client.get_workflow_handle(workflow_id)
            logger.info(f"Signaling existing orchestration {workflow_id}")
        except Exception:
            logger.info(f"Starting new orchestration {workflow_id}")
            await client.start_workflow(
                OrchestrationWorkflow.run,
                user_message,
                id=workflow_id,
                task_queue="hpagent-task-queue",
            )

    # Inject channel for send_response_activity
    inject(
        context_builder=ctx_builder,
        resource_pool=pool,
        sandbox_manager=sandbox_mgr,
        channel=napcat,
    )

    # Run Worker + channel listener concurrently
    async with worker:
        await napcat.start_monitor(handle_napcat_message)
        logger.info(
            "Orchestration Worker started on task_queue='hpagent-task-queue', "
            f"NapCat listening on ws://0.0.0.0:8082"
        )
        await asyncio.Future()
