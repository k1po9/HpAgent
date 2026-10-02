from datetime import UTC, datetime

import pytest

from orchestration.execution_strategy import StrategyRegistry, WorkExecutionPlanner
from work_domain.models import Requirement
from work_domain.timing import initial_continuation, next_daily


@pytest.mark.parametrize('capability,strategy', [('reminder','deterministic'), ('research_report','fixed_workflow'), ('generic_work','generic_agent')])
def test_registered_strategy_is_versioned_and_unknown_never_falls_back(capability, strategy):
    decision = WorkExecutionPlanner().plan({'capability_key': capability})
    assert decision.strategy_kind == strategy and decision.executor_version == 1
    with pytest.raises(ValueError, match='unsupported'):
        WorkExecutionPlanner().plan({'capability_key': 'arbitrary_workflow'})
    with pytest.raises(ValueError, match='unsupported'):
        StrategyRegistry().validate_run({'source_kind': 'work', **decision.to_dict(), 'executor_version': 99})


def test_shanghai_design_date_and_dst_gap_fold_policy():
    step = initial_continuation({'kind': 'once', 'timezone': 'Asia/Shanghai', 'due_at': '2026-10-02T09:00:00+08:00'})
    assert step['due_at'] == '2026-10-02T01:00:00+00:00'
    timing = {'kind': 'daily', 'timezone': 'America/New_York', 'local_time': '02:30'}
    assert next_daily(timing, datetime(2026, 3, 8, 0, tzinfo=UTC)) == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)
    timing['local_time'] = '01:30'
    first = next_daily(timing, datetime(2026, 11, 1, 0, tzinfo=UTC))
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert next_daily(timing, first) == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)


@pytest.mark.parametrize('changes', [
    {'timing': {'schema_version': 1, 'kind': 'once', 'timezone': 'Asia/Shanghai', 'due_at': '2026-10-02T09:00:00'}},
    {'spec': {'schema_version': 1, 'content': 'Reminder', 'target_ref': 'private_qq_group'}},
    {'timing': {'schema_version': 1, 'kind': 'daily', 'timezone': 'UTC', 'local_time': '09:00', 'missed_fire_policy': 'all_history'}, 'completion_mode': 'ongoing'},
])
def test_ambiguous_timing_unauthorized_target_or_unbounded_catchup_rejected(changes):
    with pytest.raises(ValueError):
        Requirement.from_dict({'objective': 'Reminder', 'capability_key': 'reminder',
                               'spec': {'schema_version': 1, 'content': 'Reminder'}, **changes})
