"""
Orchestration Worker —— Temporal Worker 启动与依赖初始化。

启动序列（共 7 步）:
  1. 加载 config.yaml → AppConfig
  2. 初始化 Redis 连接（按需）
  3. 初始化 nsjail 沙箱配置
  4. 初始化所有依赖 (AccountService / TemporalSessionManager / ResourcePool /
     SandboxManager / ContextBuilder / ChannelRouter)
  5. 将依赖注入到 Harness Activities（大脑拆解后的无状态操作）
  6. 连接到 Temporal Server
  7. 启动 Worker，轮询 hpagent-task-queue，执行 Workflow + Activities
  8. 启动渠道监听器 → 每条消息启动或 signal 一个 OrchestrationWorkflow

v6 变更:
  - 新增 Redis 连接初始化（用于工具执行结果持久化）
  - 新增 NsjailConfig 配置（用于 nsjail 子进程隔离执行）
  - SandboxManager 接受 nsjail 配置 + Redis 客户端

跨客户端记忆:
  - AccountService.resolve() → channel_type + sender_id → 统一 account_id
  - workflow_id = f"agent-{account_id}"（QQ 和 Web 共享同一个 Workflow）
  - ChannelRouter 根据 msg.channel_type 路由响应到正确渠道
"""
import asyncio
import logging
import os
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
    get_tool_result_activity,
)
from orchestration.workflow import OrchestrationWorkflow
from harness.context_builder import HarnessContextBuilder as ContextBuilder
from resources.resource_pool import ResourcePool
from resources.credentials import CredentialManager, ModelEndpoint
from sandbox.sandbox_manager import SandboxManager
from sandbox.nsjail import NsjailConfig
from sandbox.tools.factory import ToolFactory
from sandbox.channels.napcat import NapCatChannel
from sandbox.channels.router import ChannelRouter
from account.account_service import AccountService
from session.session_manager import TemporalSessionManager
from workspace.manager import WorkspaceManager
from common.types import UnifiedMessage, ChannelType, SessionMetadata

logger = logging.getLogger("HpAgent.OrchestrationWorker")


async def init_dependencies(config: dict) -> tuple:
    """初始化所有共享依赖并返回供注入。

    依赖清单:
      - CredentialManager: 管理模型 API 密钥（加密存储 + 临时 token）
      - ResourcePool:      模型调用池（退避链）
      - redis_cache:       Redis 缓存客户端（None 时回退为不持久化）
      - nsjail_config:     nsjail 沙箱配置
      - SandboxManager:    沙箱管理（工具注册 + nsjail 执行 + 生命周期）
      - ContextBuilder:    上下文构建器（事件 → LLM messages）
      - AccountService:    渠道 ID → 统一账号 ID 解析
      - TemporalSessionManager: 会话管理（通过 Workflow Queries 读取）
      - ChannelRouter:     多渠道响应路由

    Returns:
        (resource_pool, sandbox_manager, context_builder, account_service,
         session_manager, channel_router, redis_cache)
    """
    # ── 凭据管理: 注册模型 API 密钥 ──
    credential_manager = CredentialManager()
    credential_manager.register_model_chain([
        ModelEndpoint(
            provider="anthropic",
            api_key=config["api_key"],
            base_url=config["base_url"],
            model=config["model"],
        ),
    ])

    # ── 资源池: 加载模型客户端 ──
    resource_pool = ResourcePool(credential_manager)
    await resource_pool.initialize_models()

    # ── Redis 连接（按需启用） ──
    redis_cache = None
    redis_url = config.get("redis_url", os.getenv("REDIS_URL", ""))
    if redis_url:
        try:
            import redis.asyncio as aioredis
            from storage.redis import RedisCache
            redis_client = aioredis.from_url(redis_url, decode_responses=False)
            redis_cache = RedisCache(redis_client)
            logger.info("Redis connected for sandbox result persistence: %s", redis_url)
        except Exception as e:
            logger.warning("Failed to connect Redis (%s), sandbox results will not be persisted: %s", redis_url, e)

    # ── nsjail 沙箱配置 ──
    nsjail_config = NsjailConfig(
        nsjail_binary=config.get("nsjail_binary", "/usr/bin/nsjail"),
        chroot_path=config.get("sandbox_chroot", "/"),
        work_dir=config.get("sandbox_work_dir", "/work"),
        runner_script=config.get("sandbox_runner", "/work/runner.py"),
        python_binary=config.get("sandbox_python", "/usr/bin/python3"),
        time_limit=config.get("sandbox_timeout", 30),
        memory_limit_mb=config.get("sandbox_memory_mb", 256),
        cpu_limit_seconds=config.get("sandbox_cpu_seconds", 10),
        max_processes=config.get("sandbox_max_procs", 32),
        max_files=config.get("sandbox_max_files", 64),
        disable_proc=config.get("sandbox_disable_proc", True),
        disable_network=config.get("sandbox_disable_network", True),
        readonly_root=config.get("sandbox_readonly_root", True),
    )
    logger.info(
        "Nsjail configured: binary=%s, chroot=%s, timeout=%ds",
        nsjail_config.nsjail_binary,
        nsjail_config.chroot_path,
        nsjail_config.time_limit,
    )

    # ── 工作区: 多用户持久化工作目录 ──
    from pathlib import Path
    workspace_root = config.get("workspace_root", "users_workspace")
    workspace_db = config.get("workspace_db", "")
    workspace_manager = WorkspaceManager(
        root=Path(workspace_root),
        db_path=workspace_db or None,
    )
    logger.info("WorkspaceManager initialized: root=%s", workspace_manager.root)

    # ── 沙箱: 创建默认沙箱并注册内置工具 ──
    sandbox_manager = SandboxManager(
        nsjail_config=nsjail_config,
        redis_cache=redis_cache,
        workspace_manager=workspace_manager,
    )
    default_tools = ToolFactory.create_default_tools()
    sandbox_manager.create_sandbox(tools=default_tools)

    # ── 上下文构建器 ──
    context_builder = ContextBuilder()

    # ── 账号服务 ──
    account_service = AccountService()

    # ── 会话管理: 使用 TemporalSessionManager（通过 Workflow Query 读事件） ──
    session_manager = TemporalSessionManager()

    # ── 渠道路由器 ──
    channel_router = ChannelRouter()

    return (
        resource_pool,
        sandbox_manager,
        context_builder,
        account_service,
        session_manager,
        channel_router,
        redis_cache,
        workspace_manager,
    )


async def start_worker(config: dict) -> None:
    """完整启动流程: 初始化依赖 → 连接 Temporal → 启动 Worker + 渠道监听。

    Worker 在 hpagent-task-queue 上执行:
      - Workflow: OrchestrationWorkflow（agentic loop 编排）
      - Activities: 6 个 Harness Activity（含 nsjail 工具执行 + Redis 持久化）

    渠道监听器将所有进入的消息通过 account_id 路由到
    对应的新/已有 OrchestrationWorkflow 实例。
    """
    (
        pool,
        sandbox_mgr,
        ctx_builder,
        account_service,
        session_manager,
        channel_router,
        redis_cache,
        workspace_manager,
    ) = await init_dependencies(config)

    # 注入依赖到 Harness Activities（模块级单例）
    inject(
        context_builder=ctx_builder,
        resource_pool=pool,
        sandbox_manager=sandbox_mgr,
        channel_router=channel_router,
        redis_cache=redis_cache,
        workspace_manager=workspace_manager,
    )

    # ── 连接到 Temporal Server ──
    temporal_host = config["temporal_host"]
    client = await Client.connect(temporal_host)

    # ── 创建并启动 Worker ──
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
            get_tool_result_activity,
        ],
    )

    # ── 注册 NapCat 渠道 ──
    napcat = NapCatChannel()
    channel_router.register(ChannelType.NAPCAT, napcat)

    async def handle_message(message: UnifiedMessage) -> None:
        """渠道消息回调: 解析账号 → 查找会话 → 启动/Signal Workflow。

        处理流程:
          1. 渠道空消息过滤
          2. channel_type + sender_id → account_id（AccountService）
          3. 查找该 account 的活跃会话（TemporalSessionManager）
          4. 无活跃会话 → 创建新会话
          5. workflow_id = f"agent-{account_id}"
          6. 尝试 start_workflow；若已存在（WorkflowAlreadyStartedError）→ signal
        """
        if not message.content or not message.content.strip():
            return

        ch_type = (
            message.channel_type.value
            if hasattr(message.channel_type, "value")
            else str(message.channel_type)
        )

        account_id = await account_service.resolve(ch_type, message.sender_id)

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

        workflow_id = f"agent-{account_id}"

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
            # 为新 Workflow 创建会话工作区并挂载到 nsjail
            workspace_manager.ensure_user(account_id)
            ws_session = workspace_manager.create_session(
                user_uuid=account_id,
                session_id=session_id,
                task_summary=message.content[:100],
            )
            sandbox_mgr.create_session_sandbox(
                user_uuid=account_id,
                session_id=session_id,
                tools=ToolFactory.create_default_tools(),
            )
            user_message["workspace_user_uuid"] = account_id
            user_message["workspace_session_id"] = session_id

            handle = await client.start_workflow(
                OrchestrationWorkflow.run,
                user_message,
                id=workflow_id,
                task_queue="hpagent-task-queue",
            )
            logger.info(f"Started new orchestration {workflow_id}")
        except WorkflowAlreadyStartedError:
            handle = client.get_workflow_handle(workflow_id)
            await handle.signal(OrchestrationWorkflow.new_message, user_message)
            logger.info(f"Signaled existing orchestration {workflow_id}")
        except Exception as e:
            logger.exception(f"Failed to start or signal workflow {workflow_id}")

    # ── 并发运行 Worker + 渠道监听 ──
    async with worker:
        await napcat.start_monitor(handle_message)
        logger.info(
            "Orchestration Worker started on task_queue='hpagent-task-queue', "
            "NapCat listening on ws://0.0.0.0:8082"
        )
        await asyncio.Future()
