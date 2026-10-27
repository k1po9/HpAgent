"""Assemble durable Activities, workers, dispatchers and reconciliation without starting them."""

from __future__ import annotations

import dataclasses
import logging
import os

from bootstrap.worker_dependencies import WorkerDependencies
from orchestration.config import AppConfig

logger = logging.getLogger("HpAgent.OrchestrationWorker")


@dataclasses.dataclass
class DurableRuntimeComposition:
    """Canonical durable workers, dispatchers and reconcilers.

    QQ + Web 同进程与独立 Web Worker 都通过同一组合函数组装，
    使用按 root Execution 隔离的 ``ExecutionResourceService``。
    """

    workers: object  # WebTemporalWorkers
    dispatcher: object  # WebOutboxDispatcher
    reconciler: object  # WebRunReconciler
    memory_retention: object = None  # MemoryRetentionService | None (Phase F)


def compose_durable_runtime(
    client, config: AppConfig, deps: WorkerDependencies
) -> DurableRuntimeComposition:
    """组装 Web lifecycle/agent Worker、Outbox Dispatcher 与 Reconciler。

    Web real Agent 或任一 QQ channel 启用时组装唯一 canonical runtime（C-07 门禁）。
    该方法不启动任何 Worker/后台任务，也不注册 QQ/scheduler/channel/schedule；
    调用方决定进程边界。Web 真实执行会经 ``ExecutionResourceService``
    按 root Execution 准备独立 scratch 和 Sandbox，不取得 Account 执行锁。
    """
    worker_database_url = os.getenv("WORKER_DATABASE_URL")

    from agent_activities.runtime import DurableAgentActivities
    from agent_activities.segments import SegmentActivities
    from agent_activities.store import AgentDataStore
    from application.chat_execution import (
        PostgresWebRequestLoader,
    )
    from application.context_assembly import ContextAssemblyService
    from application.work_context import RunExecutionBindings, RunRequestLoader
    from file_domain.approvals import FileActionApprovalService
    from file_runtime import ResearchMarkdownPublisher
    from orchestration.artifact_activities import ArtifactActivities
    from orchestration.run_dispatcher import ReminderActivities, RunStrategyActivities
    from orchestration.run_lifecycle_activities import RunLifecycleActivities
    from orchestration.web_dispatcher import (
        TemporalClientAdapter,
        TemporalOutboxDispatcher,
        WebOutboxDispatcher,
    )
    from orchestration.web_reconcile_adapters import LifecycleReconcileStore
    from orchestration.web_reconciler import (
        TemporalInspectorAdapter,
        WebRunReconciler,
    )
    from orchestration.web_workers import (
        build_web_temporal_workers,
        validate_web_worker_startup,
    )
    from research_activities import ResearchActivities
    from research_adapters import (
        ResourcePoolResearchSynthesizer,
        W3libSourceCanonicalizer,
    )
    from resources.run_budget import RunBudgetService
    from tracing import (
        PostgresTraceRepository,
        TraceLifecycleObserver,
        TracingWebEventSinkFactory,
    )
    from web_artifacts.build import ArtifactBuildService
    from web_artifacts.generator import WebArtifactGenerator
    from web_domain.lifecycle import WebRunLifecycleService
    from web_domain.outbox import OutboxService
    from web_domain.run_events import RedisWebRunEventSinkFactory
    from web_domain.workflow_execution import PostgresWorkflowExecutionStore
    from workspace.execution import ExecutionResourceService

    validate_web_worker_startup(config.temporal, worker_database_url)
    assert worker_database_url is not None
    from persistence.migrate import verify_schema

    try:
        verify_schema(worker_database_url)
    except Exception:
        logger.exception("Worker schema gate failed")
        raise
    logger.info("schema verification passed")
    infrastructure = deps.infrastructure
    shared = deps.shared
    assert infrastructure.workspace_isolation is not None, "workspace isolation is required"
    # P2: the detached normalization workflow can outlive cancellation of its
    # parent Run. Keep that route unavailable until cancellation propagation
    # and descendant-stop confirmation are established.
    infrastructure.sandbox_manager.configure_file_document_router(None)
    trace_repository = PostgresTraceRepository(worker_database_url)
    event_factory = TracingWebEventSinkFactory(
        RedisWebRunEventSinkFactory(infrastructure.redis_client),
        trace_repository,
    )
    lifecycle = WebRunLifecycleService(
        worker_database_url,
        TraceLifecycleObserver(trace_repository, event_factory.detach_run),
    )
    context = ContextAssemblyService(
        worker_database_url, shared.context_builder, shared.hindsight_client,
        recall_top_n=config.memory_quality.recall.top_n,
        recall_instruction=shared.prompt_loader.get_memory("recall_instruction"),
    )
    # Chat and QQ use independent Execution resources.
    resource_prep = ExecutionResourceService(
        worker_database_url,
        infrastructure.sandbox_manager,
        infrastructure.run_file_workspace,
        execution_root=infrastructure.workspace_root / "executions",
    )
    loader = RunRequestLoader(
        worker_database_url, PostgresWebRequestLoader(worker_database_url, context)
    )
    agent_store = AgentDataStore(
        worker_database_url,
        lease_ttl_seconds=config.temporal.agent_execution_lease_ttl_seconds,
    )
    from run_domain.input import RunInputLoader

    lifecycle_activities = RunLifecycleActivities(
        lifecycle,
        RunInputLoader(agent_store, max_turns=config.agent.max_tool_turns),
        event_factory,
    )
    canonicalizer = W3libSourceCanonicalizer()
    from web_retrieval.composition import compose_retrieval

    retrieval = compose_retrieval(config.research, canonicalizer)
    research_activities = ResearchActivities(
        worker_database_url,
        retrieval,
        retrieval,
        canonicalizer,
        ResourcePoolResearchSynthesizer(infrastructure.resource_pool),
        max_sources=config.research.max_sources,
        max_fetches=config.research.max_fetches,
        max_iterations=config.research.max_iterations,
        min_evidence=config.research.min_evidence,
        min_distinct_sources=config.research.min_distinct_sources,
        markdown_publisher=(
            ResearchMarkdownPublisher(infrastructure.file_output_publisher)
            if infrastructure.file_output_publisher is not None
            else None
        ),
    )
    durable_activities = DurableAgentActivities(
        default_timezone=config.agent.default_timezone,
        context_bindings=RunExecutionBindings(),
        store=agent_store,
        loader=loader,
        brain=shared.brain_engine,
        actions=shared.action_runtime,
        event_factory=event_factory,
        resource_prep=resource_prep,
        lifecycle=lifecycle,
        run_budget=RunBudgetService(worker_database_url),
        approval_service=FileActionApprovalService(worker_database_url),
    )
    from agent_activities.text_result import TextActivities
    from research_activities.composition import compose_research
    from web_artifacts.local import HtmlActivities

    capability_activities, research_parent = compose_research(
        research_activities,
        durable_activities.store,
        html_available=infrastructure.file_output_publisher is not None,
    )
    artifact_build = ArtifactBuildService(
        worker_database_url,
        WebArtifactGenerator(
            infrastructure.resource_pool,
            max_bytes=int(os.getenv("ARTIFACT_HTML_MAX_BYTES", str(1024 * 1024))),
            read_timeout_seconds=float(os.getenv("ARTIFACT_MODEL_READ_TIMEOUT_SECONDS", "90")),
        ),
        capabilities=capability_activities,
        publisher=infrastructure.file_output_publisher,
    )
    artifact_activities = ArtifactActivities(artifact_build)
    html_activities = HtmlActivities(
        capability_activities, artifact_build.generator, infrastructure.file_output_publisher
    )
    from agent_activities.web_tools import WebTools

    durable_activities.capabilities = capability_activities
    durable_activities.web_tools = WebTools(retrieval.service, durable_activities.run_budget)
    segment_activities = SegmentActivities(durable_activities.store)
    from agent_activities.delegation import DelegationActivities

    delegation_activities = DelegationActivities(
        durable_activities.store, durable_activities.run_budget
    )
    workers = build_web_temporal_workers(
        client,
        lifecycle_activities=[
            RunStrategyActivities(worker_database_url).load_strategy,
            ReminderActivities(worker_database_url).execute,
            lifecycle_activities.prepare_run,
            lifecycle_activities.load_agent_run_input,
            lifecycle_activities.finalize_failed,
            lifecycle_activities.finalize_cancelled,
            durable_activities.finalize_agent_result,
            artifact_activities.prepare,
            artifact_activities.complete,
            research_parent.prepare_research_parent_activity,
            research_activities.prepare_research_activity,
            research_activities.commit_research_result_activity,
            research_activities.create_research_plan_activity,
            research_activities.discover_research_sources_activity,
            research_activities.rank_research_sources_activity,
            research_activities.fetch_research_sources_activity,
            research_activities.normalize_research_sources_activity,
            research_activities.extract_research_evidence_activity,
            research_activities.assess_research_corroboration_activity,
            research_activities.analyze_research_gaps_activity,
            research_activities.synthesize_research_report_activity,
            research_activities.verify_research_citations_activity,
            research_activities.compare_previous_research_activity,
            research_parent.publish_research_artifact_activity,
            research_parent.save_research_workspace_activity,
            research_parent.complete_research_activity,
            research_parent.fail_research_activity,
        ],
        agent_activities=[
            html_activities.produce,
            TextActivities(capability_activities, infrastructure.file_output_publisher).produce,
            capability_activities.prepare,
            capability_activities.observe,
            capability_activities.close,
            delegation_activities.prepare,
            delegation_activities.finish,
            delegation_activities.finish_branch,
            segment_activities.acquire,
            segment_activities.release,
            segment_activities.begin_wait,
            segment_activities.finish_wait,
            durable_activities.context_bootstrap,
            durable_activities.model_decision,
            durable_activities.tool_execution,
            durable_activities.file_action_approval_status,
            durable_activities.planning,
            durable_activities.evaluate_plan,
        ],
    )
    execution_store = PostgresWorkflowExecutionStore(worker_database_url)
    dispatcher = WebOutboxDispatcher(
        OutboxService(worker_database_url),
        TemporalOutboxDispatcher(
            execution_store,
            TemporalClientAdapter(client),
            lifecycle,
        ),
        worker_id=f"hpagent-web-dispatcher-{os.getpid()}",
    )
    reconciler = WebRunReconciler(
        LifecycleReconcileStore(execution_store, lifecycle),
        TemporalInspectorAdapter(client),
    )
    # Phase F: Memory Retention consumer only starts when Hindsight is available.
    # If it is not, retain_memory rows stay pending and are consumed on a later
    # healthy boot (doc §46) — never dead-lettered just because Hindsight was down.
    memory_retention = None
    if shared.hindsight_client is not None:
        from application.memory_retention import MemoryRetentionService

        memory_retention = MemoryRetentionService(worker_database_url, shared.hindsight_client)
        logger.info("MemoryRetentionService composed (retain_memory consumer)")
    logger.info("Web real-Agent composition passed C-07 gate")
    return DurableRuntimeComposition(workers, dispatcher, reconciler, memory_retention)
