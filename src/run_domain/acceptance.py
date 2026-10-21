"""Parent-side exact-result adoption, saving and registered evidence evaluation."""

from __future__ import annotations

import json
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg.types.json import Jsonb

from persistence.uow import UnitOfWork
from run_domain.capability_contracts import AcceptancePredicate
from workspace.resources import ResourceDenied


class ResultAdoptionService:
    def __init__(self, database, facts):
        self.database, self.facts = database, facts

    def adopt(self, ref_id):
        with UnitOfWork(self.database) as uow:
            self.facts.adopt(uow, UUID(ref_id))
            return self.facts.read_ref(uow, UUID(ref_id)).to_dict()

    def save(
        self,
        ref_id,
        *,
        operation_id,
        directory_id=None,
        name=None,
        entry_id=None,
        expected_revision=None,
        expected_sha256=None,
    ):
        from workspace.catalog import WorkspaceCatalog

        if bool(entry_id) == bool(directory_id):
            raise ValueError("choose exactly one save destination")
        if entry_id:
            if type(expected_revision) is not int or expected_revision < 1 or not expected_sha256:
                raise ValueError("update needs an exact revision and hash")
            if name is not None:
                raise ValueError("version update does not rename the entry")
        elif expected_revision is not None or expected_sha256 is not None:
            raise ValueError("new entry does not accept version preconditions")
        with UnitOfWork(self.database) as uow:
            run, root = self.facts._context(uow)
            if root["role"] != "root":
                raise ResourceDenied("only parent can save a result")
            ref = self.facts.read_ref(uow, UUID(ref_id))
            file_id = UUID(ref.object_id)
            if ref.type == "artifact_version":
                row = uow.execute(
                    "SELECT file_id FROM artifact_versions WHERE account_id=%s AND artifact_version_id=%s AND status='completed'",
                    (run["account_id"], UUID(ref.version)),
                ).fetchone()
                if not row or not row["file_id"]:
                    raise ResourceDenied("Artifact has no immutable bytes")
                file_id = row["file_id"]
            account_id, run_id = run["account_id"], run["run_id"]
        catalog = WorkspaceCatalog(self.database)
        if entry_id:
            result = catalog.update_file(
                account_id,
                UUID(entry_id),
                run_id,
                file_id,
                expected_revision,
                expected_sha256,
                operation_id,
                work_auto=True,
            )
            return result
        if not directory_id or not name:
            raise ValueError("save needs directory_id and name")
        return {
            "node_id": str(
                catalog.save_file(
                    account_id, UUID(directory_id), file_id, name, operation_id, agent_run_id=run_id
                )
            )
        }

    def evidence(self, criterion_id, predicate: AcceptancePredicate):
        value = predicate.to_dict()
        with UnitOfWork(self.database) as uow:
            run, root = self.facts._context(uow)
            if root["role"] != "root" or run["source_kind"] != "work":
                raise ResourceDenied("completion evidence requires a Work parent")
            self.facts.read_ref(uow, UUID(value["subject_ref"]))
            criterion = uow.execute(
                "SELECT c FROM work_requirements q, jsonb_array_elements(q.acceptance_criteria) c WHERE q.account_id=%s AND q.work_id=%s AND q.revision=%s AND c->>'id'=%s AND c->'evidence_types' ? %s",
                (
                    run["account_id"],
                    run["work_id"],
                    run["requirement_revision"],
                    criterion_id,
                    value["kind"],
                ),
            ).fetchone()
            if not criterion:
                raise ValueError("predicate does not match criterion")
            if not uow.execute(
                "SELECT capability_predicate_valid(%s,%s,%s) AS valid",
                (run["account_id"], run["run_id"], Jsonb(value)),
            ).fetchone()["valid"]:
                raise ValueError("predicate has no exact persisted evidence")
            ref = uuid5(
                NAMESPACE_URL, f"{run['run_id']}:{criterion_id}:{json.dumps(value, sort_keys=True)}"
            )
            uow.execute(
                "INSERT INTO capability_acceptance_evidence(evidence_id,account_id,run_id,execution_id,criterion_id,predicate) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    ref,
                    run["account_id"],
                    run["run_id"],
                    root["execution_id"],
                    criterion_id,
                    Jsonb(value),
                ),
            )
            return {"criterion_id": criterion_id, "type": value["kind"], "ref": str(ref)}


def validate_typed_evidence(uow, run, item):
    row = uow.execute(
        "SELECT predicate FROM capability_acceptance_evidence WHERE account_id=%s AND run_id=%s AND evidence_id=%s AND criterion_id=%s AND predicate->>'kind'=%s",
        (run["account_id"], run["run_id"], UUID(item["ref"]), item["criterion_id"], item["type"]),
    ).fetchone()
    if row:
        ref = uow.execute(
            "SELECT f.*,cr.run_id AS producing_run_id FROM capability_result_refs f JOIN capability_results cr USING(account_id,invocation_id) WHERE f.account_id=%s AND f.ref_id=%s",
            (run["account_id"], UUID(row["predicate"]["subject_ref"])),
        ).fetchone()
        if ref and ref["producing_run_id"] != run["run_id"]:
            from run_domain.capabilities import CapabilityFacts

            CapabilityFacts._authorize_resource(uow, run, ref)
    if (
        not row
        or not uow.execute(
            "SELECT capability_predicate_valid(%s,%s,%s) AS valid",
            (run["account_id"], run["run_id"], Jsonb(row["predicate"])),
        ).fetchone()["valid"]
    ):
        raise ValueError("typed evidence does not resolve to exact current facts")


def required_workspace_saved(uow, run, policy, evidence):
    """A mandatory save matches the selected destination as well as immutable bytes."""
    for item in evidence:
        if item["type"] != "workspace_saved":
            continue
        row = uow.execute(
            "SELECT n.node_id,n.parent_id FROM capability_acceptance_evidence e JOIN workspace_nodes n ON n.account_id=e.account_id AND n.node_id=(e.predicate->>'target_id')::uuid WHERE e.account_id=%s AND e.run_id=%s AND e.evidence_id=%s AND n.deleted_at IS NULL",
            (run["account_id"], run["run_id"], UUID(item["ref"])),
        ).fetchone()
        if row and (
            str(row["node_id"]) == policy.get("entry_id")
            if policy.get("entry_id")
            else str(row["parent_id"]) == policy.get("directory_id")
        ):
            validate_typed_evidence(uow, run, item)
            return True
    return False
