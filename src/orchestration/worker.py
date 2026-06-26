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
import uuid
from pathlib import Path
from typing import Dict

from temporalio.client import Client
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from orchestration.config import AppConfig, SandboxConfig
from orchestration.scheduler import TaskScheduler
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
from sandbox.git_repo import GitRepoManager
from sandbox.channels.napcat import NapCatChannel
from sandbox.channels.official_qq import OfficialQQChannel
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
    git_repo_manager: "GitRepoManager"
    group_context: object  # GroupContextStore | None，群聊短期上下文缓存
    scheduler: "TaskScheduler" = None


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
            from resources.reranker import create_reranker_client
            emb_client = create_embedding_client(config.models)
            reranker_client = create_reranker_client(config.models)
            vector_store = ToolVectorStore(persist_path=config.models.tool_rag.persist_path)
            retriever = ToolRetriever(vector_store, emb_client, reranker_client=reranker_client)
            logger.info("Tool RAG enabled: persist=%s top_k=%d reranker=%s",
                        config.models.tool_rag.persist_path, config.models.tool_rag.top_k,
                        "enabled" if reranker_client else "disabled")
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

    # Skills（可选）—— 支持两种格式:
    #   1. *.yaml 直接放在 skills_path 下（HpAgent 原生流水线格式）
    #   2. */SKILL.md 放在子目录中（agentskills.io 业界标准格式）
    skill_definitions = []
    if config.models.skills.enabled:
        try:
            import yaml
            from pathlib import Path
            from sandbox.tools.skills.skillmd import parse_skillmd, skillmd_to_definition

            skills_path = Path(config.models.skills.config_path)

            # 1. 加载 HpAgent 原生 YAML 格式 (tools/skills/*.yaml)
            for skill_file in skills_path.glob("*.yaml"):
                skill_def = yaml.safe_load(skill_file.read_text())
                skill_def.setdefault("type", "pipeline")
                skill_definitions.append(skill_def)

            # 2. 加载 SKILL.md 业界标准格式 (tools/skills/*/SKILL.md)
            for skillmd_file in skills_path.glob("*/SKILL.md"):
                try:
                    fm, body = parse_skillmd(skillmd_file)
                    skill_def = skillmd_to_definition(fm, body)
                    skill_definitions.append(skill_def)
                except ValueError as e:
                    logger.warning("SKILL.md parse failed: %s - %s", skillmd_file, e)

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

    for category in ("fast", "chat", "embedding", "image", "reasoning"):
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
    redis_client = None  # 保留引用，供 GroupContextStore 使用
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

    # ── 2b. 群聊短期上下文缓存（依赖 Redis 客户端）──
    group_context = None
    if redis_client:
        from memory.group_context import GroupContextStore
        group_context = GroupContextStore(
            redis_client,
            window_size=config.redis.group_context_window,
            ttl_seconds=config.redis.group_context_ttl,
            density_threshold=config.redis.group_context_density_threshold,
        )
        logger.info(
            "GroupContextStore initialized: window=%d ttl=%ds density_threshold=%.1f",
            config.redis.group_context_window, config.redis.group_context_ttl,
            config.redis.group_context_density_threshold,
        )
    else:
        logger.info("GroupContextStore: skipped (no Redis, group context disabled)")

    # ── 3. nsjail 配置 ──
    if config.sandbox.nsjail_enabled:
        nsjail_config = _build_nsjail_config(config.sandbox)
        logger.info(
            "Nsjail configured: time=%ds mem=%dMB cpu=%ds net=%s",
            nsjail_config.time_limit,
            nsjail_config.memory_limit_mb,
            nsjail_config.cpu_limit_seconds,
            "off" if nsjail_config.disable_network else "on",
        )
    else:
        nsjail_config = None
        logger.info("Nsjail disabled")

    # ── 4. 共享工具基础设施（MCP + Skills + RAG）────
    # 必须在 SandboxManager 之前调用，因为 SandboxManager 需要这些产物
    mcp_mgr, skill_definitions, retriever = await setup_tools(config)

    # ── 5. 工作区存储 ──
    workspace_root = Path(config.workspace.root)
    file_store = LocalFileStore(workspace_root)
    workspace_db = WorkspaceDB(config.workspace.db_path or str(workspace_root / "workspace.db"))
    logger.info("Workspace storage initialized: root=%s", workspace_root)

    # ── 5b. 定时调度器 ──
    scheduler: TaskScheduler = TaskScheduler(data_dir=Path(config.scheduler.data_dir))
    logger.info("TaskScheduler initialized: data_dir=%s poll_interval=%.1fs",
                config.scheduler.data_dir, config.scheduler.poll_interval)

    # ── 6. 沙箱管理器（依赖 3+4+5）────
    sandbox_manager = SandboxManager(
        nsjail_config=nsjail_config,
        redis_cache=redis_cache,
        data_root=workspace_root,
        max_idle_seconds=config.sandbox.max_idle_seconds,
        mcp_manager=mcp_mgr,
        skill_definitions=skill_definitions,
        retriever=retriever,
        max_merged_multiplier=config.models.tool_rag.max_merged_multiplier,
        per_query_min=config.models.tool_rag.per_query_min,
        native_tools_enabled=config.sandbox.native_tools_enabled,
        nsjail_enabled=config.sandbox.nsjail_enabled,
    )

    # ── 7. Prompt + Hindsight + 上下文构建器 ──
    prompt_loader = PromptLoader(config.prompts)
    logger.debug("PromptLoader initialized from AppConfig.prompts")

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
                retain_timeout=config.hindsight.retain_timeout,
                recall_timeout=config.hindsight.recall_timeout,
                reflect_timeout=config.hindsight.reflect_timeout,
            )
            logger.info("HindsightClient initialized: base_url=%s", hindsight_client.base_url)
        except Exception as e:
            logger.warning("DEGRADATION: Hindsight unavailable (%s) → long-term memory disabled", e)

    context_builder = HarnessContextBuilder(
        prompt_loader=prompt_loader,
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
        wal_enabled=config.agent.wal_enabled,
        checkpoint_enabled=config.agent.checkpoint_interval > 0,
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

    # ── 11. GitRepoManager ──
    git_repo_manager = GitRepoManager(repos_root=workspace_root)
    logger.info("GitRepoManager initialized: repos_root=%s", workspace_root)

    # ── 12. HarnessRunner ──
    channel_overrides = {
        ch_name: {"max_tokens": ch_cfg.max_tokens, "timeout": ch_cfg.timeout, "stream": ch_cfg.stream}
        for ch_name, ch_cfg in config.models.channel_overrides.items()
    }
    harness_runner = HarnessRunner(
        session_store=session_store,
        context_builder=context_builder,
        resource_pool=resource_pool,
        sandbox_manager=sandbox_manager,
        channel_router=channel_router,
        max_tool_turns=config.agent.max_tool_turns,
        agent_mode=config.agent.mode,
        multi_agent_executor=multi_agent_executor,
        channel_overrides=channel_overrides,
        git_repo_manager=git_repo_manager,
        workspace_db=workspace_db,
        file_store=file_store,
        # Prompt 配置（工具摘要用）
        prompts=prompt_loader,
        # 上下文工程参数
        context_budget=config.agent.context_budget,
        generation_headroom=config.agent.generation_headroom,
        summary_budget=config.agent.summary_budget,
        memories_budget=config.agent.memories_budget,
        compress_interval=config.agent.compress_interval,
        checkpoint_interval=config.agent.checkpoint_interval,
        tool_result_summary_enabled=config.agent.tool_result_summary_enabled,
        tool_result_summary_threshold=config.agent.tool_result_summary_threshold,
        tool_result_summary_max_chars=config.agent.tool_result_summary_max_chars,
        # 工具 RAG 参数
        tool_rag_top_k=config.models.tool_rag.top_k,
        # 群聊上下文
        group_context=group_context,
    )

    logger.info(
        "HarnessRunner assembled: all dependencies wired"
        " | budget=%d headroom=%d compress=%d ckpt=%d wal=%s rag_top_k=%d",
        harness_runner._context_budget, harness_runner._generation_headroom,
        harness_runner._compress_interval, harness_runner._checkpoint_interval,
        "on" if session_store._wal_enabled else "off",
        harness_runner._tool_rag_top_k,
    )
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
        git_repo_manager=git_repo_manager,
        group_context=group_context,
        scheduler=scheduler,
    )


async def start_worker(config: AppConfig) -> None:
    """完整启动流程: 组装依赖 → 连接 Temporal → 启动 Worker + 渠道监听。"""
    deps = await init_dependencies(config)

    inject(harness=deps.harness_runner)

    # ── 注册提醒 handler ──
    async def _handle_user_reminder(task):
        """发送用户提醒消息。"""
        ch_str = task.params.get("channel_type", "napcat")
        try:
            ch_type = ChannelType(ch_str)
        except ValueError:
            ch_type = ChannelType.NAPCAT

        metadata = task.params.get("metadata", {})
        content = f"[提醒] {task.params.get('content', '')}"

        # 群聊中 @ 回原用户
        sender_id = task.params.get("sender_id", "")
        if metadata.get("detail_type") == "group" and sender_id:
            content = f"[CQ:at,qq={sender_id}] {content}"

        msg = UnifiedMessage(
            session_id=f"reminder-{task.id}",
            account_id=task.params.get("account_id", ""),
            sender_id=sender_id,
            channel_type=ch_type,
            content=content,
            metadata=metadata,
        )
        await deps.channel_router.send(msg)

    deps.scheduler.register_handler("user_reminder", _handle_user_reminder)

    # ── 加载持久化任务 + 注入 scheduler 到 reminder 模块 + 启动轮询 ──
    scheduler_task = None
    if config.scheduler.enabled:
        await deps.scheduler.load()
        from sandbox.tools.local.reminder import inject_scheduler
        inject_scheduler(deps.scheduler)
        scheduler_task = asyncio.create_task(
            deps.scheduler.poll_loop(interval=config.scheduler.poll_interval)
        )
        logger.info("TaskScheduler poll_loop started")
    else:
        logger.info("TaskScheduler disabled by config")

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

    # ── 渠道注册（按 config.yaml 的 channels.enabled 列表动态加载）──
    _channel_factories = {
        ChannelType.NAPCAT: NapCatChannel,
        ChannelType.OFFICIAL_QQ: OfficialQQChannel,
    }

    active_channels: list = []
    for ch_name in config.channels.enabled:
        try:
            ch_type = ChannelType(ch_name)
        except ValueError:
            logger.warning("Unknown channel in config: %s, skipped", ch_name)
            continue

        factory = _channel_factories.get(ch_type)
        if factory is None:
            logger.warning("No implementation for channel: %s, skipped", ch_name)
            continue

        channel = factory()
        if hasattr(channel, "bot_name"):
            channel.bot_name = getattr(config.prompts, "bot_name", "bot")
        deps.channel_router.register(ch_type, channel)
        active_channels.append(channel)
        logger.info("Channel registered: %s", ch_name)

    async def handle_message(message: UnifiedMessage) -> None:
        if not message.content or not message.content.strip():
            return

        ch_type = (
            message.channel_type.value
            if hasattr(message.channel_type, "value")
            else str(message.channel_type)
        )

        # ── 群聊上下文写入 + @过滤 ──
        # 所有群消息（无论是否@bot）都写入短期缓存窗口。
        # 非@消息仅做上下文沉淀，不触发 agentic loop。
        metadata = message.metadata
        detail_type = metadata.get("detail_type", "")
        group_id = str(metadata.get("group_id", ""))
        is_at_bot = metadata.get("is_at_bot", False)

        if detail_type == "group" and group_id and deps.group_context:
            # 写入群短期缓存
            sender_name = metadata.get("sender_name", "")
            sender_id = message.sender_id
            raw_msg_id = metadata.get("message_id")
            msg_id = str(raw_msg_id) if raw_msg_id is not None else ""
            iso_ts = metadata.get("iso_timestamp", "")

            try:
                await deps.group_context.append(
                    group_id=group_id,
                    sender_name=sender_name,
                    sender_id=sender_id,
                    content=message.content,
                    msg_id=msg_id,
                    timestamp=iso_ts,
                )
            except Exception:
                logger.warning("Failed to append group context for group %s", group_id)

            # 非@消息：只记录上下文，不触发 agentic loop
            if not is_at_bot:
                logger.debug(
                    "Group non-@ message from %s in %s (len=%d) → context only, skipped",
                    sender_id, group_id, len(message.content),
                )
                return

        account_id = await deps.account_service.resolve(ch_type, message.sender_id)
        workflow_id = f"hpagent-{account_id}"

        session_context = {
            "account_id": account_id,
            "sender_id": message.sender_id,
            "channel_type": ch_type,
            "metadata": message.metadata,
        }

        from temporalio.exceptions import WorkflowAlreadyStartedError

        session_id = f"session-{account_id}-{uuid.uuid4().hex[:8]}"

        user_message = {
            "content": message.content,
            "sender_id": message.sender_id,
            "channel_type": ch_type,
            "session_id": session_id,
            "account_id": account_id,
            "metadata": message.metadata,
            "timestamp": message.timestamp,
            "idle_timeout_minutes": config.agent.idle_timeout_minutes,
            "activity_timeout": config.agent.activity_timeout,
        }

        try:
            # —— 先尝试创建 workflow（轻量 RPC），成功后再初始化本地资源 ——
            # 这样 WorkflowAlreadyStartedError 时不会产生幽灵 session
            await client.start_workflow(
                OrchestrationWorkflow.run,
                user_message,
                id=workflow_id,
                task_queue=config.temporal.task_queue,
            )

            # workflow 创建成功 → 初始化工作区资源
            repo_path = str(deps.workspace_root / account_id / "repo")
            init_user(deps.file_store, deps.workspace_db, account_id)
            await deps.git_repo_manager.ensure_repo(account_id)
            await deps.git_repo_manager.start_session(account_id, session_id)
            init_session(
                deps.file_store, deps.workspace_db,
                user_uuid=account_id,
                session_id=session_id,
                task_summary=message.content[:100],
            )
            deps.sandbox_manager.create_session_sandbox(
                session_id=session_id,
                workspace_path=repo_path,
                user_uuid=account_id,
                session_context=session_context,
            )
            logger.info("Started new session %s (account=%s)", session_id, account_id)

        except WorkflowAlreadyStartedError:
            # —— 复用已有 workflow：沿用其 session_id，无需新建本地资源 ——
            handle = client.get_workflow_handle(workflow_id)
            signaled = False
            try:
                status = await handle.query(OrchestrationWorkflow.get_status)
                session_id = status.get("session_id", f"session-{account_id}")

                # 确保沙箱存在（重启后内存丢失，需重建）
                sandbox_ok = False
                try:
                    deps.sandbox_manager.get_sandbox_for_session(session_id)
                    sandbox_ok = True
                except Exception:
                    pass

                if not sandbox_ok:
                    try:
                        repo_path = str(deps.workspace_root / account_id / "repo")
                        init_user(deps.file_store, deps.workspace_db, account_id)
                        await deps.git_repo_manager.ensure_repo(account_id)
                        await deps.git_repo_manager.start_session(account_id, session_id)
                        init_session(
                            deps.file_store, deps.workspace_db,
                            user_uuid=account_id,
                            session_id=session_id,
                            task_summary="",
                        )
                        deps.sandbox_manager.create_session_sandbox(
                            session_id=session_id,
                            workspace_path=repo_path,
                            user_uuid=account_id,
                            session_context=session_context,
                        )
                        logger.info("Sandbox recreated for signaled session %s", session_id)
                    except Exception as e:
                        logger.warning("Sandbox recreation failed for %s: %s", session_id, e)

                # 用已有 workflow 的 session_id 覆盖 payload
                user_message["session_id"] = session_id

                await handle.signal(OrchestrationWorkflow.new_message, user_message)
                logger.info("Signaled existing session %s", session_id)
                signaled = True
            except Exception as signal_err:
                # workflow 可能已归档退出（详见 workflow.py 的 drain 竞态窗口），
                # signal 失败时回退到启动新 workflow
                logger.info(
                    "Workflow %s signal failed (%s), starting replacement session",
                    workflow_id, signal_err,
                )

            if not signaled:
                # 启动新 session 替代已结束的 workflow
                session_id = f"session-{account_id}-{uuid.uuid4().hex[:8]}"
                user_message["session_id"] = session_id

                repo_path = str(deps.workspace_root / account_id / "repo")
                init_user(deps.file_store, deps.workspace_db, account_id)
                await deps.git_repo_manager.ensure_repo(account_id)
                await deps.git_repo_manager.start_session(account_id, session_id)
                init_session(
                    deps.file_store, deps.workspace_db,
                    user_uuid=account_id,
                    session_id=session_id,
                    task_summary=user_message["content"][:100],
                )
                deps.sandbox_manager.create_session_sandbox(
                    session_id=session_id,
                    workspace_path=repo_path,
                    user_uuid=account_id,
                    session_context=session_context,
                )

                from temporalio.common import WorkflowIDReusePolicy
                await client.start_workflow(
                    OrchestrationWorkflow.run,
                    user_message,
                    id=workflow_id,
                    task_queue=config.temporal.task_queue,
                    id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
                )
                logger.info("Started replacement session %s (account=%s)", session_id, account_id)

        except Exception as e:
            logger.exception("Failed to start or signal session %s: %s", e)

    # ── 并发运行 Worker + 渠道监听 ──
    sandbox_cleanup_task = asyncio.create_task(
        _run_sandbox_cleanup_loop(deps.sandbox_manager, interval=300)
    )

    async with worker:
        for ch in active_channels:
            await ch.start_monitor(handle_message)

        channel_names = [ch.channel_type.value for ch in active_channels]
        logger.info(
            "Orchestration Worker started on task_queue='%s' (channels: %s)",
            config.temporal.task_queue, ", ".join(channel_names) if channel_names else "none",
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

    for ch in active_channels:
        ch_name = ch.channel_type.value
        try:
            await ch.stop_monitor()
            logger.info("%s monitor stopped", ch_name)
        except Exception as e:
            logger.warning("%s stop_monitor failed: %s", ch_name, e)

    sandbox_cleanup_task.cancel()
    try:
        await sandbox_cleanup_task
    except asyncio.CancelledError:
        pass

    if scheduler_task is not None:
        scheduler_task.cancel()
        try:
            await scheduler_task
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
        SchedulePolicy,
    )

    schedule_id = "hpagent-reflect-schedule"
    try:
        # 先清理旧 schedule 再创建（upsert 语义，消除重启时的 "Schedule already running" 警告）
        try:
            handle = client.get_schedule_handle(schedule_id)
            await handle.delete()
        except Exception:
            pass

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
                policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
            ),
        )
        logger.info(
            "Reflect schedule created: id=%s every=%dh accounts=%d",
            schedule_id, config.agent.reflect_interval_hours, len(account_ids),
        )
    except Exception as e:
        logger.warning("Failed to create reflect schedule: %s", e)


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
        SchedulePolicy,
        ScheduleState,
    )
    from datetime import timedelta

    schedule_id = "hpagent-metrics-schedule"
    try:
        # 先清理旧 schedule 再创建（upsert 语义）
        try:
            handle = client.get_schedule_handle(schedule_id)
            await handle.delete()
        except Exception:
            pass

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
                policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
                state=ScheduleState(note="Hindsight 可观测性指标报告 —— 每 30 分钟采集一次"),
            ),
        )
        logger.info("Metrics report schedule created: id=%s every=30m", schedule_id)
    except Exception as e:
        logger.warning("Failed to create metrics schedule: %s", e)
