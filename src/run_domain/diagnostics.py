"""Bounded owner diagnostic projection. Persisted facts remain the only authority.

No input snapshots, descriptors, result payloads, memory or exception messages
are selected. Receipt dispositions describe history, never authorization to act.
"""
from __future__ import annotations

import re
from typing import Any

from persistence.uow import UnitOfWork
from web_domain.errors import ResourceNotFound
from work_domain.persistence import dto

_LIMIT = 100


def safe_error_code(code):
    if code is not None and not re.fullmatch(r'[a-z][a-z0-9_]{0,99}', code):
        return 'unclassified_error'
    return code


def operation_diagnostic(row: dict[str, Any]) -> dict[str, Any]:
    latest = row['attempt_no'] is not None and row['attempt_no'] == row['latest_attempt_no']
    code = row['error_code'] if latest or row['attempt_no'] is None else None
    return {
        **{key: dto(row[key]) for key in (
            'run_id', 'execution_id', 'operation_id', 'operation_type', 'operation_status',
            'attempt_no', 'fencing_token', 'receipt_disposition', 'requirement_revision',
        )},
        # Status/error belong to the Operation, not a historical producing attempt.
        'operation_error_code': safe_error_code(code),
        'is_latest_registered_attempt': latest,
        'phase': row['operation_type'], 'phase_source': 'operation_type',
        'boundary': 'capability' if row['capability_key'] else 'execution',
        'capability': None if row['capability_key'] is None else {
            'key': row['capability_key'], 'contract_version': row['contract_version'],
            'implementation_version': row['implementation_version'],
            'invocation_id': row['invocation_id'],
            'descriptor_digest': bytes(row['descriptor_digest']).hex(),
        },
    }


def run_diagnostics(database, account_id, run_id):
    with UnitOfWork(database) as uow:
        run = uow.execute(
            'SELECT r.run_id,r.work_id,r.requirement_revision,r.status,r.failure_code,r.strategy_kind,'
            'r.executor_key,r.executor_version,r.strategy_policy_version,'
            'b.policy_version AS budget_policy_version '
            'FROM runs r LEFT JOIN run_budgets b USING(run_id) '
            'WHERE r.account_id=%s AND r.run_id=%s', (account_id, run_id),
        ).fetchone()
        if run is None:
            raise ResourceNotFound()
        executions = uow.execute(
            'SELECT execution_id,parent_execution_id,role,status,attempt_no AS recorded_attempt_no '
            'FROM run_executions WHERE account_id=%s AND run_id=%s '
            'ORDER BY created_at,execution_id LIMIT %s', (account_id, run_id, _LIMIT + 1),
        ).fetchall()
        operations = uow.execute(
            'SELECT o.run_id,o.execution_id,o.operation_id,o.operation_type,'
            'o.status AS operation_status,o.error_code,o.requirement_revision,'
            'a.attempt_no,a.fencing_token,rc.disposition AS receipt_disposition,'
            '(SELECT max(a2.attempt_no) FROM execution_operation_attempts a2 '
            ' WHERE a2.operation_id=o.operation_id) AS latest_attempt_no,'
            'i.invocation_id,i.capability_key,i.contract_version,i.implementation_version,i.descriptor_digest '
            'FROM execution_operations o '
            'LEFT JOIN execution_operation_attempts a USING(account_id,run_id,execution_id,operation_id) '
            'LEFT JOIN execution_result_receipts rc USING(account_id,run_id,execution_id,operation_id,attempt_no) '
            'LEFT JOIN capability_invocations i ON i.account_id=o.account_id AND i.run_id=o.run_id '
            ' AND (i.child_execution_id=o.execution_id OR i.invocation_id=o.operation_id) '
            'WHERE o.account_id=%s AND o.run_id=%s '
            'ORDER BY o.started_at DESC,o.operation_id,a.attempt_no DESC NULLS LAST LIMIT %s',
            (account_id, run_id, _LIMIT + 1),
        ).fetchall()
        models = uow.execute(
            'SELECT s.snapshot_id,s.run_id,s.execution_id,s.operation_id,s.execution_attempt,s.fallback_attempt,'
            's.phase,s.content_hash,s.serializer_version,s.entitlement_version,d.dispatched_at '
            'FROM model_input_snapshots s LEFT JOIN model_dispatches d USING(account_id,snapshot_id) '
            'WHERE s.account_id=%s AND s.run_id=%s '
            'ORDER BY s.created_at DESC,s.snapshot_id DESC LIMIT %s',
            (account_id, run_id, _LIMIT + 1),
        ).fetchall()
        return {
            'schema_version': 1,
            'run': {
                **{key: dto(run[key]) for key in (
                    'run_id', 'work_id', 'requirement_revision', 'status', 'strategy_kind',
                    'executor_key', 'executor_version',
                )},
                'failure_code': safe_error_code(run['failure_code']),
                'config_versions': {
                    'strategy_policy_version': run['strategy_policy_version'],
                    'budget_policy_version': run['budget_policy_version'],
                    # These configurations are not frozen per Run today.
                    'agent_tools': None, 'models': None,
                },
            },
            'executions': {'items': dto(executions[:_LIMIT]), 'truncated': len(executions) > _LIMIT},
            'operations': {
                'items': [operation_diagnostic(dict(row)) for row in operations[:_LIMIT]],
                'truncated': len(operations) > _LIMIT,
            },
            'model_attempts': {'items': dto(models[:_LIMIT]), 'truncated': len(models) > _LIMIT},
        }
