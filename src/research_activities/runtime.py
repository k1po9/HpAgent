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
from uuid6 import uuid7

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
        max_sources: int = 30,
        max_fetches: int = 20,
        max_iterations: int = 3,
        min_evidence: int = 3,
        min_distinct_sources: int = 2,
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
                work = asyncio.create_task(self._run_stage_in_execution(run_id, stage, action))
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
            compact = {key: result[key] for key in ("item_count", "sufficient") if key in result}
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
            task = uow.execute(
                "SELECT q.objective,q.spec->'source_strategy' AS source_strategy FROM work_requirements q JOIN runs r "
                "ON r.account_id=q.account_id AND r.work_id=q.work_id AND r.requirement_revision=q.revision WHERE r.run_id=%s",
                (run_id,),
            ).fetchone()
            request = research_request.get()
            if request is not None and request.execution_id:
                descriptor = self.capabilities.invocation_context(uow, request)
                task = {
                    "objective": descriptor["inputs"]["objective"],
                    "source_strategy": descriptor["inputs"].get("source_strategy", {}),
                }
            if task is None:
                raise LookupError("Research objective is missing")
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
        max_chars = 128 if getattr(service, "default_search", None) == "deepseek" else 240
        return discovery_query(query, strategy, max_chars=max_chars), strategy

    @retryable_transaction
    def _start_iteration(self, run_id: UUID, iteration: int, query: str) -> None:
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            self.repository.start_iteration(uow, run_id, iteration, query)

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

    async def _discover(self, run_id: UUID, iteration: int) -> int:
        query, strategy = self._iteration_context(run_id, iteration)
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
            return existing
        remaining = max(0, self.max_sources - total)
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
            return 0
        reserved_sources = ledger_entry[1] if ledger_entry is not None else remaining
        await asyncio.to_thread(
            self.budget.reserve,
            run_id,
            operation_id,
            {"sources_discovered": reserved_sources},
        )
        from web_retrieval.budget import retrieval_budget_scope

        with retrieval_budget_scope(self.budget, run_id, operation_id):
            candidates = await self.discovery.discover(query, strategy=strategy, limit=remaining)
        with UnitOfWork(self.database) as uow:
            assert_execution_fence(uow)
            source_ids = self.repository.add_candidates(
                uow, run_id, candidates, self.canonicalizer.canonicalize, iteration=iteration
            )
        await asyncio.to_thread(
            self.budget.settle,
            run_id,
            operation_id,
            {"sources_discovered": len(source_ids)},
            "measured",
        )
        return len(source_ids)

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

    async def _fetch(self, run_id: UUID, iteration: int) -> int:
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
                uow, run_id, max(0, self.max_fetches - fetched_total), iteration=iteration
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
        return reserved_fetches

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
            for source in sources:
                content_text = str(source["content_text"])
                paragraph_matches = list(
                    __import__("re").finditer(
                        r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", content_text.rstrip(), __import__("re").S
                    )
                )
                match = paragraph_matches[0] if paragraph_matches else None
                excerpt = match.group(0)[:1000] if match else ""
                if match is not None and excerpt:
                    start = match.start()
                    end = start + len(excerpt)
                    self.repository.add_evidence(
                        uow,
                        EvidenceItem(
                            evidence_id=str(uuid7()),
                            run_id=str(run_id),
                            source_id=str(source["source_id"]),
                            excerpt=excerpt,
                            content_ref=str(source["content_ref"]),
                            source_locator={
                                "type": "web",
                                "canonical_uri": str(source["canonical_uri"]),
                                "content_ref": str(source["content_ref"]),
                                "paragraph_index": 0,
                                "text_span": {"start": start, "end": end},
                                "excerpt": excerpt,
                            },
                            source_quality=int(source["source_tier"]),
                            metadata={"extractor": "research-r3-v1", "iteration": iteration},
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
                evidence_count >= self.min_evidence and source_count >= self.min_distinct_sources
            )
            sufficient = meets_threshold
            value = {
                "evidence_count": evidence_count,
                "source_count": source_count,
                "missing_evidence": max(0, self.min_evidence - evidence_count),
                "missing_sources": max(0, self.min_distinct_sources - source_count),
                "stopped_at_iteration_limit": iteration >= self.max_iterations
                and not meets_threshold,
            }
            self.repository.save_gap_analysis(uow, run_id, iteration, value, sufficient=sufficient)
        operation_id = f"research:{research_identity(run_id)}:GapAnalysis:{iteration}:budget:v1"
        self.budget.reserve(run_id, operation_id, {"research_iterations": 1})
        self.budget.settle(run_id, operation_id, {"research_iterations": 1}, "measured")
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
        if not evidence:
            with UnitOfWork(self.database) as uow:
                return self.repository.save_empty_report(uow, run_id)
        with model_budget_scope(
            owner["account_id"],
            run_id,
            f"research:{research_identity(run_id)}:Synthesis:model:v1",
            phase="research_synthesis",
            execution_attempt=self._attempt(),
            final_response=True,
        ):
            report = await self.synthesis.synthesize(
                str(plan["objective"])
                + (
                    "\nAuthorized prior report claims: "
                    + json.dumps([item["snapshot"] for item in history], ensure_ascii=False)
                    if history
                    else ""
                ),
                [dict(row) for row in evidence],
            )
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
            self.capabilities.invocation_context(uow, request)
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
        producer = CapabilityExecutionInput(
            request.account_id,
            request.run_id,
            request.execution_id,
            request.invocation_id,
            "ResearchCapabilityWorkflow",
            3600,
            4,
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
                "Research Markdown and basic checks are available."
                if checks["passed"]
                else "Research draft requires corrected evidence.",
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
