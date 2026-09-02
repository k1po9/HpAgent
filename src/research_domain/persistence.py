"""PostgreSQL repositories for Task, source and evidence state."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from difflib import SequenceMatcher
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from uuid6 import uuid7

from persistence.uow import UnitOfWork
from research_domain.models import (
    EvidenceItem,
    ResearchPlan,
    ResearchReport,
    SourceCandidate,
    SourceContent,
)


class TaskRepository:
    def insert(
        self,
        uow: UnitOfWork,
        task_id: UUID,
        account_id: UUID,
        title: str,
        objective: str,
        source_strategy: dict[str, Any],
    ) -> None:
        uow.execute(
            "INSERT INTO tasks(task_id,account_id,task_type,title,objective,source_strategy) "
            "VALUES (%s,%s,'research_report',%s,%s,%s::jsonb)",
            (task_id, account_id, title, objective, json.dumps(source_strategy)),
        )

    def lock_active(
        self, uow: UnitOfWork, account_id: UUID, task_id: UUID
    ) -> dict[str, Any] | None:
        return cast(
            dict[str, Any] | None,
            uow.execute(
                "SELECT * FROM tasks WHERE account_id=%s AND task_id=%s FOR UPDATE",
                (account_id, task_id),
            ).fetchone(),
        )

    def mark_triggered(self, uow: UnitOfWork, task_id: UUID) -> None:
        uow.execute(
            "UPDATE tasks SET last_triggered_at=now(),updated_at=now(),version=version+1 "
            "WHERE task_id=%s",
            (task_id,),
        )

    def update_schedule(
        self,
        uow: UnitOfWork,
        task_id: UUID,
        *,
        schedule_type: str,
        timezone: str,
        expression: str | None,
        enabled: bool,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            uow.execute(
                "UPDATE tasks SET schedule_type=%s,schedule_timezone=%s,"
                "schedule_expression=%s,schedule_enabled=%s,"
                "schedule_version=schedule_version+CASE WHEN "
                "(schedule_type,schedule_timezone,schedule_expression,schedule_enabled) "
                "IS DISTINCT FROM (%s,%s,%s,%s) THEN 1 ELSE 0 END,"
                "updated_at=now(),version=version+1 WHERE task_id=%s RETURNING *",
                (schedule_type, timezone, expression, enabled,
                 schedule_type, timezone, expression, enabled, task_id),
            ).fetchone(),
        )


class ResearchRepository:
    def create_plan(self, uow: UnitOfWork, run_id: UUID, plan: ResearchPlan) -> None:
        task_id = uow.execute("SELECT task_id FROM runs WHERE run_id=%s", (run_id,)).fetchone()[
            "task_id"
        ]
        uow.execute(
            "INSERT INTO research_plans(run_id,task_id,plan_version,plan) "
            "VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT (run_id) DO NOTHING",
            (run_id, task_id, plan.version, json.dumps(plan.to_dict())),
        )

    def load_plan(self, uow: UnitOfWork, run_id: UUID) -> dict[str, Any]:
        row = uow.execute(
            "SELECT p.plan,t.task_id,t.objective FROM research_plans p JOIN tasks t "
            "ON t.task_id=p.task_id WHERE p.run_id=%s",
            (run_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"research plan not found: {run_id}")
        return dict(row)

    def add_candidates(
        self,
        uow: UnitOfWork,
        run_id: UUID,
        candidates: Iterable[SourceCandidate],
        canonicalize,
        *,
        iteration: int = 1,
        question_id: str = "q1",
    ) -> list[UUID]:
        task_id = uow.execute("SELECT task_id FROM runs WHERE run_id=%s", (run_id,)).fetchone()[
            "task_id"
        ]
        source_ids: list[UUID] = []
        for candidate in candidates:
            source_id = uuid7()
            canonical_uri = canonicalize(candidate.uri)
            row = uow.execute(
                "INSERT INTO source_records(source_id,run_id,task_id,provider,source_type,"
                "canonical_uri,title,publisher,published_at,source_tier,metadata,iteration,question_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) "
                "ON CONFLICT (run_id,canonical_uri) DO UPDATE SET "
                "metadata=source_records.metadata || EXCLUDED.metadata,updated_at=now() "
                "RETURNING source_id",
                (
                    source_id,
                    run_id,
                    task_id,
                    candidate.provider,
                    candidate.source_type,
                    canonical_uri,
                    candidate.title,
                    candidate.publisher,
                    candidate.published_at,
                    0 if candidate.metadata.get("preferred_domain") else 2,
                    json.dumps({"snippet": candidate.snippet, **candidate.metadata}),
                    iteration,
                    question_id,
                ),
            ).fetchone()
            source_ids.append(UUID(str(row["source_id"])))
        return source_ids

    def fetch_candidates(
        self, uow: UnitOfWork, run_id: UUID, limit: int, *, iteration: int | None = None
    ) -> list[dict[str, Any]]:
        iteration_sql = " AND iteration=%s" if iteration is not None else ""
        params: tuple[Any, ...] = (
            (run_id, iteration, limit) if iteration is not None else (run_id, limit)
        )
        return list(
            uow.execute(
                "SELECT * FROM source_records WHERE run_id=%s AND fetch_status='discovered' "
                + iteration_sql
                + " ORDER BY source_tier ASC,"
                "CASE WHEN metadata->>'preferred_domain'='true' THEN 0 ELSE 1 END,"
                "published_at DESC NULLS LAST,source_id LIMIT %s",
                params,
            ).fetchall()
        )

    def store_content(
        self,
        uow: UnitOfWork,
        source_id: UUID,
        content: SourceContent,
    ) -> tuple[str | None, bool]:
        source = uow.execute(
            "SELECT run_id FROM source_records WHERE source_id=%s FOR UPDATE", (source_id,)
        ).fetchone()
        if source is None:
            raise LookupError(f"source not found: {source_id}")
        duplicate = uow.execute(
            "SELECT source_id FROM source_records WHERE run_id=%s "
            "AND (content_hash=%s OR canonical_uri=%s) AND fetch_status='fetched' "
            "AND source_id<>%s ORDER BY source_id LIMIT 1",
            (source["run_id"], content.content_hash, content.canonical_uri, source_id),
        ).fetchone()
        if duplicate is not None:
            uow.execute(
                "UPDATE source_records SET fetch_status='duplicate',content_hash=%s,"
                "duplicate_of_source_id=%s,fetched_at=now(),updated_at=now() WHERE source_id=%s",
                (content.content_hash, duplicate["source_id"], source_id),
            )
            return None, True
        content_ref = f"research-content:{source_id}"
        uow.execute(
            "INSERT INTO source_contents(content_ref,source_id,content_text,byte_size) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT (source_id) DO NOTHING",
            (content_ref, source_id, content.text, len(content.text.encode("utf-8"))),
        )
        uow.execute(
            "UPDATE source_records SET canonical_uri=%s,title=%s,author=%s,publisher=%s,"
            "published_at=%s,fetched_at=now(),mime_type=%s,language=%s,content_ref=%s,"
            "content_hash=%s,fetch_status='fetched',metadata=metadata || %s::jsonb,updated_at=now() "
            "WHERE source_id=%s",
            (
                content.canonical_uri,
                content.title,
                content.author,
                content.publisher,
                content.published_at,
                content.mime_type,
                content.language,
                content_ref,
                content.content_hash,
                json.dumps(content.metadata),
                source_id,
            ),
        )
        return content_ref, False

    def mark_fetch_failed(self, uow: UnitOfWork, source_id: UUID, error_code: str) -> None:
        uow.execute(
            "UPDATE source_records SET fetch_status='failed',metadata=metadata || %s::jsonb,"
            "updated_at=now() WHERE source_id=%s",
            (json.dumps({"fetch_error": error_code[:200]}), source_id),
        )

    def evidence_sources(self, uow: UnitOfWork, run_id: UUID) -> list[dict[str, Any]]:
        return list(
            uow.execute(
                "SELECT s.*,c.content_text FROM source_records s JOIN source_contents c "
                "ON c.source_id=s.source_id WHERE s.run_id=%s AND s.fetch_status='fetched' "
                "ORDER BY s.source_id",
                (run_id,),
            ).fetchall()
        )

    def add_evidence(self, uow: UnitOfWork, item: EvidenceItem) -> bool:
        row = uow.execute(
            "INSERT INTO evidence_items(evidence_id,run_id,source_id,excerpt,content_ref,"
            "source_locator,source_quality,metadata) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb) "
            "ON CONFLICT (run_id,source_id) DO NOTHING RETURNING evidence_id",
            (
                UUID(item.evidence_id),
                UUID(item.run_id),
                UUID(item.source_id),
                item.excerpt,
                item.content_ref,
                json.dumps(item.source_locator),
                item.source_quality,
                json.dumps(item.metadata),
            ),
        ).fetchone()
        return row is not None

    def stage_result(self, uow: UnitOfWork, operation_id: str) -> dict[str, Any] | None:
        row = uow.execute(
            "SELECT result FROM research_stage_results WHERE operation_id=%s", (operation_id,)
        ).fetchone()
        return dict(row["result"]) if row else None

    def save_stage_result(
        self,
        uow: UnitOfWork,
        operation_id: str,
        run_id: UUID,
        stage: str,
        result: dict[str, Any],
    ) -> None:
        uow.execute(
            "INSERT INTO research_stage_results(operation_id,run_id,stage,result) "
            "VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT (operation_id) DO NOTHING",
            (operation_id, run_id, stage, json.dumps(result)),
        )

    def start_iteration(self, uow: UnitOfWork, run_id: UUID, iteration: int, query: str) -> None:
        uow.execute(
            "INSERT INTO research_iterations(run_id,iteration,query) VALUES (%s,%s,%s) "
            "ON CONFLICT (run_id,iteration) DO NOTHING",
            (run_id, iteration, query),
        )

    def save_corroboration(
        self, uow: UnitOfWork, run_id: UUID, iteration: int, value: dict[str, Any]
    ) -> None:
        uow.execute(
            "UPDATE research_iterations SET corroboration=%s::jsonb,updated_at=now() "
            "WHERE run_id=%s AND iteration=%s",
            (json.dumps(value), run_id, iteration),
        )

    def save_gap_analysis(
        self,
        uow: UnitOfWork,
        run_id: UUID,
        iteration: int,
        value: dict[str, Any],
        *,
        sufficient: bool,
    ) -> None:
        uow.execute(
            "UPDATE research_iterations SET gap_analysis=%s::jsonb,status=%s,updated_at=now() "
            "WHERE run_id=%s AND iteration=%s",
            (json.dumps(value), "sufficient" if sufficient else "insufficient", run_id, iteration),
        )

    def evidence_for_synthesis(self, uow: UnitOfWork, run_id: UUID) -> list[dict[str, Any]]:
        return list(
            uow.execute(
                "SELECT e.evidence_id,e.excerpt,e.source_locator,e.source_quality,"
                "s.source_id,s.title,s.canonical_uri,s.publisher,s.source_tier "
                "FROM evidence_items e JOIN source_records s ON s.source_id=e.source_id "
                "WHERE e.run_id=%s ORDER BY e.source_quality,e.evidence_id",
                (run_id,),
            ).fetchall()
        )

    def load_report(self, uow: UnitOfWork, run_id: UUID) -> dict[str, Any] | None:
        row = uow.execute("SELECT * FROM research_reports WHERE run_id=%s", (run_id,)).fetchone()
        return dict(row) if row else None

    def save_report(self, uow: UnitOfWork, run_id: UUID, report: ResearchReport) -> int:
        uow.execute(
            "INSERT INTO research_reports(run_id,report_markdown,report_structured_json) "
            "VALUES (%s,%s,%s::jsonb) ON CONFLICT (run_id) DO NOTHING",
            (run_id, report.report_markdown, json.dumps(report.to_dict())),
        )
        evidence = {
            str(row["evidence_id"]): row for row in self.evidence_for_synthesis(uow, run_id)
        }
        for ordinal, claim in enumerate(report.claims, start=1):
            claim_id = uuid5(NAMESPACE_URL, f"hpagent:research:{run_id}:claim:{ordinal}")
            uow.execute(
                "INSERT INTO research_claims(claim_id,run_id,statement,importance,ordinal) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (run_id,ordinal) DO NOTHING",
                (claim_id, run_id, claim.statement, claim.importance, ordinal),
            )
            for evidence_id_text in claim.evidence_ids:
                evidence_row = evidence.get(evidence_id_text)
                if evidence_row is None:
                    raise ValueError("claim cites EvidenceItem outside the Research Run")
                evidence_id = UUID(evidence_id_text)
                citation_id = uuid5(
                    NAMESPACE_URL,
                    f"hpagent:research:{run_id}:citation:{claim_id}:{evidence_id}",
                )
                uow.execute(
                    "INSERT INTO research_citations(citation_id,run_id,claim_id,evidence_id,locator) "
                    "VALUES (%s,%s,%s,%s,%s::jsonb) "
                    "ON CONFLICT (claim_id,evidence_id) DO NOTHING",
                    (
                        citation_id,
                        run_id,
                        claim_id,
                        evidence_id,
                        json.dumps(evidence_row["source_locator"]),
                    ),
                )
        return len(report.claims)

    def verify_citations(self, uow: UnitOfWork, run_id: UUID) -> tuple[int, bool]:
        rows = uow.execute(
            "SELECT c.citation_id,c.claim_id,c.locator,e.source_locator,e.source_quality,"
            "e.excerpt,e.content_ref,e.run_id AS evidence_run_id,cl.importance,"
            "s.run_id AS source_run_id,s.canonical_uri,sc.content_text "
            "FROM research_citations c JOIN evidence_items e ON e.evidence_id=c.evidence_id "
            "JOIN research_claims cl ON cl.claim_id=c.claim_id "
            "JOIN source_records s ON s.source_id=e.source_id "
            "JOIN source_contents sc ON sc.content_ref=e.content_ref WHERE c.run_id=%s",
            (run_id,),
        ).fetchall()
        needs_review = False
        claim_states: dict[UUID, list[str]] = {}
        for row in rows:
            locator = dict(row["locator"])
            source_locator = dict(row["source_locator"])
            start = locator.get("text_span", {}).get("start")
            end = locator.get("text_span", {}).get("end")
            text = str(row["content_text"])
            locator_matches = (
                locator == source_locator
                and row["evidence_run_id"] == run_id
                and row["source_run_id"] == run_id
                and locator.get("canonical_uri") == row["canonical_uri"]
                and locator.get("content_ref") == row["content_ref"]
                and isinstance(start, int) and isinstance(end, int)
                and 0 <= start < end <= len(text)
                and text[start:end] == row["excerpt"]
                and locator.get("excerpt") == row["excerpt"]
            )
            if not locator_matches:
                status, detail = "invalid", {"reason": "locator_mismatch"}
                needs_review = True
            elif row["importance"] == "important" and int(row["source_quality"]) > 1:
                status, detail = "weak_source", {"reason": "important_claim_requires_tier_0_or_1"}
                needs_review = True
            else:
                status, detail = "verified", {}
            uow.execute(
                "UPDATE research_citations SET verification_status=%s,"
                "verification_detail=%s::jsonb,verified_at=now() WHERE citation_id=%s",
                (status, json.dumps(detail), row["citation_id"]),
            )
            claim_states.setdefault(UUID(str(row["claim_id"])), []).append(status)
        claims = uow.execute(
            "SELECT claim_id FROM research_claims WHERE run_id=%s", (run_id,)
        ).fetchall()
        for claim in claims:
            statuses = claim_states.get(UUID(str(claim["claim_id"])), [])
            if not statuses or all(value == "invalid" for value in statuses):
                evidence_status = "unsupported"
                needs_review = True
            elif any(value in {"invalid", "weak_source"} for value in statuses):
                evidence_status = "weak"
                needs_review = True
            else:
                evidence_status = "supported"
            uow.execute(
                "UPDATE research_claims SET evidence_status=%s WHERE claim_id=%s",
                (evidence_status, claim["claim_id"]),
            )
        uow.execute(
            "UPDATE research_reports SET citation_status=%s,updated_at=now() WHERE run_id=%s",
            ("needs_review" if needs_review else "verified", run_id),
        )
        return len(rows), needs_review

    @staticmethod
    def _claim_key(statement: str) -> str:
        return re.sub(r"[^\w]+", " ", statement.casefold()).strip()

    def compare_previous_report(self, uow: UnitOfWork, run_id: UUID) -> dict[str, Any]:
        current_run = uow.execute(
            "SELECT task_id,created_at FROM runs WHERE run_id=%s", (run_id,)
        ).fetchone()
        if current_run is None:
            raise LookupError(f"research Run not found: {run_id}")
        previous = uow.execute(
            "SELECT rr.run_id FROM research_reports rr JOIN runs r ON r.run_id=rr.run_id "
            "WHERE r.task_id=%s AND r.status='completed' AND r.created_at<%s "
            "ORDER BY r.created_at DESC,r.run_id DESC LIMIT 1",
            (current_run["task_id"], current_run["created_at"]),
        ).fetchone()

        def snapshot(target: UUID) -> dict[str, Any]:
            rows = uow.execute(
                "SELECT cl.claim_id,cl.statement,cl.importance,"
                "COALESCE(jsonb_agg(DISTINCT jsonb_build_object("
                "'evidence_id',e.evidence_id,'source_id',s.source_id,"
                "'canonical_uri',s.canonical_uri,'content_hash',s.content_hash)) "
                "FILTER (WHERE e.evidence_id IS NOT NULL),'[]'::jsonb) AS evidence "
                "FROM research_claims cl LEFT JOIN research_citations c ON c.claim_id=cl.claim_id "
                "LEFT JOIN evidence_items e ON e.evidence_id=c.evidence_id "
                "LEFT JOIN source_records s ON s.source_id=e.source_id "
                "WHERE cl.run_id=%s GROUP BY cl.claim_id ORDER BY cl.ordinal",
                (target,),
            ).fetchall()
            return {"run_id": str(target), "claims": [
                {"claim_id": str(row["claim_id"]), "statement": row["statement"],
                 "importance": row["importance"], "evidence": list(row["evidence"])}
                for row in rows
            ]}

        current = snapshot(run_id)
        prior = snapshot(UUID(str(previous["run_id"]))) if previous else {"claims": []}
        unmatched = list(prior["claims"])
        diff: dict[str, list[dict[str, Any]]] = {
            "new": [], "changed": [], "continuing": [], "invalidated": []
        }
        for claim in current["claims"]:
            key = self._claim_key(str(claim["statement"]))
            exact = next((old for old in unmatched if self._claim_key(str(old["statement"])) == key), None)
            best = exact
            if best is None and unmatched:
                candidate = max(
                    unmatched,
                    key=lambda old: SequenceMatcher(
                        None, key, self._claim_key(str(old["statement"]))
                    ).ratio(),
                )
                if SequenceMatcher(
                    None, key, self._claim_key(str(candidate["statement"]))
                ).ratio() >= 0.6:
                    best = candidate
            if best is None:
                diff["new"].append(claim)
                continue
            unmatched.remove(best)
            same_statement = self._claim_key(str(best["statement"])) == key
            same_sources = {
                (item["canonical_uri"], item["content_hash"]) for item in claim["evidence"]
            } == {
                (item["canonical_uri"], item["content_hash"]) for item in best["evidence"]
            }
            bucket = "continuing" if same_statement and same_sources else "changed"
            diff[bucket].append({"current": claim, "previous": best})
        diff["invalidated"] = unmatched
        structured = self.load_report(uow, run_id)
        if structured is None:
            raise LookupError(f"research report not found: {run_id}")
        report_json = structured["report_structured_json"]
        if isinstance(report_json, str):
            report_json = json.loads(report_json)
        report_json = {**dict(report_json), "snapshot": current, "daily_diff": diff}
        labels = (("new", "新增"), ("changed", "变化"),
                  ("continuing", "持续关注"), ("invalidated", "失效 / 撤回"))
        lines = ["## Daily Diff"]
        for key_name, label in labels:
            lines.append(f"### {label}")
            for item in diff[key_name]:
                value = item.get("current", item) if isinstance(item, dict) else item
                lines.append(f"- {value.get('statement', '')}")
            if not diff[key_name]:
                lines.append("- 无")
        markdown = str(structured["report_markdown"])
        marker = "\n## Daily Diff\n"
        if marker in markdown:
            markdown = markdown.split(marker, 1)[0]
        markdown = markdown.rstrip() + "\n\n" + "\n".join(lines) + "\n"
        uow.execute(
            "UPDATE research_reports SET previous_run_id=%s,snapshot=%s::jsonb,"
            "daily_diff=%s::jsonb,report_structured_json=%s::jsonb,report_markdown=%s,"
            "updated_at=now() WHERE run_id=%s",
            (previous["run_id"] if previous else None, json.dumps(current), json.dumps(diff),
             json.dumps(report_json), markdown, run_id),
        )
        return diff

    def publish_report_artifact(self, uow: UnitOfWork, run_id: UUID, html: str) -> UUID:
        report = self.load_report(uow, run_id)
        if report is None:
            raise LookupError(f"research report not found: {run_id}")
        if report["artifact_id"] is not None:
            return UUID(str(report["artifact_id"]))
        artifact_id = uuid5(NAMESPACE_URL, f"hpagent:research:{run_id}:artifact")
        version_id = uuid5(NAMESPACE_URL, f"hpagent:research:{run_id}:artifact:v1")
        uow.execute(
            "SELECT publish_research_artifact(%s,%s,%s,%s)",
            (run_id, artifact_id, version_id, html),
        ).fetchone()
        return artifact_id
