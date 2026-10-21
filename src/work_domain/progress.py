"""Trusted, revision-scoped complex-call progress; never accepts model summaries as facts."""

from __future__ import annotations

import hashlib
import os

from work_domain.models import digest
from work_domain.persistence import WorkRepository


class ProgressBlocked(RuntimeError):
    def __init__(self, reason):
        self.code = reason
        super().__init__(reason)


def blocked_reason(uow, work):
    row = uow.execute(
        "SELECT blocked_reason FROM work_progress WHERE account_id=%s AND work_id=%s "
        "AND requirement_revision=%s",
        (work["account_id"], work["work_id"], work["current_requirement_revision"]),
    ).fetchone()
    return row["blocked_reason"] if row else None


def reset(uow, work):
    # Called only by an audited explicit user intervention, under the Work lock.
    uow.execute(
        "SELECT reset_work_progress(%s,%s,%s)",
        (work["account_id"], work["work_id"], work["current_requirement_revision"]),
    )


def research_facts(snapshot):
    """Exclude producer IDs, duplicate claims and presentation ordering."""
    claims: dict[tuple[str, str], set[tuple[str, str | None]]] = {}
    for claim in snapshot.get("claims", []):
        subject = (" ".join(claim["statement"].casefold().split()), claim["importance"])
        claims.setdefault(subject, set()).update(
            (item["canonical_uri"], item["content_hash"]) for item in claim["evidence"]
        )
    return [
        {
            "statement": subject[0],
            "importance": subject[1],
            "evidence": sorted(evidence, key=lambda item: (item[0], item[1] or "")),
        }
        for subject, evidence in sorted(claims.items())
    ]


def result_facts(uow, invocation, payload):
    if payload["outcome"] != "succeeded":
        return []
    report = (
        uow.execute(
            "SELECT rr.snapshot FROM research_reports rr JOIN capability_invocations i "
            "ON i.invocation_id=rr.invocation_id AND i.run_id=rr.run_id "
            "WHERE i.account_id=%s AND i.invocation_id=%s",
            (invocation["account_id"], invocation["invocation_id"]),
        ).fetchone()
        if invocation["capability_key"] == "research"
        else None
    )
    # The semantic Research snapshot is a trusted producer fact, not LLM summary text.
    if (
        report
        and report["snapshot"].get("claims")
        and all(c["verdict"] == "passed" for c in payload["checks"])
    ):
        return [digest({"research": research_facts(report["snapshot"])})]
    rows = uow.execute(
        "SELECT f.ref_id,f.type,sf.sha256 AS hash,v.html AS artifact_content "
        "FROM capability_result_refs f LEFT JOIN stored_files sf "
        "ON sf.account_id=f.account_id AND sf.file_id=f.file_id AND sf.status='ready' "
        "LEFT JOIN artifact_versions v ON v.account_id=f.account_id "
        "AND v.artifact_version_id=f.artifact_version_id AND v.status='completed' "
        "WHERE f.account_id=%s AND f.invocation_id=%s AND f.type<>'check_evidence'",
        (invocation["account_id"], invocation["invocation_id"]),
    ).fetchall()
    facts = []
    for row in rows:
        content_hash = row["hash"] or (
            hashlib.sha256(row["artifact_content"].encode()).hexdigest()
            if row["artifact_content"]
            else None
        )
        if not content_hash:
            continue
        subject_checks = [c for c in payload["checks"] if c["subject_ref"] == str(row["ref_id"])]
        if any(c["verdict"] != "passed" for c in subject_checks):
            continue
        subject = {
            "capability": invocation["capability_key"],
            "type": row["type"],
            "hash": content_hash,
        }
        facts.append(digest(subject))
        for check in subject_checks:
            facts.append(
                digest(
                    {
                        **subject,
                        "verifier": check["verifier_key"],
                        "version": check["verifier_version"],
                    }
                )
            )
    return facts


def record_result(uow, run, invocation, payload):
    if (
        run["source_kind"] != "work"
        or invocation["descriptor"]["definition"]["executor_kind"] == "atomic"
    ):
        return
    work = WorkRepository.get(uow, run["account_id"], run["work_id"], lock=True)
    if (
        work["status"] != "active"
        or work["current_requirement_revision"] != run["requirement_revision"]
        or work["control_epoch"] != run["work_control_epoch"]
        or work["active_coordinator_run_id"] != run["run_id"]
    ):
        return
    if (
        payload["outcome"] in {"cancelled", "uncertain"}
        or (payload.get("error") or {}).get("retry_class") == "requires_input"
    ):
        return
    identity = (run["account_id"], run["work_id"], run["requirement_revision"])
    limits = [
        int(os.getenv(name, "3"))
        for name in ("WORK_REPEATED_FAILURE_LIMIT", "WORK_NO_PROGRESS_LIMIT")
    ]
    if any(not 1 <= value <= 100 for value in limits):
        raise ValueError("invalid Work progress policy limits")
    uow.execute(
        "INSERT INTO work_progress(account_id,work_id,requirement_revision,failure_limit,"
        "no_progress_limit) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (*identity, *limits),
    )
    state = uow.execute(
        "SELECT * FROM work_progress WHERE account_id=%s AND work_id=%s "
        "AND requirement_revision=%s FOR UPDATE",
        identity,
    ).fetchone()
    if uow.execute(
        "SELECT 1 FROM work_progress_samples WHERE account_id=%s AND invocation_id=%s",
        (run["account_id"], invocation["invocation_id"]),
    ).fetchone():
        return
    new = 0
    observed_facts = result_facts(uow, invocation, payload)
    for fact in observed_facts:
        new += int(
            uow.execute(
                "INSERT INTO work_progress_facts(account_id,work_id,requirement_revision,"
                "fingerprint,invocation_id) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING fingerprint",
                (*identity, fact, invocation["invocation_id"]),
            ).fetchone()
            is not None
        )
    failed = payload["outcome"] in {"failed", "insufficient"}
    failure = (
        digest(
            {
                "capability": invocation["capability_key"],
                "version": invocation["contract_version"],
                "inputs": invocation["descriptor"]["inputs"],
                "error": (payload.get("error") or {}).get("code")
                or sorted(g["code"] for g in payload.get("gaps", []))
                or payload["outcome"],
            }
        )
        if failed
        else None
    )
    failures = (
        (state["consecutive_failures"] + 1 if state["last_failure"] == failure else 1)
        if failed
        else 0
    )
    timing = run["input_snapshot"]["requirement"]["timing"]["kind"]
    # A quiet occurrence still needs trustworthy output/check facts. A successful
    # empty response is not evidence of an unchanged observation.
    normal_daily = timing == "daily" and not failed and bool(observed_facts)
    no_progress = 0 if new or normal_daily else state["no_progress_count"] + 1
    reason = state["blocked_reason"] or (
        "repeated_failure"
        if failures >= state["failure_limit"]
        else "no_progress"
        if no_progress >= state["no_progress_limit"]
        else None
    )
    uow.execute(
        "INSERT INTO work_progress_samples(account_id,work_id,requirement_revision,invocation_id,"
        "outcome,failure_signature,new_facts) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (*identity, invocation["invocation_id"], payload["outcome"], failure, new),
    )
    uow.execute(
        "UPDATE work_progress SET consecutive_failures=%s,no_progress_count=%s,last_failure=%s,"
        "blocked_reason=%s WHERE account_id=%s AND work_id=%s AND requirement_revision=%s",
        (failures, no_progress, failure, reason, *identity),
    )


def daily_signature(uow, run):
    if run["input_snapshot"]["requirement"]["timing"]["kind"] != "daily":
        return None
    rows = uow.execute(
        "SELECT i.*,r.payload FROM capability_invocations i "
        "LEFT JOIN capability_results r USING(account_id,invocation_id) "
        "WHERE i.account_id=%s AND i.run_id=%s ORDER BY i.invocation_id",
        (run["account_id"], run["run_id"]),
    ).fetchall()
    if not rows or any(
        not r["payload"]
        or r["payload"]["outcome"] != "succeeded"
        or any(c["verdict"] != "passed" for c in r["payload"]["checks"])
        for r in rows
    ):
        return None
    facts = [f.hex() for row in rows for f in result_facts(uow, row, row["payload"])]
    return digest(sorted(set(facts))) if facts else None


def previous_daily(uow, work):
    return uow.execute(
        "SELECT d.* FROM work_daily_results d JOIN runs r USING(account_id,run_id) "
        "WHERE d.account_id=%s AND d.work_id=%s AND d.requirement_revision=%s "
        "ORDER BY r.work_control_epoch DESC LIMIT 1",
        (work["account_id"], work["work_id"], work["current_requirement_revision"]),
    ).fetchone()


def comparison_baseline(uow, work, run):
    baseline = run["input_snapshot"].get("daily_baseline_run_id")
    if baseline is None:
        return None
    return uow.execute(
        "SELECT * FROM work_daily_results WHERE account_id=%s AND work_id=%s "
        "AND requirement_revision=%s AND run_id=%s",
        (work["account_id"], work["work_id"], run["requirement_revision"], baseline),
    ).fetchone()


def accept_daily_result(uow, work, run):
    """Advance a fixed baseline only after successful parent completion in the same transaction."""
    if run["input_snapshot"]["requirement"]["timing"]["kind"] != "daily":
        return
    signature = daily_signature(uow, run)
    if signature is None:
        return
    previous = comparison_baseline(uow, work, run)
    changed = previous is None or previous["signature"] != signature
    inserted = uow.execute(
        "INSERT INTO work_daily_results(account_id,work_id,requirement_revision,run_id,"
        "previous_run_id,signature,changed) VALUES (%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT DO NOTHING RETURNING run_id",
        (
            work["account_id"],
            work["work_id"],
            run["requirement_revision"],
            run["run_id"],
            previous["run_id"] if previous else None,
            signature,
            changed,
        ),
    ).fetchone()
    if not inserted:
        return
    uow.execute(
        "INSERT INTO work_daily_result_refs(account_id,run_id,ref_id) "
        "SELECT account_id,%s,ref_id FROM capability_result_refs WHERE account_id=%s "
        "AND invocation_id IN (SELECT invocation_id FROM capability_invocations WHERE run_id=%s)",
        (run["run_id"], run["account_id"], run["run_id"]),
    )
    if changed and work["continuation"]["kind"] != "awaiting_delivery":
        WorkRepository.event(uow, work, "result_changed", run_id=run["run_id"])
