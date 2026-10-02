"""Bounded, versioned values for accepted mandates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WORK_STATUSES = frozenset({'active', 'pausing', 'paused', 'stopping', 'stopped', 'completed'})
CONTINUATIONS = frozenset({'ready', 'at_time', 'awaiting_input', 'awaiting_delivery',
                           'retry_after', 'blocked', 'none'})


def bounded(value: dict[str, Any], allowed: set[str], limit: int = 16384) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed or value.get('schema_version') != 1:
        raise ValueError('invalid versioned value')
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > limit:
        raise ValueError('value exceeds size limit')
    return value


def digest(value: object) -> bytes:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).digest()


def continuation(kind: str = 'ready', reason: str = 'accepted', **fields: Any) -> dict[str, Any]:
    value = {'schema_version': 1, 'kind': kind, 'reason': reason, **fields}
    bounded(value, {'schema_version', 'kind', 'reason', 'due_at', 'receipt_ref', 'operation_ref'})
    if kind not in CONTINUATIONS or not isinstance(reason, str) or not 1 <= len(reason) <= 500:
        raise ValueError('invalid continuation')
    if kind in {'at_time', 'retry_after'}:
        due = datetime.fromisoformat(str(value.get('due_at', '')))
        if due.tzinfo is None:
            raise ValueError('due_at must include timezone')
    return value


@dataclass(frozen=True)
class Requirement:
    objective: str
    capability_key: str
    spec: dict[str, Any]
    constraints: tuple[str, ...] = ()
    acceptance_criteria: tuple[dict[str, Any], ...] = ()
    completion_mode: str = 'deliverable'
    timing: dict[str, Any] | None = None
    resource_requests: tuple[dict[str, Any], ...] = ()
    deliverable_policy: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        if not isinstance(self.objective, str) or not self.objective.strip() or len(self.objective) > 20000:
            raise ValueError('objective is required and bounded')
        if self.capability_key not in {'reminder', 'research_report'}:
            raise ValueError('unsupported capability')
        if self.completion_mode not in {'deliverable', 'ongoing'}:
            raise ValueError('unsupported completion mode')
        allowed = ({'schema_version', 'content', 'target_ref'} if self.capability_key == 'reminder'
                   else {'schema_version', 'source_strategy', 'report_format'})
        bounded(self.spec, allowed)
        if self.capability_key == 'reminder' and (
            not isinstance(self.spec.get('content'), str) or not self.spec['content'].strip()
        ):
            raise ValueError('reminder content is required')
        if self.capability_key == 'research_report':
            from research_domain.models import SourceStrategy

            raw = self.spec.get('source_strategy', {})
            if not isinstance(raw, dict) or set(raw) - set(SourceStrategy().to_dict()):
                raise ValueError('invalid source strategy')
            SourceStrategy.from_dict(raw)
        timing = self.timing or {'schema_version': 1, 'kind': 'immediate', 'timezone': 'UTC'}
        bounded(timing, {'schema_version', 'kind', 'timezone', 'local_time', 'due_at',
                         'missed_fire_policy'})
        if not isinstance(timing.get('timezone'), str):
            raise ValueError('timing timezone is required')
        try:
            ZoneInfo(timing['timezone'])
        except ZoneInfoNotFoundError as exc:
            raise ValueError('unknown timing timezone') from exc
        if timing['kind'] not in {'immediate', 'once', 'daily'}:
            raise ValueError('unsupported timing')
        if timing['kind'] == 'once':
            continuation('at_time', due_at=timing.get('due_at'))
        if timing['kind'] == 'daily':
            import re

            if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', str(timing.get('local_time'))):
                raise ValueError('daily local_time must be HH:MM')
            if self.completion_mode != 'ongoing':
                raise ValueError('daily mandates must be ongoing')
        if (not isinstance(self.constraints, (list, tuple)) or
            any(not isinstance(item, str) or not item.strip() or len(item) > 1000
                for item in self.constraints)):
            raise ValueError('invalid constraints')
        if not isinstance(self.acceptance_criteria, (list, tuple)):
            raise ValueError('invalid acceptance criteria')
        if not isinstance(self.resource_requests, (list, tuple)) or any(
            not isinstance(item, dict) or set(item) - {'schema_version', 'node_id', 'operations', 'recursive'} or
            item.get('schema_version') != 1 or not isinstance(item.get('node_id'), str) or
            not isinstance(item.get('operations'), list) or not item['operations'] or
            set(item['operations']) - {'list_metadata', 'read_content', 'create_child',
                                       'update_content', 'delete_entry'} or
            not isinstance(item.get('recursive', False), bool)
            for item in self.resource_requests
        ):
            raise ValueError('invalid resource requests')
        try:
            for item in self.resource_requests:
                UUID(item['node_id'])
        except ValueError as exc:
            raise ValueError('resource node_id must be a UUID') from exc
        ids = set()
        for criterion in self.acceptance_criteria:
            if not isinstance(criterion, dict) or set(criterion) != {'id', 'required', 'evidence_types'} or (
                not isinstance(criterion['id'], str) or not criterion['id'] or
                criterion['id'] in ids or not isinstance(criterion['required'], bool) or
                not isinstance(criterion['evidence_types'], list) or
                not criterion['evidence_types'] or
                set(criterion['evidence_types']) - {'research_report', 'operation_receipt', 'user_acceptance'}
            ):
                raise ValueError('invalid acceptance criterion')
            ids.add(criterion['id'])
        policy = self.deliverable_policy or {'schema_version': 1, 'required': False}
        bounded(policy, {'schema_version', 'required', 'directory_id', 'entry_id', 'operation'})
        if not isinstance(policy.get('required'), bool):
            raise ValueError('invalid deliverable policy')
        if policy.get('required') and not policy.get('directory_id'):
            raise ValueError('required save needs a directory')
        try:
            if policy.get('directory_id'):
                UUID(policy['directory_id'])
            if policy.get('entry_id'):
                UUID(policy['entry_id'])
        except (ValueError, TypeError) as exc:
            raise ValueError('deliverable target must be a UUID') from exc
        value = {'objective': self.objective.strip(), 'capability_key': self.capability_key,
                 'spec': self.spec, 'constraints': list(self.constraints),
                 'acceptance_criteria': list(self.acceptance_criteria),
                 'completion_mode': self.completion_mode, 'timing': timing,
                 'resource_requests': list(self.resource_requests), 'deliverable_policy': policy}
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 32768:
            raise ValueError('requirement exceeds size limit')
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Requirement:
        if not isinstance(value, dict):
            raise ValueError('requirement must be an object')
        allowed = set(cls.__dataclass_fields__)
        if set(value) - allowed:
            raise ValueError('unknown requirement fields')
        try:
            requirement = cls(**value)
            requirement.to_dict()
        except (TypeError, KeyError, AttributeError) as exc:
            raise ValueError('invalid requirement structure') from exc
        return requirement
