"""
Orchestration Worker —— Temporal Worker 启动与依赖组装。

启动序列:
  1. AppConfig.from_yaml() → 加载全量结构化配置
  2. init_dependencies()    → 按 config 组装所有依赖
  3. inject(harness)         → 注入 HarnessRunner 到 Activities
  4. 连接 Temporal → 启动 Worker → 渠道监听

架构:
  Temporal Workflow （纯编排）
      ↓ 只调用 Harness Activities
  HarnessRunner    （无状态协调器）
      ↓ 协调
  SessionStore / ContextBuilder / ResourcePool / SandboxManager / ChannelRouter
"""
import asyncio
import dataclasses
import logging
import os
from pathlib import Path
from typing import Dict

from temporalio.client import Client
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from orchestration.config import AppConfig, SandboxConfig
from orchestration.workflow import OrchestrationWorkflow, ReflectWorkflow, MetricsReportWorkflow
from harness.activities import (
    inject,
    process_turn_activity,
    archive_session_activity,
    reflect_activity,
    reflect_batch_activity,
    metrics_report_activity,
)
from harness.context_builder import HarnessContextBuilder
from harness.prompts import PromptLoader
from harness.runner import HarnessRunner
from session.store import SessionStore
from session.db import WorkspaceDB
from session.workspace import init_user, init_session
from storage.file_store import LocalFileStore
from resources.resource_pool import ResourcePool
from resources.credentials import CredentialManager, ModelEndpoint
from sandbox.sandbox_manager import SandboxManager
from sandbox.nsjail import NsjailConfig
from sandbox.channels.napcat import NapCatChannel
from sandbox.channels.router import ChannelRouter
from account.account_service import AccountService
from common.types import UnifiedMessage, ChannelType

logger = logging.getLogger("HpAgent.OrchestrationWorker")


@dataclasses.dataclass
class WorkerDependencies:
    """init_dependencies() 的返回值 —— 所有组装好的共享依赖。

    使用 dataclass 而非裸 tuple，避免位置耦合，便于后续扩展。
    """
    harness_runner: "HarnessRunner"
    account_service: "AccountService"
    channel_router: "ChannelRouter"
    sandbox_manager: "SandboxManager"
    file_store: "LocalFileStore"
    workspace_db: "WorkspaceDB"
    workspace_root: Path
    mcp_manager: object  # MCPToolManager | None，用于 shutdown cleanup
    resource_pool: "ResourcePool"


async def setup_tools(config: AppConfig):
    """启动时加载共享工具基础设施（MCP + Skills + RAG）。

    本地工具（fs_read 等）在 per-session 沙箱创建时才实例化，
    因为它们需要绑定 workspace 路径。

    Returns:
        (mcp_manager, skill_definitions, retriever)
    """
    # RAG 检索器
    retriever = None
    if config.models.tool_rag.enabled:
        try:
            from sandbox.tools.retriever import ToolVectorStore, ToolRetriever
            from resources.embedding import create_embedding_client
            emb_client = create_embedding_client(config.models)
            vector_store = ToolVectorStore(persist_path=config.models.tool_rag.persist_path)
            retriever = ToolRetriever(vector_store, emb_client)
            logger.info("Tool RAG enabled: persist=%s top_k=%d",
                        config.models.tool_rag.persist_path, config.models.tool_rag.top_k)
        except Exception as e:
            logger.warning("Tool RAG init failed, falling back to full injection: %s", e)

    # MCP 工具（可选，默认不连接）
    mcp_mgr = None
    if config.models.mcp.auto_connect:
        try:
            from sandbox.tools.adapters.mcp import MCPToolManager
            mcp_mgr = MCPToolManager(config_path=config.models.mcp.config_path)
            await mcp_mgr.load_config()
            await mcp_mgr.connect()
            logger.info("MCP tools registered: %d servers, %d tools",
                        mcp_mgr.server_count, len(mcp_mgr.get_cached_tools()))
        except Exception as e:
            logger.warning("MCP tools loading failed: %s", e)
            mcp_mgr = None

    # Skills（可选）
    skill_definitions = []
    if config.models.skills.enabled:
        try:
            import yaml
            from pathlib import Path
            skills_path = Path(config.models.skills.config_path)
            for skill_file in skills_path.glob("*.yaml"):
                skill_def = yaml.safe_load(skill_file.read_text())
                skill_definitions.append(skill_def)
            logger.info("Skills loaded: %d from %s", len(skill_definitions), skills_path)
        except Exception as e:
            logger.warning("Skills loading failed: %s", e)

    return mcp_mgr, skill_definitions, retriever


def _build_nsjail_config(sandbox: SandboxConfig) -> NsjailConfig:
    """从 SandboxConfig 构建 NsjailConfig。

    只传递 SandboxConfig 中与 NsjailConfig 字段名匹配的值，
    其余字段使用 NsjailConfig 自身默认值。
    """
    nsjail_fields = {f.name for f in dataclasses.fields(NsjailConfig)}
    kwargs = {
        k: v for k, v in dataclasses.asdict(sandbox).items()
        if k in nsjail_fields
    }
    return NsjailConfig(**kwargs)


async def init_dependencies(config: AppConfig) -> WorkerDependencies:
    """初始化所有共享依赖并组装 HarnessRunner。

    初始化顺序（严格遵守拓扑依赖 DAG）：
      1. CredentialManager → ResourcePool
      2. Redis（可选）
      3. NsjailConfig
      4. setup_tools() → mcp_mgr, skill_definitions, retriever
      5. workspace_root + LocalFileStore + WorkspaceDB
      6. SandboxManager（依赖 3+4+5）
      7. PromptLoader → HindsightClient → HarnessContextBuilder
      8. AccountService + ChannelRouter
      9. SessionStore（依赖 2+7）
     10. MultiAgentExecutor（条件）
     11. HarnessRunner（组装所有上述组件）
    """
    # ── 1. 凭据 + 资源池 ──
    credential_manager = CredentialManager()
    all_endpoints: list[ModelEndpoint] = []
    category_ids: Dict[str, list[str]] = {}

    for category in ("chat", "embedding", "image", "reasoning"):
        chain = config.models.get_chain(category)
        if not chain:
            continue
        ids: list[str] = []
        for entry in chain:
            ep = config.models.resolve_endpoint(entry)
            model_id = f"{ep.provider}:{ep.model}"
            all_endpoints.append(ep)
            ids.append(model_id)
        if ids:
            category_ids[category] = ids
    if not all_endpoints:
        raise RuntimeError("No models configured in models.yaml")

    credential_manager.register_model_chain(all_endpoints)
    resource_pool = ResourcePool(credential_manager)
    await resource_pool.initialize_models()
    for category, ids in category_ids.items():
        resource_pool.configure_fallback_group(category, ids)
    if "chat" in category_ids:
        resource_pool.configure_fallback_group("default", category_ids["chat"])
        logger.info("Model chains registered: %s", ", ".join(
            f"{c}={len(ids)}" for c, ids in category_ids.items()
        ))

    # ── 2. Redis ──
    redis_cache = None
    redis_url = config.redis.url or os.getenv("REDIS_URL", "")
    if redis_url:
        try:
            import redis.asyncio as aioredis
            from storage.redis import RedisCache
            redis_client = aioredis.from_url(redis_url, decode_responses=False)
            redis_cache = RedisCache(redis_client, default_ttl=config.redis.default_ttl)
            logger.info("Redis connected: %s", redis_url)
        except Exception as e:
            logger.warning("DEGRADATION: Redis unavailable (%s) → falling back to in-memory storage", e)

    # ── 3. nsjail 配置 ──
    nsjail_config = _build_nsjail_config(config.sandbox)
    logger.info(
        "Nsjail configured: time=%ds mem=%dMB cpu=%ds net=%s",
        nsjail_config.time_limit,
        nsjail_config.memory_limit_mb,
        nsjail_config.cpu_limit_seconds,
        "off" if nsjail_config.disable_network else "on",
    )

    # ── 4. 共享工具基础设施（MCP + Skills + RAG）────
    # 必须在 SandboxManager 之前调用，因为 SandboxManager 需要这些产物
    mcp_mgr, skill_definitions, retriever = await setup_tools(config)

    # ── 5. 工作区存储 ──
    workspace_root = Path(config.workspace.root)
    file_store = LocalFileStore(workspace_root)
    workspace_db = WorkspaceDB(config.workspace.db_path or str(workspace_root / "workspace.db"))
    logger.info("Workspace storage initialized: root=%s", workspace_root)

    # ── 6. 沙箱管理器（依赖 3+4+5）────
    sandbox_manager = SandboxManager(
        nsjail_config=nsjail_config,
        redis_cache=redis_cache,
        data_root=workspace_root,
        max_idle_seconds=config.sandbox.max_idle_seconds,
        mcp_manager=mcp_mgr,
        skill_definitions=skill_definitions,
        retriever=retriever,
    )

    # ── 7. Prompt + Hindsight + 上下文构建器 ──
    prompt_loader = PromptLoader(config.prompts)
    logger.info("PromptLoader initialized from AppConfig.prompts")

    from memory.hindsight_client import HindsightClient
    hindsight_client = None
    if config.hindsight.enabled:
        try:
            hindsight_client = HindsightClient(
                base_url=config.hindsight.base_url,
                api_key=config.hindsight.api_key,
                timeout=config.hindsight.timeout,
                prompt_loader=prompt_loader,
                retain_mission=config.hindsight.retain_mission,
                reflect_mission=config.hindsight.reflect_mission,
            )
            logger.info("HindsightClient initialized: base_url=%s", hindsight_client.base_url)
        except Exception as e:
            logger.warning("DEGRADATION: Hindsight unavailable (%s) → long-term memory disabled", e)

    context_builder = HarnessContextBuilder(
        prompt_loader=prompt_loader,
        enable_context_files=True,
        enable_tool_guidance=True,
    )

    # ── 8. 账号服务 + 渠道路由器 ──
    account_service = AccountService(data_dir=Path(config.workspace.root).parent)
    channel_router = ChannelRouter()

    # ── 9. SessionStore ──
    backup_store = None
    if config.session.backup_dir:
        backup_store = LocalFileStore(root=Path(config.session.backup_dir))
    session_store = SessionStore(
        redis_cache=redis_cache,
        hindsight_client=hindsight_client,
        file_store=backup_store,
    )
    logger.info(
        "SessionStore: redis=%s hindsight=%s backup=%s",
        "connected" if redis_cache else "in-memory fallback",
        "connected" if hindsight_client else "disabled",
        config.session.backup_dir if backup_store else "disabled",
    )

    # ── 10. Multi-Agent Executor（mode=multi 时加载）────
    multi_agent_executor = None
    if config.agent.mode == "multi" and config.agents:
        from agent.runner import MultiAgentExecutor
        agents_list = [dataclasses.asdict(a) for a in config.agents]
        multi_agent_executor = MultiAgentExecutor(
            resource_pool=resource_pool,
            agents_config=agents_list,
            strategy=config.agent.multi_agent.strategy,
            max_review_rounds=config.agent.multi_agent.max_review_rounds,
        )
        logger.info(
            "MultiAgentExecutor: strategy=%s agents=%d",
            config.agent.multi_agent.strategy, len(config.agents),
        )
    elif config.agent.mode == "multi":
        logger.warning("No agents defined in config, falling back to single-agent")

    # ── 11. HarnessRunner ──
    harness_runner = HarnessRunner(
        session_store=session_store,
        context_builder=context_builder,
        resource_pool=resource_pool,
        sandbox_manager=sandbox_manager,
        channel_router=channel_router,
        max_tool_turns=config.agent.max_tool_turns,
        agent_mode=config.agent.mode,
        multi_agent_executor=multi_agent_executor,
    )

    logger.info("HarnessRunner assembled: all dependencies wired")
    return WorkerDependencies(
        harness_runner=harness_runner,
        account_service=account_service,
        channel_router=channel_router,
        sandbox_manager=sandbox_manager,
        file_store=file_store,
        workspace_db=workspace_db,
        workspace_root=workspace_root,
        mcp_manager=mcp_mgr,
        resource_pool=resource_pool,
    )


async def start_worker(config: AppConfig) -> None:
    """完整启动流程: 组装依赖 → 连接 Temporal → 启动 Worker + 渠道监听。"""
    deps = await init_dependencies(config)

    inject(harness=deps.harness_runner)

    # ── 连接 Temporal ──
    client = await Client.connect(config.temporal.host)

    # ── 创建 Worker ──
    worker = Worker(
        client,
        task_queue=config.temporal.task_queue,
        workflows=[OrchestrationWorkflow, ReflectWorkflow, MetricsReportWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
        activities=[
            process_turn_activity,
            archive_session_activity,
            reflect_activity,
            reflect_batch_activity,
            metrics_report_activity,
        ],
    )

    # ── 渠道注册 ──
    napcat = NapCatChannel()
    deps.channel_router.register(ChannelType.NAPCAT, napcat)

    async def handle_message(message: UnifiedMessage) -> None:
        if not message.content or not message.content.strip():
            return

        ch_type = (
            message.channel_type.value
            if hasattr(message.channel_type, "value")
            else str(message.channel_type)
        )

        account_id = await deps.account_service.resolve(ch_type, message.sender_id)
        session_id = f"session-{account_id}"

        user_message = {
            "content": message.content,
            "sender_id": message.sender_id,
            "channel_type": ch_type,
            "session_id": session_id,
            "account_id": account_id,
            "metadata": message.metadata,
            "timestamp": message.timestamp,
        }

        # 准备工作区 + 沙箱（基础设施前置，幂等）
        workspace_path = str(deps.workspace_root / account_id / "sessions" / session_id / "workspace")
        try:
            init_user(deps.file_store, deps.workspace_db, account_id)
            init_session(
                deps.file_store, deps.workspace_db,
                user_uuid=account_id,
                session_id=session_id,
                task_summary=message.content[:100],
            )
            deps.sandbox_manager.create_session_sandbox(
                session_id=session_id,
                workspace_path=workspace_path,
                user_uuid=account_id,
            )
        except Exception as e:
            logger.warning("Workspace/sandbox setup failed for %s: %s", session_id, e)

        from temporalio.exceptions import WorkflowAlreadyStartedError
        try:
            await client.start_workflow(
                OrchestrationWorkflow.run,
                user_message,
                id=session_id,
                task_queue=config.temporal.task_queue,
            )
            logger.info("Started new session %s (account=%s)", session_id, account_id)
        except WorkflowAlreadyStartedError:
            handle = client.get_workflow_handle(session_id)
            await handle.signal(OrchestrationWorkflow.new_message, user_message)
            logger.info("Signaled existing session %s", session_id)
        except Exception as e:
            logger.exception("Failed to start or signal session %s: %s", session_id, e)

    # ── 并发运行 Worker + 渠道监听 ──
    sandbox_cleanup_task = asyncio.create_task(
        _run_sandbox_cleanup_loop(deps.sandbox_manager, interval=300)
    )

    async with worker:
        await napcat.start_monitor(handle_message)
        logger.info(
            "Orchestration Worker started on task_queue='%s'",
            config.temporal.task_queue,
        )

        # 设置定期记忆反思 Schedule
        await _setup_reflect_schedule(client, deps.account_service, config)

        # 设置定期指标报告 Schedule（每 30 分钟）
        await _setup_metrics_schedule(client, config)

        logger.info(
            "Periodic schedules configured: reflect=every-%dh metrics=every-30m",
            config.agent.reflect_interval_hours,
        )

        await asyncio.Future()

    # ── Worker shutdown cleanup ──
    logger.info("Worker shutting down, cleaning up resources...")

    try:
        await napcat.stop_monitor()
        logger.info("NapCat monitor stopped")
    except Exception as e:
        logger.warning("NapCat stop_monitor failed: %s", e)

    sandbox_cleanup_task.cancel()
    try:
        await sandbox_cleanup_task
    except asyncio.CancelledError:
        pass

    if deps.mcp_manager is not None:
        try:
            await deps.mcp_manager.disconnect()
            logger.info("MCP connections closed")
        except Exception as e:
            logger.warning("MCP disconnect failed: %s", e)

    logger.info("Worker shutdown complete")


async def _run_sandbox_cleanup_loop(sandbox_manager, interval: int = 300) -> None:
    """后台任务：每 interval 秒清理一次闲置沙箱。"""
    while True:
        try:
            await asyncio.sleep(interval)
            cleaned = sandbox_manager.cleanup_idle_sandboxes()
            if cleaned > 0:
                logger.info("Sandbox cleanup: %d idle sandboxes destroyed", cleaned)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Sandbox cleanup error: %s", e)


async def _setup_reflect_schedule(client, account_service, config) -> None:
    """创建 Temporal Schedule，每 6 小时触发一次 ReflectWorkflow。

    ReflectWorkflow 遍历所有已知账号，调用 reflect_activity
    触发 Hindsight 深度推理（记忆关联、矛盾检测、知识抽象、经验总结）。
    """
    from datetime import timedelta
    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleSpec,
        ScheduleIntervalSpec,
        ScheduleOverlapPolicy,
    )

    schedule_id = "hpagent-reflect-schedule"
    try:
        account_ids = account_service.list_all_ids()
        if not account_ids:
            logger.info("Reflect schedule skipped: no accounts registered")
            return

        await client.create_schedule(
            schedule_id,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    ReflectWorkflow.run,
                    args=[account_ids],
                    id=f"hpagent-reflect-{len(account_ids)}",
                    task_queue=config.temporal.task_queue,
                ),
                spec=ScheduleSpec(
                    intervals=[ScheduleIntervalSpec(
                        every=timedelta(hours=config.agent.reflect_interval_hours)
                    )]
                ),
                policy=ScheduleOverlapPolicy.SKIP,
            ),
        )
        logger.info(
            "Reflect schedule created: id=%s every=%dh accounts=%d",
            schedule_id, config.agent.reflect_interval_hours, len(account_ids),
        )
    except Exception as e:
        logger.warning("Failed to create reflect schedule (may already exist): %s", e)


async def _setup_metrics_schedule(client, config) -> None:
    """创建 Temporal Schedule，每 30 分钟触发一次 MetricsReportWorkflow。

    MetricsReportWorkflow 调用 metrics_report_activity 采集 Hindsight
    可观测性指标并以结构化 JSON 日志输出，供外部监控系统（Prometheus/
    Grafana/ELK）采集。
    """
    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleSpec,
        ScheduleIntervalSpec,
        ScheduleOverlapPolicy,
    )
    from datetime import timedelta

    schedule_id = "hpagent-metrics-schedule"
    try:
        await client.create_schedule(
            schedule_id,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    MetricsReportWorkflow.run,
                    id="hpagent-metrics-report",
                    task_queue=config.temporal.task_queue,
                ),
                spec=ScheduleSpec(
                    intervals=[ScheduleIntervalSpec(every=timedelta(minutes=30))]
                ),
                policy=ScheduleOverlapPolicy.SKIP,
                notes="Hindsight 可观测性指标报告 —— 每 30 分钟采集一次",
            ),
        )
        logger.info("Metrics report schedule created: id=%s every=30m", schedule_id)
    except Exception as e:
        logger.warning("Failed to create metrics schedule (may already exist): %s", e)
