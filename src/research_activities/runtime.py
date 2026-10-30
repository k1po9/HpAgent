"""Persisted, idempotent Activities for bounded iterative Research."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent_activities.store import AgentDataStore
from agent_workflows.lifecycle_contracts import LIFECYCLE_SCHEMA_VERSION, SegmentInput
from orchestration.research_workflow import (
    ResearchIterationInput,
    ResearchStageRef,
    ResearchWorkflowInput,
)
from persistence.uow import UnitOfWork, retryable_transaction
from research_domain.context import (
    invocation_id,
    local_research_activity,
    research_identity,
    research_key,
    research_policy,
    research_request,
)
from research_domain.history import authorized_history, freeze_history
from research_domain.models import (
    EvidenceItem,
    ResearchPlan,
    ResearchQuestion,
    SourceCandidate,
    SourceStrategy,
)
from research_domain.persistence import ResearchRepository
from research_domain.policy import ResearchPolicy
from research_domain.providers import (
    ResearchSynthesisProvider,
    SourceCanonicalizer,
    SourceContentProvider,
    SourceDiscoveryProvider,
)
from resources.model_budget_context import model_budget_scope
from resources.model_governance_errors import classify_model_governance_failure
from resources.run_budget import RunBudgetExhausted, RunBudgetService
from run_domain.execution import LeaseConflict, StaleFencingToken, assert_execution_fence
from run_domain.execution_context import execution_fence, fence_scope
from run_domain.lifecycle import RunLifecycleService
from run_domain.results import (
    ResultReceiptService,
    complete_operation,
)
from tracing.repository import PostgresTraceRepository
from web_domain.errors import ResourceNotFound

# Renewed during a stage; expiry must fit the shortest Activity recovery window.
RESEARCH_EXECUTION_LEASE_SECONDS = 30
RESEARCH_LEASE_RENEW_SECONDS = 5


class ResearchActivities:
    def __init__(
        self,
        database: object,
        discovery: SourceDiscoveryProvider,
        content: SourceContentProvider,
        canonicalizer: SourceCanonicalizer,
        synthesis: ResearchSynthesisProvider,
        *,
        max_sources: int = ResearchPolicy.max_sources,
        max_fetches: int = ResearchPolicy.max_fetches,
        max_iterations: int = ResearchPolicy.max_iterations,
        min_evidence: int = ResearchPolicy.min_evidence,
        min_distinct_sources: int = ResearchPolicy.min_distinct_sources,
        markdown_publisher: Any | None = None,
    ) -> None:
        self.database, self.discovery, self.content = database, discovery, content
        self.canonicalizer, self.synthesis = canonicalizer, synthesis
        self.max_sources, self.max_fetches = max_sources, max_fetches
        self.max_iterations = max_iterations
        self.min_evidence, self.min_distinct_sources = min_evidence, min_distinct_sources
        self.markdown_publisher = markdown_publisher
        self.capabilities = None
        self.store = AgentDataStore(database, lease_ttl_seconds=RESEARCH_EXECUTION_LEASE_SECONDS)
        self.repository = ResearchRepository()
        self.budget = RunBudgetService(database)
        self.trace = PostgresTraceRepository(database)

    def registration_policy(self) -> ResearchPolicy:
        from application.prompt_catalog import agent_prompt
        service = getattr(self.discovery, "service", None)
        return ResearchPolicy(
            synthesis_prompt=agent_prompt("research"),
            model_selector=getattr(self.synthesis, "model_selector", ResearchPolicy.model_selector),
            **{name: getattr(self, name) for name in (
                "max_sources", "max_fetches", "max_iterations", "min_evidence", "min_distinct_sources")},
            search_provider=getattr(service, "default_search", ResearchPolicy.search_provider),
            fetch_provider=getattr(service, "default_fetch", ResearchPolicy.fetch_provider),
            search_timeout_seconds=getattr(self.discovery, "search_timeout", ResearchPolicy.search_timeout_seconds),
            fetch_timeout_seconds=getattr(self.content, "fetch_timeout", ResearchPolicy.fetch_timeout_seconds),
        )

    def _execution_policy(self):
        request = research_request.get()
        if request is None or not request.execution_id:
            return None
        with UnitOfWork(self.database) as uow:
            descriptor = self.capabilities.invocation_context(uow, request)
            definition = descriptor["definition"]
            if definition["implementation_version"] == "research-web-v3":
                return ResearchPolicy.from_snapshot(definition.get("execution_policy"))
            if definition["implementation_version"] != "research-web-v2":
                raise ValueError("research_policy: unsupported implementation version")
            return None

    def _limit(self, name):
        policy = research_policy.get()
        return getattr(policy, name) if policy is not None else getattr(self, name)

    @staticmethod
    def _attempt() -> int:
        try:
            return int(activity.info().attempt)
        except RuntimeError:
            return 1

    @staticmethod
    def _root_event_id(run_id: UUID) -> UUID:
        return uuid5(NAMESPACE_URL, f"hpagent:research:{research_identity(run_id)}:root")

    @staticmethod
    def _event_id(run_id: UUID, stage: str) -> UUID:
        return uuid5(NAMESPACE_URL, f"hpagent:research:{research_identity(run_id)}:{stage}")

    @staticmethod
    def _ref(run_id: UUID, stage: str, result: dict[str, Any]) -> ResearchStageRef:
        ref: ResearchStageRef = {
            "schema_version": 1,
            "run_id": str(run_id),
            "stage": stage,
            "ref": f"postgres://research_stage_results/{research_identity(run_id)}:{stage}",
            "item_count": int(result.get("item_count", 0)),
        }
        if invocation_id():
            ref["invocation_id"] = invocation_id()
        if "sufficient" in result:
            ref["sufficient"] = bool(result["sufficient"])
        if "stop_reason" in result:
            ref["stop_reason"] = str(result["stop_reason"])
        return ref

    @retryable_transaction
    def _load_stage_result(self, operation_id: str) -> dict[str, Any] | None:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            return cast(dict[str, Any] | None, self.repository.stage_result(uow, operation_id))

    @retryable_transaction
    def _save_stage_result(
        self, operation_id: str, run_id: UUID, stage: str, result: dict[str, Any]
    ) -> None:
        try:
            with UnitOfWork(self.database) as uow:
                assert_execution_fence(uow)
                self.repository.save_stage_result(uow, operation_id, run_id, stage, result)
                complete_operation(uow, operation_id, f"operation:{operation_id}", result)
        except StaleFencingToken:
            fence = execution_fence.get()
            if fence is not None:
                account, run, execution, token = fence
                ResultReceiptService(self.database).receive(
                    account, run, execution, operation_id, token, result
                )
            raise

    @retryable_transaction
    def _budget_ledger_entry(
        self, run_id: UUID, operation_id: str, dimension: str
    ) -> tuple[str, int, int | None] | None:
        """Return a prior reservation so an Activity retry reuses its dimensions."""
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            row = uow.execute(
                "SELECT state,reserved_amount,actual_amount FROM run_usage_ledger "
                "WHERE run_id=%s AND operation_id=%s AND dimension=%s",
                (run_id, operation_id, dimension),
            ).fetchone()
        if row is None:
            return None
        return (
            str(row["state"]),
            int(row["reserved_amount"]),
            (int(row["actual_amount"]) if row["actual_amount"] is not None else None),
        )

    def _assert_current(self, run_id: UUID) -> None:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            row = uow.execute("SELECT account_id FROM runs WHERE run_id=%s", (run_id,)).fetchone()
            if row is None:
                raise ResourceNotFound()
            run = RunLifecycleService.lock(uow, row["account_id"], run_id)
            if run["source_kind"] == "work":
                RunLifecycleService.check_work(uow, run)
            request = research_request.get()
            if request is not None and request.execution_id:
                self.capabilities.invocation_context(uow, request)
            if run["status"] != "running":
                raise RuntimeError("Research Run is not running")

    async def _run_stage(
        self, run_id: UUID, stage: str, action: Callable[[], Any]
    ) -> ResearchStageRef:
        store = self.store
        identity = await asyncio.to_thread(store.run_identity, str(run_id))
        try:
            info = activity.info()
            attempt_key = f"{info.workflow_run_id}:{info.activity_id}:{info.attempt}"
        except RuntimeError:
            from uuid import uuid4

            attempt_key = str(uuid4())
        segment = SegmentInput(
            LIFECYCLE_SCHEMA_VERSION,
            str(run_id),
            identity["account_id"],
            f"research:{research_identity(run_id)}:{stage}:{attempt_key}",
            execution_id=identity["execution_id"],
        )

        request = research_request.get()
        if request is not None and request.execution_id:
            identity = {"account_id": request.account_id, "execution_id": request.execution_id}
            segment = SegmentInput(
                LIFECYCLE_SCHEMA_VERSION,
                str(run_id),
                request.account_id,
                f"research:{research_identity(run_id)}:{stage}:{attempt_key}",
                execution_id=request.execution_id,
            )
        pulse = work = None
        try:
            while True:
                try:
                    token = await asyncio.to_thread(store.acquire_segment, segment)
                    break
                except LeaseConflict:
                    with suppress(RuntimeError):
                        activity.heartbeat({"stage": stage, "state": "waiting_for_execution"})
                    await asyncio.sleep(1)
            with fence_scope(identity["account_id"], str(run_id), token, identity["execution_id"]):
                pulse = asyncio.create_task(self._maintain_stage_lease(segment, token, stage))
                policy_token = research_policy.set(await asyncio.to_thread(self._execution_policy))
                try:
                    work = asyncio.create_task(self._run_stage_in_execution(run_id, stage, action))
                finally:
                    research_policy.reset(policy_token)
                done, _ = await asyncio.wait((pulse, work), return_when=asyncio.FIRST_COMPLETED)
                if pulse in done:
                    await pulse
                return await work
        except StaleFencingToken as exc:
            raise ApplicationError(str(exc), type="run_not_executable", non_retryable=True) from exc
        finally:
            tasks = [task for task in (pulse, work) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.shield(asyncio.to_thread(store.release_segment, segment))

    async def _maintain_stage_lease(self, segment: SegmentInput, token: int, stage: str) -> None:
        while True:
            await asyncio.to_thread(
                self.store.validate_and_renew_lease,
                segment.account_id,
                segment.run_id,
                token,
                segment.execution_id,
            )
            with suppress(RuntimeError):
                activity.heartbeat({"stage": stage})
            await asyncio.sleep(RESEARCH_LEASE_RENEW_SECONDS)

    def _trace_parent(self, run_id):
        trace = self.trace.create_trace_run(run_id, {"run_kind": "research"})
        with UnitOfWork(self.database) as uow:
            root = uow.execute(
                "SELECT trace_event_id FROM trace_events WHERE trace_run_id=%s AND parent_event_id IS NULL",
                (trace.trace_run_id,),
            ).fetchone()
        if root is None:
            root_id = uuid5(NAMESPACE_URL, f"hpagent:research:{run_id}:root")
            self.trace.start_event(run_id, root_id, None, "Research", "research_run", {})
        else:
            root_id = root["trace_event_id"]
        if invocation_id():
            child_root = self._root_event_id(run_id)
            self.trace.start_event(
                run_id, child_root, root_id, "ResearchInvocation", "research_run", {}
            )
            return child_root
        return root_id

    async def _run_stage_in_execution(
        self, run_id: UUID, stage: str, action: Callable[[], Any]
    ) -> ResearchStageRef:
        await asyncio.to_thread(self._assert_current, run_id)
        if stage in {"AdmitResearch", "Complete", "Failed"}:
            return await asyncio.to_thread(action)
        operation_id = f"research:{research_identity(run_id)}:{stage}:v1"
        store = self.store
        await asyncio.to_thread(store.begin_operation, operation_id, str(run_id), "research_stage")
        existing = await asyncio.to_thread(self._load_stage_result, operation_id)
        if existing is not None:
            return self._ref(run_id, stage, existing)
        parent_event_id = await asyncio.to_thread(self._trace_parent, run_id)
        event_id = self._event_id(run_id, stage)
        await asyncio.to_thread(
            self.trace.start_event,
            run_id,
            event_id,
            parent_event_id,
            stage,
            "research_stage",
            {"operation_id": operation_id},
        )
        try:
            started = time.monotonic()
            wall_operation_id = f"{operation_id}:wall"
            wall_entry = await asyncio.to_thread(
                self._budget_ledger_entry, run_id, wall_operation_id, "wall_time_ms"
            )
            await asyncio.to_thread(
                self.budget.reserve, run_id, wall_operation_id, {"wall_time_ms": 1}
            )
            from contextlib import AsyncExitStack

            from resources.capacity import CapacityService

            async with AsyncExitStack() as capacity_stack:
                if stage.startswith(("Fetch:", "SourceDiscovery:")):
                    await capacity_stack.enter_async_context(
                        CapacityService(self.database).slot(
                            run_id, "fetch" if stage.startswith("Fetch:") else "tool"
                        )
                    )
                await asyncio.to_thread(self._assert_current, run_id)
                value = await asyncio.to_thread(action)
                if asyncio.iscoroutine(value):
                    value = await value
            result = value if isinstance(value, dict) else {"item_count": int(value or 0)}
            compact = {key: result[key] for key in ("item_count", "sufficient", "stop_reason", "search", "fetch") if key in result}
            elapsed_ms = max(1, int((time.monotonic() - started) * 1000))
            if wall_entry is None or wall_entry[0] == "reserved":
                await asyncio.to_thread(
                    self.budget.settle,
                    run_id,
                    wall_operation_id,
                    {"wall_time_ms": elapsed_ms},
                    "measured",
                )
            await asyncio.to_thread(self._save_stage_result, operation_id, run_id, stage, compact)
            await asyncio.to_thread(self.trace.finish_event, run_id, event_id, "completed", compact)
            return self._ref(run_id, stage, compact)
        except Exception as exc:
            await asyncio.to_thread(
                self.trace.finish_event, run_id, event_id, "failed", {"error": "stage_failed"}
            )
            if isinstance(exc, RunBudgetExhausted):
                raise ApplicationError(
                    "执行预算已耗尽。", type=exc.code, non_retryable=True
                ) from exc
            governance = classify_model_governance_failure(exc)
            if governance is not None:
                raise ApplicationError(
                    governance.safe_message, type=governance.code, non_retryable=True
                ) from exc
            raise

    def _research_feedback(self, run_id, *, passed=False):
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            budget = uow.execute("SELECT limits,used,reserved FROM run_budgets WHERE run_id=%s", (run_id,)).fetchone()
            rows = uow.execute("SELECT query,gap_analysis FROM research_iterations WHERE run_id=%s ORDER BY created_at,iteration", (run_id,)).fetchall()
            stages = uow.execute("SELECT stage,result FROM research_stage_results WHERE research_id=%s AND (stage LIKE 'SourceDiscovery:%%' OR stage LIKE 'Fetch:%%') ORDER BY stage", (research_key(run_id),)).fetchall()
        used = budget["used"].get("research_iterations", 0)
        limit = budget["limits"].get("research_iterations", 0)
        remaining = max(0, limit - used - budget["reserved"].get("research_iterations", 0))
        searches = [row for row in stages if row["stage"].startswith("SourceDiscovery:")]
        # Keep bounded facts in the existing immutable result envelope.
        return {"research": {"used": used, "limit": limit, "remaining": remaining,
            "stop_reason": next((row["result"].get("stop_reason") for row in reversed(searches) if row["result"].get("stop_reason")), "sufficient" if passed else "iteration_limit" if not remaining else "insufficient_evidence"),
            "queries": list(dict.fromkeys(row["query"] for row in rows))[:3],
            "major_gaps": [row["gap_analysis"] for row in rows if row["gap_analysis"]][-1:],
            "search_statuses": [row["result"].get("search", {}).get("status", "unknown") for row in searches],
            "freshness_rejected": [row["result"].get("search", {}).get("freshness_rejected", {}) for row in searches],
            "fetch": [row["result"].get("fetch", {}) for row in stages if row["stage"].startswith("Fetch:")]}}

    @activity.defn
    @local_research_activity
    async def prepare_research_activity(self, request: ResearchWorkflowInput) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(run_id, "Planning", lambda: self._prepare(run_id))

    @retryable_transaction
    def _prepare(self, run_id: UUID) -> None:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            run = uow.execute(
                "SELECT status,executor_key,account_id FROM runs WHERE run_id=%s", (run_id,)
            ).fetchone()
            if run is None:
                raise LookupError(f"research Run not found: {run_id}")
            run = RunLifecycleService.lock(uow, run["account_id"], run_id)
            if run["source_kind"] == "work":
                RunLifecycleService.check_work(uow, run)
            if run["status"] != "running":
                raise RuntimeError("Research Run cannot start")
            freeze_history(uow, run["account_id"], run_id)

    @activity.defn
    @local_research_activity
    async def create_research_plan_activity(
        self, request: ResearchWorkflowInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(run_id, "SourceStrategy", lambda: self._create_plan(run_id))

    @retryable_transaction
    def _create_plan(self, run_id: UUID) -> int:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            request = research_request.get()
            if request is None or not request.execution_id:
                from workspace.resources import ResourceDenied

                raise ResourceDenied("Research plan requires an admitted capability identity")
            descriptor = self.capabilities.invocation_context(uow, request)
            task = {
                "objective": descriptor["inputs"]["objective"],
                "source_strategy": descriptor["inputs"].get("source_strategy", {}),
            }
            strategy_value = task["source_strategy"]
            if isinstance(strategy_value, str):
                strategy_value = json.loads(strategy_value)
            history = authorized_history(uow, run_id)
            prior_claims = [
                claim.get("statement", "")
                for item in history
                for claim in item["snapshot"].get("claims", [])
            ]
            question = str(task["objective"])
            if prior_claims:
                question += (
                    "\nCheck what changed since these prior claims: "
                    + json.dumps(prior_claims, ensure_ascii=False)[:1000]
                )
            plan = ResearchPlan(
                objective=str(task["objective"]),
                questions=(ResearchQuestion("q1", question, 1),),
                source_strategy=SourceStrategy.from_dict(dict(strategy_value)),
            )
            self.repository.create_plan(uow, run_id, plan)
            return len(plan.questions)

    def _iteration_context(self, run_id: UUID, iteration: int) -> tuple[str, SourceStrategy]:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            row = self.repository.load_plan(uow, run_id)
            previous = uow.execute(
                "SELECT gap_analysis FROM research_iterations WHERE research_id=%s AND iteration<%s ORDER BY iteration DESC LIMIT 1",
                (research_key(run_id), iteration),
            ).fetchone()
        plan = row["plan"]
        if isinstance(plan, str):
            plan = json.loads(plan)
        from research_adapters.web import discovery_query

        strategy = SourceStrategy.from_dict(dict(plan["source_strategy"]))
        query = str(plan["questions"][0]["question"])
        missing = (previous["gap_analysis"] or {}).get("missing_concepts", []) if previous else []
        if missing:
            sites = re.findall(r"site:[a-zA-Z0-9.-]+", discovery_query(query, strategy))
            query = " ".join(missing)
            if strategy.preferred_domains:
                sites = ["site:" + domain for domain in strategy.preferred_domains[:2]]
            if sites:
                query = "(" + " OR ".join(sites) + ") " + query
        service = getattr(self.discovery, "service", None)
        policy = research_policy.get()
        provider = policy.search_provider if policy is not None else getattr(service, "default_search", None)
        max_chars = 128 if provider == "deepseek" else 240
        return discovery_query(query, strategy, max_chars=max_chars), strategy

    @retryable_transaction
    def _start_iteration(self, run_id: UUID, iteration: int, query: str) -> None:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            self.repository.start_iteration(uow, run_id, iteration, query)
            # The committed start consumes one round, including failed network attempts.
            # A hold without this start remains releasable by existing termination cleanup.
            self.budget.settle_in_uow(uow, run_id,
                f"research:{research_identity(run_id)}:iteration:{iteration}:budget:v1",
                {"research_iterations": 1}, "measured")

    @activity.defn
    @local_research_activity
    async def discover_research_sources_activity(
        self, request: ResearchIterationInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(
            run_id,
            f"SourceDiscovery:{request.iteration}",
            lambda: self._discover(run_id, request.iteration),
        )

    async def _discover(self, run_id: UUID, iteration: int) -> dict[str, Any]:
        query, strategy = self._iteration_context(run_id, iteration)
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            duplicate = uow.execute(
                "SELECT 1 FROM research_iterations i JOIN research_plans p USING(research_id) "
                "WHERE i.run_id=%s AND i.query=%s AND p.plan->'source_strategy'=%s::jsonb "
                "AND NOT (i.research_id=%s AND i.iteration=%s) LIMIT 1",
                (run_id, query, json.dumps(strategy.to_dict()), research_key(run_id), iteration),
            ).fetchone()
        if duplicate:
            return {"item_count": 0, "stop_reason": "no_new_query", "search": {"query": query, "status": "duplicate_query", "freshness_days": strategy.freshness_days}}
        round_operation = f"research:{research_identity(run_id)}:iteration:{iteration}:budget:v1"
        try:
            await asyncio.to_thread(self.budget.reserve, run_id, round_operation, {"research_iterations": 1})
        except RunBudgetExhausted:
            return {"item_count": 0, "stop_reason": "research_iterations_exhausted", "search": {"query": query, "status": "not_started"}}
        await asyncio.to_thread(self._start_iteration, run_id, iteration, query)
        operation_id = f"research:{research_identity(run_id)}:SourceDiscovery:{iteration}:budget:v1"
        ledger_entry = await asyncio.to_thread(
            self._budget_ledger_entry, run_id, operation_id, "sources_discovered"
        )
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            existing = int(
                uow.execute(
                    "SELECT count(*) AS count FROM source_records WHERE research_id=%s AND iteration=%s",
                    (research_key(run_id), iteration),
                ).fetchone()["count"]
            )
            total = int(
                uow.execute(
                    "SELECT count(*) AS count FROM source_records WHERE research_id=%s",
                    (research_key(run_id),),
                ).fetchone()["count"]
            )
        if existing:
            if ledger_entry is not None and ledger_entry[0] == "reserved":
                await asyncio.to_thread(
                    self.budget.settle,
                    run_id,
                    operation_id,
                    {"sources_discovered": existing},
                    "measured",
                )
            return {"item_count": existing}
        remaining = max(0, self._limit("max_sources") - total)
        if ledger_entry is None:
            with UnitOfWork(self.database) as uow:
                assert_execution_fence(uow)
                budget = uow.execute(
                    "SELECT limits,used,reserved FROM run_budgets WHERE run_id=%s", (run_id,)
                ).fetchone()
                remaining = min(
                    remaining,
                    max(
                        0,
                        budget["limits"].get("sources_discovered", 0)
                        - budget["used"].get("sources_discovered", 0)
                        - budget["reserved"].get("sources_discovered", 0),
                    ),
                )
        else:
            remaining = min(remaining, ledger_entry[1])
        if not remaining:
            return {"item_count": 0, "stop_reason": "source_limit"}
        reserved_sources = ledger_entry[1] if ledger_entry is not None else remaining
        await asyncio.to_thread(
            self.budget.reserve,
            run_id,
            operation_id,
            {"sources_discovered": reserved_sources},
        )
        try:
            from web_retrieval.budget import retrieval_budget_scope

            with retrieval_budget_scope(self.budget, run_id, operation_id):
                candidates = await self.discovery.discover(query, strategy=strategy, limit=remaining)
            diagnostic = getattr(candidates, "diagnostics", {"status": "ok", "candidate_count": len(candidates)})
        except RunBudgetExhausted:
            raise
        except Exception as exc:
            candidates = []
            diagnostic = {"status": "all_unavailable", "upstream_errors": [{"error": type(exc).__name__}]}
        from research_adapters.web import freshness_status
        rejected: dict[str, int] = {}
        accepted = []
        for candidate in candidates:
            status = freshness_status(candidate, strategy)
            if status in {"date_unknown", "outside_interval"}:
                rejected[status] = rejected.get(status, 0) + 1
            else:
                accepted.append(candidate)
        diagnostic = {**diagnostic, "query": query, "freshness_days": strategy.freshness_days,
                      "freshness_rejected": rejected, "usable_candidates": len(accepted)}
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            source_ids = self.repository.add_candidates(
                uow, run_id, accepted, self.canonicalizer.canonicalize, iteration=iteration
            )
        await asyncio.to_thread(
            self.budget.settle,
            run_id,
            operation_id,
            {"sources_discovered": len(source_ids)},
            "measured",
        )
        return {"item_count": len(source_ids), "search": diagnostic}

    @activity.defn
    @local_research_activity
    async def rank_research_sources_activity(
        self, request: ResearchIterationInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(
            run_id,
            f"SourceRanking:{request.iteration}",
            lambda: self._rank(run_id, request.iteration),
        )

    @retryable_transaction
    def _rank(self, run_id: UUID, iteration: int) -> int:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            uow.execute(
                "UPDATE source_records SET source_tier=CASE WHEN metadata->>'preferred_domain'='true' THEN 0 ELSE 2 END,updated_at=now() WHERE research_id=%s AND iteration=%s",
                (research_key(run_id), iteration),
            )
            return int(
                uow.execute(
                    "SELECT count(*) AS count FROM source_records WHERE research_id=%s AND iteration=%s",
                    (research_key(run_id), iteration),
                ).fetchone()["count"]
            )

    @activity.defn
    @local_research_activity
    async def fetch_research_sources_activity(
        self, request: ResearchIterationInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(
            run_id, f"Fetch:{request.iteration}", lambda: self._fetch(run_id, request.iteration)
        )

    async def _fetch(self, run_id: UUID, iteration: int) -> dict[str, Any]:
        _, strategy = self._iteration_context(run_id, iteration)
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            fetched_total = int(
                uow.execute(
                    "SELECT count(*) AS count FROM source_records WHERE research_id=%s AND fetch_status IN ('fetched','duplicate','failed')",
                    (research_key(run_id),),
                ).fetchone()["count"]
            )
            rows = self.repository.fetch_candidates(
                uow, run_id, max(0, self._limit("max_fetches") - fetched_total), iteration=iteration
            )
        operation_id = f"research:{research_identity(run_id)}:Fetch:{iteration}:budget:v1"
        ledger_entry = await asyncio.to_thread(
            self._budget_ledger_entry, run_id, operation_id, "source_fetches"
        )
        reserved_fetches = ledger_entry[1] if ledger_entry is not None else len(rows)
        await asyncio.to_thread(
            self.budget.reserve,
            run_id,
            operation_id,
            {"source_fetches": reserved_fetches},
        )
        for row in rows:
            candidate = SourceCandidate(
                uri=str(row["canonical_uri"]),
                title=str(row["title"]),
                publisher=row["publisher"],
                published_at=row["published_at"],
                provider=str(row["provider"]),
                source_type=str(row["source_type"]),
                metadata=dict(row["metadata"]),
            )
            try:
                from web_retrieval.budget import retrieval_budget_scope
                from web_retrieval.context import source_scope

                scope_token = source_scope.set(strategy)
                try:
                    with retrieval_budget_scope(self.budget, run_id,
                            f"research:{research_identity(run_id)}:source:{row['source_id']}"):
                        fetch_task = asyncio.create_task(self.content.fetch(candidate))
                finally:
                    source_scope.reset(scope_token)
                try:
                    while True:
                        done, _pending = await asyncio.wait(
                            {fetch_task}, timeout=5, return_when=asyncio.FIRST_COMPLETED
                        )
                        if done:
                            content = fetch_task.result()
                            break
                        if activity.in_activity():
                            activity.heartbeat({"source_id": str(row["source_id"])})
                finally:
                    if not fetch_task.done():
                        fetch_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await fetch_task
                with UnitOfWork(self.database) as uow:
                    assert_execution_fence(uow)
                    self.repository.store_content(uow, UUID(str(row["source_id"])), content)
            except RunBudgetExhausted:
                raise
            except Exception as exc:
                with UnitOfWork(self.database) as uow:
                    assert_execution_fence(uow)
                    self.repository.mark_fetch_failed(
                        uow, UUID(str(row["source_id"])), type(exc).__name__
                    )
        if ledger_entry is None or ledger_entry[0] == "reserved":
            await asyncio.to_thread(
                self.budget.settle,
                run_id,
                operation_id,
                {"source_fetches": reserved_fetches},
                "measured",
            )
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            statuses = uow.execute("SELECT fetch_status,count(*) AS n FROM source_records WHERE research_id=%s AND iteration=%s GROUP BY fetch_status", (research_key(run_id), iteration)).fetchall()
        return {"item_count": reserved_fetches, "fetch": {str(row["fetch_status"]): int(row["n"]) for row in statuses}}

    @activity.defn
    @local_research_activity
    async def normalize_research_sources_activity(
        self, request: ResearchIterationInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(
            run_id,
            f"NormalizeDeduplicate:{request.iteration}",
            lambda: self._normalized_count(run_id, request.iteration),
        )

    @retryable_transaction
    def _normalized_count(self, run_id: UUID, iteration: int) -> int:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            return int(
                uow.execute(
                    "SELECT count(*) AS count FROM source_records WHERE research_id=%s AND iteration=%s AND fetch_status IN ('fetched','duplicate')",
                    (research_key(run_id), iteration),
                ).fetchone()["count"]
            )

    @activity.defn
    @local_research_activity
    async def extract_research_evidence_activity(
        self, request: ResearchIterationInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(
            run_id,
            f"EvidenceExtraction:{request.iteration}",
            lambda: self._extract_evidence(run_id, request.iteration),
        )

    @retryable_transaction
    def _extract_evidence(self, run_id: UUID, iteration: int) -> int:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            sources = uow.execute(
                "SELECT s.*,c.content_text FROM source_records s JOIN source_contents c ON c.source_id=s.source_id LEFT JOIN evidence_items e ON e.source_id=s.source_id WHERE s.research_id=%s AND s.iteration=%s AND s.fetch_status='fetched' AND e.evidence_id IS NULL ORDER BY s.source_id",
                (research_key(run_id), iteration),
            ).fetchall()
            existing = uow.execute(
                "SELECT count(*) AS count,COALESCE(sum(octet_length(excerpt)),0) AS bytes FROM evidence_items WHERE research_id=%s",
                (research_key(run_id),),
            ).fetchone()
            remaining_spans = max(0, 24 - int(existing["count"]))
            remaining_bytes = max(0, 24000 - int(existing["bytes"]))
            for source in sources:
                if not remaining_spans or not remaining_bytes:
                    break
                content_text = str(source["content_text"])
                from research_domain.excerpts import evidence_spans

                plan = self.repository.load_plan(uow, run_id)
                spans = evidence_spans(content_text, str(plan['objective']), max_spans=min(6, remaining_spans), max_bytes=min(7200, remaining_bytes))
                for start, end, paragraph_index in spans:
                    remaining_spans -= 1
                    remaining_bytes -= len(content_text[start:end].encode('utf-8'))
                    excerpt = content_text[start:end]
                    self.repository.add_evidence(
                        uow,
                        EvidenceItem(
                            evidence_id=str(uuid5(NAMESPACE_URL, f"hpagent:evidence:{research_identity(run_id)}:{source['source_id']}:{source['content_ref']}:{start}:{end}")),
                            run_id=str(run_id),
                            source_id=str(source["source_id"]),
                            excerpt=excerpt,
                            content_ref=str(source["content_ref"]),
                            source_locator={
                                "type": "web",
                                "canonical_uri": str(source["canonical_uri"]),
                                "content_ref": str(source["content_ref"]),
                                "paragraph_index": paragraph_index,
                                "text_span": {"start": start, "end": end},
                                "excerpt": excerpt,
                            },
                            source_quality=int(source["source_tier"]),
                            metadata={"extractor": "research-passages-v2", "iteration": iteration},
                        ),
                    )
            return int(
                uow.execute(
                    "SELECT count(*) AS count FROM evidence_items WHERE research_id=%s",
                    (research_key(run_id),),
                ).fetchone()["count"]
            )

    @activity.defn
    @local_research_activity
    async def assess_research_corroboration_activity(
        self, request: ResearchIterationInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(
            run_id,
            f"Corroboration:{request.iteration}",
            lambda: self._assess_corroboration(run_id, request.iteration),
        )

    @retryable_transaction
    def _assess_corroboration(self, run_id: UUID, iteration: int) -> int:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            row = uow.execute(
                "SELECT count(*) AS evidence_count,count(DISTINCT source_id) AS source_count,count(*) FILTER (WHERE source_quality<=1) AS high_quality_count FROM evidence_items WHERE research_id=%s",
                (research_key(run_id),),
            ).fetchone()
            value = {key: int(row[key]) for key in row.keys()}
            self.repository.save_corroboration(uow, run_id, iteration, value)
            return value["source_count"]

    @activity.defn
    @local_research_activity
    async def analyze_research_gaps_activity(
        self, request: ResearchIterationInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(
            run_id,
            f"GapAnalysis:{request.iteration}",
            lambda: self._analyze_gaps(run_id, request.iteration),
        )

    @retryable_transaction
    def _analyze_gaps(self, run_id: UUID, iteration: int) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            row = uow.execute(
                "SELECT count(*) AS evidence_count,count(DISTINCT source_id) AS source_count FROM evidence_items WHERE research_id=%s",
                (research_key(run_id),),
            ).fetchone()
            evidence_count, source_count = int(row["evidence_count"]), int(row["source_count"])
            meets_threshold = (
                evidence_count >= self._limit("min_evidence") and source_count >= self._limit("min_distinct_sources")
            )
            from research_domain.excerpts import missing_concepts

            plan = self.repository.load_plan(uow, run_id)
            excerpts = uow.execute("SELECT excerpt FROM evidence_items WHERE research_id=%s", (research_key(run_id),)).fetchall()
            missing = missing_concepts(str(plan["objective"]), [str(e["excerpt"]) for e in excerpts])
            sufficient = meets_threshold and not missing
            value = {
                "missing_concepts": missing,
                "evidence_count": evidence_count,
                "source_count": source_count,
                "missing_evidence": max(0, self._limit("min_evidence") - evidence_count),
                "missing_sources": max(0, self._limit("min_distinct_sources") - source_count),
                "stopped_at_iteration_limit": iteration >= self._limit("max_iterations")
                and not meets_threshold,
            }
            self.repository.save_gap_analysis(uow, run_id, iteration, value, sufficient=sufficient)
        return {"item_count": evidence_count, "sufficient": sufficient}

    @activity.defn
    @local_research_activity
    async def synthesize_research_report_activity(
        self, request: ResearchWorkflowInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(run_id, "Synthesis", lambda: self._synthesize(run_id))

    async def _synthesize(self, run_id: UUID) -> int:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            owner = uow.execute("SELECT account_id FROM runs WHERE run_id=%s", (run_id,)).fetchone()
            if owner is None:
                raise ValueError("research Run does not exist")
            existing = self.repository.load_report(uow, run_id)
            if existing is not None:
                return int(
                    uow.execute(
                        "SELECT count(*) AS count FROM research_claims WHERE research_id=%s",
                        (research_key(run_id),),
                    ).fetchone()["count"]
                )
            plan = self.repository.load_plan(uow, run_id)
            evidence = self.repository.evidence_for_synthesis(uow, run_id)
            history = authorized_history(uow, run_id)
        from research_domain.excerpts import missing_concepts

        missing = missing_concepts(str(plan["objective"]), [str(row["excerpt"]) for row in evidence])
        gaps = [{"code": "missing_concept", "message": f"No source excerpt covers {term}."} for term in missing]
        if len(evidence) < self._limit("min_evidence"):
            gaps.append({"code": "missing_evidence", "message": "Too few usable evidence spans."})
        if len({str(row["source_id"]) for row in evidence}) < self._limit("min_distinct_sources"):
            gaps.append({"code": "missing_sources", "message": "Too few distinct usable sources."})
        if gaps:
            with UnitOfWork(self.database) as uow:
                assert_execution_fence(uow)
                return self.repository.save_empty_report(uow, run_id, gaps)
        from research_adapters.synthesis import (
            ResourcePoolResearchSynthesizer,
            SynthesisValidationError,
        )

        objective = str(plan["objective"]) + (
            "\nAuthorized prior report claims: "
            + json.dumps([item["snapshot"] for item in history], ensure_ascii=False) if history else ""
        )
        operation_id = f"research:{research_identity(run_id)}:Synthesis:model:v1"
        with model_budget_scope(
            owner["account_id"], run_id, operation_id, phase="research_synthesis",
            execution_attempt=self._attempt(), final_response=True,
        ):
            try:
                if isinstance(self.synthesis, ResourcePoolResearchSynthesizer):
                    # The existing stage receipt stores exact received text before parsing.
                    # A dispatch marker without a response is unknown, never permission to resend.
                    receipt = self._load_stage_result(operation_id)
                    if receipt is None:
                        if self._load_stage_result(operation_id + ":dispatch") is not None:
                            raise ApplicationError("Research synthesis response is uncertain; reconcile original operation.",
                                                   type="research_synthesis_uncertain", non_retryable=True)
                        self.store.begin_operation(operation_id, str(run_id), "research_stage")
                        self.store.begin_operation(operation_id + ":dispatch", str(run_id), "research_stage")
                        self._save_stage_result(operation_id + ":dispatch", run_id, "SynthesisDispatch", {"operation_id": operation_id})
                        receipt = await self.synthesis.request(objective, [dict(row) for row in evidence],
                                                               policy=research_policy.get())
                        self._save_stage_result(operation_id, run_id, "SynthesisResponse", receipt)
                    report = self.synthesis.parse(receipt, objective, [dict(row) for row in evidence])
                    self.store.begin_operation(operation_id + ":validation", str(run_id), "research_stage")
                    self._save_stage_result(operation_id + ":validation", run_id, "SynthesisValidation", {"status": "valid"})
                else:
                    report = await self.synthesis.synthesize(objective, [dict(row) for row in evidence])
            except SynthesisValidationError as exc:
                gaps = [{"code": exc.code, "message": str(exc)}]
                self.store.begin_operation(operation_id + ":validation", str(run_id), "research_stage")
                self._save_stage_result(operation_id + ":validation", run_id, "SynthesisValidation", {"status": "invalid", "gaps": gaps})
                with UnitOfWork(self.database) as uow:
                    assert_execution_fence(uow)
                    return self.repository.save_empty_report(uow, run_id, gaps)
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            return cast(int, self.repository.save_report(uow, run_id, report))

    @activity.defn
    @local_research_activity
    async def verify_research_citations_activity(
        self, request: ResearchWorkflowInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(run_id, "CitationVerification", lambda: self._verify(run_id))

    @retryable_transaction
    def _verify(self, run_id: UUID) -> int:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            count, _needs_review = self.repository.verify_citations(uow, run_id)
            return cast(int, count)

    @activity.defn
    @local_research_activity
    async def compare_previous_research_activity(
        self, request: ResearchWorkflowInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(run_id, "DailyDiff", lambda: self._compare_previous(run_id))

    @retryable_transaction
    def _compare_previous(self, run_id: UUID) -> int:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            diff = self.repository.compare_previous_report(uow, run_id)
            return sum(len(items) for items in diff.values())

    @activity.defn
    @local_research_activity
    async def commit_research_result_activity(
        self, request: ResearchWorkflowInput
    ) -> ResearchStageRef:
        request.validate()
        return await self._run_stage(
            UUID(request.run_id), "CommitResult", lambda: self._commit_result(request)
        )

    def _commit_result(self, request):
        from dataclasses import replace

        from agent_workflows.capability_contracts import CapabilityExecutionInput
        from research_domain.checks import basic_checks
        from run_domain.capability_contracts import CapabilityResult, Check, ResultRef

        run_id = UUID(request.run_id)
        with UnitOfWork(self.database) as uow:
            descriptor = self.capabilities.invocation_context(uow, request)
            if uow.execute(
                "SELECT 1 FROM capability_results WHERE invocation_id=%s", (request.invocation_id,)
            ).fetchone():
                return 1
            report = self.repository.load_report(uow, run_id)
            if report is None:
                raise ValueError("Research report is missing")
            claims = uow.execute(
                "SELECT claim_id,ordinal FROM research_claims WHERE invocation_id=%s ORDER BY ordinal",
                (request.invocation_id,),
            ).fetchall()
            citations = uow.execute(
                "SELECT claim_id,verification_status FROM research_citations WHERE invocation_id=%s",
                (request.invocation_id,),
            ).fetchall()
            checks = basic_checks(report, citations, claims)
        if self.markdown_publisher is None:
            raise RuntimeError("Research output publisher is unavailable")
        markdown = self.markdown_publisher.publish(run_id, report["report_markdown"])
        check_file = self.markdown_publisher.publish(
            run_id, json.dumps(checks, sort_keys=True), kind="checks"
        )
        operation_id = f"research:{research_identity(run_id)}:Result:v1"
        self.store.begin_operation(operation_id, request.run_id, "tool")
        definition = descriptor['definition']
        binding = self.capabilities.binding_for(definition['key'], definition['contract_version'])
        producer = CapabilityExecutionInput(
            request.account_id,
            request.run_id,
            request.execution_id,
            request.invocation_id,
            binding.workflow_name,
            definition['limits']['max_duration_seconds'],
            definition['retry_policy']['max_attempts'],
            operation_id,
        )
        producer = replace(producer, lease_token=execution_fence.get()[3])

        def ref(output, kind):
            return ResultRef(
                str(uuid5(NAMESPACE_URL, f"{request.invocation_id}:{kind}")),
                kind,
                str(output.file_id),
                str(output.file_id),
                request.invocation_id,
                request.execution_id,
                operation_id,
                output.sha256,
            )

        subject, evidence = ref(markdown, "markdown"), ref(check_file, "check_evidence")
        # The exact file relation is committed before result publication, so retries can recover it.
        with UnitOfWork(self.database) as uow:
            self.capabilities.invocation_context(uow, request)
            uow.execute(
                "UPDATE research_reports SET markdown_file_id=%s WHERE invocation_id=%s",
                (markdown.file_id, request.invocation_id),
            )
        self.capabilities.commit_result(
            producer,
            CapabilityResult(
                "succeeded" if checks["passed"] else "insufficient",
                ("Research Markdown and basic checks are available. " if checks["passed"] else "Research insufficient; HTML has not been generated. ")
                + json.dumps(self._research_feedback(run_id, passed=checks["passed"]), ensure_ascii=False)[:1800],
                outputs=(subject, evidence),
                checks=(
                    Check(
                        "research.basic",
                        "1",
                        subject.ref_id,
                        "passed" if checks["passed"] else "failed",
                        evidence.ref_id,
                    ),
                ),
                gaps=tuple(checks["gaps"]),
            ),
        )
        return 1
