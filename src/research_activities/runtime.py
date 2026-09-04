"""Persisted, idempotent Activities for bounded iterative Research."""

from __future__ import annotations

import asyncio
import html
import json
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from temporalio import activity
from uuid6 import uuid7

from agent_execution.model_budget_context import model_budget_scope
from agent_execution.run_budget import RunBudgetService
from agent_execution.tracing.repository import PostgresTraceRepository
from orchestration.research_workflow import (
    ResearchIterationInput,
    ResearchStageRef,
    ResearchWorkflowInput,
)
from persistence.uow import UnitOfWork, retryable_transaction
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
from research_domain.services import ResearchTaskCommandService, TaskBusy, TaskNotActive
from web_domain.errors import ResourceNotFound
from web_domain.services import CommandService


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
        self.repository = ResearchRepository()
        self.budget = RunBudgetService(database)
        self.trace = PostgresTraceRepository(database)
        self.task_commands = ResearchTaskCommandService(database)

    @activity.defn
    async def trigger_scheduled_research_activity(
        self, request: dict[str, Any]
    ) -> dict[str, str | bool]:
        if int(request.get("schema_version", 0)) != 1 or not request.get("fire_id"):
            raise ValueError("invalid scheduled Research request")
        account_id = UUID(str(request["account_id"]))
        task_id = UUID(str(request["task_id"]))
        key = f"research-schedule:{task_id}:{request['fire_id']}"
        try:
            result = await asyncio.to_thread(
                self.task_commands.trigger_task, account_id, task_id, key
            )
        except (TaskBusy, TaskNotActive, ResourceNotFound) as exc:
            return {"created": False, "reason": type(exc).__name__}
        return {"created": True, "run_id": str(result.body["run_id"])}

    @staticmethod
    def _root_event_id(run_id: UUID) -> UUID:
        return uuid5(NAMESPACE_URL, f"hpagent:research:{run_id}:root")

    @staticmethod
    def _event_id(run_id: UUID, stage: str) -> UUID:
        return uuid5(NAMESPACE_URL, f"hpagent:research:{run_id}:{stage}")

    @staticmethod
    def _ref(run_id: UUID, stage: str, result: dict[str, Any]) -> ResearchStageRef:
        ref: ResearchStageRef = {
            "schema_version": 1,
            "run_id": str(run_id),
            "stage": stage,
            "ref": f"postgres://research_stage_results/{run_id}:{stage}",
            "item_count": int(result.get("item_count", 0)),
        }
        if "sufficient" in result:
            ref["sufficient"] = bool(result["sufficient"])
        return ref

    @retryable_transaction
    def _load_stage_result(self, operation_id: str) -> dict[str, Any] | None:
        with UnitOfWork(self.database) as uow:
            return cast(dict[str, Any] | None, self.repository.stage_result(uow, operation_id))

    @retryable_transaction
    def _save_stage_result(
        self, operation_id: str, run_id: UUID, stage: str, result: dict[str, Any]
    ) -> None:
        with UnitOfWork(self.database) as uow:
            self.repository.save_stage_result(uow, operation_id, run_id, stage, result)

    @retryable_transaction
    def _budget_ledger_entry(
        self, run_id: UUID, operation_id: str, dimension: str
    ) -> tuple[str, int, int | None] | None:
        """Return a prior reservation so an Activity retry reuses its dimensions."""
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT state,reserved_amount,actual_amount FROM run_usage_ledger "
                "WHERE run_id=%s AND operation_id=%s AND dimension=%s",
                (run_id, operation_id, dimension),
            ).fetchone()
        if row is None:
            return None
        return str(row["state"]), int(row["reserved_amount"]), (
            int(row["actual_amount"]) if row["actual_amount"] is not None else None
        )

    async def _run_stage(
        self, run_id: UUID, stage: str, action: Callable[[], Any]
    ) -> ResearchStageRef:
        operation_id = f"research:{run_id}:{stage}:v1"
        existing = await asyncio.to_thread(self._load_stage_result, operation_id)
        if existing is not None:
            return self._ref(run_id, stage, existing)
        event_id = self._event_id(run_id, stage)
        await asyncio.to_thread(
            self.trace.start_event,
            run_id,
            event_id,
            self._root_event_id(run_id),
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
            value = action()
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
        except Exception:
            await asyncio.to_thread(
                self.trace.finish_event, run_id, event_id, "failed", {"error": "stage_failed"}
            )
            raise

    @activity.defn
    async def prepare_research_activity(self, request: ResearchWorkflowInput) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        await asyncio.to_thread(self._prepare, run_id)
        return self._ref(run_id, "Planning", {"item_count": 1})

    @retryable_transaction
    def _prepare(self, run_id: UUID) -> None:
        with UnitOfWork(self.database) as uow:
            run = uow.execute(
                "SELECT status,run_kind FROM runs WHERE run_id=%s FOR UPDATE", (run_id,)
            ).fetchone()
            if run is None or run["run_kind"] != "research":
                raise LookupError(f"research Run not found: {run_id}")
            if run["status"] == "queued":
                uow.execute(
                    "UPDATE runs SET status='running',started_at=GREATEST(now(),created_at),"
                    "updated_at=now(),"
                    "version=version+1 WHERE run_id=%s",
                    (run_id,),
                )
            elif run["status"] not in ("running", "completed"):
                raise RuntimeError(f"research Run cannot start from {run['status']}")
        self.trace.create_trace_run(run_id, {"run_kind": "research"})
        self.trace.start_event(
            run_id,
            self._root_event_id(run_id),
            None,
            "ResearchReport",
            "research_run",
            {
                "max_sources": self.max_sources,
                "max_fetches": self.max_fetches,
                "max_iterations": self.max_iterations,
            },
        )

    @activity.defn
    async def create_research_plan_activity(
        self, request: ResearchWorkflowInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(run_id, "SourceStrategy", lambda: self._create_plan(run_id))

    @retryable_transaction
    def _create_plan(self, run_id: UUID) -> int:
        with UnitOfWork(self.database) as uow:
            task = uow.execute(
                "SELECT t.objective,t.source_strategy FROM tasks t JOIN runs r "
                "ON r.task_id=t.task_id WHERE r.run_id=%s",
                (run_id,),
            ).fetchone()
            if task is None:
                raise LookupError(f"Task not found for research Run: {run_id}")
            strategy_value = task["source_strategy"]
            if isinstance(strategy_value, str):
                strategy_value = json.loads(strategy_value)
            plan = ResearchPlan(
                objective=str(task["objective"]),
                questions=(ResearchQuestion("q1", str(task["objective"]), 1),),
                source_strategy=SourceStrategy.from_dict(dict(strategy_value)),
            )
            self.repository.create_plan(uow, run_id, plan)
            return len(plan.questions)

    def _iteration_context(self, run_id: UUID, iteration: int) -> tuple[str, SourceStrategy]:
        with UnitOfWork(self.database) as uow:
            row = self.repository.load_plan(uow, run_id)
        plan = row["plan"]
        if isinstance(plan, str):
            plan = json.loads(plan)
        suffix = {1: "", 2: " official primary sources", 3: " independent corroboration"}[iteration]
        return f"{row['objective']}{suffix}", SourceStrategy.from_dict(
            dict(plan["source_strategy"])
        )

    @retryable_transaction
    def _start_iteration(self, run_id: UUID, iteration: int, query: str) -> None:
        with UnitOfWork(self.database) as uow:
            self.repository.start_iteration(uow, run_id, iteration, query)

    @activity.defn
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
        operation_id = f"research:{run_id}:SourceDiscovery:{iteration}:budget:v1"
        ledger_entry = await asyncio.to_thread(
            self._budget_ledger_entry, run_id, operation_id, "sources_discovered"
        )
        with UnitOfWork(self.database) as uow:
            existing = int(
                uow.execute(
                    "SELECT count(*) AS count FROM source_records WHERE run_id=%s AND iteration=%s",
                    (run_id, iteration),
                ).fetchone()["count"]
            )
            total = int(
                uow.execute(
                    "SELECT count(*) AS count FROM source_records WHERE run_id=%s", (run_id,)
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
        reserved_sources = ledger_entry[1] if ledger_entry is not None else remaining
        await asyncio.to_thread(
            self.budget.reserve,
            run_id,
            operation_id,
            {"sources_discovered": reserved_sources},
        )
        candidates = await self.discovery.discover(query, strategy=strategy, limit=remaining)
        with UnitOfWork(self.database) as uow:
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
            uow.execute(
                "UPDATE source_records SET source_tier=CASE "
                "WHEN metadata->>'preferred_domain'='true' THEN 0 "
                "ELSE 2 END,updated_at=now() "
                "WHERE run_id=%s AND iteration=%s",
                (run_id, iteration),
            )
            return int(
                uow.execute(
                    "SELECT count(*) AS count FROM source_records WHERE run_id=%s AND iteration=%s",
                    (run_id, iteration),
                ).fetchone()["count"]
            )

    @activity.defn
    async def fetch_research_sources_activity(
        self, request: ResearchIterationInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(
            run_id, f"Fetch:{request.iteration}", lambda: self._fetch(run_id, request.iteration)
        )

    async def _fetch(self, run_id: UUID, iteration: int) -> int:
        with UnitOfWork(self.database) as uow:
            fetched_total = int(
                uow.execute(
                    "SELECT count(*) AS count FROM source_records "
                    "WHERE run_id=%s AND fetch_status IN ('fetched','duplicate','failed')",
                    (run_id,),
                ).fetchone()["count"]
            )
            rows = self.repository.fetch_candidates(
                uow, run_id, max(0, self.max_fetches - fetched_total), iteration=iteration
            )
        operation_id = f"research:{run_id}:Fetch:{iteration}:budget:v1"
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
                fetch_task = asyncio.create_task(self.content.fetch(candidate))
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
                    self.repository.store_content(uow, UUID(str(row["source_id"])), content)
            except Exception as exc:
                with UnitOfWork(self.database) as uow:
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
            return int(
                uow.execute(
                    "SELECT count(*) AS count FROM source_records WHERE run_id=%s "
                    "AND iteration=%s AND fetch_status IN ('fetched','duplicate')",
                    (run_id, iteration),
                ).fetchone()["count"]
            )

    @activity.defn
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
            sources = uow.execute(
                "SELECT s.*,c.content_text FROM source_records s "
                "JOIN source_contents c ON c.source_id=s.source_id LEFT JOIN evidence_items e "
                "ON e.source_id=s.source_id WHERE s.run_id=%s AND s.iteration=%s "
                "AND s.fetch_status='fetched' AND e.evidence_id IS NULL ORDER BY s.source_id",
                (run_id, iteration),
            ).fetchall()
            for source in sources:
                content_text = str(source["content_text"])
                paragraph_matches = list(__import__("re").finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", content_text, __import__("re").S))
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
                    "SELECT count(*) AS count FROM evidence_items WHERE run_id=%s", (run_id,)
                ).fetchone()["count"]
            )

    @activity.defn
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
            row = uow.execute(
                "SELECT count(*) AS evidence_count,count(DISTINCT source_id) AS "
                "source_count,count(*) FILTER (WHERE source_quality<=1) AS high_quality_count "
                "FROM evidence_items WHERE run_id=%s",
                (run_id,),
            ).fetchone()
            value = {key: int(row[key]) for key in row.keys()}
            self.repository.save_corroboration(uow, run_id, iteration, value)
            return value["source_count"]

    @activity.defn
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
            row = uow.execute(
                "SELECT count(*) AS evidence_count,count(DISTINCT source_id) AS "
                "source_count FROM evidence_items WHERE run_id=%s",
                (run_id,),
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
        operation_id = f"research:{run_id}:GapAnalysis:{iteration}:budget:v1"
        self.budget.reserve(run_id, operation_id, {"research_iterations": 1})
        self.budget.settle(run_id, operation_id, {"research_iterations": 1}, "measured")
        return {"item_count": evidence_count, "sufficient": sufficient}

    @activity.defn
    async def synthesize_research_report_activity(
        self, request: ResearchWorkflowInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(run_id, "Synthesis", lambda: self._synthesize(run_id))

    async def _synthesize(self, run_id: UUID) -> int:
        with UnitOfWork(self.database) as uow:
            existing = self.repository.load_report(uow, run_id)
            if existing is not None:
                return int(
                    uow.execute(
                        "SELECT count(*) AS count FROM research_claims WHERE run_id=%s", (run_id,)
                    ).fetchone()["count"]
                )
            plan = self.repository.load_plan(uow, run_id)
            evidence = self.repository.evidence_for_synthesis(uow, run_id)
        if not evidence:
            raise ValueError("research report requires persisted EvidenceItems")
        with model_budget_scope(
            self.budget, str(run_id), f"research:{run_id}:Synthesis:model:v1", final_response=True
        ):
            report = await self.synthesis.synthesize(
                str(plan["objective"]), [dict(row) for row in evidence]
            )
        with UnitOfWork(self.database) as uow:
            return cast(int, self.repository.save_report(uow, run_id, report))

    @activity.defn
    async def verify_research_citations_activity(
        self, request: ResearchWorkflowInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(run_id, "CitationVerification", lambda: self._verify(run_id))

    @retryable_transaction
    def _verify(self, run_id: UUID) -> int:
        with UnitOfWork(self.database) as uow:
            count, _needs_review = self.repository.verify_citations(uow, run_id)
            return cast(int, count)

    @activity.defn
    async def compare_previous_research_activity(
        self, request: ResearchWorkflowInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        return await self._run_stage(
            run_id, "DailyDiff", lambda: self._compare_previous(run_id)
        )

    @retryable_transaction
    def _compare_previous(self, run_id: UUID) -> int:
        with UnitOfWork(self.database) as uow:
            diff = self.repository.compare_previous_report(uow, run_id)
            return sum(len(items) for items in diff.values())

    @activity.defn
    async def publish_research_artifact_activity(
        self, request: ResearchWorkflowInput
    ) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        result = await self._run_stage(
            run_id, "PublishArtifact", lambda: self._publish(run_id)
        )
        if self.markdown_publisher is not None:
            markdown = await asyncio.to_thread(self._report_markdown, run_id)
            await asyncio.to_thread(self.markdown_publisher.publish, run_id, markdown)
        return result

    @retryable_transaction
    def _report_markdown(self, run_id: UUID) -> str:
        with UnitOfWork(self.database) as uow:
            report = self.repository.load_report(uow, run_id)
            if report is None:
                raise LookupError(f"research report not found: {run_id}")
            return str(report["report_markdown"])

    @retryable_transaction
    def _publish(self, run_id: UUID) -> int:
        with UnitOfWork(self.database) as uow:
            report = self.repository.load_report(uow, run_id)
            if report is None:
                raise LookupError(f"research report not found: {run_id}")
            structured = report["report_structured_json"]
            if isinstance(structured, str):
                structured = json.loads(structured)
            title = html.escape(str(structured.get("title") or "Research Report"))
            markdown = html.escape(str(report["report_markdown"]))
            run = uow.execute(
                "SELECT r.run_id,r.created_at,t.objective FROM runs r JOIN tasks t "
                "ON t.task_id=r.task_id WHERE r.run_id=%s", (run_id,)
            ).fetchone()
            claims = uow.execute(
                "SELECT cl.statement,cl.importance,cl.evidence_status,"
                "COALESCE(jsonb_agg(DISTINCT jsonb_build_object('title',s.title,'uri',s.canonical_uri)) "
                "FILTER (WHERE s.source_id IS NOT NULL),'[]'::jsonb) AS sources "
                "FROM research_claims cl LEFT JOIN research_citations c ON c.claim_id=cl.claim_id "
                "LEFT JOIN evidence_items e ON e.evidence_id=c.evidence_id "
                "LEFT JOIN source_records s ON s.source_id=e.source_id "
                "WHERE cl.run_id=%s GROUP BY cl.claim_id ORDER BY cl.ordinal", (run_id,)
            ).fetchall()
            findings = "".join(
                f"<li><strong>{html.escape(str(row['importance']))}</strong> "
                f"{html.escape(str(row['statement']))} "
                f"<em>({html.escape(str(row['evidence_status']))})</em></li>"
                for row in claims
            )
            source_map: dict[str, str] = {}
            for row in claims:
                for source in row["sources"]:
                    source_map[str(source["uri"])] = str(source["title"] or source["uri"])
            sources_html = "".join(
                f"<li><a href='{html.escape(uri, quote=True)}'>{html.escape(label)}</a></li>"
                for uri, label in sorted(source_map.items())
            )
            document = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>{title}</title></head><body><article><h1>{title}</h1>"
                f"<p><time>{html.escape(run['created_at'].isoformat())}</time> · Run "
                f"<code>{run_id}</code></p><h2>Summary</h2>"
                f"<p>{html.escape(str(run['objective']))}</p><h2>Key Findings</h2>"
                f"<ul>{findings}</ul><h2>Sources / Citations</h2><ol>{sources_html}</ol>"
                f"<h2>Research Report (Markdown truth source)</h2>"
                f"<pre style='white-space:pre-wrap'>{markdown}</pre>"
                f"<h2>Research metadata</h2><dl><dt>Citation status</dt>"
                f"<dd>{html.escape(str(report['citation_status']))}</dd></dl>"
                "</article></body></html>"
            )
            self.repository.publish_report_artifact(uow, run_id, document)
            return 1

    @activity.defn
    async def complete_research_activity(self, request: ResearchWorkflowInput) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        await asyncio.to_thread(self._complete, run_id)
        return self._ref(run_id, "Complete", {"item_count": 1})

    @retryable_transaction
    def _complete(self, run_id: UUID) -> None:
        with UnitOfWork(self.database) as uow:
            run = uow.execute(
                "SELECT account_id,conversation_id FROM runs WHERE run_id=%s", (run_id,)
            ).fetchone()
        if run is None:
            raise LookupError(f"research Run not found: {run_id}")
        if run["conversation_id"] is not None:
            CommandService(self.database).complete_run(
                run["account_id"], run_id, "Research report completed."
            )
        else:
            with UnitOfWork(self.database) as uow:
                uow.execute(
                    "UPDATE runs SET status='completed',finished_at=COALESCE(finished_at,now()),"
                    "updated_at=now(),version=version+1 WHERE run_id=%s AND status='running'",
                    (run_id,),
                )
        with UnitOfWork(self.database) as uow:
            uow.execute(
                "UPDATE workflow_executions SET status='completed',"
                "closed_at=COALESCE(closed_at,now()),updated_at=now(),version=version+1 "
                "WHERE run_id=%s AND is_current AND status IN ('scheduled','running')",
                (run_id,),
            )
        self.trace.finish_event(
            run_id, self._root_event_id(run_id), "completed", {"outcome": "report_published"}
        )

    @activity.defn
    async def fail_research_activity(self, request: ResearchWorkflowInput) -> ResearchStageRef:
        request.validate()
        run_id = UUID(request.run_id)
        await asyncio.to_thread(self._fail, run_id)
        return self._ref(run_id, "Failed", {"item_count": 0})

    @retryable_transaction
    def _fail(self, run_id: UUID) -> None:
        with UnitOfWork(self.database) as uow:
            run = uow.execute(
                "SELECT account_id,conversation_id FROM runs WHERE run_id=%s", (run_id,)
            ).fetchone()
        if run is None:
            raise LookupError(f"research Run not found: {run_id}")
        if run["conversation_id"] is not None:
            CommandService(self.database).fail_run(
                run["account_id"], run_id, "research_stage_failed", "Research stage failed."
            )
        else:
            with UnitOfWork(self.database) as uow:
                uow.execute(
                    "UPDATE runs SET status='failed',failure_code='research_stage_failed',"
                    "failure_message='Research stage failed.',"
                    "finished_at=COALESCE(finished_at,now()),updated_at=now(),version=version+1 "
                    "WHERE run_id=%s AND status IN ('queued','running')",
                    (run_id,),
                )
        with UnitOfWork(self.database) as uow:
            uow.execute(
                "UPDATE workflow_executions SET status='failed',"
                "closed_at=COALESCE(closed_at,now()),updated_at=now(),version=version+1 "
                "WHERE run_id=%s AND is_current AND status IN ('scheduled','running')",
                (run_id,),
            )
        try:
            self.trace.finish_event(
                run_id,
                self._root_event_id(run_id),
                "failed",
                {"error_code": "research_stage_failed"},
            )
        except LookupError:
            pass
