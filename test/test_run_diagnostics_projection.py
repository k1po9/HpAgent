"""Read-only diagnostic projection checks, using an in-memory retained history.

No application/Beta database or connection settings are read by this module.
"""
from __future__ import annotations

import sqlite3
import unittest

from web_api.queries import QueryService
from web_domain.run_usage_projection import budget_dto


class History:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE runs(account_id TEXT, source_kind TEXT, conversation_id TEXT, "
            "work_id TEXT, created_at TEXT, run_id TEXT)"
        )

    def add(self, run_id, parent="conversation", source="chat", account="owner", created="2026-10-19"):
        self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)", (
            account, source, parent if source == "chat" else None,
            parent if source == "work" else None, created, run_id,
        ))
        return dict(self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())

    def execute(self, query, params):
        return self.db.execute(query.replace("%s", "?"), params)


class RunDiagnosticProjectionTests(unittest.TestCase):
    def test_ordinal_is_independent_of_later_runs_pagination_and_retry_messages(self):
        history = History()
        first = history.add("001")
        retry = history.add("002")
        history.add("003")
        history.add("004", created="2026-10-20")
        self.assertEqual(QueryService._run_ordinal(history, first), 1)
        self.assertEqual(QueryService._run_ordinal(history, retry), 2)
        # A UUID tie-breaker makes persisted equal timestamps deterministic.
        self.assertEqual(QueryService._run_ordinal(history, retry), 2)

    def test_ordinal_scopes_to_real_parent_and_account(self):
        history = History()
        history.add("001", account="other")
        history.add("002", parent="other-conversation")
        target = history.add("003")
        task_first = history.add("004", parent="task", source="work")
        task_second = history.add("005", parent="task", source="work")
        history.add("006", parent="other-task", source="work")
        self.assertEqual(QueryService._run_ordinal(history, target), 1)
        self.assertEqual(QueryService._run_ordinal(history, task_first), 1)
        self.assertEqual(QueryService._run_ordinal(history, task_second), 2)

    def test_unknown_limits_are_not_zero_and_protection_is_not_a_reservation(self):
        result = budget_dto({
            "status": "ok", "mode": "enforce", "policy_version": "unit",
            "used": {"model_total_tokens": 100}, "reserved": {},
            "limits": {"model_output_tokens": 0},
            "final_response_reserve_tokens": 2000,
        })
        self.assertIsNone(result["tokens"]["total"]["limit"])
        self.assertEqual(result["tokens"]["output"]["limit"], 0)
        self.assertIsNone(result["model_calls"]["limit"])
        self.assertEqual(result["tokens"]["total"]["reserved"], 0)
        self.assertEqual(result["final_response_reserve_tokens"], 2000)

    def test_ledger_counts_attempts_without_using_snapshot_or_trace_count(self):
        result = budget_dto({
            "status": "ok", "mode": "observe", "policy_version": "unit",
            "used": {"model_total_tokens": 100}, "reserved": {}, "limits": {},
        }, [
            {"state": "settled", "dimension": "model_calls", "operation_count": 2, "usage_source": "provider"},
            {"state": "reserved", "dimension": "model_calls", "operation_count": 1},
            {"state": "released", "dimension": "model_calls", "operation_count": 1},
        ])
        self.assertEqual(result["tokens"]["total"]["used"], 100)
        self.assertEqual(result["model_calls"]["total_attempts"], 4)
        self.assertEqual(result["usage_state"], "partial")


if __name__ == "__main__":
    unittest.main()


def test_operation_diagnostic_drops_sensitive_unknowns_and_distinguishes_no_attempt():
    from run_domain.diagnostics import operation_diagnostic

    row = dict(
        run_id='run', execution_id='execution', operation_id='operation', operation_type='model',
        operation_status='failed', attempt_no=None, latest_attempt_no=None, fencing_token=None,
        receipt_disposition=None, requirement_revision=None, capability_key=None,
        error_code='Provider error: sensitive-input', result_payload={'api_key': 'sensitive-key'},
        provider_request_body={'messages': ['sensitive-memory']},
    )
    result = operation_diagnostic(row)
    assert result['is_latest_registered_attempt'] is False
    assert result['operation_error_code'] == 'unclassified_error'
    assert result['capability'] is None
    assert result['phase'] == 'model' and result['phase_source'] == 'operation_type'
    assert 'sensitive' not in str(result)
